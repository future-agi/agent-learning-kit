"""The world's door for tools an agent runs in its own process: the door decides, the agent's process runs the call."""

from __future__ import annotations

import json
import sys

import pytest

from fi.alk.harness import in_process_door_bootstrap as bootstrap
from fi.alk.harness.in_process_door import InProcessDoor
from fi.alk.harness.world_door import DictRoster, MemoryLog, Routed, ToolCall, WorldDoor


def _door(faults=()):
    log = MemoryLog()
    world = WorldDoor(Routed(None), log, DictRoster({}), faults=lambda: list(faults), faults_by_tool=True)
    return InProcessDoor(world), log


def test_a_call_the_door_lets_run_is_logged_with_what_the_agent_reported():
    door, log = _door()
    decision = door.begin("issue_refund", {"order": 7})
    assert decision["run"] is True and decision["id"]
    assert door.end(decision["id"], True, {"refund": 1}) == {"lost": False, "outcome": "committed"}
    [row] = log.rows()
    assert (row["name"], row["outcome"], row["result"], row["actor_id"]) == ("issue_refund", "committed", {"refund": 1}, "agent")


def test_a_tool_that_raised_is_logged_as_the_world_refusing_it():
    door, log = _door()
    decision = door.begin("issue_refund", {"order": 7})
    door.end(decision["id"], False, None, "ValueError('no such order')")
    [row] = log.rows()
    assert (row["outcome"], row["error"]) == ("refused", "ValueError('no such order')")


@pytest.mark.parametrize(
    ("fault", "status"),
    [
        ({"mode": "unavailable"}, 503),
        ({"mode": "error", "status": 429, "message": "slow down"}, 429),
        ({"mode": "timeout_before_commit", "delay_seconds": 0}, 504),
    ],
)
def test_a_fault_before_commit_never_lets_the_agent_s_tool_run(fault, status):
    door, log = _door([{"action": "issue_refund", "on_call": 1, **fault}])
    decision = door.begin("issue_refund", {"order": 7})
    assert decision["run"] is False and decision["status"] == status and decision["mode"] == fault["mode"]
    [row] = log.rows()
    assert row["outcome"] == "faulted"
    # The next call of the tool is not held.
    assert door.begin("issue_refund", {"order": 7})["run"] is True


def test_a_timeout_after_commit_lets_the_tool_run_and_loses_its_answer():
    door, log = _door([{"action": "issue_refund", "mode": "timeout_after_commit", "on_call": 1, "delay_seconds": 0}])
    decision = door.begin("issue_refund", {"order": 7})
    assert decision["run"] is True
    answer = door.end(decision["id"], True, {"refund": 1})
    assert answer["lost"] is True and answer["delay_seconds"] == 0.0
    [row] = log.rows()
    assert (row["outcome"], row["error"]) == ("committed", "response_lost")


def test_a_door_that_fails_lets_the_tool_run_as_without_it():
    door, log = _door()

    def broken(call):
        raise RuntimeError("down")

    door.door._enter = broken
    decision = door.begin("issue_refund", {})
    assert decision == {"run": True, "id": "", "door": "failed"}


def test_an_actor_cannot_take_one_of_the_agent_s_in_process_tools_but_can_change_a_record():
    from fi.alk.harness.call_runner import record_door  # noqa: F401 - the door actors use beside an in-process agent
    from fi.alk.harness.in_process_door import _NotHere
    from fi.alk.harness.world_door import Executed

    class _Records:
        def run(self, call):
            return Executed(200, {"changed": 1})

    door = WorldDoor(
        Routed(_NotHere(), _Records()),
        MemoryLog(),
        DictRoster({"dana": {"permitted_actions": ["issue_refund", "approvals.change"], "on_behalf_of": None}}),
    )
    refused = door.enter(ToolCall(action="issue_refund", name="issue_refund", actor="dana"))
    changed = door.enter(ToolCall(action="approvals.change", name="change", actor="dana", form="record",
                                  arguments={"where": {"id": 1}, "set": {"status": "approved"}}))
    assert (refused.outcome, changed.outcome) == ("refused", "committed")


def test_the_door_is_the_innermost_decorator_so_a_framework_decorator_keeps_the_wrapped_tool():
    source = "def tool(fn):\n    REGISTRY.append(fn)\n    return fn\n\n@tool\ndef issue_refund(order):\n    return order\n"
    tree = bootstrap.decorate_source(source, {"issue_refund": "issue_refund"})
    assert tree is not None
    namespace = {"REGISTRY": [], bootstrap.DECORATOR: lambda name: (lambda fn: setattr(fn, "door", name) or fn)}
    exec(compile(tree, "<agent>", "exec"), namespace)  # noqa: S102 - the test's own source
    [registered] = namespace["REGISTRY"]
    assert registered.door == "issue_refund"


def test_a_method_tool_is_found_by_its_class_path():
    source = "class Tools:\n    def issue_refund(self, order):\n        return order\n"
    assert bootstrap.decorate_source(source, {"Tools.issue_refund": "issue_refund"}) is not None
    assert bootstrap.decorate_source(source, {"issue_refund": "issue_refund"}) is None


def test_the_wrapped_tool_keeps_its_signature_and_answer_and_raises_what_the_fault_says(monkeypatch):
    import inspect

    answers = iter([{"run": True, "id": "c1"}, {"lost": False}, {"run": False, "mode": "unavailable", "status": 503}])
    sent = []
    monkeypatch.setattr(bootstrap, "_post", lambda path, body: sent.append((path, body)) or next(answers))

    def issue_refund(order: int, note: str = "") -> dict:
        """Refund an order."""
        return {"refunded": order}

    held = bootstrap.door("issue_refund")(issue_refund)
    assert str(inspect.signature(held)) == str(inspect.signature(issue_refund)) and held.__doc__ == "Refund an order."
    assert held(7) == {"refunded": 7}
    assert sent[0] == ("/begin", {"name": "issue_refund", "arguments": {"order": 7}})
    with pytest.raises(ConnectionError):
        held(8)


def test_a_door_out_of_reach_lets_the_tool_run(monkeypatch):
    monkeypatch.setattr(bootstrap, "_post", lambda path, body: None)
    assert bootstrap.door("issue_refund")(lambda order: order + 1)(1) == 2


def test_an_async_tool_stays_async_and_loses_its_answer_on_a_timeout_after_commit(monkeypatch):
    import asyncio
    import inspect

    answers = iter([{"run": True, "id": "c1"}, {"lost": True, "delay_seconds": 0}])
    monkeypatch.setattr(bootstrap, "_post", lambda path, body: next(answers))

    async def issue_refund(order):
        return order

    held = bootstrap.door("issue_refund")(issue_refund)
    assert inspect.iscoroutinefunction(held)
    with pytest.raises(TimeoutError):
        asyncio.run(held(7))


def test_an_imported_module_s_tool_is_wrapped_as_it_is_defined(tmp_path, monkeypatch):
    (tmp_path / "agent_tools_mod.py").write_text(
        "REGISTRY = []\n\ndef tool(fn):\n    REGISTRY.append(fn)\n    return fn\n\n@tool\ndef issue_refund(order):\n"
        "    return order\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(bootstrap, "URL", "http://127.0.0.1:1")
    monkeypatch.setenv(
        "ALK_WORLD_DOOR_BINDINGS",
        json.dumps([{"name": "issue_refund", "module": "agent_tools_mod", "callable": "issue_refund"}]),
    )
    before = list(sys.meta_path)
    try:
        assert bootstrap.install() is True
        import agent_tools_mod

        assert getattr(agent_tools_mod.REGISTRY[0], "__alk_world_door__", False)
    finally:
        sys.meta_path[:] = before
        sys.modules.pop("agent_tools_mod", None)


def test_the_plan_gets_a_door_for_in_process_tools_and_none_behind_the_tool_proxy():
    from fi.alk.harness.bundle_author_v2 import WORLD_DOOR_PROCESS, in_process_door_bindings

    entries = [
        {"tool": "issue_refund", "mode": "import", "module": "agent.tools", "callable": "issue_refund"},
        {"tool": "lookup", "mode": "service", "service": "tools-api", "endpoint": "orders/lookup"},
        {"tool": "closure", "mode": "unreachable"},
    ]
    assert in_process_door_bindings(entries) == [
        {"name": "issue_refund", "module": "agent.tools", "callable": "issue_refund"}
    ]
    assert in_process_door_bindings(entries, "typescript") == []
    assert WORLD_DOOR_PROCESS == "world-door"


def _wired(monkeypatch, in_process):
    def post(path, body):
        if path == "/begin":
            return in_process.begin(body["name"], body["arguments"])
        return in_process.end(body["id"], body["ok"], body.get("result"), body.get("error", ""))

    monkeypatch.setattr(bootstrap, "_post", post)
    monkeypatch.setattr("fi.alk.harness.in_process_door.RUN_SECONDS", 3)


def test_an_interrupted_tool_still_tells_the_door_it_ended(monkeypatch):
    """Not told, the door waits out the call for minutes, holding every later call of a faulted action."""
    in_process, _ = _door()
    _wired(monkeypatch, in_process)

    @bootstrap.door("refund")
    def refund(order):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        refund(1)
    assert [row["outcome"] for row in in_process.door.events()] == ["refused"]


def test_a_cancelled_async_tool_still_tells_the_door_it_ended(monkeypatch):
    import asyncio
    import time

    in_process, _ = _door()
    _wired(monkeypatch, in_process)

    @bootstrap.door("refund")
    async def refund(order):
        await asyncio.sleep(10)

    async def cancel_it():
        task = asyncio.ensure_future(refund(1))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_it())
    deadline = time.monotonic() + 2
    while not in_process.door.events() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert [row["outcome"] for row in in_process.door.events()] == ["refused"]


def test_a_tool_that_is_not_a_plain_def_in_its_module_is_wrapped_beside_one_that_is(tmp_path, monkeypatch):
    (tmp_path / "mixed_tools_mod.py").write_text(
        "def issue_refund(order):\n    return order\n\n"
        "def _make():\n    def inner(order):\n        return order\n    return inner\n\n"
        "lookup = _make()\n"
        "try:\n    def cancel(order):\n        return order\nexcept Exception:\n    pass\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(bootstrap, "URL", "http://127.0.0.1:1")
    monkeypatch.setenv(
        "ALK_WORLD_DOOR_BINDINGS",
        json.dumps(
            [{"name": name, "module": "mixed_tools_mod", "callable": name} for name in ("issue_refund", "lookup", "cancel")]
        ),
    )
    before = list(sys.meta_path)
    try:
        assert bootstrap.install() is True
        import mixed_tools_mod

        for name in ("issue_refund", "lookup", "cancel"):
            assert getattr(getattr(mixed_tools_mod, name), "__alk_world_door__", False), name
    finally:
        sys.meta_path[:] = before
        sys.modules.pop("mixed_tools_mod", None)
