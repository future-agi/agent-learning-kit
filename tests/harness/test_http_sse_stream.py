import asyncio
import json
import runpy
import socket
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pydantic import ValidationError

from fi.alk.harness.chat_transport import create_chat_wrapper
from fi.alk.harness.contract import RuntimeInterface
from fi.simulate.agent.wrapper import AgentInput
from fi.simulate.agent.wrappers.http import HTTPAgentWrapper, _ResponseFailure
from fi.simulate.agent.wrappers.http_stream import (
    HTTPStreamSpec,
    MAX_STREAM_BYTES,
    MAX_STREAM_EVENT_BYTES,
    MAX_STREAM_EVENTS,
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


def test_decoder_handles_comments_multiline_data_and_repeated_ids():
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
    frames.extend(decoder.finish())
    assert len(frames) == 1
    assert frames[0].data == "hello\nworld"
    reducer.accept(frames[0], bytes_received=decoder.bytes_received)
    reducer.accept(frames[0], bytes_received=decoder.bytes_received)
    done = reducer.accept(
        decoder.feed(b"event: done\ndata: null\n\n")[0],
        bytes_received=decoder.bytes_received,
    )
    assert done.text == "hello\nworldhello\nworld"


def test_decoder_preserves_crlf_fields_across_byte_boundaries():
    decoder = SSEDecoder()
    frames = []
    raw = b"event: update\r\nid: seven\r\ndata: hello\r\ndata: world\r\n\r\n"
    for byte in raw:
        frames.extend(decoder.feed(bytes([byte])))
    frames.extend(decoder.finish())
    assert len(frames) == 1
    assert frames[0].event == "update"
    assert frames[0].event_id == "seven"
    assert frames[0].data == "hello\nworld"


def test_decoder_accepts_bom_before_the_first_data_field():
    frames = SSEDecoder().feed(b'\xef\xbb\xbfdata: {"type":"RUN_FINISHED"}\n\n')
    assert frames[0].data == {"type": "RUN_FINISHED"}


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


def test_decoder_counts_processed_and_pending_bytes_toward_event_limit(monkeypatch):
    monkeypatch.setattr(
        "fi.simulate.agent.wrappers.http_stream.MAX_STREAM_EVENT_BYTES", 16
    )
    decoder = SSEDecoder()
    decoder.feed(b"data: a\r\n")
    with pytest.raises(SSEProtocolError, match="event exceeds"):
        decoder.feed(b"data: bc")


def test_reducer_accepts_reused_ids_and_enforces_event_limit(monkeypatch):
    spec = HTTPStreamSpec.model_validate(SPEC)
    reducer = SSEReducer(spec)
    first = SSEDecoder().feed(b'id: same\ndata: {"type":"OTHER"}\n\n')[0]
    reducer.accept(first, bytes_received=30)
    changed = SSEDecoder().feed(b'id: same\ndata: {"type":"CHANGED"}\n\n')[0]
    reducer.accept(changed, bytes_received=60)
    assert reducer.last_event_id == "same"
    monkeypatch.setattr("fi.simulate.agent.wrappers.http_stream.MAX_STREAM_EVENTS", 1)
    limited = SSEReducer(spec)
    limited.accept(first, bytes_received=30)
    with pytest.raises(SSEProtocolError, match="exceeds 1 events"):
        limited.accept(changed, bytes_received=60)


def test_common_agent_stream_sizes_fit_default_limits():
    assert MAX_STREAM_EVENTS >= 4_100
    assert MAX_STREAM_BYTES >= 1_600 * 1_024
    assert MAX_STREAM_EVENT_BYTES >= 70 * 1_024


def test_null_text_is_ignored_and_message_ids_separate_delta_groups():
    spec = {
        "completion": {"path": "data.type", "values": ["RUN_FINISHED"]},
        "text": [
            {
                "mode": "delta",
                "path": "data.delta",
                "message_id_path": "data.messageId",
                "when": {
                    "path": "data.type",
                    "values": ["TEXT_MESSAGE_CONTENT"],
                },
            }
        ],
    }
    raw = b"".join(
        _event(payload)
        for payload in (
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": "one", "delta": "Let me check."},
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": "one", "delta": None},
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": "two", "delta": "It is done."},
            {"type": "RUN_FINISHED"},
        )
    )
    assert _reduce(raw, spec).text == "Let me check.\nIt is done."


def test_reducer_rejects_one_event_matching_multiple_text_rules():
    spec = {
        "completion": {"path": "data.type", "values": ["RUN_FINISHED"]},
        "text": [
            {"mode": "delta", "path": "data.text"},
            {"mode": "cumulative", "path": "data.text"},
        ],
    }
    with pytest.raises(SSEProtocolError, match="multiple text rules"):
        _reduce(_event({"type": "RUN_FINISHED", "text": "hello"}), spec)


@contextmanager
def sse_server():
    state = {
        "release": threading.Event(),
        "cancel_started": threading.Event(),
        "disconnected": threading.Event(),
        "requests": [],
        "cookie_headers": [],
        "mutated": threading.Event(),
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
            if case == "redirect":
                self.send_response(307)
                self.send_header("Location", "/redirected")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/event-streaming"
                if case == "invalid_media"
                else "text/event-stream",
            )
            self.send_header("Cache-Control", "no-cache")
            if case == "compressed":
                self.send_header("Content-Encoding", "gzip")
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
            if case == "cancel_mutates":
                self.wfile.write(
                    _event({"type": "TEXT_MESSAGE_CONTENT", "delta": "partial"})
                )
                self.wfile.flush()
                state["cancel_started"].set()
                time.sleep(0.2)
                state["mutated"].set()
                self.wfile.write(_event({"type": "RUN_FINISHED"}))
                self.wfile.flush()
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
            if case == "eof_without_completion_path":
                self.wfile.write(_event({"delta": "partial"}))
                self.wfile.flush()
                self.close_connection = True
                return
            if case == "idle":
                state["cancel_started"].set()
                state["release"].wait(3)
                return
            if case == "partial_idle":
                self.wfile.write(
                    _event({"type": "TEXT_MESSAGE_CONTENT", "delta": "partial"})
                )
                self.wfile.flush()
                state["release"].wait(3)
                return
            if case == "ping_only":
                for _ in range(50):
                    try:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    time.sleep(0.02)
                return
            if case == "slow_active":
                for value in "hello":
                    self.wfile.write(
                        _event({"type": "TEXT_MESSAGE_CONTENT", "delta": value})
                    )
                    self.wfile.flush()
                    time.sleep(0.06)
                self.wfile.write(_event({"type": "RUN_FINISHED"}))
                self.wfile.flush()
                return
            if case == "terminal_tools":
                for index in range(2):
                    self.wfile.write(
                        _event(
                            {
                                "type": "CHUNK",
                                "tool_calls": [
                                    {
                                        "id": f"partial-{index}",
                                        "name": "",
                                        "arguments": {},
                                    }
                                ],
                            }
                        )
                    )
                self.wfile.write(
                    _event(
                        {
                            "type": "RUN_FINISHED",
                            "tool_calls": [
                                {
                                    "id": "final-1",
                                    "name": "weather",
                                    "arguments": {"city": "Pune"},
                                }
                            ],
                            "tool_responses": [
                                {
                                    "tool_call_id": "final-1",
                                    "name": "weather",
                                    "result": "20C",
                                }
                            ],
                        }
                    )
                )
                self.wfile.flush()
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
            self.wfile.write(
                _event(
                    {
                        "type": "MESSAGES_SNAPSHOT",
                        "messages": [
                            {"role": "user", "content": case},
                            {"role": "assistant", "content": "hello"},
                        ],
                    },
                    event_id="snapshot-1",
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
    ("case", "reason", "completion_path_seen"),
    [
        ("error", "sse_application", True),
        ("eof", "sse_incomplete", True),
        ("eof_without_completion_path", "sse_incomplete", False),
    ],
)
def test_live_stream_requires_success_terminal(case, reason, completion_path_seen):
    with sse_server() as (endpoint, _state):
        response = asyncio.run(_wrapper(endpoint).call(_input(case)))
        trace = response.metadata["external_agent"]
        assert trace["success"] is False
        assert trace["failure_stage"] == "stream"
        assert trace["stream_reason"] == reason
        if reason == "sse_incomplete":
            assert trace["stream_completion_path_seen"] is completion_path_seen
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


def test_cancellation_waits_for_the_inflight_target_before_returning():
    async def exercise(endpoint, state):
        task = asyncio.create_task(_wrapper(endpoint).call(_input("cancel_mutates")))
        await asyncio.to_thread(state["cancel_started"].wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert state["mutated"].is_set()

    with sse_server() as (endpoint, state):
        asyncio.run(exercise(endpoint, state))


def test_live_stream_has_an_absolute_deadline():
    with sse_server() as (endpoint, _state):
        response = asyncio.run(
            _wrapper(
                endpoint, timeout=0.1, total_timeout=0.1, secret_values={}
            ).call(
                _input("partial_idle")
            )
        )
        trace = response.metadata["external_agent"]
        assert trace["success"] is False
        assert trace["failure_stage"] == "stream"
        assert trace["stream_reason"] == "sse_deadline"
        assert trace["timeout_error"] is True
        assert trace["stream_events_received"] == 1
        assert trace["response_excerpt"] == "partial"


def test_live_stream_absolute_deadline_stops_ping_only_keepalives():
    with sse_server() as (endpoint, _state):
        started = time.monotonic()
        response = asyncio.run(
            _wrapper(
                endpoint, timeout=0.1, total_timeout=0.2, secret_values={}
            ).call(_input("ping_only"))
        )
        elapsed = time.monotonic() - started

    trace = response.metadata["external_agent"]
    assert elapsed < 0.8
    assert trace["success"] is False
    assert trace["stream_reason"] == "sse_deadline"
    assert trace["timeout_error"] is True


def test_regular_stream_timeout_is_idle_not_absolute():
    with sse_server() as (endpoint, _state):
        response = asyncio.run(
            _wrapper(endpoint, timeout=0.1, secret_values={}).call(
                _input("slow_active")
            )
        )
        assert response.content == "hello"
        assert response.metadata["external_agent"]["success"] is True


def test_only_terminal_payload_contributes_executable_tool_evidence():
    with sse_server() as (endpoint, _state):
        response = asyncio.run(_wrapper(endpoint).call(_input("terminal_tools")))
        assert [call["function"]["name"] for call in response.tool_calls] == [
            "weather"
        ]
        assert response.tool_responses == [
            {
                "tool_call_id": "final-1",
                "name": "weather",
                "result": "20C",
                "error": None,
            }
        ]


def test_live_stream_rejects_content_type_prefixes():
    with sse_server() as (endpoint, _state):
        response = asyncio.run(
            _wrapper(endpoint, secret_values={}).call(_input("invalid_media"))
        )
        trace = response.metadata["external_agent"]
        assert trace["success"] is False
        assert trace["failure_stage"] == "stream"
        assert response.content == ""


def test_stream_rejects_compression_and_does_not_use_environment_proxy(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    with sse_server() as (endpoint, _state):
        successful = asyncio.run(_wrapper(endpoint).call(_input("delta")))
        compressed = asyncio.run(_wrapper(endpoint).call(_input("compressed")))
    assert successful.content == "hello"
    assert compressed.metadata["external_agent"]["success"] is False
    assert compressed.metadata["external_agent"]["stream_reason"] == "sse_encoding"


def test_stream_does_not_follow_redirects():
    with sse_server() as (endpoint, state):
        redirected = asyncio.run(_wrapper(endpoint).call(_input("redirect")))
    assert redirected.metadata["external_agent"]["status_code"] == 307
    assert state["requests"] == [("POST", "/chat")]


def test_stream_disconnect_is_reported_as_a_network_error(monkeypatch):
    wrapper = _wrapper("http://127.0.0.1:1/chat")

    async def disconnect(*_args, **_kwargs):
        wrapper._response_status = 200
        wrapper._stream_trace = {
            "stream_state": "incomplete",
            "stream_reason": "sse_disconnect",
        }
        raise _ResponseFailure("stream", 200, "SSE connection ended unexpectedly")

    monkeypatch.setattr(wrapper, "_post_sse", disconnect)
    response = asyncio.run(wrapper.call(_input("delta")))
    trace = response.metadata["external_agent"]
    assert trace["network_error"] is True
    assert trace["failure_stage"] == "stream"


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


def test_open_source_qualifier_runs_two_turns_without_credentials():
    script = (
        Path(__file__).parents[2]
        / "examples/harness/sse_transport_compatibility/qualify_reference.py"
    )
    qualify = runpy.run_path(str(script))["qualify"]
    with sse_server() as (endpoint, _state):
        base_url = endpoint.removesuffix("/chat")
        summaries = asyncio.run(qualify(base_url, 2))
    assert [summary["success"] for summary in summaries] == [True, True]
    assert all(summary["response_characters"] == 5 for summary in summaries)
