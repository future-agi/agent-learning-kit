import asyncio
import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import ValidationError

from fi.alk.harness.chat_transport import create_chat_wrapper
from fi.alk.harness.contract import RuntimeInterface
from fi.simulate.agent.wrapper import AgentInput
from fi.simulate.agent.wrappers.http import HTTPAgentWrapper
from fi.simulate.agent.wrappers.http_stream import (
    HTTPStreamSpec,
    SSEDecoder,
    SSEProtocolError,
    SSEReducer,
)


SPEC = {
    "completion": {"path": "data.type", "values": ["RUN_FINISHED"]},
    "failure": {"path": "data.type", "values": ["RUN_ERROR"]},
    "text": [
        {
            "mode": "delta",
            "path": "data.delta",
            "when": {"path": "data.type", "values": ["TEXT_MESSAGE_CONTENT"]},
        }
    ],
}


def _event(payload, *, event_id=None, event=None):
    lines = []
    if event:
        lines.append(f"event: {event}")
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append("data: " + json.dumps(payload, separators=(",", ":")))
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def _reduce(raw, spec):
    decoder = SSEDecoder()
    reducer = SSEReducer(HTTPStreamSpec.model_validate(spec))
    complete = None
    for byte in raw:
        for frame in decoder.feed(bytes([byte])):
            complete = reducer.accept(frame, bytes_received=decoder.bytes_received)
    for frame in decoder.finish():
        complete = reducer.accept(frame, bytes_received=decoder.bytes_received)
    return complete


@pytest.mark.parametrize(
    ("mode", "events", "expected"),
    [
        ("delta", [("TEXT", "hel"), ("TEXT", "lo")], "hello"),
        ("cumulative", [("TEXT", "hel"), ("TEXT", "hello")], "hello"),
        ("final", [("TEXT", "ignored"), ("RUN_FINISHED", "answer")], "answer"),
    ],
)
def test_reducer_assembles_declared_text_modes(mode, events, expected):
    text_rule = {
        "mode": mode,
        "path": "data.text",
        "when": {
            "path": "data.type",
            "values": ["RUN_FINISHED" if mode == "final" else "TEXT"],
        },
    }
    spec = {
        "completion": {"path": "data.type", "values": ["RUN_FINISHED"]},
        "text": [text_rule],
    }
    raw = b"".join(_event({"type": kind, "text": value}) for kind, value in events)
    if events[-1][0] != "RUN_FINISHED":
        raw += _event({"type": "RUN_FINISHED"})
    assert _reduce(raw, spec).text == expected


def test_decoder_handles_comments_multiline_data_and_duplicate_ids():
    decoder = SSEDecoder()
    reducer = SSEReducer(
        HTTPStreamSpec.model_validate(
            {
                "completion": {"path": "event", "values": ["done"]},
                "text": [
                    {
                        "mode": "delta",
                        "path": "data",
                        "when": {"path": "event", "values": ["message"]},
                    }
                ],
            }
        )
    )
    raw = b"\xef\xbb\xbf: heartbeat\r\rid: one\rdata: hello\rdata: world\r\r"
    frames = decoder.feed(raw)
    assert len(frames) == 1
    assert frames[0].data == "hello\nworld"
    reducer.accept(frames[0], bytes_received=decoder.bytes_received)
    reducer.accept(frames[0], bytes_received=decoder.bytes_received)
    done = reducer.accept(
        decoder.feed(b"event: done\ndata: null\n\n")[0],
        bytes_received=decoder.bytes_received,
    )
    assert done.text == "hello\nworld"
    assert done.duplicate_events == 1


def test_decoder_rejects_invalid_utf8_json_and_bounds(monkeypatch):
    with pytest.raises(SSEProtocolError, match="valid UTF-8"):
        SSEDecoder().feed(b"data: \xff\n\n")
    with pytest.raises(SSEProtocolError, match="valid JSON"):
        SSEDecoder().feed(b"data: {broken}\n\n")
    monkeypatch.setattr(
        "fi.simulate.agent.wrappers.http_stream.MAX_STREAM_EVENT_BYTES", 8
    )
    with pytest.raises(SSEProtocolError, match="event exceeds"):
        SSEDecoder().feed(b"data: 123456789")
    monkeypatch.setattr("fi.simulate.agent.wrappers.http_stream.MAX_STREAM_BYTES", 8)
    with pytest.raises(SSEProtocolError, match="response exceeds"):
        SSEDecoder().feed(b"123456789")


def test_reducer_rejects_conflicting_ids_and_event_limit(monkeypatch):
    spec = HTTPStreamSpec.model_validate(SPEC)
    reducer = SSEReducer(spec)
    first = SSEDecoder().feed(b'id: same\ndata: {"type":"OTHER"}\n\n')[0]
    reducer.accept(first, bytes_received=30)
    changed = SSEDecoder().feed(b'id: same\ndata: {"type":"CHANGED"}\n\n')[0]
    with pytest.raises(SSEProtocolError, match="reused"):
        reducer.accept(changed, bytes_received=60)
    monkeypatch.setattr("fi.simulate.agent.wrappers.http_stream.MAX_STREAM_EVENTS", 1)
    limited = SSEReducer(spec)
    limited.accept(first, bytes_received=30)
    with pytest.raises(SSEProtocolError, match="4096 events"):
        limited.accept(changed, bytes_received=60)


@contextmanager
def sse_server():
    state = {
        "release": threading.Event(),
        "cancel_started": threading.Event(),
        "disconnected": threading.Event(),
        "requests": [],
        "cookie_headers": [],
    }

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}"
            )
            if self.path == "/setup":
                state["requests"].append(("POST", self.path))
                encoded = b"{}"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Set-Cookie", "session=sse-session; Path=/")
                self.end_headers()
                self.wfile.write(encoded)
                return
            case = body.get("message", "delta")
            state["requests"].append(("POST", self.path))
            state["cookie_headers"].append(self.headers.get("Cookie", ""))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            if case == "events":
                self.send_header("X-Conversation-ID", "conversation-1")
            self.end_headers()
            if case == "cancel":
                self.wfile.write(
                    _event({"type": "TEXT_MESSAGE_CONTENT", "delta": "partial"})
                )
                self.wfile.flush()
                state["cancel_started"].set()
                self.connection.settimeout(3)
                try:
                    if self.connection.recv(1) == b"":
                        state["disconnected"].set()
                except (ConnectionError, socket.timeout):
                    pass
                return
            if case == "error":
                self.wfile.write(
                    _event({"type": "RUN_ERROR", "message": "agent failed"})
                )
                self.wfile.flush()
                return
            if case == "eof":
                self.wfile.write(
                    _event({"type": "TEXT_MESSAGE_CONTENT", "delta": "partial"})
                )
                self.wfile.flush()
                self.close_connection = True
                return
            if case == "idle":
                state["cancel_started"].set()
                state["release"].wait(3)
                return
            if case == "tools":
                self.wfile.write(
                    _event(
                        {
                            "type": "TOOLS",
                            "items": [
                                {
                                    "id": "tool-1",
                                    "name": "weather",
                                    "arguments": {"city": "Pune"},
                                    "result": {"temperature": 20},
                                    "error": False,
                                }
                            ],
                        },
                        event_id="tools-1",
                    )
                )
            self.wfile.write(
                _event(
                    {"type": "TEXT_MESSAGE_CONTENT", "delta": "hello"},
                    event_id="text-1",
                )
            )
            self.wfile.write(_event({"type": "RUN_FINISHED"}, event_id="done-1"))
            self.wfile.flush()
            state["release"].wait(3)

        def do_GET(self):
            state["requests"].append(("GET", self.path))
            page = 2 if "page=2" in self.path else 1
            body = (
                {
                    "items": [
                        {
                            "kind": "action",
                            "id": "tool-2",
                            "name": "weather",
                            "arguments": '{"city":"Pune"}',
                        }
                    ],
                    "next": "2",
                }
                if page == 1
                else {
                    "items": [
                        {
                            "kind": "completed",
                            "action_id": "tool-2",
                            "result": {"temperature": 20},
                            "failed": False,
                        }
                    ],
                    "next": None,
                }
            )
            encoded = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/chat", state
    finally:
        state["release"].set()
        server.shutdown()
        server.server_close()
        thread.join()


def _wrapper(endpoint, **changes):
    timeout = changes.pop("timeout", 2)
    return HTTPAgentWrapper(
        endpoint=endpoint,
        protocol="json_template",
        request_template={"message": "{{new_message_content}}"},
        response_path="delta",
        sse=SPEC,
        timeout=timeout,
        **changes,
    )


def _input(message):
    return AgentInput(
        thread_id="thread-1",
        messages=[],
        new_message={"role": "user", "content": message},
    )


def test_live_stream_returns_on_completion_before_server_eof():
    with sse_server() as (endpoint, state):
        response = asyncio.run(_wrapper(endpoint).call(_input("delta")))
        assert response.content == "hello"
        assert response.metadata["external_agent"]["stream_state"] == "complete"
        assert not state["release"].is_set()


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("error", "sse_application"),
        ("eof", "sse_incomplete"),
    ],
)
def test_live_stream_requires_success_terminal(case, reason):
    with sse_server() as (endpoint, _state):
        response = asyncio.run(_wrapper(endpoint).call(_input(case)))
        trace = response.metadata["external_agent"]
        assert trace["success"] is False
        assert trace["failure_stage"] == "stream"
        assert trace["stream_reason"] == reason
        assert response.content == ""
        assert not response.tool_calls


def test_cancellation_closes_stream_and_next_call_succeeds():
    async def exercise(endpoint, state):
        wrapper = _wrapper(endpoint)
        task = asyncio.create_task(wrapper.call(_input("cancel")))
        await asyncio.to_thread(state["cancel_started"].wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(state["disconnected"].wait, 1)
        return await wrapper.call(_input("delta"))

    with sse_server() as (endpoint, state):
        response = asyncio.run(exercise(endpoint, state))
        assert response.content == "hello"


def test_live_stream_has_an_absolute_deadline():
    with sse_server() as (endpoint, _state):
        response = asyncio.run(_wrapper(endpoint, timeout=0.1).call(_input("idle")))
        trace = response.metadata["external_agent"]
        assert trace["success"] is False
        assert trace["failure_stage"] == "stream"
        assert trace["stream_reason"] == "sse_deadline"


def test_completed_response_tools_are_retained_only_after_success():
    completed = {
        "items_path": "items",
        "name_path": "name",
        "arguments_path": "arguments",
        "result_path": "result",
        "id_path": "id",
        "error_path": "error",
    }
    with sse_server() as (endpoint, _state):
        wrapper = _wrapper(endpoint, completed_tools=completed)
        response = asyncio.run(wrapper.call(_input("tools")))
        assert response.tool_calls == []
        assert response.metadata["native_completed_tools"] == [
            {
                "name": "weather",
                "arguments": {"city": "Pune"},
                "result": {"temperature": 20},
                "ok": True,
            }
        ]
        again = asyncio.run(wrapper.call(_input("tools")))
        assert again.metadata["native_completed_tools"] == []


def test_stream_preserves_endpoint_completed_tool_correlation():
    completed_events = {
        "correlation_header": "X-Conversation-ID",
        "path": "/events/{{correlation_id}}",
        "items_path": "items",
        "next_page_path": "next",
        "page_parameter": "page",
        "kind_path": "kind",
        "action_kind": "action",
        "observation_kind": "completed",
        "action_id_path": "id",
        "observation_action_id_path": "action_id",
        "name_path": "name",
        "arguments_path": "arguments",
        "result_path": "result",
        "error_path": "failed",
    }
    with sse_server() as (endpoint, state):
        response = asyncio.run(
            _wrapper(endpoint, completed_tool_events=completed_events).call(
                _input("events")
            )
        )
        assert response.metadata["native_completed_tools"] == [
            {
                "name": "weather",
                "arguments": {"city": "Pune"},
                "result": {"temperature": 20},
                "ok": True,
            }
        ]
        assert state["requests"] == [
            ("POST", "/chat"),
            ("GET", "/events/conversation-1"),
            ("GET", "/events/conversation-1?page=2"),
        ]


def test_stream_reuses_setup_session_cookie():
    with sse_server() as (endpoint, state):
        response = asyncio.run(
            _wrapper(endpoint, setup_requests=[{"path": "/setup"}]).call(
                _input("cookie")
            )
        )
        assert response.content == "hello"
        assert state["requests"] == [("POST", "/setup"), ("POST", "/chat")]
        assert state["cookie_headers"] == ["session=sse-session"]


def test_runtime_contract_validates_and_forwards_sse():
    interface = RuntimeInterface(
        kind="http",
        port=8000,
        path="/chat",
        protocol="json_template",
        request_template={"message": "{{new_message_content}}"},
        sse=SPEC,
    )
    wrapper = create_chat_wrapper(interface, "http://127.0.0.1:8000")
    assert wrapper.sse.completion.path == "data.type"
    with pytest.raises(ValidationError, match="runtime_sse_requires_json_template"):
        RuntimeInterface(
            kind="http", port=8000, path="/chat", protocol="openai_chat", sse=SPEC
        )
    with pytest.raises(ValidationError, match="runtime_sse_requires_http"):
        RuntimeInterface(kind="command", sse=SPEC)


def test_sse_authoring_schema_is_self_contained(tmp_path):
    from fi.alk.harness.tools import contract_tools

    submit = next(
        tool
        for tool in contract_tools(tmp_path).tools
        if tool.name == "submit_contract"
    )
    schema = submit.input_schema["properties"]["runtime"]["properties"]["interface"][
        "properties"
    ]["sse"]
    assert "$defs" not in schema
    assert "$ref" not in json.dumps(schema)
    assert schema["required"] == ["completion", "text"]
