"""Safe, deterministic credential discovery for submitted agent repositories.

The reasoning model may later explain a requirement, but it never needs to see a resolved
credential.  This module performs the cheap preflight first and emits a structured manifest the
platform can use to ask for missing secrets before a build or provider call starts.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import math
import os
import re
import stat
from collections import deque
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import AbstractSet, Callable, Iterable, Iterator, Mapping, TYPE_CHECKING, Literal

from pydantic import BaseModel, Field
import yaml

from fi.simulate.runtime.spec import SecretRef

from .python_source import ParseBudget, parsed, source_encoding, utf8_size
from .secrets import process_secret_environment_names
from .source_folder import SubfolderNotFound, agent_folder, agent_scope, in_agent_scope, is_within

_LOG = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .contract import Runtime
    from .runtime_selection import ComposeSelection, NativeSelection

CREDENTIAL_MANIFEST_VERSION = "futureagi.credential-requirements.v1"


class RequirementKind(str, Enum):
    SECRET = "secret"
    CONFIGURATION = "configuration"
    HARNESS_INFRASTRUCTURE = "harness_infrastructure"


class RequirementStatus(str, Enum):
    MISSING = "missing"
    CONFIGURED = "configured"
    OPTIONAL = "optional"
    HARNESS_PROVIDED = "harness_provided"


class CredentialRequirement(BaseModel):
    id: str
    environment_name: str
    provider: str
    purpose: str
    kind: RequirementKind
    required: bool = True
    status: RequirementStatus
    detected_from: list[str] = Field(default_factory=list)
    accepted_secret_types: list[str] = Field(default_factory=list)


class CredentialChoice(BaseModel):
    id: str
    purpose: str
    options: list[list[str]]
    satisfied: bool = False


class CredentialManifest(BaseModel):
    schema_version: str = CREDENTIAL_MANIFEST_VERSION
    source_digest: str
    detected_connectors: list[str] = Field(default_factory=list)
    requirements: list[CredentialRequirement] = Field(default_factory=list)
    credential_choices: list[CredentialChoice] = Field(default_factory=list)
    scanned_files: int = Field(ge=0)
    truncated: bool = False

    @property
    def missing_required(self) -> list[CredentialRequirement]:
        choice_members = {
            name
            for choice in self.credential_choices
            for option in choice.options
            for name in option
        }
        return [
            item
            for item in self.requirements
            if item.required
            and item.status is RequirementStatus.MISSING
            and item.environment_name not in choice_members
        ]

    @property
    def ready(self) -> bool:
        return not self.missing_required and all(
            choice.satisfied for choice in self.credential_choices
        )


_IGNORED_PARTS = {
    ".git",
    ".venv",
    "venv",
    ".tox",
    ".nox",
    "node_modules",
    "test",
    "tests",
    "__pycache__",
    "vendor",
}
_SOURCE_SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".go",
    ".rb",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
}
_CONFIG_NAMES = {
    ".env.example",
    ".env.sample",
    ".env.template",
    "env.example",
    "env.sample",
    "env.template",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
    "pyproject.toml",
    "package.json",
}
_MAX_FILES = 2_000
_MAX_FILE_BYTES = 1_000_000
# The import walk's whole budget: a file limit alone still allows minutes of reading.
_MAX_WALK_BYTES = 32_000_000
_MAX_WALK_IMPORTS = 20_000
# How many folders below the repository the import walk follows. Honest repositories nest a
# dozen or so; resolving a path costs a lookup for each of its folders, and Linux allows 2,000.
_DEEPEST_FOLLOWED = 64
# Linux's PATH_MAX: an import name longer than this can't name a file.
_LONGEST_NAME = 4096
_SCRIPT_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_SCRIPT_IMPORT = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*(?:\(\s*)?|\brequire\s*\(\s*)["']([^"'\n]+)["']"""
)
# An import, or a whole string: matched from the left, a string holding import text is taken
# from its opening quote, before the words in it can be read as an import.
_SCRIPT_IMPORT_OR_STRING = re.compile(
    _SCRIPT_IMPORT.pattern
    + r"""|"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?|`(?:\\.|[^`\\])*`?"""
)
# A type-only import runs nothing. The keyword is matched alone, and the quotes after it found
# by `_without_type_imports`, so the text is scanned once, however many keywords it holds.
_SCRIPT_TYPE_KEYWORD = re.compile(r"\b(?:import|export)\s+type\b")
_SCRIPT_QUOTE_OR_LINE_END = re.compile(r"""["'\n]""")
# How far after the keyword the module's quote may start.
_SCRIPT_TYPE_REACH = 1000

_ENV_DECLARATION = re.compile(
    r"(?m)^(?:export[ \t]+)?([A-Z][A-Z0-9_]{2,})[ \t]*=[ \t]*([^\r\n#]*)"
)
_ENV_TEMPLATE_NAMES = {
    ".env.example",
    ".env.sample",
    ".env.template",
    "env.example",
    "env.sample",
    "env.template",
}
_COMPOSE_NAMES = {
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
}
_PLACEHOLDER_VALUE = re.compile(
    r"^(?:"
    r"\[[^\]]*\]|<[^>]*>|\{[^}]*\}|"
    r"(?:your|replace|insert|change)[-_ ]?(?:me|this|.*)|"
    r"example|placeholder|todo|xxx+"
    r")$",
    re.IGNORECASE,
)
_COMPOSE_VARIABLE = re.compile(r"\$\{([A-Z][A-Z0-9_]{2,})(?:(:?[-?])([^}]*))?\}")


def _literal_compose_environment(content: str) -> set[str]:
    """Names given concrete values by Compose, not host interpolation."""

    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError:
        return set()
    services = document.get("services") if isinstance(document, dict) else None
    if not isinstance(services, dict):
        return set()
    names: set[str] = set()
    for service in services.values():
        environment = service.get("environment") if isinstance(service, dict) else None
        if isinstance(environment, dict):
            pairs = environment.items()
        elif isinstance(environment, list):
            pairs = (
                item.split("=", 1)
                for item in environment
                if isinstance(item, str) and "=" in item
            )
        else:
            continue
        for name, value in pairs:
            if (
                isinstance(name, str)
                and isinstance(value, (str, int, float, bool))
                and str(value).strip()
                and "$" not in str(value)
            ):
                names.add(name.upper())
    return names
_PYTHON_REQUIRED = re.compile(r"os\.environ\s*\[\s*['\"]([A-Z][A-Z0-9_]{2,})['\"]\s*\]")
_PYTHON_GETENV = re.compile(
    r"(?:os\.getenv|os\.environ\.get)\s*\(\s*['\"]([A-Z][A-Z0-9_]{2,})['\"](?:\s*,\s*([^\)]+))?"
)
_JS_ENV = re.compile(r"process\.env\.([A-Z][A-Z0-9_]{2,})")

_SECRET_NAME = re.compile(
    r"(?:^|_)(?:API_?KEY|API_?SECRET|AUTHORIZATION|CREDENTIALS?|PASSWORD|PRIVATE_?KEY|SECRET|TOKEN)(?:_|$)"
)
_HARNESS_PROVIDED = {
    "DATABASE_URL",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "REDIS_URL",
    "TOOLS_API_URL",
    "MCP_URL",
}
_NON_USER_CONFIGURATION = {
    "PORT",
    "HOST",
    "LOG_LEVEL",
    "ENVIRONMENT",
    "NODE_ENV",
    "PYTHONPATH",
}

_PROVIDERS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("LIVEKIT_", "ACCEPTANCE_LIVEKIT_"), "livekit", "connect to the voice room"),
    (("DEEPGRAM_",), "deepgram", "speech-to-text"),
    (("CARTESIA_",), "cartesia", "text-to-speech"),
    (("ELEVENLABS_",), "elevenlabs", "text-to-speech"),
    (("OPENAI_",), "openai", "language or realtime model"),
    (("ANTHROPIC_",), "anthropic", "language model"),
    (("GOOGLE_", "VERTEX_", "CLOUD_ML_"), "google_cloud", "model or cloud runtime"),
    (("VAPI_",), "vapi", "hosted voice agent connection"),
    (("RETELL_",), "retell", "hosted voice agent connection"),
    (("TWILIO_",), "twilio", "telephony connection"),
    (("AWS_",), "aws", "cloud service"),
    (("AZURE_",), "azure", "cloud service"),
    (("MONGODB_", "MONGO_"), "mongodb", "database connection"),
    (("PINECONE_",), "pinecone", "vector database"),
)

_CONNECTOR_SIGNALS = {
    "livekit": ("livekit", "@livekit/", "livekit-agents"),
    "vapi": ("vapi", "@vapi-ai"),
    "retell": ("retell", "retell-sdk"),
    "twilio": ("twilio",),
    "pipecat": ("pipecat",),
    "mcp": ("mcp", "modelcontextprotocol"),
    "http": ("fastapi", "flask", "express", "axios", "httpx"),
}

# Some SDKs consume credentials internally, so the submitted source never calls ``getenv``.
# Connector detection must still make those requirements visible before the expensive build or
# worker-start phase. Keep this list to credentials inherent to the connector itself; model and
# optional application integrations continue to be discovered from explicit source declarations.
_CONNECTOR_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "livekit": ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"),
}

_LIVEKIT_PROVIDER_CREDENTIALS = {
    "deepgram": ("DEEPGRAM_API_KEY",),
    "cartesia": ("CARTESIA_API_KEY",),
    "elevenlabs": ("ELEVENLABS_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
}


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _python_environment_reads(content: str) -> list[tuple[str, bool]]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        # A newer Python syntax must not hide strict reads from an older scanner.
        return [(match.group(1), False) for match in _PYTHON_REQUIRED.finditer(content)]
    nodes = list(ast.walk(tree))
    augmented_targets = {node.target for node in nodes if isinstance(node, ast.AugAssign)}
    nullable_reads = {
        (node.lineno, node.args[0].value)
        for node in nodes
        if isinstance(node, ast.Call)
        and _dotted_name(node.func) in {"os.getenv", "os.environ.get"}
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    found = []
    for node in nodes:
        if (
            isinstance(node, ast.Subscript)
            and (isinstance(node.ctx, ast.Load) or node in augmented_targets)
            and _dotted_name(node.value) == "os.environ"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
            and re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", node.slice.value)
        ):
            name = node.slice.value
            found.append((name, (node.lineno, name) in nullable_reads))
    return found


def _python_sdk_requirements(content: str) -> list[tuple[str, str]]:
    """Discover credentials consumed internally by constructors in submitted Python.

    This is AST-based because model constructors routinely span multiple lines and contain
    nested calls; a regex ending at the first ``)`` silently misses valid ``vertexai=True``
    configurations.  Parsing is read-only and never imports or executes customer code.
    """
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    aliases: dict[str, str] = {}
    imports: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for item in node.names:
                local = item.asname or item.name
                imports[local] = f"{module}.{item.name}" if module else item.name
                if module == "livekit.plugins":
                    aliases[local] = item.name
        elif isinstance(node, ast.Import):
            for item in node.names:
                local = item.asname or item.name.split(".")[0]
                imports[local] = item.name
                prefix = "livekit.plugins."
                if item.name.startswith(prefix):
                    aliases[item.asname or item.name] = item.name[len(prefix) :].split(
                        "."
                    )[0]

    found: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        path = _dotted_name(node.func)
        if not path:
            continue
        parts = path.split(".")
        provider = aliases.get(parts[0], parts[-2] if len(parts) > 1 else "")
        constructor = parts[-1]
        imported_path = imports.get(parts[0], parts[0])
        resolved_path = ".".join([imported_path, *parts[1:]])
        if provider in _LIVEKIT_PROVIDER_CREDENTIALS and constructor in {
            "LLM",
            "STT",
            "TTS",
            "RealtimeModel",
        }:
            for name in _LIVEKIT_PROVIDER_CREDENTIALS[provider]:
                found.add((name, f"sdk:livekit.plugins.{provider}"))
        if provider == "google" and constructor in {"LLM", "STT", "TTS"}:
            vertex = next(
                (
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg == "vertexai"
                ),
                None,
            )
            if isinstance(vertex, ast.Constant) and vertex.value is True:
                for name in (
                    "GOOGLE_APPLICATION_CREDENTIALS",
                    "GOOGLE_APPLICATION_CREDENTIALS_JSON",
                    "GOOGLE_CLOUD_PROJECT",
                ):
                    found.add((name, "sdk:livekit.plugins.google.vertex"))
        # LangChain and Google's first-party SDKs resolve Application Default Credentials
        # internally, so customer code commonly reads only project/location and never calls
        # getenv for the credential file itself. Recognize the constructor and explicit Vertex
        # mode without importing or executing submitted code.
        vertex = next(
            (keyword.value for keyword in node.keywords if keyword.arg == "vertexai"),
            None,
        )
        is_explicit_vertex_client = (
            resolved_path == "langchain_google_genai.ChatGoogleGenerativeAI"
            and isinstance(vertex, ast.Constant)
            and vertex.value is True
        ) or (
            resolved_path in {"google.genai.Client", "genai.Client"}
            and isinstance(vertex, ast.Constant)
            and vertex.value is True
        )
        if is_explicit_vertex_client:
            for name in (
                "GOOGLE_APPLICATION_CREDENTIALS",
                "GOOGLE_APPLICATION_CREDENTIALS_JSON",
                "GOOGLE_CLOUD_PROJECT",
            ):
                found.add((name, f"sdk:{resolved_path}.vertex"))
    return sorted(found)


def discover_credentials(
    root: str | Path,
    *,
    secret_refs: dict[str, SecretRef] | None = None,
    provided_environment: Iterable[str] = (),
    scan_paths: Iterable[str | Path] | None = None,
    template_secrets_required: bool = True,
    agent_subfolder: str | None = None,
    agent_compose_file: str | None = None,
) -> CredentialManifest:
    """Inspect declarations and environment reads without executing submitted code.

    With ``agent_subfolder``, only that agent's own files can require a value: its folder, the
    folders above it, and the code it imports. Anything else shipped alongside it is another
    agent's, so what it reads is listed as optional. A Compose file above the folder belongs to
    the whole repository, so it counts only when ``agent_compose_file`` names it.
    """
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"credential_source_missing: {root}")
    configured = {str(name).upper() for name in provided_environment}
    for alias, ref in (secret_refs or {}).items():
        configured.add(str(alias).upper())
        configured.add(str(ref.key).upper())
        configured.add(_identifier(ref.purpose).upper())
    configured.update(process_secret_environment_names(configured))

    findings: dict[str, dict[str, object]] = {}
    connector_hits: set[str] = set()
    scanned = 0
    truncated = False
    digest = hashlib.sha256()
    walk_stopped = [False]
    imported = (
        imported_by_agent(root, agent_subfolder, stopped=walk_stopped)
        if agent_subfolder
        else frozenset()
    )
    if walk_stopped[0]:
        # Keys in the files it didn't reach count as optional, so say the scan was cut short.
        truncated = True
        _LOG.warning("credential import walk stopped at its limits in %s", agent_subfolder)
    scope = agent_scope(root, agent_subfolder) if agent_subfolder else None
    named_compose = (root / agent_compose_file).resolve() if agent_compose_file else None

    candidates = _candidate_files(root, scan_paths=scan_paths)
    if scope is not None:
        # The file limit must not leave the agent's own files unread.
        candidates.sort(
            key=lambda path: not (
                path in imported
                or in_agent_scope(PurePosixPath(path.relative_to(root).as_posix()), scope)
            )
        )
    for path in candidates:
        if scanned >= _MAX_FILES:
            truncated = True
            break
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > _MAX_FILE_BYTES:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode())
        digest.update(hashlib.sha256(content.encode()).digest())
        lowered = content.lower()
        agents_own = (
            scope is None
            or path in imported
            or in_agent_scope(PurePosixPath(relative), scope)
        )
        if scope is not None and path.name.lower() in _COMPOSE_NAMES:
            agents_own = path == named_compose or PurePosixPath(relative).is_relative_to(scope)
        for connector, signals in _CONNECTOR_SIGNALS.items():
            if agents_own and any(signal in lowered for signal in signals):
                connector_hits.add(connector)

        def record(
            name: str, *, required: bool, declared_default: bool = False
        ) -> None:
            # ALK_* is a reserved control-plane namespace. Provider adapters and the
            # hosted lifecycle legitimately read these values from their process
            # environment, but they are injected at launch and must never be admitted as
            # customer-supplied target configuration.
            if name in _NON_USER_CONFIGURATION or name.startswith("ALK_"):
                return
            item = findings.setdefault(
                name,
                {
                    "required": False,
                    "declared_default": False,
                    "detected_from": set(),
                },
            )
            item["required"] = bool(item["required"]) or (required and agents_own)
            item["declared_default"] = bool(item["declared_default"]) or (
                declared_default and agents_own
            )
            detected = item["detected_from"]
            assert isinstance(detected, set)
            detected.add(relative)

        # An uppercase assignment in source is usually a constant, not an
        # environment declaration. Only env templates use NAME=value syntax.
        if path.name.lower() in _ENV_TEMPLATE_NAMES:
            for match in _ENV_DECLARATION.finditer(content):
                name = match.group(1)
                value = match.group(2).strip().strip("\"'")
                usable_default = bool(value) and not _PLACEHOLDER_VALUE.fullmatch(value)
                record(
                    name,
                    # A blank non-secret setting in an example file documents a knob; it
                    # does not prove that the selected runtime path needs a value. Strict
                    # source reads and Compose's :? operator remain authoritative. Secret
                    # placeholders stay required by default because SDKs commonly consume them
                    # without an explicit getenv call in customer code. Generic certification
                    # may defer template-only names to runtime inspection; strict reads and SDK
                    # constructor evidence remain required in that mode.
                    required=(
                        template_secrets_required
                        and not usable_default
                        and _kind(name) is RequirementKind.SECRET
                    ),
                    declared_default=usable_default,
                )
        if path.name.lower() in _COMPOSE_NAMES:
            if agents_own:
                configured.update(_literal_compose_environment(content))
            for match in _COMPOSE_VARIABLE.finditer(content):
                operator, fallback = match.group(2), (match.group(3) or "").strip()
                # Compose substitutes an unset plain ${NAME} with an empty string. Only its
                # explicit error operators are admission requirements; source-level strict
                # reads can still make the same name required when a built runtime is scanned.
                required = operator in {"?", ":?"}
                record(
                    match.group(1),
                    required=required,
                    declared_default=bool(fallback) and operator not in {"?", ":?"},
                )
        if path.suffix.lower() == ".py":
            for name, guarded in _python_environment_reads(content):
                # Preserve nullable same-line reads such as ``env[NAME] if env.get(NAME) else default``.
                record(name, required=not guarded, declared_default=guarded)
        for match in _PYTHON_GETENV.finditer(content):
            default = (match.group(2) or "").strip()
            record(
                match.group(1),
                # getenv/get is explicitly nullable. Treat it as optional unless
                # a blank env template or a strict indexed read says otherwise.
                required=False,
                declared_default=bool(default),
            )
        for match in _JS_ENV.finditer(content):
            record(match.group(1), required=True)
        if path.suffix.lower() == ".py":
            for name, origin in _python_sdk_requirements(content):
                record(name, required=True)
                detected = findings[name]["detected_from"]
                assert isinstance(detected, set)
                detected.add(origin)

    for connector in sorted(connector_hits):
        for name in _CONNECTOR_REQUIREMENTS.get(connector, ()):
            item = findings.setdefault(
                name,
                {
                    "required": True,
                    "declared_default": False,
                    "detected_from": set(),
                },
            )
            item["required"] = True
            detected = item["detected_from"]
            assert isinstance(detected, set)
            detected.add(f"connector:{connector}")

    requirements = []
    for name, finding in sorted(findings.items()):
        kind = _kind(name)
        required = bool(finding["required"])
        if kind is RequirementKind.CONFIGURATION and finding["declared_default"]:
            required = False
        if kind is RequirementKind.HARNESS_INFRASTRUCTURE:
            status = RequirementStatus.HARNESS_PROVIDED
        elif name in configured:
            status = RequirementStatus.CONFIGURED
        elif not required:
            status = RequirementStatus.OPTIONAL
        else:
            status = RequirementStatus.MISSING
        provider, purpose = _provider(name)
        requirements.append(
            CredentialRequirement(
                id=_identifier(name),
                environment_name=name,
                provider=provider,
                purpose=purpose,
                kind=kind,
                required=required,
                status=status,
                detected_from=sorted(finding["detected_from"]),
                accepted_secret_types=(
                    [_secret_type(name)] if kind is RequirementKind.SECRET else []
                ),
            )
        )

    choices = _credential_choices(requirements)
    manifest_core = {
        "files": digest.hexdigest(),
        "connectors": sorted(connector_hits),
        "requirements": [item.model_dump(mode="json") for item in requirements],
        "credential_choices": [item.model_dump(mode="json") for item in choices],
    }
    return CredentialManifest(
        source_digest=hashlib.sha256(
            json.dumps(manifest_core, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        detected_connectors=sorted(connector_hits),
        requirements=requirements,
        credential_choices=choices,
        scanned_files=scanned,
        truncated=truncated,
    )


def _file_inside(
    candidate: Path, root: Path, deepest: int, left: list[float] | None = None
) -> Path | None:
    """``candidate`` resolved, if it's a plain file in ``root`` at most ``deepest`` parts long.

    ``left`` is the lookups on disk still allowed: one for the file, then one for each part of
    its path, which resolving it looks up in turn.
    """
    if len(candidate.parts) > deepest:
        return None
    if left is not None:
        left[0] -= 1
    if not _plain_file(candidate):
        return None
    if left is not None:
        left[0] -= len(candidate.parts)
    resolved = candidate.resolve()
    return resolved if len(resolved.parts) <= deepest and is_within(resolved, root) else None


def _code_in(folder: Path) -> Iterator[Path]:
    """The Python and script files in ``folder``, outside the folders no one's code is in.

    Those folders aren't entered, and each file costs its name: building a parent for each part,
    as 3.12 does to make a path relative, cost a crafted folder of 31,000 nested packages 2.8 s.
    """
    suffixes = {".py", *_SCRIPT_SUFFIXES}
    for directory, subdirectories, files in os.walk(folder):
        subdirectories[:] = [name for name in subdirectories if name not in _IGNORED_PARTS]
        for name in files:
            if name not in _IGNORED_PARTS and os.path.splitext(name)[1] in suffixes:
                yield Path(directory, name)


def _plain_file(path: Path) -> bool:
    """A regular file small enough to read: a committed link can point at anything."""
    try:
        details = path.lstat()
    except (OSError, ValueError):
        return False
    return stat.S_ISREG(details.st_mode) and details.st_size <= _MAX_FILE_BYTES


def _python_imports(
    content: str, limit: int = _MAX_FILE_BYTES, budget: ParseBudget | None = None
) -> list[tuple[int, str]]:
    # In bytes, as the file's size is: the parser's cost follows them, not the characters.
    if utf8_size(content) > limit or (budget is not None and not budget.spend(content)):
        return []
    tree = parsed(content)
    if tree is None:
        return []
    names: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend((0, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.append((node.level, module))
            names.extend(
                (node.level, f"{module}.{alias.name}".lstrip(".")) for alias in node.names
            )
    return [(level, name) for level, name in names if name]


def _script_imports(content: str) -> list[str]:
    # Here because agent_type_check imports this module.
    from .agent_type_check import _SCRIPT_COMMENT_OR_STRING, _script_kept

    code = _without_type_imports(_SCRIPT_COMMENT_OR_STRING.sub(_script_kept, content))
    return [hit.group(1) for hit in _SCRIPT_IMPORT_OR_STRING.finditer(code) if hit.group(1)]


def _without_type_imports(code: str) -> str:
    """``code`` without its type-only imports: the keyword, then within 1,000 characters on its
    line a quoted module name.

    One regular expression doing this rescans the reach after every keyword, which a file of
    nothing but keywords turns into seconds a megabyte, holding the interpreter throughout. Here
    the searches for opening quotes and those for closing ones each start where the last of
    their kind did or later, and each kind keeps its own answer, so the code is scanned at most
    twice over.
    """
    kept: list[str] = []
    done = 0
    opening_after = _next_quote_or_line_end(code)
    closing_after = _next_quote_or_line_end(code)
    for keyword in _SCRIPT_TYPE_KEYWORD.finditer(code):
        if keyword.start() < done:
            continue
        opening = opening_after(keyword.end())
        if opening == len(code) or code[opening] == "\n":
            continue
        if opening - keyword.end() > _SCRIPT_TYPE_REACH:
            continue
        closing = closing_after(opening + 1)
        if closing == len(code) or code[closing] == "\n":
            continue
        kept.append(code[done : keyword.start()])
        done = closing + 1
    kept.append(code[done:])
    return "".join(kept)


def _next_quote_or_line_end(code: str) -> Callable[[int], int]:
    """Where the first quote or line end at or after a position is, or the code's end.

    Asked from positions that only grow, it scans each stretch of ``code`` once.
    """
    searched_from = stop = -1

    def after(position: int) -> int:
        # No quote or line end lies between searched_from and stop, so the answer stands.
        nonlocal searched_from, stop
        if not searched_from <= position <= stop:
            hit = _SCRIPT_QUOTE_OR_LINE_END.search(code, position)
            searched_from, stop = position, hit.start() if hit else len(code)
        return stop

    return after


def imported_by_agent(
    root: Path,
    subfolder: str,
    *,
    parse_limit: int = _MAX_FILE_BYTES,
    parse_budget: ParseBudget | None = None,
    script_budget: ParseBudget | None = None,
    import_limit: int | None = None,
    lookup_limit: int | None = None,
    stopped: list[bool] | None = None,
) -> frozenset[Path]:
    """The files the agent's folder imports, followed as far as they go.

    Python imports, and JavaScript or TypeScript imports written as a path. A package name that
    only a workspace or a path alias can resolve isn't followed. A conversation waiting on the
    answer passes tighter parse limits than bundling's: a Python file is followed only if it's
    at most ``parse_limit`` bytes and the walk has parsed under ``parse_budget``, and a script
    only while the walk has matched under ``script_budget``. It tries at most ``import_limit``
    import names in all (bundling's 20,000 when not given), and makes at most ``lookup_limit``
    lookups on disk (no limit when not given): a file found costs one for each part of its path,
    and one name can resolve a package for each of its parts. Nothing more than
    ``_DEEPEST_FOLLOWED`` folders below the repository is followed. Given ``stopped``, its one
    item says whether a limit stopped the walk with files or names left to follow.
    """
    root = Path(root).resolve()
    try:
        folder = agent_folder(root, subfolder)
    except SubfolderNotFound:
        return frozenset()
    python_bases = (folder, folder / "src", root, root / "src")
    deepest = len(root.parts) + _DEEPEST_FOLLOWED
    # Each package and each file found is looked up once in the whole walk: resolving one costs a
    # lookup for each folder in it, and a chain of packages names the same ones from every file.
    # Other misses aren't kept: a script name tries a few dozen places, which 20,000 names would
    # fill gigabytes with. A package is looked up only above a file found, so they stay few.
    looked_up: dict[Path, Path | None] = {}
    # A name is tried once, but a crafted chain of packages makes it resolve dozens of paths, each
    # dozens of parts deep: 1,000 names took the save 12 s. What is looked up again costs nothing.
    lookups_left = [math.inf if lookup_limit is None else float(lookup_limit)]

    def found_at(candidates: Iterable[Path]) -> Path | None:
        for candidate in candidates:
            if candidate in looked_up:
                hit = looked_up[candidate]
            elif lookups_left[0] <= 0:
                return None
            else:
                hit = _file_inside(candidate, root, deepest, lookups_left)
                if hit is not None or candidate.name == "__init__.py":
                    looked_up[candidate] = hit
            if hit is not None:
                return hit
        return None

    def python_modules(path: Path, level: int, name: str) -> list[Path]:
        module = name.split(".")
        # A path past the system's limit can't be opened, so it isn't built.
        if len(module) > _DEEPEST_FOLLOWED or len(name) > _LONGEST_NAME:
            return []
        for start in (path.parents[level - 1],) if level else python_bases:
            target = start.joinpath(*module)
            if hit := found_at((target.with_suffix(".py"), target / "__init__.py")):
                # Importing a module runs every package above it first.
                packages: list[Path] = []
                directory = start
                for part in module[:-1]:
                    directory = directory / part
                    if package := found_at((directory / "__init__.py",)):
                        packages.append(package)
                return [hit, *packages]
        return []

    def script_module(path: Path, name: str) -> Path | None:
        if len(name) > _LONGEST_NAME:
            return None
        for start in (path.parent,) if name.startswith(".") else (folder, root):
            target = start / name
            written = [target.with_suffix(suffix) for suffix in (".ts", ".tsx")]
            if hit := found_at(
                (
                    target,
                    *(written if target.suffix == ".js" else ()),
                    *(Path(f"{target}{suffix}") for suffix in _SCRIPT_SUFFIXES),
                    *(target / f"index{suffix}" for suffix in _SCRIPT_SUFFIXES),
                )
            ):
                return hit
        return None

    seen: set[Path] = set()
    pending = deque(sorted(_code_in(folder)))
    own = set(pending)  # Read once already, so importing one doesn't queue it again.
    read = bytes_read = imports_tried = 0
    cut = False
    import_limit = _MAX_WALK_IMPORTS if import_limit is None else import_limit
    while (
        pending
        and read < _MAX_FILES
        and bytes_read < _MAX_WALK_BYTES
        and imports_tried < import_limit
        and lookups_left[0] > 0
    ):
        # The folder's own files first, so the budgets can't spend themselves on a chain.
        path = pending.popleft()
        if len(path.parts) > deepest or not _plain_file(path):
            continue
        read += 1
        try:
            with path.open("rb") as handle:
                raw = handle.read(_MAX_FILE_BYTES + 1)
        except OSError:
            continue
        if len(raw) > _MAX_FILE_BYTES:
            continue  # Grew since it was looked at.
        bytes_read += len(raw)
        # As read_text reads it, line ends included; a .py in the encoding its coding line names,
        # as Python reads it.
        encoding = (source_encoding(raw) if path.suffix == ".py" else None) or "utf-8-sig"
        try:
            # Strictly: replacing runs Python's own error handler for each byte in most codecs,
            # 60 ms a megabyte, where UTF-8's is in C. Python itself refuses such a file.
            text = raw.decode(encoding)
        except (UnicodeError, LookupError):
            # Read as UTF-8, as is a codec source_encoding refuses: a crafted file Python reads in
            # one of those can then give fewer imports here than it does to Python.
            text = raw.decode("utf-8-sig", errors="replace")
        content = text.replace("\r\n", "\n").replace("\r", "\n")
        hits: list[Path] = []
        if path.suffix == ".py":
            names = list(dict.fromkeys(_python_imports(content, parse_limit, parse_budget)))
            # One file can hold more names than the walk may try; the rest aren't tried.
            cut |= len(names) > import_limit - imports_tried
            names = names[: import_limit - imports_tried]
            imports_tried += len(names)
            for level, name in names:
                try:
                    hits.extend(python_modules(path, level, name))
                except (IndexError, ValueError, OSError):
                    continue  # An import that can't be a path in this repository.
        elif script_budget is not None and not script_budget.spend(content):
            continue
        else:
            names = list(dict.fromkeys(_script_imports(content)))
            cut |= len(names) > import_limit - imports_tried
            names = names[: import_limit - imports_tried]
            imports_tried += len(names)
            for name in names:
                try:
                    hits.extend(filter(None, (script_module(path, name),)))
                except (ValueError, OSError):
                    continue
        for hit in hits:
            if hit not in seen:
                seen.add(hit)
                if hit not in own:
                    pending.append(hit)
    if stopped is not None:
        # The loop ends with files left only when a limit ended it.
        stopped[:] = [cut or bool(pending)]
    return frozenset(seen)


def _candidate_files(
    root: Path, *, scan_paths: Iterable[str | Path] | None = None
) -> list[Path]:
    scopes: list[Path] | None = None
    if scan_paths is not None:
        scopes = []
        for value in scan_paths:
            candidate = (root / value).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if candidate.exists():
                scopes.append(candidate)
    result: list[Path] = []
    for path in sorted(root.rglob("*")):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in _IGNORED_PARTS for part in relative.parts):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        if scopes is not None and not any(
            path == scope or (scope.is_dir() and scope in path.parents)
            for scope in scopes
        ):
            continue
        if path.name in _CONFIG_NAMES or path.suffix.lower() in _SOURCE_SUFFIXES:
            result.append(path)
    return result


def _kind(name: str) -> RequirementKind:
    if name in _HARNESS_PROVIDED:
        return RequirementKind.HARNESS_INFRASTRUCTURE
    if _SECRET_NAME.search(name):
        return RequirementKind.SECRET
    return RequirementKind.CONFIGURATION


def _provider(name: str) -> tuple[str, str]:
    for prefixes, provider, purpose in _PROVIDERS:
        if name.startswith(prefixes):
            return provider, purpose
    if name in _HARNESS_PROVIDED:
        return "harness", "generated test infrastructure"
    return "agent", "agent runtime configuration"


def _secret_type(name: str) -> str:
    if "PRIVATE_KEY" in name or name.endswith("CREDENTIALS"):
        return "credential_file"
    if "TOKEN" in name:
        return "token"
    if "SECRET" in name or "PASSWORD" in name:
        return "secret"
    return "api_key"


def _credential_choices(
    requirements: list[CredentialRequirement],
) -> list[CredentialChoice]:
    """Describe common provider auth alternatives without reading secret values."""
    by_name = {item.environment_name: item for item in requirements}
    google_options = [
        ["GEMINI_API_KEY"],
        ["GOOGLE_API_KEY"],
        ["GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"],
        ["GOOGLE_APPLICATION_CREDENTIALS_JSON", "GOOGLE_CLOUD_PROJECT"],
    ]
    # Repositories often keep more than one model backend behind LLM_PROVIDER. A strict
    # credential read inside one branch (or a standalone bakeoff utility in the same runtime
    # tree) must not make every backend credential mandatory. The selected provider's one
    # complete authentication route is the requirement. Without an explicit provider selector
    # we remain conservative and do not merge independent integrations into one choice.
    if "LLM_PROVIDER" in by_name:
        google_options.extend(
            [option for option in (["OPENAI_API_KEY"], ["AGENTCC_API_KEY"])]
        )
    present_options = [
        option for option in google_options if all(name in by_name for name in option)
    ]
    google_names = {
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_APPLICATION_CREDENTIALS_JSON",
    }
    if len(present_options) < 2 or not any(
        any(name in google_names for name in option) for option in present_options
    ):
        return []
    configured = {
        RequirementStatus.CONFIGURED,
        RequirementStatus.HARNESS_PROVIDED,
    }
    satisfied = any(
        all(by_name[name].status in configured for name in option)
        for option in present_options
    )
    if not satisfied:
        # These variables are optional individually but one complete route is
        # mandatory. Mark the members input-capable; the choice prevents the UI
        # from requiring every alternative.
        for name in {name for option in present_options for name in option}:
            if by_name[name].status not in configured:
                by_name[name].required = True
                by_name[name].status = RequirementStatus.MISSING
    return [
        CredentialChoice(
            id="google_model_auth",
            purpose="authenticate the Google/Gemini model runtime",
            options=present_options,
            satisfied=satisfied,
        )
    ]


def _identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


__all__ = [
    "CREDENTIAL_MANIFEST_VERSION",
    "CredentialManifest",
    "CredentialChoice",
    "CredentialRequirement",
    "RequirementKind",
    "RequirementStatus",
    "discover_credentials",
]


class EnvironmentObligation(BaseModel):
    process: str
    environment_name: str
    origin: Literal["runtime_declaration", "compose_required"]
    source_location: str


class ConfigurationAdmission(BaseModel):
    status: Literal["selection_pending", "validation_pending"] = "selection_pending"
    missing: list[EnvironmentObligation] = Field(default_factory=list)

    def require_no_missing_declarations(self) -> None:
        if self.missing:
            raise ValueError(
                "target_runtime_configuration_missing: "
                + ", ".join(
                    f"{item.process}:{item.environment_name} ({item.source_location})"
                    for item in self.missing
                )
            )


def assess_configuration(
    *,
    runtime: Runtime | None = None,
    selected: NativeSelection | ComposeSelection | None = None,
    supplied_by_process: Mapping[str, AbstractSet[str]] | None = None,
) -> ConfigurationAdmission:
    from .runtime_selection import ComposeSelection

    if runtime is None or selected is None:
        return ConfigurationAdmission()
    supplied_by_process = supplied_by_process or {}
    unknown = set(runtime.required_environment) - set(supplied_by_process)
    if unknown:
        raise ValueError(
            "runtime_required_environment_scope_unknown: " + ", ".join(sorted(unknown))
        )
    obligations = [
        EnvironmentObligation(
            process=process,
            environment_name=name,
            origin="runtime_declaration",
            source_location=f"runtime.required_environment.{process}",
        )
        for process, names in runtime.required_environment.items()
        for name in names
    ]
    if isinstance(selected, ComposeSelection):
        for process, service in selected.services.items():
            environment = service.get("environment") or {}
            values = (
                environment.values() if isinstance(environment, dict) else environment
            )
            for value in values:
                for match in re.finditer(
                    r"\$\{([A-Za-z_][A-Za-z0-9_]*):?\?[^}]*\}", str(value)
                ):
                    obligations.append(
                        EnvironmentObligation(
                            process=process,
                            environment_name=match.group(1),
                            origin="compose_required",
                            source_location=f"{selected.compose_file.name}:services.{process}.environment",
                        )
                    )
    return ConfigurationAdmission(
        status="validation_pending",
        missing=[
            obligation
            for obligation in obligations
            if obligation.environment_name
            not in supplied_by_process.get(obligation.process, set())
        ],
    )
