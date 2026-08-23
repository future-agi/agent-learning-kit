"""Deterministic container packaging for repositories that ship no container metadata.

The adapter packages a submitted process; it never generates application behavior.  It accepts
only dependency and entrypoint conventions that can be proven from files in the repository, and
stages a sanitized build context outside the submitted checkout so generated files and secret
configuration never mutate or enter the customer's source tree.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


GENERATED_DOCKERFILE = ".alk-generated.Dockerfile"
GENERATED_PLAN = "generated-runtime.json"
_MAX_FILES = 20_000
_MAX_FILE_BYTES = 64 * 1024 * 1024
_MAX_CONTEXT_BYTES = 512 * 1024 * 1024
_IGNORED_DIRECTORIES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "vendor",
}
_SECRET_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
}
_SECRET_SUFFIXES = {".key", ".p12", ".pem", ".pfx"}
_SECRET_CONTENT = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
)
_PYTHON_ENTRYPOINTS = ("agent.py", "main.py", "app.py", "bot.py", "server.py")
_NODE_ENTRYPOINTS = ("agent.js", "index.js", "main.js", "server.js")


class GeneratedRuntimeError(ValueError):
    """The repository cannot be packaged without unsafe or ambiguous guessing."""


@dataclass(frozen=True)
class GeneratedRuntimePlan:
    language: str
    component: str
    version: str
    dependency_file: str
    install_strategy: str
    extras: tuple[str, ...]
    command: tuple[str, ...]
    dockerfile: str
    context_directory: str = ""

    @property
    def fingerprint(self) -> str:
        payload = asdict(self)
        payload.pop("context_directory", None)
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


def can_generate_runtime(source: str | Path, runtime: Any | None = None) -> bool:
    """Whether a deterministic language manifest exists; this performs no writes."""
    try:
        detect_generated_runtime(source, runtime)
    except GeneratedRuntimeError:
        return False
    return True


def detect_generated_runtime(
    source: str | Path, runtime: Any | None = None
) -> GeneratedRuntimePlan:
    """Return an auditable runtime plan, or one actionable admission error."""
    root = Path(source).expanduser().resolve()
    component = _component_root(root, str(getattr(runtime, "workdir", "") or ""))
    language = str(getattr(runtime, "language", "") or "").strip().lower()
    python_markers = _python_markers(component)
    node_marker = component / "package.json"

    if language in {"python", "py"}:
        if not python_markers:
            raise GeneratedRuntimeError(
                "generated_runtime_dependency_manifest_missing: expected pyproject.toml, "
                "requirements.txt, or a Python lock file"
            )
        language = "python"
    elif language in {"node", "nodejs", "javascript", "typescript", "js", "ts"}:
        if not node_marker.is_file():
            raise GeneratedRuntimeError(
                "generated_runtime_dependency_manifest_missing: expected package.json"
            )
        language = "node"
    elif python_markers and not node_marker.is_file():
        language = "python"
    elif node_marker.is_file() and not python_markers:
        language = "node"
    elif python_markers and node_marker.is_file():
        raise GeneratedRuntimeError(
            "generated_runtime_language_ambiguous: both Python and Node manifests exist; "
            "set runtime.language and runtime.workdir to the intended component"
        )
    else:
        nested = _nested_components(component)
        if len(nested) == 1 and component == root:
            component = nested[0]
            return detect_generated_runtime(
                root,
                _runtime_override(
                    runtime, workdir=component.relative_to(root).as_posix()
                ),
            )
        if len(nested) > 1:
            choices = ", ".join(
                path.relative_to(root).as_posix() for path in nested[:12]
            )
            raise GeneratedRuntimeError(
                "generated_runtime_component_ambiguous: multiple runnable components found: "
                f"{choices}; set runtime.workdir"
            )
        raise GeneratedRuntimeError(
            "generated_runtime_dependency_manifest_missing: no supported Python or Node "
            "dependency manifest was found"
        )

    explicit_command = _explicit_command(runtime)
    relative_component = component.relative_to(root).as_posix()
    if relative_component == ".":
        relative_component = ""
    if language == "python":
        version = _python_language_version(
            component, str(getattr(runtime, "version", "") or "")
        )
        command = explicit_command or _python_command(component)
        dependency, strategy, install, extras = _python_install(
            component, runtime, command
        )
        dockerfile = _python_dockerfile(version, dependency, strategy, install, command)
    else:
        version = _language_version(str(getattr(runtime, "version", "") or ""), "22")
        dependency, strategy, install, runner = _node_install(component)
        command = explicit_command or _node_command(component, runner)
        dockerfile = _node_dockerfile(version, dependency, install, command)
        extras = []
    return GeneratedRuntimePlan(
        language=language,
        component=relative_component,
        version=version,
        dependency_file=dependency,
        install_strategy=strategy,
        extras=tuple(extras),
        command=tuple(command),
        dockerfile=dockerfile,
    )


def prepare_generated_runtime(
    source: str | Path, destination: str | Path, runtime: Any | None = None
) -> GeneratedRuntimePlan:
    """Detect the runtime and create its sanitized, session-owned build context."""
    source_root = Path(source).expanduser().resolve()
    destination_root = Path(destination).expanduser().resolve()
    try:
        destination_root.relative_to(source_root)
    except ValueError:
        pass
    else:
        raise GeneratedRuntimeError(
            "generated_runtime_destination_inside_source: session artifacts must be outside "
            "the submitted repository"
        )
    plan = detect_generated_runtime(source_root, runtime)
    component = source_root / plan.component if plan.component else source_root
    context = destination_root / "generated-runtime-context"
    if context.exists():
        shutil.rmtree(context)
    context.mkdir(parents=True)
    _copy_sanitized_context(component, context)
    context.joinpath(GENERATED_DOCKERFILE).write_text(plan.dockerfile, encoding="utf-8")
    prepared = GeneratedRuntimePlan(
        **{**asdict(plan), "context_directory": str(context)}
    )
    destination_root.mkdir(parents=True, exist_ok=True)
    destination_root.joinpath(GENERATED_PLAN).write_text(
        json.dumps(
            {
                **asdict(prepared),
                "fingerprint": prepared.fingerprint,
                "generated_files": [
                    f"generated-runtime-context/{GENERATED_DOCKERFILE}",
                    GENERATED_PLAN,
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return prepared


def _runtime_override(runtime: Any | None, **values: str) -> Any:
    payload = {
        "language": str(getattr(runtime, "language", "") or ""),
        "version": str(getattr(runtime, "version", "") or ""),
        "workdir": str(getattr(runtime, "workdir", "") or ""),
        "command": list(getattr(runtime, "command", None) or []),
        "extras": list(getattr(runtime, "extras", None) or []),
    }
    payload.update(values)
    return type("RuntimeHints", (), payload)()


def _component_root(root: Path, configured: str) -> Path:
    # Contracts produced for a selected monorepo component sometimes retain the
    # repository-relative path with a leading slash (for example
    # ``/examples/drive_thru``).  The submitted ``root`` is already the hard
    # filesystem boundary and the selected component, so an absolute hint whose
    # final component names that root means "the selected root".  Never resolve
    # or read the absolute path itself.
    configured_path = Path(configured) if configured else None
    if (
        configured_path is not None
        and configured_path.is_absolute()
        and configured_path.name == root.name
    ):
        return root
    # The same thing is common without a leading slash: understanding runs at
    # a selected component but records its path relative to the original
    # monorepo (``examples/hotel_receptionist``).  If the submitted root already
    # ends in that exact path, it is provenance for the selected root rather
    # than a request to descend into it a second time.
    if configured_path is not None and not configured_path.is_absolute():
        configured_parts = tuple(part for part in configured_path.parts if part != ".")
        if (
            configured_parts
            and tuple(root.parts[-len(configured_parts) :]) == configured_parts
        ):
            return root
    component = (root / configured).resolve() if configured else root
    try:
        component.relative_to(root)
    except ValueError as exc:
        raise GeneratedRuntimeError(
            "generated_runtime_workdir_escapes_repository"
        ) from exc
    if not component.is_dir():
        raise GeneratedRuntimeError(
            f"generated_runtime_workdir_missing: {configured or '.'}"
        )
    return component


def _python_markers(root: Path) -> list[Path]:
    return [
        root / name
        for name in ("pyproject.toml", "requirements.txt", "uv.lock", "poetry.lock")
        if root.joinpath(name).is_file()
    ]


def _nested_components(root: Path) -> list[Path]:
    found: set[Path] = set()
    for marker in ("pyproject.toml", "requirements.txt", "package.json"):
        for path in root.glob(f"*/*/{marker}"):
            if not any(
                part in _IGNORED_DIRECTORIES for part in path.relative_to(root).parts
            ):
                found.add(path.parent)
        for path in root.glob(f"*/{marker}"):
            if not any(
                part in _IGNORED_DIRECTORIES for part in path.relative_to(root).parts
            ):
                found.add(path.parent)
    return sorted(found)


def _explicit_command(runtime: Any | None) -> list[str]:
    value = getattr(runtime, "command", None)
    if not value:
        return []
    if isinstance(value, str):
        try:
            command = shlex.split(value)
        except ValueError as exc:
            raise GeneratedRuntimeError("generated_runtime_command_invalid") from exc
    else:
        command = [str(item) for item in value]
    if not command or any(not item or "\x00" in item for item in command):
        raise GeneratedRuntimeError("generated_runtime_command_invalid")
    return command


def _language_version(value: str, default: str) -> str:
    if not value:
        return default
    match = re.search(r"(?<!\d)(\d{1,2}(?:\.\d{1,2})?)(?!\d)", value)
    return match.group(1) if match else default


def _python_language_version(root: Path, configured: str) -> str:
    """Choose a concrete image version that satisfies a declared Python range.

    ``>=3.10`` is a compatibility floor, not an instruction to use 3.10. Selecting the first
    number from it makes otherwise reproducible projects fail as transitive dependencies retire
    old interpreter wheels. Exact values remain exact; ranges prefer the harness' stable default.
    """
    declared = configured.strip()
    if not declared:
        requires = (
            _pyproject_document(root / "pyproject.toml")
            .get("project", {})
            .get("requires-python", "")
        )
        declared = str(requires or "").strip()
    if not declared:
        return "3.12"
    if not any(marker in declared for marker in "<>=!~,*"):
        return _language_version(declared, "3.12")
    try:
        from packaging.specifiers import InvalidSpecifier, SpecifierSet
        from packaging.version import Version

        specifier = SpecifierSet(declared)
        for candidate in ("3.12", "3.13", "3.11", "3.10", "3.14", "3.9"):
            if Version(candidate) in specifier:
                return candidate
    except (InvalidSpecifier, ValueError):
        pass
    raise GeneratedRuntimeError(
        f"generated_runtime_python_version_unsupported: {declared}"
    )


def _python_install(
    root: Path, runtime: Any | None, command: list[str]
) -> tuple[str, str, list[str], list[str]]:
    extras = _python_extras(root, runtime, command)
    uv_extras = [value for extra in extras for value in ("--extra", extra)]
    uv_script_project = _uv_script_project(root / "pyproject.toml")
    if root.joinpath("uv.lock").is_file() and root.joinpath("pyproject.toml").is_file():
        return (
            "uv.lock",
            "uv-frozen",
            [
                "RUN pip install --no-cache-dir uv==0.8.13",
                "RUN " + shlex.join(["uv", "sync", "--frozen", *uv_extras]),
                'ENV PATH="/app/.venv/bin:$PATH"',
            ],
            extras,
        )
    if root.joinpath("requirements.txt").is_file():
        return (
            "requirements.txt",
            "pip-requirements",
            ["RUN pip install --no-cache-dir -r requirements.txt"],
            [],
        )
    if uv_script_project:
        install = [
            "RUN pip install --no-cache-dir uv==0.8.13",
            "RUN " + shlex.join(["uv", "sync", "--no-cache", *uv_extras]),
            'ENV PATH="/app/.venv/bin:$PATH"',
        ]
        if _needs_livekit_model_download(root / "pyproject.toml"):
            install.append("RUN python -m livekit.agents download-files")
        return (
            "pyproject.toml",
            "uv-script-project-unlocked",
            install,
            extras,
        )
    if (
        root.joinpath("poetry.lock").is_file()
        and root.joinpath("pyproject.toml").is_file()
    ):
        return (
            "poetry.lock",
            "poetry-locked",
            [
                "RUN pip install --no-cache-dir poetry==2.1.3",
                "RUN poetry config virtualenvs.create false && "
                + shlex.join(
                    [
                        "poetry",
                        "install",
                        "--only",
                        "main",
                        "--no-interaction",
                        *(["--extras", " ".join(extras)] if extras else []),
                    ]
                ),
            ],
            extras,
        )
    if root.joinpath("pyproject.toml").is_file():
        target = ".[" + ",".join(extras) + "]" if extras else "."
        return (
            "pyproject.toml",
            "pip-project",
            ["RUN " + shlex.join(["pip", "install", "--no-cache-dir", target])],
            extras,
        )
    raise GeneratedRuntimeError("generated_runtime_python_dependencies_unsupported")


def _pyproject_document(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import tomllib

        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _uv_script_project(path: Path) -> bool:
    uv = _pyproject_document(path).get("tool", {}).get("uv", {})
    return isinstance(uv, dict) and uv.get("package") is False


def _needs_livekit_model_download(path: Path) -> bool:
    dependencies = _pyproject_document(path).get("project", {}).get("dependencies", [])
    if not isinstance(dependencies, list):
        return False
    normalized = [str(value).lower().replace("_", "-") for value in dependencies]
    return any(value.startswith("livekit-agents") for value in normalized) and any(
        value.startswith(("livekit-plugins-silero", "livekit-plugins-turn-detector"))
        for value in normalized
    )


def _python_extras(root: Path, runtime: Any | None, command: list[str]) -> list[str]:
    path = root / "pyproject.toml"
    if not path.is_file():
        return []
    try:
        import tomllib

        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    declared = document.get("project", {}).get("optional-dependencies", {})
    available = (
        sorted(str(name) for name in declared) if isinstance(declared, dict) else []
    )
    requested = [str(item) for item in (getattr(runtime, "extras", None) or [])]
    if requested:
        unknown = sorted(set(requested) - set(available))
        if unknown:
            raise GeneratedRuntimeError(
                "generated_runtime_extra_unknown: " + ", ".join(unknown)
            )
        return sorted(set(requested))
    tokens = re.split(r"[^a-z0-9]+", " ".join(command).lower())
    return [
        extra
        for extra in available
        if extra.lower() not in {"all", "dev", "test", "tests"}
        and extra.lower() in tokens
    ]


def _python_command(root: Path) -> list[str]:
    scripts = _pyproject_scripts(root / "pyproject.toml")
    if len(scripts) == 1:
        return [scripts[0]]
    if len(scripts) > 1:
        raise GeneratedRuntimeError(
            "generated_runtime_entrypoint_ambiguous: multiple pyproject scripts exist: "
            + ", ".join(scripts)
        )
    candidates = [name for name in _PYTHON_ENTRYPOINTS if root.joinpath(name).is_file()]
    runnable = [
        name
        for name in candidates
        if "__main__"
        in root.joinpath(name).read_text(encoding="utf-8", errors="replace")
        or "cli.run_app"
        in root.joinpath(name).read_text(encoding="utf-8", errors="replace")
    ]
    choices = runnable or candidates
    if len(choices) != 1:
        detail = ", ".join(choices) if choices else "none"
        raise GeneratedRuntimeError(
            "generated_runtime_entrypoint_ambiguous: expected exactly one runnable Python "
            f"entrypoint, found {detail}; set runtime.command"
        )
    command = ["python", choices[0]]
    content = root.joinpath(choices[0]).read_text(encoding="utf-8", errors="replace")
    if "cli.run_app" in content:
        command.append("start")
    return command


def _pyproject_scripts(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        import tomllib

        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    scripts = document.get("project", {}).get("scripts", {})
    return sorted(str(name) for name in scripts) if isinstance(scripts, dict) else []


def _node_install(root: Path) -> tuple[str, str, list[str], str]:
    if root.joinpath("pnpm-lock.yaml").is_file():
        return (
            "pnpm-lock.yaml",
            "pnpm-frozen",
            ["RUN corepack enable && pnpm install --frozen-lockfile"],
            "pnpm",
        )
    if root.joinpath("package-lock.json").is_file():
        return "package-lock.json", "npm-ci", ["RUN npm ci"], "npm"
    if root.joinpath("yarn.lock").is_file():
        return (
            "yarn.lock",
            "yarn-frozen",
            ["RUN corepack enable && yarn install --immutable"],
            "yarn",
        )
    return "package.json", "npm-install-unlocked", ["RUN npm install"], "npm"


def _node_command(root: Path, runner: str) -> list[str]:
    try:
        package = json.loads(root.joinpath("package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GeneratedRuntimeError("generated_runtime_package_json_invalid") from exc
    scripts = package.get("scripts", {})
    if isinstance(scripts, dict) and scripts.get("start"):
        return [runner, "run", "start"] if runner == "npm" else [runner, "start"]
    main = package.get("main")
    if isinstance(main, str) and main and root.joinpath(main).is_file():
        return ["node", main]
    candidates = [name for name in _NODE_ENTRYPOINTS if root.joinpath(name).is_file()]
    if len(candidates) != 1:
        detail = ", ".join(candidates) if candidates else "none"
        raise GeneratedRuntimeError(
            "generated_runtime_entrypoint_ambiguous: package.json needs a start script or "
            f"one existing main file, found {detail}; set runtime.command"
        )
    return ["node", candidates[0]]


def _python_dockerfile(
    version: str,
    dependency: str,
    strategy: str,
    install: list[str],
    command: list[str],
) -> str:
    del dependency, strategy
    return "\n".join(
        [
            f"FROM python:{version}-slim",
            "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1",
            # Poorly packaged repositories commonly declare Python extensions that
            # only publish wheels for a subset of Python/CPU combinations.  A slim
            # image has no compiler, so otherwise a valid manifest fails depending
            # on the runner architecture (for example webrtcvad on arm64).  This is
            # runtime infrastructure, not a rewrite of the submitted application.
            "RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*",
            "WORKDIR /app",
            "COPY . /app",
            *install,
            "RUN groupadd --gid 10001 alk && useradd --uid 10001 --gid 10001 --no-create-home alk && chown -R alk:alk /app",
            "USER 10001:10001",
            "CMD " + json.dumps(command),
            "",
        ]
    )


def _node_dockerfile(
    version: str, dependency: str, install: list[str], command: list[str]
) -> str:
    del dependency
    return "\n".join(
        [
            f"FROM node:{version}-slim",
            "WORKDIR /app",
            "COPY --chown=node:node . /app",
            *install,
            "USER node",
            "CMD " + json.dumps(command),
            "",
        ]
    )


def _copy_sanitized_context(source: Path, destination: Path) -> None:
    count = 0
    total = 0
    for current, directories, files in os.walk(source, followlinks=False):
        current_path = Path(current)
        linked_directories = [
            current_path / name
            for name in directories
            if current_path.joinpath(name).is_symlink()
        ]
        if linked_directories:
            relative = linked_directories[0].relative_to(source)
            raise GeneratedRuntimeError(
                f"generated_runtime_symlink_forbidden: {relative.as_posix()}"
            )
        directories[:] = [
            name
            for name in directories
            if name not in _IGNORED_DIRECTORIES
            and not current_path.joinpath(name).is_symlink()
        ]
        relative_directory = current_path.relative_to(source)
        for name in files:
            path = current_path / name
            relative = relative_directory / name
            if _sensitive_file(relative):
                continue
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise GeneratedRuntimeError(
                    f"generated_runtime_symlink_forbidden: {relative.as_posix()}"
                )
            if not stat.S_ISREG(info.st_mode):
                raise GeneratedRuntimeError(
                    f"generated_runtime_file_type_unsupported: {relative.as_posix()}"
                )
            if info.st_size > _MAX_FILE_BYTES:
                raise GeneratedRuntimeError(
                    f"generated_runtime_file_too_large: {relative.as_posix()}"
                )
            count += 1
            total += info.st_size
            if count > _MAX_FILES or total > _MAX_CONTEXT_BYTES:
                raise GeneratedRuntimeError("generated_runtime_context_limit_exceeded")
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    if any(pattern.search(chunk) for pattern in _SECRET_CONTENT):
                        raise GeneratedRuntimeError(
                            "generated_runtime_secret_material_detected: "
                            + relative.as_posix()
                        )
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target, follow_symlinks=False)


def _sensitive_file(relative: Path) -> bool:
    name = relative.name.lower()
    return (
        name in _SECRET_NAMES
        or name.startswith(".env.")
        or relative.suffix.lower() in _SECRET_SUFFIXES
    )


__all__ = [
    "GENERATED_DOCKERFILE",
    "GENERATED_PLAN",
    "GeneratedRuntimeError",
    "GeneratedRuntimePlan",
    "can_generate_runtime",
    "detect_generated_runtime",
    "prepare_generated_runtime",
]
