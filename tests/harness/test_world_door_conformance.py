"""One conformance suite for the world's door: the same cases against every surface and executor, the
same outcomes on each. A new surface (stdio MCP, a prebuilt world's tool server) is added with one entry in
`SURFACES`, and is not done until every case here passes for it."""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from fi.alk.harness.world_door import (
    AGENT,
    DictRoster,
    HandlerExecutor,
    HttpExecutor,
    MemoryLog,
    RecordExecutor,
    Routed,
    ToolCall,
    WorldDoor,
)

ROSTER = {
    "manager": {
        "permitted_actions": ["crm.approve", "approvals.change"],
        "on_behalf_of": None,
    }
}


class _Upstream(BaseHTTPRequestHandler):
    """The agent's own tools service behind the proxy: answers every POST, or fails when told."""

    failing = False

    def log_message(self, *args):
        return

    def do_POST(self):  # noqa: N802
        size = int(self.headers.get("content-length") or 0)
        self.rfile.read(size)
        if _Upstream.failing:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"down")
            return
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def upstream():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


class _Failing:
    """Sets an executor to fail for one case."""

    def __init__(self):
        self.on = False


def _http(upstream, failing):
    class _Toggle(HttpExecutor):
        def run(self, call):
            _Upstream.failing = failing.on
            try:
                return super().run(call)
            finally:
                _Upstream.failing = False

    return _Toggle(upstream)


def _handler(_upstream, failing):
    def handle(call):
        if failing.on:
            raise RuntimeError("handler down")
        return True, {"ok": True}, '{"ok": true}'

    return HandlerExecutor(handle)


def _records(_upstream, failing):
    def execute(sql, params):
        if failing.on:
            raise RuntimeError("database down")
        return 1

    return Routed(_handler(_upstream, failing), RecordExecutor(execute))


# Each surface: how calls reach the door (`via`) and the executor behind it.
SURFACES = {
    "proxy over HTTP": (_http, "direct"),
    "chat runner, in-process handler": (_handler, "direct"),
    "dispatcher, in-process door": (_handler, "dispatcher"),
    "record changes": (_records, "direct"),
}


def _enter(door, call, via):
    if via == "dispatcher" and call.actor != AGENT:
        from fi.alk.harness.actor_dispatch import DoorActs

        sent = asyncio.run(
            DoorActs(door)(
                call.actor,
                call.action,
                call.arguments,
                key=call.key or "k",
                parent=None,
                form=call.form,
            )
        )
        return SimpleNamespace(outcome=sent["outcome"], refusal_code=None, fault=None)
    return door.enter(call)


def _door(name, upstream, log=None, faults=()):
    make, via = SURFACES[name]
    failing = _Failing()
    door = WorldDoor(
        make(upstream, failing),
        log or MemoryLog(),
        DictRoster(ROSTER),
        faults=lambda: list(faults),
    )
    return door, via, failing


def _action(name):
    return "approvals.change" if name == "record changes" else "crm.approve"


def _form(name):
    return "record" if name == "record changes" else "tool"


ARGS = {"where": {"id": 1}, "set": {"status": "approved"}}


@pytest.mark.parametrize("surface", SURFACES)
def test_a_permitted_actor_action_commits_and_is_logged_as_the_actor_s(
    surface, upstream
):
    door, via, _ = _door(surface, upstream)
    outcome = _enter(
        door,
        ToolCall(
            action=_action(surface),
            name="x",
            actor="manager",
            key="k1",
            arguments=ARGS,
            form=_form(surface),
        ),
        via,
    )
    assert outcome.outcome == "committed"
    assert [(row["actor_id"], row["outcome"]) for row in door.events()] == [
        ("manager", "committed")
    ]


@pytest.mark.parametrize("surface", SURFACES)
def test_an_actor_action_it_may_not_take_is_refused_and_logged(surface, upstream):
    door, via, _ = _door(surface, upstream)
    outcome = _enter(
        door,
        ToolCall(action="crm.delete", name="delete", actor="manager", key="k2"),
        via,
    )
    assert outcome.outcome == "refused"
    assert [row["refusal_code"] for row in door.events()] == ["not_permitted"]


@pytest.mark.parametrize("surface", SURFACES)
def test_the_agent_s_call_commits_with_no_permission_check(surface, upstream):
    door, via, _ = _door(surface, upstream)
    assert (
        _enter(door, ToolCall(action="crm.delete", name="delete"), via).outcome
        == "committed"
    )


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("mode", ["timeout_before_commit", "timeout_after_commit"])
def test_the_agent_s_call_with_a_fault_is_faulted_on_its_named_call_only(
    surface, mode, upstream
):
    door, via, _ = _door(
        surface, upstream, faults=[{"action": "crm.update", "mode": mode, "on_call": 1}]
    )
    assert (
        _enter(door, ToolCall(action="crm.update", name="update"), via).fault
        is not None
    )
    assert _enter(door, ToolCall(action="crm.update", name="update"), via).fault is None


@pytest.mark.parametrize("surface", SURFACES)
def test_a_replayed_key_is_a_duplicate_and_reaches_the_world_once(surface, upstream):
    door, via, _ = _door(surface, upstream)
    call = ToolCall(
        action=_action(surface),
        name="x",
        actor="manager",
        key="k3",
        arguments=ARGS,
        form=_form(surface),
    )
    assert [_enter(door, call, via).outcome for _ in range(2)] == [
        "committed",
        "duplicate",
    ]


@pytest.mark.parametrize("surface", SURFACES)
def test_an_executor_error_is_a_fault_never_an_exception(surface, upstream):
    door, via, failing = _door(surface, upstream)
    failing.on = True
    outcome = _enter(
        door,
        ToolCall(
            action=_action(surface),
            name="x",
            actor="manager",
            key="k4",
            arguments=ARGS,
            form=_form(surface),
        ),
        via,
    )
    assert outcome.outcome == "faulted"


@pytest.mark.parametrize("surface", SURFACES)
def test_an_unreachable_log_never_changes_the_answer(surface, upstream):
    class _Down(MemoryLog):
        def write(self, row):
            raise RuntimeError("log down")

    door, via, _ = _door(surface, upstream, log=_Down())
    assert (
        _enter(door, ToolCall(action="crm.update", name="update"), via).outcome
        == "committed"
    )


def test_over_http_a_fault_names_the_world_action_and_never_hits_another_service_s_call(
    upstream,
):
    # Byte for byte with the proxy before the door: a fault matches the action only, so `crm.get` never faults `chat.get`.
    faults = [{"action": "crm.get", "mode": "timeout_before_commit"}]
    door, via, _ = _door("proxy over HTTP", upstream, faults=faults)
    assert _enter(door, ToolCall(action="chat.get", name="get"), via).fault is None
    door, via, _ = _door("proxy over HTTP", upstream, faults=faults)
    assert _enter(door, ToolCall(action="crm.get", name="get"), via).fault is not None
    # A fault written as the bare tool name never fired at the proxy, and still does not.
    door, via, _ = _door(
        "proxy over HTTP",
        upstream,
        faults=[{"action": "get", "mode": "timeout_before_commit"}],
    )
    assert _enter(door, ToolCall(action="crm.get", name="get"), via).fault is None
