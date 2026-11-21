"""A CLI-shaped transcript and a local stub of the Messages API, for the transcript mirror's tests.

The transcript has two tool rounds, thinking-only lines, an Agent result with token counts, a
compact_boundary whose kept tail starts with a thinking-only line, and a leafUuid summary. Only
fake credentials appear in it.
"""

from __future__ import annotations

import json
import threading
import uuid as _uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

SESSION_ID = "5b0c7e4e-1f2a-4c3d-9e8f-0a1b2c3d4e5f"
NAMES = ["u1", "a1", "a2", "u2", "a3", "a4", "u3", "b", "s1", "a5", "a6"]
UUIDS = {name: str(_uuid.uuid5(_uuid.NAMESPACE_URL, "alk/transcript/" + name)) for name in NAMES}
PASSWORD = "hunter2"
URL_PASSWORD = "Zq9fakePw41"
QUERY_KEY = "t0k3nFAKEq"
PLANTS = (PASSWORD, URL_PASSWORD, QUERY_KEY)
# From this CLI on, a transcript also holds api-request lines (each one without a uuid).
REQUEST_REPLAY_FROM = (2, 1, 316)

POINTER_FIELDS = (
    "parentUuid",
    "logicalParentUuid",
    "leafUuid",
    "sourceToolAssistantUUID",
    "messageId",
    "afterUuid",
)


def build(cwd: str, cli_version: tuple[int, int, int] = (2, 1, 276)) -> list[dict[str, Any]]:
    """The raw lines a CLI of `cli_version` writes, in the order it writes them."""
    base = {
        "isSidechain": False,
        "userType": "external",
        "cwd": cwd,
        "sessionId": SESSION_ID,
        "version": ".".join(str(part) for part in cli_version),
        "gitBranch": "",
    }
    replay = cli_version >= REQUEST_REPLAY_FROM
    think = {"type": "thinking", "thinking": "plan the call", "signature": "RkFLRVNJRw=="}

    def line(name, kind, parent, second, **fields):
        body = {
            "parentUuid": UUIDS[parent] if parent else None,
            **base,
            "type": kind,
            "uuid": UUIDS[name],
            "timestamp": f"2026-11-12T09:00:{second:02d}.000Z",
        }
        body.update(fields)
        return body

    def assistant(message_id, blocks, stop=None):
        return {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-5",
            "content": blocks,
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": {"input_tokens": 50, "output_tokens": 9, "cache_read_input_tokens": 0},
        }

    def request_lines(ref, blocks):
        """What a 2.1.316+ CLI writes before each assistant line: the request, its shape, and a
        second copy of the API message (thinking included), none of them with a uuid."""
        if not replay:
            return []
        return [
            {"type": "api-request", "id": ref, "sessionId": SESSION_ID},
            {"type": "api-request-shape", "sessionId": SESSION_ID, "shapeHash": "shape-" + ref},
            {
                "type": "api-request-blob",
                "sessionId": SESSION_ID,
                "hash": "blob-" + ref,
                "message": {"role": "assistant", "content": blocks},
            },
        ]

    def with_ref(ref, entry):
        return {**entry, "requestRef": ref} if replay else entry

    use_one = {"type": "tool_use", "id": "toolu_01T1", "name": "Bash", "input": {"command": "echo one", "description": "first"}}
    use_two = {
        "type": "tool_use",
        "id": "toolu_01T2",
        "name": "Bash",
        "input": {
            "command": "curl https://api.example.com/v1/items?api_key=" + QUERY_KEY,
            "description": "second",
            "password": PASSWORD,
        },
    }
    out = [
        line(
            "u1", "user", None, 1,
            message={"role": "user", "content": "Check the db at postgres://svc:" + URL_PASSWORD + "@db.internal/app"},
        ),
        *request_lines("req_A", [think]),
        with_ref("req_A", line("a1", "assistant", "u1", 2, requestId="req_011A", message=assistant("msg_01A", [think]))),
        with_ref("req_A", line("a2", "assistant", "a1", 3, requestId="req_011A", message=assistant("msg_01A", [use_one], "tool_use"))),
        line(
            "u2", "user", "a2", 4, sourceToolAssistantUUID=UUIDS["a2"],
            message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_01T1", "content": "one", "is_error": False}]},
            toolUseResult={"stdout": "one", "stderr": "", "interrupted": False, "isImage": False},
        ),
        # The kept tail starts here, with a thinking-only line.
        *request_lines("req_B", [think]),
        with_ref("req_B", line("a3", "assistant", "u2", 5, requestId="req_011B", message=assistant("msg_01B", [think]))),
        with_ref("req_B", line("a4", "assistant", "a3", 6, requestId="req_011B", message=assistant("msg_01B", [use_two], "tool_use"))),
        line(
            "u3", "user", "a4", 7, sourceToolAssistantUUID=UUIDS["a4"],
            message={
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_01T2", "content": "DB_PASSWORD=" + PASSWORD + " ok", "is_error": False}],
            },
            toolUseResult={
                "stdout": "DB_PASSWORD=" + PASSWORD + " ok",
                "stderr": "",
                "interrupted": False,
                "isImage": False,
                "totalTokens": 51234,
                "totalToolUseCount": 3,
                "usage": {"input_tokens": 12, "output_tokens": 900},
                "db_password": PASSWORD,
            },
        ),
        {
            "parentUuid": None,
            "logicalParentUuid": UUIDS["u3"],
            **base,
            "type": "system",
            "subtype": "compact_boundary",
            "content": "Conversation compacted",
            "isMeta": False,
            "timestamp": "2026-11-12T09:00:08.000Z",
            "uuid": UUIDS["b"],
            "level": "info",
            "compactMetadata": {
                "trigger": "auto",
                "preTokens": 180000,
                "preservedSegment": {"headUuid": UUIDS["a3"], "anchorUuid": UUIDS["s1"], "tailUuid": UUIDS["u3"]},
                "preservedMessages": {
                    "anchorUuid": UUIDS["s1"],
                    "uuids": [UUIDS["a3"], UUIDS["a4"], UUIDS["u3"]],
                    "allUuids": [UUIDS["a3"], UUIDS["a4"], UUIDS["u3"]],
                },
            },
        },
        line(
            "s1", "user", "b", 9, isCompactSummary=True, isVisibleInTranscriptOnly=True,
            message={"role": "user", "content": "This session is being continued from a previous conversation. Summary: ran echo one."},
        ),
        *request_lines("req_C", [think]),
        with_ref("req_C", line("a5", "assistant", "s1", 10, requestId="req_011C", message=assistant("msg_01C", [think]))),
        with_ref(
            "req_C",
            line("a6", "assistant", "a5", 11, requestId="req_011C", message=assistant("msg_01C", [{"type": "text", "text": "Both commands ran."}], "end_turn")),
        ),
        # A summary of an earlier leaf, as a resumed session writes it.
        {"type": "summary", "summary": "Ran two commands", "leafUuid": UUIDS["a6"]},
    ]
    return out


def pointer_problems(entries: list[dict[str, Any]]) -> list[str]:
    """Every uuid-valued field that names a line the store doesn't hold."""
    held = {entry["uuid"] for entry in entries if entry.get("uuid")}
    problems: list[str] = []

    def check(where: str, value: Any) -> None:
        if value is not None and value not in held:
            problems.append(f"{where} -> {value}")

    for number, entry in enumerate(entries):
        for field in POINTER_FIELDS:
            if field in entry:
                check(f"line {number} {field}", entry[field])
        meta = entry.get("compactMetadata")
        if isinstance(meta, dict):
            messages = meta.get("preservedMessages") or {}
            for field in ("uuids", "allUuids"):
                for value in messages.get(field) or []:
                    check(f"line {number} preservedMessages.{field}", value)
            segment = meta.get("preservedSegment") or {}
            for field in ("headUuid", "tailUuid"):
                check(f"line {number} preservedSegment.{field}", segment.get(field))
    return problems


def _thinking_in(message: Any) -> int:
    content = (message or {}).get("content")
    if not isinstance(content, list):
        return 0
    return sum(1 for block in content if isinstance(block, dict) and block.get("type") in ("thinking", "redacted_thinking"))


def thinking_blocks(entries: list[dict[str, Any]]) -> int:
    """The thinking blocks in transcript lines."""
    return sum(_thinking_in(entry.get("message")) for entry in entries)


def api_thinking_blocks(messages: list[dict[str, Any]]) -> int:
    """The thinking blocks in the messages of an API request."""
    return sum(_thinking_in(message) for message in messages)


# -- A stub of the Messages API on a loopback port ---------------------------------------------


def _stream(model: str) -> bytes:
    events = [
        ("message_start", {"type": "message_start", "message": {"id": "msg_stub", "type": "message", "role": "assistant", "model": model, "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "STUB_OK"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 2}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


class StubAPI:
    """Records each request's path and body (header names only, never their values) and answers
    one short reply: as an event stream when the request streams, as JSON when it doesn't."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _record(self, body: Any) -> None:
                stub.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "dummy_key": self.headers.get("x-api-key") == "sk-ant-test-dummy",
                        "body": body,
                    }
                )

            def _send(self, status: int, kind: str, data: bytes) -> None:
                self.send_response(status)
                self.send_header("content-type", kind)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                self._record(None)
                self._send(404, "application/json", b'{"type":"error","error":{"type":"not_found_error","message":"stub"}}')

            do_HEAD = do_GET  # noqa: N815

            def do_POST(self) -> None:  # noqa: N802
                raw = self.rfile.read(int(self.headers.get("content-length") or 0))
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = {"_raw": raw[:2000].decode("utf8", "replace")}
                self._record(body)
                if "count_tokens" in self.path:
                    self._send(200, "application/json", b'{"input_tokens": 10}')
                    return
                if not self.path.startswith("/v1/messages"):
                    self._send(200, "application/json", b"{}")
                    return
                model = body.get("model", "stub") if isinstance(body, dict) else "stub"
                if isinstance(body, dict) and body.get("stream"):
                    self._send(200, "text/event-stream", _stream(model))
                    return
                reply = {
                    "id": "msg_stub", "type": "message", "role": "assistant", "model": model,
                    "content": [{"type": "text", "text": "STUB_OK"}], "stop_reason": "end_turn",
                    "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 2},
                }
                self._send(200, "application/json", json.dumps(reply).encode())

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def probe_request(self, marker: str) -> dict[str, Any] | None:
        """The messages request whose last message carries `marker`: the resumed turn's own."""
        for request in self.requests:
            body = request["body"]
            if (
                request["path"].startswith("/v1/messages")
                and isinstance(body, dict)
                and marker in json.dumps((body.get("messages") or [])[-1:])
            ):
                return request
        return None


def pairing_problems(messages: list[dict[str, Any]]) -> list[str]:
    """What the real API rejects: a tool_result with no tool_use before it, or a tool_use with no result."""
    problems: list[str] = []
    open_uses: set[str] = set()
    for number, message in enumerate(messages):
        content = message.get("content")
        blocks = content if isinstance(content, list) else []
        if message.get("role") == "user":
            results = {b.get("tool_use_id") for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"}
            problems += [f"message {number}: tool_result {one} has no tool_use" for one in sorted(results - open_uses)]
            problems += [f"message {number}: tool_use {one} has no result" for one in sorted(open_uses - results)]
            open_uses = set()
        else:
            open_uses = {b.get("id") for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"}
    return problems
