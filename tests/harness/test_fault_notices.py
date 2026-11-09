"""A fault the scenario sets that did not take effect is said on the trial, never only logged."""

from __future__ import annotations

import json

from fi.alk.harness.call_runner import fault_notices
from fi.alk.harness.run.conversation import Transcript
from fi.alk.harness.world.runtime import Call

FAULT = {"action": "crm.apply_discount", "on_call": 1, "mode": "timeout_before_commit"}


def _call(name: str) -> Call:
    return Call(name=name, arguments={}, ok=True)


def test_a_fault_the_door_applied_says_nothing():
    log = [{"name": "apply_discount", "outcome": "faulted"}]
    assert fault_notices([FAULT], log, [_call("apply_discount")]) == []


import pytest


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
