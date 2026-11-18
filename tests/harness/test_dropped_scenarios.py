"""Scenarios a rebuild drops for a removed capability keep the reason and come back with it.

The fixture walks one agent through v5, which removes `cancel_booking`, and v7, which brings it
back. Proving is stubbed: these tests pin what is recorded, restored and reported, not the prover.
"""

import asyncio
import json
from pathlib import Path

import pytest

from fi.alk.harness import dropped, nudge
from fi.alk.harness.catalogue import Catalogue, SubGoal, load_catalogue, save_catalogue
from fi.alk.harness.contract import AgentContract, ToolSpec
from fi.alk.harness.prove import Proof
from fi.alk.harness.scenario import Scenario
from fi.alk.harness.scenario_tools import load_scenarios, write_scenarios

CANCELS = ("cancels-a-booking", "cancels-after-a-change", "cancels-the-only-booking")
# A negative control the lane ran and the checks failed: what a proved scenario carries.
_RAN = [{"name": "wrong", "why": "does it wrong", "failed": True, "held": [], "assumed": []}]
_CHECK = "def check(world, calls):\n    return None\n"


def _contract(
    root: Path, *, cancel: bool, cancel_args: tuple[str, ...] = ("booking_id",)
) -> None:
    tools = [
        ToolSpec(name="find_booking", args=["name"]),
        ToolSpec(name="book", args=["slot"]),
    ]
    if cancel:
        tools.append(ToolSpec(name="cancel_booking", args=list(cancel_args)))
    (root / "contract.json").write_text(
        AgentContract(agent="bookings", tools=tools).model_dump_json(), encoding="utf-8"
    )


def _catalogue(root: Path, *, cancel: bool) -> Catalogue:
    goals = [SubGoal(name="booked", what="a booking was made", check=_CHECK)]
    if cancel:
        goals.append(
            SubGoal(name="booking-cancelled", what="the booking is gone", check=_CHECK)
        )
    catalogue = Catalogue(sub_goals=goals)
    save_catalogue(catalogue, root)
    return catalogue


def _scenario(name: str, *, cancels: bool) -> Scenario:
    last = (
        {"tool": "cancel_booking", "arguments": {"booking_id": "b-1"}}
        if cancels
        else {"tool": "book", "arguments": {"slot": "monday-10"}}
    )
    return Scenario.model_validate(
        {
            "name": name,
            "use_case": "manage a booking",
            "instruction": f"{name}: do what the caller asks.",
            "tests": f"whether the agent handles {name}",
            "solution": [{"tool": "find_booking", "arguments": {"name": "Ana"}}, last],
            "sub_goals": ["booking-cancelled"] if cancels else ["booked"],
        }
    )


def _v4(root: Path) -> Path:
    """The suite as agent v4 had it: one booking scenario and three that cancel."""
    _contract(root, cancel=True)
    catalogue = _catalogue(root, cancel=True)
    suite = [_scenario("books-a-slot", cancels=False)] + [
        _scenario(name, cancels=True) for name in CANCELS
    ]
    write_scenarios(suite, root, catalogue)
    return root


def _environment(root: Path, *, cancel: bool, **contract) -> None:
    """What the environment rebuild for that agent version leaves behind."""
    _contract(root, cancel=cancel, **contract)
    _catalogue(root, cancel=cancel)


def _call(server, name: str, args: dict) -> dict:
    handler = next(one.handler for one in server.tools if one.name == name)
    return asyncio.run(handler(args))


def _suite_turn(root: Path, agent: str, suite: str):
    catalogue = load_catalogue(root)
    kept = load_scenarios(root)
    server = nudge.nudge_server(
        root,
        {
            "nudge_id": f"n-{agent}",
            "stage": "suite",
            "agent_label": agent,
            "base_labels": {"agent": agent, "environment": "v2", "suite": suite},
        },
        catalogue=catalogue,
        kept=kept,
    )
    return server, kept, catalogue


def _result(root: Path) -> dict:
    return json.loads((root / nudge.NUDGE_RESULT).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _proved(monkeypatch):
    monkeypatch.setattr(
        nudge, "prove", lambda *_args, **_kwargs: Proof(ready=True, solvable=True, vacuous=False, controls=_RAN)
    )


def _drop_cancels(root: Path) -> Path:
    """v5 removes `cancel_booking`; its suite turn drops the three cancel scenarios."""
    _environment(root, cancel=False)
    server, kept, catalogue = _suite_turn(root, "v5", "v3")
    kept[:] = [one for one in kept if one.name not in CANCELS]
    write_scenarios(kept, root, catalogue)
    said = _call(
        server, "report_update", {"changed": True, "notes": "dropped the cancels"}
    )
    assert not said.get("is_error"), said
    return root


# Done when 1: v5 drops the three cancel scenarios and each records why.


def test_v5_drops_the_cancel_scenarios_and_records_capability_agent_and_reason(
    tmp_path,
):
    root = _drop_cancels(_v4(tmp_path))

    records = dropped.load(root)
    assert sorted(records) == sorted(CANCELS)
    for key in CANCELS:
        record = records[key]
        assert record["capabilities"] == {
            "tools": ["cancel_booking", "find_booking"],
            "sub_goals": ["booking-cancelled"],
        }
        assert record["missing"] == {
            "tools": ["cancel_booking"],
            "sub_goals": ["booking-cancelled"],
        }
        assert record["agent_version"] == "v5"
        assert record["reason"].startswith("agent v5 no longer has `cancel_booking`")
        assert record["last_suite_version"] == "v3"
        assert record["content"]["scenario_key"] == key
    left_out = {one["key"]: one["why"] for one in _result(root)["left_out"]}
    assert set(left_out) == set(CANCELS)
    assert all(
        why.startswith("agent v5 no longer has `cancel_booking`")
        for why in left_out.values()
    )
    assert _result(root)["checks"]["passed"] is True


def test_a_scenario_dropped_for_another_reason_is_not_recorded(tmp_path):
    root = _v4(tmp_path)
    server, kept, catalogue = _suite_turn(root, "v5", "v3")
    kept[:] = [one for one in kept if one.name != "books-a-slot"]
    write_scenarios(kept, root, catalogue)

    _call(server, "report_update", {"changed": True, "notes": "dropped a duplicate"})

    assert dropped.load(root) == {}
    assert not (root / dropped.DROPPED).exists()


def test_a_rebuild_without_the_capability_back_restores_nothing(tmp_path):
    root = _drop_cancels(_v4(tmp_path))
    server, kept, _ = _suite_turn(root, "v6", "v4")

    _call(server, "report_update", {"changed": False, "notes": "nothing to change"})

    assert {one.name for one in load_scenarios(root)} == {"books-a-slot"}
    assert sorted(dropped.load(root)) == sorted(CANCELS)
    assert _result(root)["restored"] == []


def test_drops_sit_beside_the_scenarios_and_leave_no_file_once_none_remain(tmp_path):
    """The platform hashes a suite with no dropped.json exactly as before drops existed."""
    from fi.alk.harness.authoring_runtime_validation import _artifact_digest

    root = _drop_cancels(_v4(tmp_path))
    with_drops = _artifact_digest(root / "scenarios")
    records = dropped.load(root)
    dropped.save(root, {})

    assert not (root / dropped.DROPPED).exists()
    assert _artifact_digest(root / "scenarios") == with_drops
    dropped.save(root, records)
    assert (root / dropped.DROPPED).exists()


# Done when 2: v7 brings `cancel_booking` back and the rebuild restores the three.


def test_v7_restores_the_cancel_scenarios_with_their_keys_and_last_content(tmp_path):
    root = _drop_cancels(_v4(tmp_path))
    last = {key: dropped.load(root)[key]["content"] for key in CANCELS}
    _environment(root, cancel=True)

    server, kept, _ = _suite_turn(root, "v7", "v4")

    on_disk = {one.scenario_key: one for one in load_scenarios(root)}
    assert set(CANCELS) <= set(on_disk)
    assert set(CANCELS) <= {one.scenario_key for one in kept}
    for key in CANCELS:
        assert on_disk[key].model_dump(mode="json") == last[key]

    said = _call(
        server, "report_update", {"changed": True, "notes": "restored the cancels"}
    )

    assert not said.get("is_error"), said
    result = _result(root)
    assert [one["key"] for one in result["restored"]] == sorted(CANCELS)
    assert result["restore_failed"] == [] and result["left_out"] == []
    assert dropped.load(root) == {}


def test_restored_scenarios_are_proved_like_any_other(tmp_path, monkeypatch):
    root = _drop_cancels(_v4(tmp_path))
    _environment(root, cancel=True)
    proved: list[str] = []

    def prove(scenario, *_args, **_kwargs):
        proved.append(scenario.scenario_key)
        return Proof(ready=True, solvable=True, vacuous=False, controls=_RAN)

    monkeypatch.setattr(nudge, "prove", prove)
    server, _, _ = _suite_turn(root, "v7", "v4")
    _call(server, "report_update", {"changed": True, "notes": "restored"})

    assert set(CANCELS) <= set(proved)


def test_a_scenario_is_restored_only_once_every_capability_it_needed_is_back(tmp_path):
    root = _drop_cancels(_v4(tmp_path))
    # The tool is back, the sub-goal is not.
    _contract(root, cancel=True)
    _catalogue(root, cancel=False)

    server, _, _ = _suite_turn(root, "v7", "v4")
    _call(server, "report_update", {"changed": False, "notes": "half back"})

    assert {one.name for one in load_scenarios(root)} == {"books-a-slot"}
    assert sorted(dropped.load(root)) == sorted(CANCELS)


def test_a_settled_restore_is_still_listed_when_report_update_runs_again(tmp_path):
    root = _drop_cancels(_v4(tmp_path))
    _environment(root, cancel=True)
    server, _, _ = _suite_turn(root, "v7", "v4")

    _call(server, "report_update", {"changed": True, "notes": "first"})
    _call(server, "report_update", {"changed": True, "notes": "again"})

    assert [one["key"] for one in _result(root)["restored"]] == sorted(CANCELS)


def test_the_message_names_the_restored_scenarios_and_a_changed_argument(tmp_path):
    root = _drop_cancels(_v4(tmp_path))
    _environment(root, cancel=True, cancel_args=("reference",))
    contract = AgentContract.model_validate_json((root / "contract.json").read_text())

    said = dropped.restore_note(root, contract, load_catalogue(root).names())

    for key in CANCELS:
        assert key in said
    assert "cancel_booking no longer takes booking_id" in said


# Requirement 4: a restore that cannot be proved again stays dropped and is reported.


def test_a_restore_that_cannot_be_proved_stays_dropped_and_is_listed_with_why(
    tmp_path, monkeypatch
):
    root = _drop_cancels(_v4(tmp_path))
    _environment(root, cancel=True)

    def prove(scenario, *_args, **_kwargs):
        if scenario.scenario_key == "cancels-the-only-booking":
            return Proof(ready=True, solvable=False, vacuous=False)
        return Proof(ready=True, solvable=True, vacuous=False, controls=_RAN)

    monkeypatch.setattr(nudge, "prove", prove)
    server, kept, _ = _suite_turn(root, "v7", "v4")
    said = _call(server, "report_update", {"changed": True, "notes": "restored two"})

    assert not said.get("is_error"), said
    result = _result(root)
    assert result["checks"]["passed"] is True
    assert [one["key"] for one in result["restored"]] == [
        "cancels-a-booking",
        "cancels-after-a-change",
    ]
    assert [one["key"] for one in result["restore_failed"]] == [
        "cancels-the-only-booking"
    ]
    assert result["restore_failed"][0]["why"].startswith("could not be proved again")
    assert "cancels-the-only-booking" not in {
        one.scenario_key for one in load_scenarios(root)
    }
    assert "cancels-the-only-booking" not in {one.scenario_key for one in kept}
    record = dropped.load(root)["cancels-the-only-booking"]
    assert record["restore_failed"].startswith("could not be proved again")
    assert record["reason"].startswith("agent v5 no longer has")


def test_a_restored_scenario_dropped_again_in_the_turn_is_a_failed_restore(tmp_path):
    root = _drop_cancels(_v4(tmp_path))
    _environment(root, cancel=True)
    server, kept, catalogue = _suite_turn(root, "v7", "v4")
    kept[:] = [one for one in kept if one.scenario_key != "cancels-a-booking"]
    write_scenarios(kept, root, catalogue)

    _call(server, "report_update", {"changed": True, "notes": "dropped one again"})

    failed = _result(root)["restore_failed"]
    assert failed == [
        {"key": "cancels-a-booking", "why": "dropped again in this rebuild"}
    ]
    assert "cancels-a-booking" in dropped.load(root)


# Requirement 2: the key stays reserved; only the restore brings it back.


def test_a_rebuild_may_not_write_a_new_scenario_under_a_dropped_key(tmp_path):
    root = _drop_cancels(_v4(tmp_path))

    said = dropped.submit_refusal(
        root,
        {"name": "cancels-a-booking"},
        {one.scenario_key for one in load_scenarios(root)},
    )

    assert said.startswith("Not kept: cancels-a-booking was dropped")


def test_editing_a_restored_scenario_is_not_refused(tmp_path):
    root = _drop_cancels(_v4(tmp_path))
    _environment(root, cancel=True)
    _suite_turn(root, "v7", "v4")

    said = dropped.submit_refusal(
        root,
        {"name": "cancels-a-booking"},
        {one.scenario_key for one in load_scenarios(root)},
    )

    assert said == ""


# A restored scenario comes back only once proved again, in this rebuild.


def _restore_with(tmp_path, monkeypatch, proof_for):
    root = _drop_cancels(_v4(tmp_path))
    _environment(root, cancel=True)
    monkeypatch.setattr(nudge, "prove", lambda scenario, *_args, **_kwargs: proof_for(scenario.scenario_key))
    server, kept, _ = _suite_turn(root, "v7", "v4")
    said = _call(server, "report_update", {"changed": True, "notes": "restored"})
    assert not said.get("is_error"), said
    return root, kept, _result(root)


def test_a_restored_scenario_without_a_negative_control_is_not_restored(tmp_path, monkeypatch):
    def proof_for(key):
        controls = [] if key == "cancels-a-booking" else _RAN
        return Proof(ready=True, solvable=True, vacuous=False, controls=controls)

    root, kept, result = _restore_with(tmp_path, monkeypatch, proof_for)

    assert [one["key"] for one in result["restored"]] == ["cancels-after-a-change", "cancels-the-only-booking"]
    assert all(one["proof"] == "proved" for one in result["restored"])
    [failed] = result["restore_failed"]
    assert failed["key"] == "cancels-a-booking" and "no negative control" in failed["why"]
    assert "cancels-a-booking" not in {one.scenario_key for one in load_scenarios(root)}
    assert "cancels-a-booking" not in {one.scenario_key for one in kept}
    assert "no negative control" in dropped.load(root)["cancels-a-booking"]["restore_failed"]


# The cancel tool runs inside the agent, so its assumed step leaves the world the state check reads unchanged (the
# check is excused, so the proof still reads solvable) and the control could not run either.
_NOT_RUN = [{"name": "wrong", "why": "cancels twice", "failed": None, "held": [], "assumed": ["cancel_booking"]}]


@pytest.mark.parametrize(
    ("solvable", "controls"), [(True, _RAN), (True, _NOT_RUN)], ids=["call-check", "agent-bound-state-check"]
)
def test_a_restored_scenario_the_lane_could_not_run_is_restored_as_kept_as_written(
    tmp_path, monkeypatch, solvable, controls
):
    def proof_for(key):
        return Proof(ready=True, solvable=solvable, vacuous=False, assumed=["cancel_booking"], controls=controls)

    _root, _kept, result = _restore_with(tmp_path, monkeypatch, proof_for)

    assert [one["key"] for one in result["restored"]] == sorted(CANCELS)
    assert {one["proof"] for one in result["restored"]} == {"kept_as_written"}
    assert all("kept as written" in one["why"] for one in result["restored"])


def test_a_proof_from_before_the_restore_does_not_bring_it_back(tmp_path):
    record = {"name": "cancels", "restored_at": "2026-11-18T10:00:00Z"}
    earlier = {"proved": True, "proved_at": "2026-11-18T09:59:59Z"}
    later = {**earlier, "proved_at": "2026-11-18T10:00:01Z"}

    done, failed, _ = dropped.finish_restore(tmp_path, {"k": record}, [], {"k"}, {"k": earlier})
    assert done == [] and failed == [{"key": "k", "why": "not proved again in this rebuild"}]
    done, failed, _ = dropped.finish_restore(tmp_path, {"k": record}, [], {"k"}, {})
    assert done == [] and failed[0]["why"] == "not proved again in this rebuild"
    done, failed, _ = dropped.finish_restore(tmp_path, {"k": record}, [], {"k"}, {"k": later})
    assert failed == [] and done == [{"key": "k", "name": "cancels", "proof": "proved"}]


def test_the_rebuild_message_asks_for_a_control_for_each_restored_scenario_without_one(tmp_path):
    root = _drop_cancels(_v4(tmp_path))
    _environment(root, cancel=True)
    contract = AgentContract.model_validate_json((root / "contract.json").read_text())

    said = dropped.restore_note(root, contract, load_catalogue(root).names())

    assert "no negative control" in said
    for key in CANCELS:
        assert key in said.split("no negative control", 1)[1]


def test_a_restore_recorded_before_restores_were_stamped_still_needs_a_proof(tmp_path):
    # A drop record written by an older harness: no `restored_at`, so any proof of this rebuild counts.
    record = {"name": "cancels"}

    done, failed, _ = dropped.finish_restore(tmp_path, {"k": record}, [], {"k"}, {"k": {"proved": True}})
    assert failed == [] and [one["key"] for one in done] == ["k"]
    done, failed, _ = dropped.finish_restore(tmp_path, {"k": record}, [], {"k"}, {})
    assert done == [] and failed == [{"key": "k", "why": "not proved again in this rebuild"}]
