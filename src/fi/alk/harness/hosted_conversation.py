from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import sqlite3
import tarfile
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..providers.agent import _redact, redact_strings
from ..providers.secret_shapes import credential_in
from .outbound import RequestsTransport, Transport, TransportError

CONVERSATION_SCHEMA_VERSION = "futureagi.harness-conversation.v1"
EVENT_SCHEMA_VERSION = "futureagi.harness-conversation-event.v1"
DEFAULT_CAPABILITIES_PATH = Path("/run/futureagi/conversation.json")
# Finish retries before the platform's 90-second heartbeat expiry can replace this process.
_UNAVAILABLE_BUDGET_SECONDS = 60.0
logger = logging.getLogger(__name__)


class ConversationTransportError(RuntimeError):
    pass


class ConversationEndpoints(BaseModel):
    # A newer platform may offer endpoints this harness does not use yet.
    model_config = ConfigDict(extra="ignore")

    commands: str
    events: str
    rerun: str
    adjust: str
    run_status: str
    workspace: str

    session_store: str
    session_store_append: str
    # Absent on platforms that predate the builder; its tools say so instead of failing.
    scenarios: str = ""
    publish: str = ""
    runs: str = ""
    run_start: str = ""
    overview: str = ""

    @field_validator(
        "commands",
        "events",
        "rerun",
        "adjust",
        "run_status",
        "workspace",
        "session_store",
        "session_store_append",
        "scenarios",
        "publish",
        "runs",
        "run_start",
        "overview",
    )
    @classmethod
    def _https_endpoint(cls, value: str) -> str:
        if not value:
            return value
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc or not value.endswith("/"):
            raise ValueError(
                "conversation endpoints must be absolute HTTPS URLs ending in '/'"
            )
        return value


class ConversationIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_name: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=255)


class ConversationCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    conversation_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    token: str = Field(min_length=1)
    fence: str = Field(min_length=1)
    expires_at: datetime
    identity: ConversationIdentity
    turn_context: dict[str, Any]
    endpoints: ConversationEndpoints

    @model_validator(mode="after")
    def _valid(self) -> ConversationCapabilities:
        if self.schema_version != CONVERSATION_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {self.schema_version}")
        if self.expires_at <= datetime.now(timezone.utc):
            raise ValueError("conversation capability is expired")
        for endpoint in self.endpoints.model_dump().values():
            if endpoint and self.conversation_id not in urlparse(endpoint).path.split("/"):
                raise ValueError(
                    "conversation endpoint is bound to another conversation"
                )
        return self

    def auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "X-Harness-Conversation-Fence": self.fence,
        }


def load_conversation_capabilities(
    path: str | Path = DEFAULT_CAPABILITIES_PATH,
    *,
    unlink: bool = True,
) -> ConversationCapabilities:
    target = Path(path)
    try:
        body = json.loads(target.read_text(encoding="utf-8"))
        capabilities = ConversationCapabilities.model_validate(body)
    except (OSError, ValueError) as exc:
        raise ConversationTransportError(
            f"conversation capability could not be loaded: {type(exc).__name__}"
        ) from exc
    if unlink:
        try:
            target.unlink()
        except OSError:
            pass
    return capabilities


class ConversationClient:
    def __init__(
        self,
        capabilities: ConversationCapabilities,
        *,
        transport: Transport | None = None,
    ) -> None:
        self.capabilities = capabilities
        self.transport = transport or RequestsTransport()
        self.command_watermark = int(
            capabilities.turn_context.get("command_watermark") or 0
        )
        self.event_watermark = int(
            capabilities.turn_context.get("event_watermark") or 0
        )
        self.turn_context = dict(capabilities.turn_context)
        self._emit_lock = asyncio.Lock()

    async def commands(self) -> list[dict[str, Any]]:
        separator = "&" if "?" in self.capabilities.endpoints.commands else "?"
        url = (
            f"{self.capabilities.endpoints.commands}{separator}"
            f"after={self.command_watermark}"
        )
        body = await self._request("GET", url)
        if isinstance(body.get("turn_context"), dict):
            self.turn_context = body["turn_context"]
        return list(body.get("commands") or [])

    async def emit(
        self,
        kind: str,
        *,
        payload: dict[str, Any] | None = None,
        message_id: str | None = None,
        stage: str = "",
        invocation_id: str | None = None,
        function_call_id: str | None = None,
        acknowledge_through: int | None = None,
    ) -> dict[str, Any]:
        # Provider IDs may include long signed metadata. Only the correlation key
        # crosses this channel; the SDK retains the original ID for tool replies.
        if function_call_id and len(function_call_id) > 255:
            function_call_id = "call-" + hashlib.sha256(
                function_call_id.encode("utf-8")
            ).hexdigest()
        async with self._emit_lock:
            sequence = self.event_watermark + 1
            event: dict[str, Any] = {
                "schema_version": EVENT_SCHEMA_VERSION,
                "event_id": f"ce-{uuid.uuid4().hex}",
                "conversation_id": self.capabilities.conversation_id,
                "sequence": sequence,
                "kind": kind,
                "message_id": message_id,
                "stage": stage,
                "invocation_id": invocation_id,
                "function_call_id": function_call_id,
                "emitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "payload": payload or {},
            }
            event["digest"] = _canonical_digest(event)
            acknowledged = (
                self.command_watermark
                if acknowledge_through is None
                else acknowledge_through
            )
            body = await self._request(
                "POST",
                self.capabilities.endpoints.events,
                json_body={
                    "schema_version": EVENT_SCHEMA_VERSION,
                    "acknowledged_through": acknowledged,
                    "events": [event],
                },
            )
            self.event_watermark = int(body["acked_through_sequence"])
            if acknowledge_through is not None:
                self.command_watermark = max(
                    self.command_watermark, acknowledge_through
                )
            return event

    async def checkpoint(self, workspace: Path) -> dict[str, Any]:
        body = await asyncio.to_thread(_workspace_archive, workspace)
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        headers = {
            **self.capabilities.auth_headers(),
            "Content-Type": "application/gzip",
            "X-Workspace-Digest": digest,
            "X-Workspace-Size": str(len(body)),
        }
        response = await asyncio.to_thread(
            self.transport.request,
            "PUT",
            self.capabilities.endpoints.workspace,
            headers=headers,
            data=body,
            timeout=120.0,
        )
        if response.status_code != 200 or response.body is None:
            raise ConversationTransportError(
                f"workspace checkpoint returned HTTP {response.status_code}"
            )
        return response.body

    async def rerun(self, workspace: Path) -> dict[str, Any]:
        await self.checkpoint(workspace)
        return await self._request(
            "POST",
            self.capabilities.endpoints.rerun,
            json_body={},
        )

    async def run_status(self) -> dict[str, Any]:
        return await self._request(
            "GET",
            self.capabilities.endpoints.run_status,
        )

    async def scenarios(self, query: dict[str, Any]) -> dict[str, Any]:
        return await self._platform("scenarios", query)

    async def publish(self, digest: str) -> dict[str, Any]:
        return await self._platform("publish", {"digest": digest})

    async def runs(self, run_id: str = "", limit: int = 10) -> dict[str, Any]:
        return await self._platform(
            "runs", {"run_id": run_id, "limit": limit} if run_id else {"limit": limit}
        )

    async def start_run(
        self, numbers: list[int], ranges: list[dict[str, int]], trials: int
    ) -> dict[str, Any]:
        # One id for every retry of this call, so a lost response never starts a second run.
        return await self._platform(
            "run_start",
            {"numbers": numbers, "ranges": ranges, "trials": trials, "operation_id": uuid.uuid4().hex},
        )

    async def overview(self) -> dict[str, Any]:
        return await self._platform("overview", {})

    async def _platform(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        url = getattr(self.capabilities.endpoints, endpoint)
        if not url:
            raise ConversationTransportError(
                f"this platform does not offer the {endpoint} endpoint"
            )
        return await self._request("POST", url, json_body=body)

    async def adjust(
        self, instruction: str, target_stage: str = "", scenario_delta: int = 0
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            self.capabilities.endpoints.adjust,
            json_body={
                "instruction": instruction,
                "client_request_id": f"conversation-{uuid.uuid4().hex}",
                **({"target_stage": target_stage} if target_stage else {}),
                **({"scenario_delta": scenario_delta} if scenario_delta > 0 else {}),
            },
        )

    async def load_session(self, key: dict[str, Any]) -> dict[str, Any]:
        query = urlencode(
            {
                "project_key": str(key["project_key"]),
                "session_id": str(key["session_id"]),
                "subpath": str(key.get("subpath") or ""),
            }
        )
        return await self._request(
            "GET",
            f"{self.capabilities.endpoints.session_store}?{query}",
        )

    async def append_session(
        self,
        key: dict[str, Any],
        entries: list[dict[str, Any]],
    ) -> None:
        await self._request(
            "POST",
            self.capabilities.endpoints.session_store_append,
            json_body={
                "project_key": str(key["project_key"]),
                "session_id": str(key["session_id"]),
                "subpath": str(key.get("subpath") or ""),
                "entries": entries,
            },
        )

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _UNAVAILABLE_BUDGET_SECONDS
        delay = 0.25
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ConversationTransportError("conversation endpoint remained unavailable")
            try:
                response = await asyncio.to_thread(
                    self.transport.request,
                    method,
                    url,
                    headers=self.capabilities.auth_headers(),
                    json_body=json_body,
                    timeout=min(35.0, remaining),
                )
            except TransportError:
                response = None
            if (
                response is not None
                and response.status_code == 200
                and response.body is not None
            ):
                return response.body
            if response is not None and response.status_code in {
                400,
                401,
                403,
                404,
                409,
                422,
            }:
                said = (
                    response.body.get("message") or response.body.get("detail") or ""
                    if isinstance(response.body, dict)
                    else ""
                )
                raise ConversationTransportError(
                    f"conversation endpoint returned HTTP {response.status_code}"
                    + (f": {said}" if said else "")
                )
            if loop.time() + delay > deadline:
                status = response.status_code if response is not None else "unreachable"
                raise ConversationTransportError(
                    f"conversation endpoint remained unavailable ({status})"
                )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 5.0)


PENDING_QUESTION = ".futureagi-pending-question.json"


def cleaned_pending_question(body: dict[str, Any]) -> dict[str, Any]:
    """The pending question as it may leave the sandbox: its words cleaned, its id as it is."""
    return {
        **body,
        "question": redact_strings(body.get("question")),
        "options": redact_strings(body.get("options")),
    }


# The lines of a transcript that point at another line by its uuid.
_UUID_POINTERS = (
    "parentUuid",
    "logicalParentUuid",
    "leafUuid",
    "sourceToolAssistantUUID",
    "messageId",
    "afterUuid",
)
_THINKING = frozenset({"thinking", "redacted_thinking"})
# What a CLI from 2.1.316 on writes before each request. None has a uuid, and the blob is a
# second copy of an API message, thinking included.
_REQUEST_REPLAY = frozenset({"api-request", "api-request-shape", "api-request-blob"})


class PlatformSessionStore:
    """Claude Agent SDK transcript storage over the scoped outbound channel.

    What `append` stores is cleaned, and a resumed session reads it back: the SDK treats a line
    as an opaque blob, but the CLI it resumes with follows uuid pointers from line to line, so a
    line that is dropped leaves its pointers resolved to the nearest line that stays.
    """

    def __init__(self, client: ConversationClient) -> None:
        self._client = client
        # uuid of each dropped line -> its parent's uuid. Kept across calls: a later batch can point
        # at a line dropped in an earlier one.
        self._dropped: dict[str, str | None] = {}

    async def append(
        self,
        key: dict[str, Any],
        entries: list[dict[str, Any]],
    ) -> None:
        cleaned = self._clean(entries)
        if cleaned:
            await self._client.append_session(dict(key), cleaned)

    def _clean(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        for original in entries:
            entry = _copy_line(original)
            if not entry.get("uuid") and entry.get("type") in _REQUEST_REPLAY:
                continue
            if _strip_thinking(entry):
                if entry.get("uuid"):
                    self._dropped[entry["uuid"]] = entry.get("parentUuid")
                continue
            kept.append(entry)
        return [_clean_entry_or_payload(self._resolve_pointers(entry)) for entry in kept]

    def _resolve(self, value: Any) -> Any:
        """The nearest line at or above `value` that was not dropped (None past the first line)."""
        seen: set[str] = set()
        while isinstance(value, str) and value in self._dropped and value not in seen:
            seen.add(value)
            value = self._dropped[value]
        return value

    def _resolve_pointers(self, entry: dict[str, Any]) -> dict[str, Any]:
        for field in _UUID_POINTERS:
            if entry.get(field) is not None:
                entry[field] = self._resolve(entry[field])
        metadata = entry.get("compactMetadata")
        if isinstance(metadata, dict):
            self._resolve_compaction(metadata)
        return entry

    def _resolve_compaction(self, metadata: dict[str, Any]) -> None:
        """A compaction names the lines it kept. A dropped one leaves that list rather than being
        replaced by its parent: its parent can be a line from before the boundary, which would pull
        it into the kept tail."""
        messages = metadata.get("preservedMessages")
        listed: list[str] = []
        if isinstance(messages, dict):
            for field in ("uuids", "allUuids"):
                if isinstance(messages.get(field), list):
                    messages[field] = [one for one in messages[field] if one not in self._dropped]
            listed = messages.get("uuids") or []
        segment = metadata.get("preservedSegment")
        if isinstance(segment, dict):
            head = segment.get("headUuid")
            if head in self._dropped:
                segment["headUuid"] = listed[0] if listed else None
            if segment.get("tailUuid") is not None:
                segment["tailUuid"] = self._resolve(segment["tailUuid"])

    async def load(
        self,
        key: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        body = await self._client.load_session(dict(key))
        entries = body.get("entries")
        if not isinstance(entries, list):
            return None
        # What was stored before the mirror cleaned its lines is still raw. A resume reads the
        # store through here, so the same per-line cleaning (and the same pointer closing, over
        # the whole history in order) runs on the way out; a line already cleaned comes back as
        # it is.
        return self._clean(entries)

    async def list_subkeys(self, key: dict[str, Any]) -> list[str]:
        body = await self._client.load_session(dict(key))
        return [str(value) for value in body.get("subkeys") or []]


def _strip_thinking(entry: dict[str, Any]) -> bool:
    """Remove the line's thinking blocks. True when that leaves it with no blocks at all.

    Editing an earlier turn invalidates the thinking after it, and an enforced account then gets a
    400, so the documented recovery is to send none. A line with an empty content list can't stay:
    the CLI turns it into "[No message content]".
    """
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return False
    blocks = [one for one in content if not (isinstance(one, dict) and one.get("type") in _THINKING)]
    if len(blocks) == len(content):
        return False
    message["content"] = blocks
    return not blocks


def _copy_line(original: dict[str, Any]) -> dict[str, Any]:
    """A copy of a line that nothing downstream shares. A JSON round trip rather than
    copy.deepcopy, which needs twice the stack and fails on a line the cleaner could still read;
    a line too deep even for that keeps its top-level scalars, which carry the uuid pointers."""
    try:
        return json.loads(json.dumps(original))
    except RecursionError:
        return {
            name: value if value is None or isinstance(value, (str, int, float, bool)) else _GONE
            for name, value in original.items()
        }


_GONE = "[redacted]"


def _clean_entry_or_payload(entry: dict[str, Any]) -> dict[str, Any]:
    """`_clean_entry`, except that a line nested too deep to clean keeps its place with the
    payloads replaced, rather than failing the whole batch: a line left out would leave the lines
    after it pointing at a uuid that was never stored."""
    try:
        return _clean_entry(entry)
    except RecursionError:
        pass
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and "input" in block:
            block["input"] = _GONE
    if "toolUseResult" in entry:
        entry["toolUseResult"] = _GONE
    try:
        return _clean_entry(entry)
    except RecursionError:
        # Deep somewhere else: keep the line's scalars, which carry its uuid and pointers.
        return {
            name: value if value is None or isinstance(value, (str, int, float, bool)) else _GONE
            for name, value in entry.items()
        }


def _clean_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """A tool's arguments and a tool result's fields are keyed by what the model or a tool chose,
    so they get the key mask (a result keeps its token counts). Every other string is text, cleaned
    by what it says; ids, hashes and numbers pass through."""
    cleaned: dict[str, Any] = {}
    for name, value in entry.items():
        if name == "toolUseResult":
            cleaned[name] = _redact(value, name, keep_scalars=True)
        elif name == "message" and isinstance(value, dict):
            cleaned[name] = {
                field: _clean_content(item) if field == "content" else redact_strings(item)
                for field, item in value.items()
            }
        else:
            cleaned[name] = redact_strings(value)
    return cleaned


def _clean_content(content: Any) -> Any:
    if not isinstance(content, list):
        return redact_strings(content)
    return [
        {
            field: _redact(item, field) if field == "input" else redact_strings(item)
            for field, item in block.items()
        }
        if isinstance(block, dict)
        else redact_strings(block)
        for block in content
    ]


def _workspace_archive(workspace: Path) -> bytes:
    """The workspace as a gzipped tar, packed again once if a file changed while it was packed.

    A file that shrinks after its size is read fails the pack after its header is written, so the
    file cannot be skipped: the archive would be corrupt. A second failure is left to the next
    checkpoint.
    """
    try:
        return _pack(workspace)
    except OSError as exc:
        logger.warning("the workspace changed while it was packed, packing it again: %s", exc)
        return _pack(workspace)


# The CLI's own state under the workspace. It holds the transcripts the CLI wrote, raw, and
# the session store above already carries the cleaned ones.
_CLI_STATE = frozenset({".claude", ".claude-coordinator"})
# What SQLite writes beside a database while a transaction is open: pages not yet cleaned.
_DATABASE_SIDECARS = ("-journal", "-wal", "-shm")
ADK_DATABASE = ".futureagi-adk.db"
_RAW_STAGING = frozenset(
    name + ".tmp" for name in (PENDING_QUESTION, ADK_DATABASE)
) | frozenset(str(Path(name).with_suffix(".tmp")) for name in (PENDING_QUESTION, ADK_DATABASE))
_SIGNATURES = frozenset({"thought_signature", "thoughtSignature"})
_STATE_FIELDS = frozenset({"state_delta", "stateDelta"})
_CALL_FIELDS = {
    "function_call": "args",
    "functionCall": "args",
    "function_response": "response",
    "functionResponse": "response",
}


def _clean_adk_event(value: Any, field: str = "") -> Any:
    """One stored ADK event, cleaned: a tool's arguments and a tool's result are keyed by what
    the model or the tool chose, so they get the key mask; every other string is text, cleaned by
    what it says (a compaction summary, an error, a state change). The model's reasoning
    signature is dropped wherever it sits, as the transcript's thinking is."""
    if isinstance(value, dict):
        masked = _CALL_FIELDS.get(field)
        return {
            # State is keyed by names a tool chose, so a state change gets the key mask too.
            name: _redact(item, name)
            if name == masked or name in _STATE_FIELDS
            else _clean_adk_event(item, name)
            for name, item in value.items()
            if name not in _SIGNATURES
        }
    if isinstance(value, list):
        return [_clean_adk_event(item, field) for item in value]
    if isinstance(value, str):
        return redact_strings(value)
    return value


_ADK_TABLES = frozenset(
    {"adk_internal_metadata", "app_states", "events", "sessions", "user_states"}
)


def _cleaned_adk_database(path: Path) -> bytes | None:
    """The session database as it may leave the sandbox, or None when it can't be made clean.

    The live file is read through SQLite's backup into memory, so no raw copy touches the disk,
    and written out with VACUUM INTO, so no freed page carries text that was deleted. Its key
    columns are not touched: ADK finds a session by them.
    """
    try:
        source = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=10)
        memory = sqlite3.connect(":memory:")
        try:
            source.backup(memory)
        finally:
            source.close()
        try:
            # VACUUM INTO copies every table, so one the cleaner does not read would carry its
            # text out raw. ADK 2.7.1's schema is exactly these; anything else is left out.
            tables = {
                name
                for (name,) in memory.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if tables - _ADK_TABLES:
                raise ValueError("a table the cleaner does not know")
            for rowid, data in memory.execute("SELECT rowid, event_data FROM events").fetchall():
                if data is not None:
                    memory.execute(
                        "UPDATE events SET event_data = ? WHERE rowid = ?",
                        (json.dumps(_clean_adk_event(json.loads(data))), rowid),
                    )
            # Session state is built from the events' state changes, so a secret cleaned in an
            # event would otherwise sit raw here.
            for table in ("sessions", "app_states", "user_states"):
                for rowid, data in memory.execute(f"SELECT rowid, state FROM {table}").fetchall():
                    if data is not None:
                        memory.execute(
                            f"UPDATE {table} SET state = ? WHERE rowid = ?",
                            (json.dumps(_redact(json.loads(data))), rowid),
                        )
            memory.commit()
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / "adk.db"
                memory.execute("VACUUM INTO ?", (str(target),))
                return target.read_bytes()
        finally:
            memory.close()
    except (sqlite3.Error, ValueError, OSError, RecursionError) as exc:
        # A damaged file or an unknown schema: left out. SQLite and JSON errors carry no row values.
        logger.warning(
            "the session database was left out of the workspace checkpoint, because it could not "
            "be cleaned (%s); a resumed session opens fresh",
            type(exc).__name__,
            exc_info=True,
        )
        return None
    except Exception:  # noqa: BLE001 - a bug in the cleaner also fails closed, but is not hidden.
        logger.exception(
            "the session database was left out of the workspace checkpoint, because cleaning it "
            "failed unexpectedly; a resumed session opens fresh"
        )
        return None


def _cleaned_pending_question_file(path: Path) -> bytes:
    raw = path.read_bytes()
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return redact_strings(raw.decode("utf-8", "replace")).encode("utf-8")
    return json.dumps(cleaned_pending_question(body), sort_keys=True, separators=(",", ":")).encode("utf-8")


def _add_bytes(archive: tarfile.TarFile, name: str, body: bytes, source: Path) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(body)
    info.mtime = source.stat().st_mtime
    info.mode = 0o644
    archive.addfile(info, io.BytesIO(body))


# Written as they are and replayed as they are, and refused at their writers. A workspace an older
# kit wrote can still hold a credential in one; that file is left out, not cleaned.
_REPLAYED_FILES = frozenset({"actors.json", "simulator_prompt.md"})


def _holds_a_credential(path: Path) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace")
    if credential_in(text):
        return True
    try:
        return credential_in(json.loads(text))
    except ValueError:
        return False


def _pack(workspace: Path) -> bytes:
    root = workspace.resolve()
    with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as file:
        with tarfile.open(fileobj=file, mode="w:gz") as archive:
            for path in sorted(root.rglob("*")):
                relative = path.relative_to(root)
                if not relative.parts or any(
                    part in {"__pycache__", "outbound-spool", "telemetry"}
                    for part in relative.parts
                ):
                    continue
                if relative.parts[0] in _CLI_STATE or path.name.endswith(_DATABASE_SIDECARS):
                    continue
                # A cleaned file's writer stages it in a sibling .tmp, raw, before renaming it.
                if relative.as_posix() in _RAW_STAGING:
                    continue
                if path.is_symlink():
                    continue
                # The model's CLI writes and removes its own files under the workspace while we pack.
                try:
                    if relative.as_posix() == PENDING_QUESTION and path.is_file():
                        _add_bytes(archive, PENDING_QUESTION, _cleaned_pending_question_file(path), path)
                    elif relative.as_posix() in _REPLAYED_FILES and path.is_file() and _holds_a_credential(path):
                        logger.warning(
                            "%s was left out of the workspace checkpoint, because it holds a "
                            "credential; rewrite it with a placeholder",
                            relative.as_posix(),
                        )
                    elif relative.as_posix() == ADK_DATABASE and path.is_file():
                        body = _cleaned_adk_database(path)
                        if body is not None:
                            _add_bytes(archive, ADK_DATABASE, body, path)
                    else:
                        archive.add(path, arcname=relative.as_posix(), recursive=False)
                except FileNotFoundError:
                    continue
        file.seek(0)
        return file.read()


def _canonical_digest(value: object) -> str:
    body = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()
