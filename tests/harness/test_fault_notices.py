"""A fault the scenario sets that did not take effect is said on the trial, never only logged."""

from __future__ import annotations

import json

import pytest

from fi.alk.harness.call_runner import fault_notices
from fi.alk.harness.run.conversation import Transcript
from fi.alk.harness.world.runtime import Call

FAULT = {"action": "crm.apply_discount", "on_call": 1, "mode": "timeout_before_commit"}


def _call(name: str) -> Call:
    return Call(name=name, arguments={}, ok=True)


def test_a_fault_the_door_applied_says_nothing():
    log = [{"name": "apply_discount", "outcome": "faulted", "error": "timeout_before_commit"}]
    assert fault_notices([FAULT], log, [_call("apply_discount")]) == []


@pytest.mark.parametrize("mode", ["timeout_before_commit", "timeout_after_commit"])
def test_a_fault_the_real_door_applied_says_nothing_in_either_mode(mode):
    from fi.alk.harness.world_door import DictRoster, Executed, MemoryLog, ToolCall, WorldDoor

    class _Ok:
        def run(self, call):
            return Executed(200, {"applied": True})

    fault = {**FAULT, "mode": mode, "delay_seconds": 0}
    log = MemoryLog()
    door = WorldDoor(_Ok(), log, DictRoster({}), faults=lambda: [fault])
    door.enter(ToolCall(action="crm.apply_discount", name="apply_discount", arguments={"pct": 10}))
    assert fault_notices([fault], log.rows(), [_call("apply_discount")]) == []


def test_a_fault_the_proxy_applied_under_the_route_s_name_says_nothing():
    fault = {"action": "get_order", "on_call": 1, "mode": "timeout_before_commit"}
    # As the door writes an applied fault: held before commit, with the mode as its reason.
    log = [{"name": "get", "agent_tool": "get_order", "outcome": "faulted", "error": "timeout_before_commit"}]
    assert fault_notices([fault], log, [_call("get_order")]) == []


def test_a_fault_on_a_call_made_outside_the_door_is_said_not_applied():
    (notice,) = fault_notices([FAULT], [], [_call("apply_discount")])
    assert notice["code"] == "fault_not_applied"
    assert "outside the world's door" in notice["message"]
    assert notice["action"] == "crm.apply_discount"


def test_a_fault_the_agent_never_reached_says_how_many_calls_it_made():
    fault = {**FAULT, "on_call": 2}
    (notice,) = fault_notices([fault], [{"name": "apply_discount", "outcome": "committed"}], [_call("apply_discount")])
    assert notice["code"] == "fault_not_triggered"
    assert "made 1 call of it" in notice["message"]


def test_a_world_without_a_database_cannot_hold_any_fault():
    (notice,) = fault_notices([FAULT], None, [], unreachable="this world has no database")
    assert notice["code"] == "fault_not_applied"
    assert notice["message"].endswith("could not be applied: this world has no database.")


def test_a_call_named_by_another_tool_is_not_taken_for_the_fault_s_action():
    (notice,) = fault_notices([FAULT], [], [_call("apply_refund")])
    assert notice["code"] == "fault_not_triggered"


def test_the_transcript_carries_its_notices_and_omits_them_when_none():
    transcript = Transcript()
    assert "notices" not in json.loads(transcript.artifact())
    transcript.notices = fault_notices([FAULT], [], [_call("apply_discount")])
    assert json.loads(transcript.artifact())["notices"][0]["code"] == "fault_not_applied"


def test_a_call_of_another_action_with_the_same_last_part_is_not_taken_for_the_fault_s_action():
    fault = {"action": "crm.update", "on_call": 1, "mode": "timeout_before_commit"}
    log = [{"name": "update", "action": "ticket.update", "actor_id": "agent", "outcome": "committed"}]
    (notice,) = fault_notices([fault], log, [_call("update")])
    assert notice["code"] == "fault_not_triggered"
    assert "made 0 calls of it" in notice["message"]


def test_a_fault_the_door_applied_after_another_action_with_the_same_last_part_says_nothing():
    from fi.alk.harness.world_door import DictRoster, Executed, MemoryLog, ToolCall, WorldDoor

    class _Ok:
        def run(self, call):
            return Executed(200, {"applied": True})

    fault = {"action": "crm.update", "on_call": 1, "mode": "timeout_before_commit", "delay_seconds": 0}
    log = MemoryLog()
    door = WorldDoor(_Ok(), log, DictRoster({}), faults=lambda: [fault])
    door.enter(ToolCall(action="ticket.update", name="update"))
    door.enter(ToolCall(action="crm.update", name="update"))
    assert fault_notices([fault], log.rows(), [_call("update"), _call("update")]) == []


@pytest.mark.parametrize("mode", ["unavailable", "error"])
def test_an_unavailable_or_error_fault_the_door_applied_says_nothing(mode):
    from fi.alk.harness.world_door import DictRoster, Executed, MemoryLog, ToolCall, WorldDoor

    class _Ok:
        def run(self, call):
            return Executed(200, {"applied": True})

    fault = {**FAULT, "mode": mode, "status": 503}
    log = MemoryLog()
    door = WorldDoor(_Ok(), log, DictRoster({}), faults=lambda: [fault])
    door.enter(ToolCall(action="crm.apply_discount", name="apply_discount", arguments={"pct": 10}))
    assert fault_notices([fault], log.rows(), [_call("apply_discount")]) == []


CONTRACT = {
    "tool_entrypoints": [
        {"tool": "apply_discount", "endpoint": "/crm/apply_discount"},
        {"tool": "post_note", "mode": "import", "door_unreached": "the tool runs inside the agent's own process"},
    ]
}


def test_a_fault_on_an_action_the_agent_does_not_have_is_never_applied_and_leaves_the_trial_unmeasured():
    from fi.alk.harness.call_runner import unmeasured_reasons

    [notice] = fault_notices([{**FAULT, "action": "crm.no_such_action"}], [], [], contract=CONTRACT)
    assert notice["code"] == "fault_unknown_action" and "no tool or world action by that name" in notice["message"]
    assert unmeasured_reasons([notice]) == (notice["message"],)
    # The same fault on an action the agent has, which it never reached, is said but still graded.
    [reached_not] = fault_notices([FAULT], [], [], contract=CONTRACT)
    assert reached_not["code"] == "fault_not_triggered" and unmeasured_reasons([reached_not]) == ()


def test_a_fault_on_a_tool_the_door_cannot_hold_is_never_applied_and_says_why():
    from fi.alk.harness.call_runner import unmeasured_reasons

    [notice] = fault_notices([{**FAULT, "action": "post_note"}], [], [], contract=CONTRACT)
    assert notice["code"] == "fault_not_applied" and "runs inside the agent's own process" in notice["message"]
    assert unmeasured_reasons([notice])


def test_faults_that_cannot_be_read_back_are_reported_unreadable(monkeypatch):
    from fi.alk.harness import call_runner as cr

    class _Connection:
        def __init__(self, row):
            self.row = row

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, *args):
            if isinstance(self.row, Exception):
                raise self.row
            return type("C", (), {"fetchone": lambda _self: self.row})()

    for row, readable in (((json.dumps([FAULT]),), True), (("not json",), False), (RuntimeError("no table"), False),
                          (({"action": "x"},), False)):
        monkeypatch.setattr(cr, "_read_only", lambda dsn, timeout_ms=None, row=row: _Connection(row))
        assert cr.trial_faults_readable("dsn") is readable
    [notice] = fault_notices(
        [FAULT], None, [], unreachable="the trial's faults were unreadable in the world's database", code="fault_unreadable"
    )
    assert notice["code"] == "fault_unreadable" and "unreadable" in notice["message"]
    assert cr.unmeasured_reasons([notice])


def test_a_fault_is_applied_only_on_its_own_call_never_by_another_fault_s_row():
    """An error on call 1 and a timeout on call 3: the agent made two calls, so the door applied the first and never
    reached the second, which is said not to have taken effect."""
    from fi.alk.harness.world_door import DictRoster, Executed, MemoryLog, ToolCall, WorldDoor

    class _Ok:
        def run(self, call):
            return Executed(200, {"applied": True})

    first = {**FAULT, "mode": "error", "on_call": 1, "status": 500}
    third = {**FAULT, "mode": "timeout_after_commit", "on_call": 3, "delay_seconds": 0}
    log = MemoryLog()
    door = WorldDoor(_Ok(), log, DictRoster({}), faults=lambda: [first, third])
    for pct in (10, 11):
        door.enter(ToolCall(action="crm.apply_discount", name="apply_discount", arguments={"pct": pct}))

    notices = fault_notices([first, third], log.rows(), [_call("apply_discount"), _call("apply_discount")])

    assert [(one["on_call"], one["code"]) for one in notices] == [(3, "fault_not_triggered")]


@pytest.mark.parametrize(
    ("row", "code"),
    [
        ({"outcome": "faulted", "error": "RuntimeError: unavailable"}, "fault_not_applied"),
        ({"outcome": "faulted", "error": "this trial's test conditions could not be read",
          "refusal_code": "faults_unknown"}, "fault_unreadable"),
        ({"outcome": "faulted", "error": "whether this action was approved could not be read",
          "refusal_code": "approval_unknown"}, "fault_unreadable"),
    ],
    ids=["world failed", "faults unreadable", "approval unreadable"],
)
def test_a_held_call_that_is_not_the_fault_never_reads_as_the_fault_applied(row, code):
    from fi.alk.harness.call_runner import unmeasured_reasons

    notices = fault_notices([FAULT], [{"name": "apply_discount", **row}], [_call("apply_discount")])

    assert [one["code"] for one in notices] == [code]
    assert unmeasured_reasons(notices)
    assert "outside the world's door" not in notices[0]["message"]
