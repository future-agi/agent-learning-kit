"""The world's door for tools an agent runs in its own process: the door decides, the agent's process runs the call."""

from __future__ import annotations

import json
import sys

import pytest

from fi.alk.harness import in_process_door_bootstrap as bootstrap
from fi.alk.harness import in_process_door
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
    "answer",
    [
        json.dumps({"error": "the payment provider did not accept the refund: URLError"}),
        {"error": "the payment provider did not accept the refund: URLError"},
    ],
)
def test_a_tool_that_answered_with_an_error_is_refused_never_committed(answer, monkeypatch):
    # The tool returned instead of raising, but it did not do its work: the same as a tools service's 4xx.
    door, log = _door()
    decision = door.begin("issue_refund", {"order": 7})
    assert door.end(decision["id"], True, answer)["outcome"] == "refused"
    [row] = log.rows()
    assert row["outcome"] == "refused" and row["error"].startswith("the payment provider did not accept")
    # A read that answers "not found" is still a read, never a refused write.
    monkeypatch.setattr(in_process_door, "READ_TOOLS", {"get_order"})
    decision = door.begin("get_order", {"order": 99})
    assert door.end(decision["id"], True, {"error": "no order 99"})["outcome"] == "committed"
    assert [row["effect"] for row in log.rows() if row["name"] == "get_order"] == ["read"]
    # An answer that only mentions an error elsewhere still counts as done.
    decision = door.begin("issue_refund", {"order": 8})
    assert door.end(decision["id"], True, {"refund_id": "RF-9", "error_count": 0})["outcome"] == "committed"


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


def test_a_lost_answer_is_recorded_and_its_trial_graded_though_the_agent_s_tracers_keep_it_twice(monkeypatch):
    from fi.alk.harness.call_runner import (
        _merge_span_calls,
        evidence_notices,
        missing_evidence,
        one_call_per_lost_answer,
    )
    from fi.alk.harness.world.runtime import Call

    door, log = _door([{"action": "issue_refund", "mode": "timeout_after_commit", "on_call": 1, "delay_seconds": 0}])
    _wired(monkeypatch, door)
    held = bootstrap.door("issue_refund")(lambda order_id, amount: {"refund_id": "RF-100", "amount": amount})
    with pytest.raises(TimeoutError):
        held("1043", 89.0)
    [row] = log.rows()
    assert (row["outcome"], row["error"], row["arguments"]) == (
        "committed", "response_lost", {"order_id": "1043", "amount": 89.0}
    )
    # The tool's own trace line saw the run commit; the framework's span saw the timeout the agent heard.
    ran = Call(name="issue_refund", arguments={"order_id": "1043", "amount": 89}, ok=True, result={"refund_id": "RF-100"})
    heard = Call(name="issue_refund", arguments={"order_id": "1043", "amount": 89.0}, ok=False, error="timed out")
    calls = one_call_per_lost_answer(_merge_span_calls([ran], [heard]), log.rows())
    # Graded as the one call the world made: done, its answer lost on the way back.
    assert [(call.name, call.ok, call.answer_lost) for call in calls] == [("issue_refund", True, True)]
    notices = evidence_notices(log.rows(), calls, {"issue_refund"})
    assert notices == [] and missing_evidence(notices, {}) == {}
    # Through grading, a sub-goal that needs the refund done holds; a refund the world refused is not done.
    from fi.alk.harness.checks import run_check

    refunded = (
        "def check(world, calls):\n"
        "    if not any(c.name == 'issue_refund' and c.ok for c in calls):\n"
        "        return 'not done: issue_refund was not called'\n"
    )
    assert run_check(refunded, None, calls).held
    refused = Call(name="issue_refund", arguments={"order_id": "1043", "amount": 89.0}, ok=False, error="refused")
    assert not run_check(refunded, None, one_call_per_lost_answer([refused], [])).held
    # A retry the door never saw is still a call it kept no row for.
    retried = [*calls, Call(name="issue_refund", arguments={"order_id": "1043", "amount": 89.0}, ok=True)]
    assert evidence_notices(log.rows(), retried, {"issue_refund"})[0]["count"] == 1


def test_a_call_that_needs_an_approval_never_given_does_not_run():
    """A production-wrapped refund that needs a supervisor's approval: refused by the door on purpose, so the tool
    never runs and the world never changes, whatever its own code would do."""
    door, log = _door([{"action": "issue_refund", "mode": "approval_required", "approval": "approvals.change"}])

    decision = door.begin("issue_refund", {"order": 7})

    assert decision["run"] is False and decision["outcome"] == "refused" and decision["status"] == 403
    [row] = log.rows()
    assert (row["name"], row["outcome"], row["refusal_code"]) == ("issue_refund", "refused", "approval_missing")


def test_a_call_whose_approval_cannot_be_read_does_not_run(monkeypatch):
    door, log = _door([{"action": "issue_refund", "mode": "approval_required", "approval": "approvals.change"}])

    def unreadable(fault):
        raise RuntimeError("approvals table down")

    monkeypatch.setattr(door.door, "_approved", unreadable)
    decision = door.begin("issue_refund", {"order": 7})

    assert decision["run"] is False and decision["outcome"] == "faulted"
    assert [row["refusal_code"] for row in log.rows()] == ["approval_unknown"]


def test_a_door_that_fails_lets_the_tool_run_and_marks_the_trial_s_evidence_incomplete(monkeypatch):
    import fi.alk.harness.call_runner as cr
    from fi.alk.harness.call_runner import evidence_notices, missing_evidence, read_tool_trace
    from fi.alk.harness.world_door import EVIDENCE_INCOMPLETE

    door, log = _door()

    def broken(call):
        raise RuntimeError("down")

    door.door._enter = broken
    decision = door.begin("issue_refund", {})
    assert decision == {"run": True, "id": "", "door": "failed"}
    marks = [row for row in log.rows() if row.get("refusal_code") == EVIDENCE_INCOMPLETE]
    assert [(row["name"], row["outcome"], row["error"]) for row in marks] == [
        ("issue_refund", "unrecorded", "the world's door failed")
    ]
    [notice] = evidence_notices(log.rows())
    assert notice["code"] == EVIDENCE_INCOMPLETE and notice["count"] == 1
    assert missing_evidence([notice], {}) == {"tool_calls": notice["message"]}
    # The mark is never one of the agent's calls.
    monkeypatch.setattr(cr, "_read_action_log", lambda dsn, **_: marks)
    assert read_tool_trace("dsn") == []


def test_a_door_that_does_not_decide_in_time_lets_the_tool_run_and_marks_it(monkeypatch):
    from fi.alk.harness.world_door import EVIDENCE_INCOMPLETE

    door, log = _door()
    monkeypatch.setattr(in_process_door, "DECIDE_SECONDS", 0.01)
    release = __import__("threading").Event()
    door.door._enter = lambda call: release.wait(2) or None
    assert door.begin("issue_refund", {}) == {"run": True, "id": "", "door": "undecided"}
    release.set()
    assert [row["error"] for row in log.rows() if row.get("refusal_code") == EVIDENCE_INCOMPLETE] == [
        "the world's door did not decide in time"
    ]


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


def test_a_door_out_of_reach_lets_the_tool_run_and_says_so_once_it_is_back(monkeypatch):
    from fi.alk.harness.world_door import EVIDENCE_INCOMPLETE

    monkeypatch.setattr(bootstrap, "_UNRECORDED", {})
    monkeypatch.setattr(bootstrap, "_post", lambda path, body: None)
    held = bootstrap.door("issue_refund")(lambda order: order + 1)
    assert held(1) == 2 and held(2) == 3
    door, log = _door()
    sent = []

    def back(path, body):
        sent.append(body)
        if path == "/begin":
            return door.begin(body["name"], body["arguments"], unrecorded=body.get("unrecorded", 0))
        return door.end(body["id"], body["ok"], body.get("result"))

    monkeypatch.setattr(bootstrap, "_post", back)
    assert held(3) == 4
    assert sent[0]["unrecorded"] == 2 and "unrecorded" not in sent[-1]
    # One log write for both, carrying how many.
    marks = [row for row in log.rows() if row.get("refusal_code") == EVIDENCE_INCOMPLETE]
    assert [(row["error"], row["arguments"]) for row in marks] == [("the world's door was out of reach", {"count": 2})]
    from fi.alk.harness.call_runner import evidence_notices

    assert evidence_notices(log.rows())[0]["count"] == 2


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


def test_a_script_started_by_its_path_is_started_as_a_module_so_the_door_reaches_its_tools():
    from fi.alk.harness.bundle_author_v2 import run_bound_script_as_module

    bound = [{"name": "issue_refund", "module": "agent", "callable": "issue_refund"}]
    assert run_bound_script_as_module(["python", "agent.py"], bound) == ["python", "-m", "agent"]
    assert run_bound_script_as_module([".venv/bin/python3.12", "agent.py", "--port", "8080"], bound) == [
        ".venv/bin/python3.12", "-m", "agent", "--port", "8080",
    ]
    assert run_bound_script_as_module(["uv", "run", "--no-sync", "python", "agent.py"], bound)[-3:] == [
        "python", "-m", "agent",
    ]
    assert run_bound_script_as_module(["sh", "-c", "PORT=$PORT_x exec python agent.py"], bound) == [
        "sh", "-c", "PORT=$PORT_x exec python -m agent",
    ]
    # Nothing bound in the script, a script in a folder, or a server that imports the module: left as they are.
    assert run_bound_script_as_module(["python", "server.py"], bound) == ["python", "server.py"]
    assert run_bound_script_as_module(["python", "src/agent.py"], bound) == ["python", "src/agent.py"]
    assert run_bound_script_as_module(["uvicorn", "agent:app"], bound) == ["uvicorn", "agent:app"]
    assert run_bound_script_as_module(["sh", "-c", "python my_agent.py"], bound) == ["sh", "-c", "python my_agent.py"]


@pytest.mark.parametrize(
    ("command", "module", "environment"),
    [
        (["python", "src/agent.py"], "agent", None),  # sys.path[0] would move from src/ to the working directory
        (["python", "src/agent.py"], "src.agent", None),
        (["python", "my-agent.py"], "my-agent", None),  # not a module name
        (["python", "test.py"], "test", None),  # the standard library's `test` package
        (["python", "json.py"], "json", None),
        (["python", "sys.py"], "sys", None),  # built in: found before any file
        (["sh", "-c", "exec python test.py"], "test", None),
        (["python", "-P", "agent.py"], "agent", None),  # the working directory kept off the path
        (["python", "-u", "agent.py"], "agent", None),  # interpreter flags: not proven the same, left alone
        (["python", "agent.py"], "agent", {"PYTHONSAFEPATH": "1"}),
    ],
)
def test_a_script_is_left_as_it_starts_wherever_running_it_as_a_module_could_differ(command, module, environment):
    from fi.alk.harness.bundle_author_v2 import run_bound_script_as_module

    bound = [{"name": "issue_refund", "module": module, "callable": "issue_refund"}]
    assert run_bound_script_as_module(command, bound, environment) == command


def test_a_script_run_as_its_module_starts_exactly_as_it_did(tmp_path):
    import subprocess
    from pathlib import Path

    hook = tmp_path / "hook"
    hook.mkdir()
    (hook / "in_process_door_bootstrap.py").write_bytes(Path(bootstrap.__file__).read_bytes())
    (hook / "sitecustomize.py").write_text("import in_process_door_bootstrap\nin_process_door_bootstrap.install()\n")
    app = tmp_path / "app"
    app.mkdir()
    (app / "store.py").write_text("ORDERS = {'A-1': 1}\n")
    (app / "orders.json").write_text('{"A-1": 1}')
    (app / "agent.py").write_text(
        "import json, os, sys\nimport store\n\ndef issue_refund(order):\n    return store.ORDERS[order]\n\n"
        "if __name__ == '__main__':\n"
        "    here = os.path.dirname(os.path.abspath(__file__))\n"
        "    print(json.dumps({'name': __name__, 'path0': sys.path[0], 'cwd': os.getcwd(), 'file': __file__,\n"
        "        'beside': json.load(open(os.path.join(here, 'orders.json'))), 'refund': issue_refund('A-1')}))\n"
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(hook),
        "ALK_WORLD_DOOR_URL": "http://127.0.0.1:1",
        "ALK_WORLD_DOOR_BINDINGS": json.dumps([{"name": "issue_refund", "module": "agent", "callable": "issue_refund"}]),
    }

    def started(command):
        done = subprocess.run(command, cwd=app, env=env, capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout)

    by_path = started([sys.executable, "agent.py"])
    as_module = started([sys.executable, "-m", "agent"])
    assert as_module == by_path
    assert as_module["name"] == "__main__" and as_module["refund"] == 1
    assert Path(as_module["path0"]).resolve() == app.resolve() == Path(as_module["file"]).resolve().parent


def test_a_module_run_as_main_gets_the_door_on_its_tools_before_its_framework_decorator(tmp_path):
    import subprocess
    from pathlib import Path

    hook = tmp_path / "hook"
    hook.mkdir()
    (hook / "in_process_door_bootstrap.py").write_bytes(Path(bootstrap.__file__).read_bytes())
    (hook / "sitecustomize.py").write_text("import in_process_door_bootstrap\nin_process_door_bootstrap.install()\n")
    app = tmp_path / "app"
    app.mkdir()
    (app / "agent.py").write_text(
        "from __future__ import annotations\n\nKEPT = []\n\ndef function_tool(fn):\n    KEPT.append(fn)\n    return fn\n\n"
        "@function_tool\ndef issue_refund(order: str) -> str:\n    return order\n\n"
        "if __name__ == '__main__':\n    print(getattr(KEPT[0], '__alk_world_door__', False), issue_refund('A-1'))\n"
    )
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(hook),
        "ALK_WORLD_DOOR_URL": "http://127.0.0.1:1",
        "ALK_WORLD_DOOR_BINDINGS": json.dumps([{"name": "issue_refund", "module": "agent", "callable": "issue_refund"}]),
    }
    as_module = subprocess.run(
        [sys.executable, "-m", "agent"], cwd=app, env=env, capture_output=True, text=True, timeout=60
    )
    assert as_module.stdout.split() == ["True", "A-1"], as_module.stderr
    by_path = subprocess.run([sys.executable, "agent.py"], cwd=app, env=env, capture_output=True, text=True, timeout=60)
    assert by_path.stdout.split() == ["False", "A-1"], by_path.stderr


@pytest.mark.parametrize(
    ("command", "module", "unreached"),
    [
        (["python", "-m", "agent"], "agent", False),
        (["uvicorn", "agent:app"], "agent", False),
        (["python", "agent.py"], "agent", True),
        (["python", "-u", "agent.py"], "agent", True),
        (["python", "src/agent.py"], "agent", True),
        (["python", "src/agent.py"], "src.agent", True),
        (["sh", "-c", "exec python ./test.py --port $PORT"], "test", True),
        (["python", "server.py"], "agent", False),
    ],
)
def test_the_sealed_contract_says_which_bound_tools_the_door_cannot_reach(command, module, unreached):
    from fi.alk.harness.bundle_author_v2 import (
        DOOR_UNREACHED_SCRIPT,
        WORLD_DOOR_PROCESS,
        door_unreached_tools,
        with_door_unreached,
    )
    from fi.alk.harness.bundle_v2 import ProcessUser, SourceProcess

    agent = SourceProcess(name="agent", working_directory="source", run_command=command, user=ProcessUser.SVC_AGENT)
    door = SourceProcess(
        name=WORLD_DOOR_PROCESS, working_directory="d", run_command=["python", "server.py"], user=ProcessUser.SVC_TOOLS
    )
    entries = [
        {"tool": "issue_refund", "mode": "import", "module": module, "callable": "issue_refund"},
        {"tool": "lookup", "mode": "service", "service": "tools-api", "endpoint": "orders/lookup"},
    ]
    found = door_unreached_tools([agent, door], "agent", entries, "python")
    assert found == ({"issue_refund": DOOR_UNREACHED_SCRIPT} if unreached else {})
    marked = with_door_unreached({"tool_entrypoints": entries}, found)["tool_entrypoints"]
    assert bool(marked[0].get("door_unreached")) is unreached
    assert "door_unreached" not in marked[1]
    # No door in the bundle: nothing is said.
    assert door_unreached_tools([agent], "agent", entries, "python") == {}


def test_a_mark_from_an_earlier_seal_is_cleared_when_the_door_reaches_the_tool():
    from fi.alk.harness.bundle_author_v2 import with_door_unreached

    contract = {"tool_entrypoints": [{"tool": "issue_refund", "mode": "import", "door_unreached": "old"}]}
    assert "door_unreached" not in with_door_unreached(contract, {})["tool_entrypoints"][0]


def test_a_tool_whose_approval_was_never_given_raises_the_world_s_refusal_not_a_timeout(monkeypatch):
    """The decorated tool, held by the door for an approval never given, tells the agent it was refused and why, so
    it does not read a timeout and retry."""
    door, _ = _door([{"action": "issue_refund", "mode": "approval_required", "approval": "approvals.change"}])
    monkeypatch.setattr(bootstrap, "_post", lambda path, body: door.begin(body["name"], body["arguments"]))
    ran = []

    held = bootstrap.door("issue_refund")(lambda order: ran.append(order))

    with pytest.raises(bootstrap.ToolRefused) as refused:
        held(7)
    assert not isinstance(refused.value, TimeoutError)
    assert "403" in str(refused.value) and "approv" in str(refused.value).lower()
    assert ran == []


def test_a_tool_whose_approval_cannot_be_read_fails_with_the_door_s_reason_not_a_timeout(monkeypatch):
    door, _ = _door([{"action": "issue_refund", "mode": "approval_required", "approval": "approvals.change"}])

    def unreadable(fault):
        raise RuntimeError("approvals table down")

    monkeypatch.setattr(door.door, "_approved", unreadable)
    monkeypatch.setattr(bootstrap, "_post", lambda path, body: door.begin(body["name"], body["arguments"]))

    with pytest.raises(bootstrap.ToolFailed):
        bootstrap.door("issue_refund")(lambda order: order)(7)


def test_a_timeout_fault_on_a_call_the_world_refused_still_reaches_the_agent_as_a_timeout(monkeypatch):
    """A timeout_after_commit whose run the world refused keeps the refused outcome in the log, but the agent's tool
    still raises the scenario's timeout, never a refusal."""
    decision = {"run": False, "mode": "timeout_after_commit", "outcome": "refused", "status": 504, "detail": "no",
                "delay_seconds": 0}
    monkeypatch.setattr(bootstrap, "_post", lambda path, body: decision)

    with pytest.raises(bootstrap.ToolTimedOut):
        bootstrap.door("issue_refund")(lambda order: order)(7)


@pytest.mark.parametrize(
    ("raised", "faults"),
    [("UndefinedTable", []), ("OperationalError", None)],
    ids=["no faults table: no faults", "database down: unknown"],
)
def test_the_trial_s_faults_read_as_none_only_when_none_were_ever_set(monkeypatch, raised, faults):
    """No faults table means no faults were ever set; a database that cannot be read says nothing, so the door is told
    it cannot read them rather than that there are none."""
    import types

    from fi.alk.harness import tool_trace_proxy as proxy

    error = type(raised, (Exception,), {})

    def connect(*_args, **_kwargs):
        raise error("no")

    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=connect))
    monkeypatch.setattr(proxy, "DATABASE_URL", "postgresql://world")
    monkeypatch.setattr(proxy, "_FAULTS_CONNECTION", None)

    if faults is not None:
        assert proxy._trial_faults() == faults
    else:
        with pytest.raises(error):
            proxy._trial_faults()


def test_a_mark_for_several_calls_counts_each_of_them_once():
    """Three refunds, one recorded and two run while the door was out of reach, told in one mark of count 2: two
    calls are unrecorded, never four."""
    from fi.alk.harness.call_runner import evidence_notices
    from fi.alk.harness.world.runtime import Call
    from fi.alk.harness.world_door import DOOR_OUT_OF_REACH, EVIDENCE_INCOMPLETE

    log = [
        {"name": "issue_refund", "actor_id": "agent", "outcome": "committed"},
        {"name": "issue_refund", "actor_id": "agent", "outcome": "unrecorded", "error": DOOR_OUT_OF_REACH,
         "refusal_code": EVIDENCE_INCOMPLETE, "arguments": {"count": 2}},
    ]
    calls = [Call(name="issue_refund", arguments={}, ok=True) for _ in range(3)]

    [notice] = evidence_notices(log, calls, {"issue_refund"})

    assert notice["count"] == 2


def test_a_call_whose_flag_is_true_is_never_taken_for_the_lost_answer_of_a_call_sent_with_1():
    """`89` and `89.0` are one amount across two traces; `true` is never the number 1."""
    from fi.alk.harness.call_runner import one_call_per_lost_answer
    from fi.alk.harness.world.runtime import Call

    log = [{"name": "issue_refund", "actor_id": "agent", "outcome": "committed", "error": "response_lost",
            "arguments": {"order_id": "1043", "notify": 1, "amount": 89.0}}]
    other = Call(name="issue_refund", arguments={"order_id": "1043", "notify": True, "amount": 89}, ok=True)

    [kept] = one_call_per_lost_answer([other], log)

    assert not kept.answer_lost
