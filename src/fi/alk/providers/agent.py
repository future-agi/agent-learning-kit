from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .setup_hash import compute_setup_hash

logger = logging.getLogger(__name__)

AGENT_SCHEMA_VERSION = "1.0"
MAIN_UNIT_ID = "main"

_REDACTION_MARKERS = (
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "authorization",
    "password",
    "passphrase",
    "cookie",
    "api-key",
    "private_key",
)
_REDACTED = "[redacted]"
_DIRECTION_ORDER = ("inbound", "outbound")
_AUTHORED_TYPES = ("voice", "chat", "browser")
_AUTHORED_TOOL_KINDS = {"service": "http", "import": "builtin", "construct": "builtin"}
_NAME_MAX = 255

Direction = Literal["inbound", "outbound"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderRef(_Strict):
    provider: str
    id: str = Field(min_length=1, max_length=255)
    version: str | None = None


class ModelConfig(_Strict):
    provider: str | None = None
    name: str
    temperature: float | None = None
    max_tokens: int | None = None


class VoiceConfig(_Strict):
    provider: str | None = None
    voice_id: str
    language: str | None = None
    speed: float | None = None


class Endpoint(_Strict):
    url: str


class Tool(_Strict):
    name: str
    description: str | None = None
    kind: Literal["builtin", "http", "mcp", "unknown"]
    parameters: dict[str, Any] | None = None
    endpoint: Endpoint | None = None


class BehaviourUnit(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
    shape: Literal["single_prompt", "state", "flow_node", "external"]
    instructions: str | None = None
    tools: list[str] = []
    transitions: list[str] = []


class Telephony(_Strict):
    number: str
    provider: str | None = None
    direction_supported: list[Direction] = []


class Agent(_Strict):
    schema_version: Literal["1.0"]
    type: Literal["voice", "chat", "coding", "cua", "browser", "crawler"]
    kind: Literal["hosted", "source", "endpoint"]
    name: str
    provider_ref: ProviderRef | None
    behaviour: list[BehaviourUnit] = Field(min_length=1)
    tools: list[Tool]
    model: ModelConfig | None
    voice: VoiceConfig | None
    telephony: list[Telephony]
    supports: list[Direction]
    direction_answer: Direction | None
    gaps: list[
        Literal["states_not_described", "flow_not_described", "engine_not_readable"]
    ]
    instrumentation: Literal["traceai", "opentelemetry", "none"]
    extension: dict[str, Any]
    setup_hash: str

    @model_validator(mode="after")
    def _consistent(self) -> Agent:
        if self.kind == "hosted" and self.provider_ref is None:
            raise ValueError("provider_ref_required")
        if self.kind != "hosted" and self.provider_ref is not None:
            raise ValueError("provider_ref_not_allowed")
        unit_ids = [unit.id for unit in self.behaviour]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("unit_id_repeated")
        tool_names = {tool.name for tool in self.tools}
        if any(
            name not in tool_names for unit in self.behaviour for name in unit.tools
        ):
            raise ValueError("unit_tool_unknown")
        directions = [
            self.supports,
            *(entry.direction_supported for entry in self.telephony),
        ]
        if any(not _sorted_directions(listed) for listed in directions):
            raise ValueError("direction_list_unsorted")
        if self.setup_hash != compute_setup_hash(self.model_dump(mode="json")):
            raise ValueError("setup_hash_mismatch")
        return self


class AgentBuildError(ValueError):
    def __init__(
        self,
        code: Literal[
            "provider_unsupported",
            "document_missing",
            "retell_response_engine_unsupported",
            "agent_invalid",
        ],
        detail: str = "",
    ) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def build_authored_agent(
    document: Mapping[str, Any],
    *,
    subfolder: str | None = None,
    framework: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    contract = _redact(
        {key: value for key, value in document.items() if key != "base_environment"}
    )
    modality = contract.get("modality")
    if modality not in _AUTHORED_TYPES:
        raise AgentBuildError("agent_invalid", "modality")
    tool_specs = contract.get("tools") or []
    tools = [
        _authored_tool(spec, contract.get("tool_entrypoints") or [])
        for spec in tool_specs
    ]
    agent = {
        "schema_version": AGENT_SCHEMA_VERSION,
        "type": modality,
        "kind": "source",
        "name": str(contract.get("agent") or "").strip()[:_NAME_MAX],
        "provider_ref": None,
        "behaviour": [
            {
                "id": MAIN_UNIT_ID,
                "shape": "external",
                "instructions": contract.get("system_prompt_excerpt") or None,
                "tools": [tool["name"] for tool in tools],
                "transitions": [],
            }
        ],
        "tools": tools,
        "model": None,
        "voice": None,
        "telephony": [],
        "supports": [],
        "direction_answer": None,
        "gaps": ["flow_not_described"],
        "instrumentation": contract.get("instrumentation") or "none",
        "extension": {
            "contract": contract,
            **({"framework": dict(framework)} if framework is not None else {}),
        },
    }
    if subfolder:
        # The platform compares this to what it sent, so it is never trimmed or normalised.
        agent["extension"]["source"] = {"subfolder": subfolder}
    agent["setup_hash"] = compute_setup_hash(agent)
    try:
        return Agent.model_validate(agent).model_dump(mode="json")
    except ValidationError as error:
        raise AgentBuildError("agent_invalid", _error_path(error)) from None


def _authored_tool(
    spec: Mapping[str, Any], entrypoints: list[Mapping[str, Any]]
) -> dict[str, Any]:
    name = spec["name"]
    mode = next(
        (entry.get("mode") for entry in entrypoints if entry.get("tool") == name), ""
    )
    args = spec.get("args") or []
    arg_values = spec.get("arg_values") or {}
    parameters = None
    if args:
        parameters = {
            "type": "object",
            "properties": {
                arg: {"enum": arg_values[arg]}
                if isinstance(arg_values.get(arg), list)
                else {}
                for arg in args
            },
            "required": list(args),
        }
    return {
        "name": name,
        "description": spec.get("description") or None,
        "kind": _AUTHORED_TOOL_KINDS.get(str(mode), "unknown"),
        "parameters": parameters,
        "endpoint": None,
    }


def _redact(value: Any, key: str = "") -> Any:
    refused: list[str] = []
    redacted = _redact_in(value, key, refused)
    if refused:
        # Once per document, not per string: a crafted contract can hold half a million of them.
        logger.warning("a URL was redacted whole: class=%s", refused[0])
    return redacted


def _redact_in(value: Any, key: str, refused: list[str]) -> Any:
    lowered = key.lower()
    if any(marker in lowered for marker in _REDACTION_MARKERS):
        return _REDACTED
    if lowered == "headers":
        if isinstance(value, Mapping):
            return {str(item_key): _REDACTED for item_key in value}
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(item_key): _redact_in(item, str(item_key), refused)
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_in(item, key, refused) for item in value]
    if isinstance(value, str):
        return _redact_text(value, refused)
    return value


# Every :// starts a URL. Each match is one run of scheme characters before a ://, and the
# lookbehind means a start inside such a run is never tried, which keeps the scan linear.
_START = re.compile(r"(?<![A-Za-z0-9+.\-])[A-Za-z0-9+.\-]*://")
_LETTER = re.compile(r"[A-Za-z]")
# A value that is one URL begins with a letter scheme and ://, checked before anything is removed.
_LEADING_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://")
# urlsplit itself removes these inside a URL.
_URL_BREAKS = str.maketrans("", "", "\t\r\n")
_SPACE = re.compile(r"(\s+)")
_QUERY_OR_FRAGMENT = re.compile(r"[?#]")


def _redact_text(value: str, refused: list[str]) -> str:
    """Drop every URL's userinfo, query and fragment, and keep the rest of the text as written.

    A URL runs from its scheme to the next whitespace. Within one whitespace-free token, a later
    :// that starts before the current URL's own ? or # begins a new URL; once a URL has a ? or
    #, the rest of the token is its query or fragment.
    """
    if "://" not in value:
        return value
    if _LEADING_SCHEME.match(value):
        joined = value.translate(_URL_BREAKS)
        if not _SPACE.search(joined):
            # A field that is one URL, perhaps broken by a tab or a line break: one token.
            return _redact_token(joined, refused)
    return "".join(
        part if not part or part.isspace() else _redact_token(part, refused)
        for part in _SPACE.split(value)
    )


def _redact_token(token: str, refused: list[str]) -> str:
    starts: list[tuple[int, bool]] = []
    for match in _START.finditer(token):
        # The scheme is the run from its first letter; the text before that letter stays.
        letter = _LETTER.search(token, match.start(), match.end() - 3)
        starts.append((letter.start() if letter else match.end() - 3, letter is None))
    if not starts:
        return token
    out = [token[: starts[0][0]]]
    # The first ? or # from the first start, searched for once. If it comes before the next
    # start, the query owns the rest of the token and the loop ends; if not, it is also the
    # first ? or # from every later start up to it. Searching from each start instead would
    # make the scan quadratic.
    query_at = -1
    for index, (start, schemeless) in enumerate(starts):
        if query_at < start:
            found = _QUERY_OR_FRAGMENT.search(token, start)
            query_at = found.start() if found else len(token)
        following = starts[index + 1][0] if index + 1 < len(starts) else len(token)
        end = len(token) if query_at < following else following
        piece = token[start:end]
        if schemeless:
            # "${SCHEME}://user:pw@host": parsed with a stand-in scheme, written back without it.
            cleaned = _redact_url("x" + piece, refused)
            out.append(cleaned[1:] if cleaned.startswith("x://") else cleaned)
        else:
            out.append(_redact_url(piece, refused))
        if end == len(token):
            break
    return "".join(out)


def _redact_url(url: str, refused: list[str]) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError as error:
        # A netloc urlsplit refuses may still hold credentials, so none of it is kept, and
        # only the error's class is logged: its text repeats the netloc.
        if not refused:
            refused.append(type(error).__name__)
        return _REDACTED
    if not parsed.scheme:
        return url
    # Built by hand in both branches: urlunsplit drops the // for a scheme urllib does not
    # know, so sqlite:///./app.db would come back as sqlite:/./app.db.
    if not parsed.netloc:
        # file:///x?token=... has no host, but its query and fragment still go.
        return f"{parsed.scheme}://{parsed.path}"
    hostname = parsed.hostname or ""
    if ":" in hostname:
        # IPv6 keeps its brackets, so cleaning it a second time changes nothing.
        hostname = f"[{hostname}]"
    try:
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        # A template such as http://{HOST}:{PORT} has no numeric port;
        # keep the host rather than failing the whole document.
        port = ""
    return f"{parsed.scheme}://{hostname}{port}{parsed.path}"


def _sorted_directions(listed: Sequence[str]) -> bool:
    positions = [_DIRECTION_ORDER.index(direction) for direction in listed]
    return positions == sorted(set(positions))


def _error_path(error: ValidationError) -> str:
    first = error.errors()[0]
    return "/".join(str(part) for part in first["loc"])
