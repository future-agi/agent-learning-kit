"""Grading with sub-actors: results that say not applicable or inconclusive, and why."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fi.alk.harness.hosted_scheduler import (
    SubGoalResult,
    _skipped_receipt,
    _sub_actor_absent,
)


@pytest.mark.parametrize(
    "held, reason, verdict",
    [
        (True, None, "pass"),
        (False, "the record was not changed", "fail"),
        (True, "Not applicable: the situation never arose.", "not_applicable"),
        (None, "Inconclusive: the recording could not be played.", "inconclusive"),
        (None, None, None),
    ],
)
def test_each_sub_goal_result_says_which_of_the_four_it_is(held, reason, verdict):
    assert SubGoalResult("goal", held, reason, True).verdict == verdict


def _goal(sub_actor=""):
    return SimpleNamespace(
        name="noticed",
        judged="did the agent check with the customer",
        sub_actor=sub_actor,
    )


def test_a_sub_goal_about_a_sub_actor_is_settled_by_whether_it_took_part():
    never = [
        {
            "sub_actor": "friend",
            "name": "Rowan",
            "triggered": False,
            "delivered": 0,
            "dropped": [],
        }
    ]
    dropped = [
        {
            "sub_actor": "friend",
            "name": "Rowan",
            "triggered": True,
            "delivered": 0,
            "dropped": ["not ready in time"],
        }
    ]
    heard = [
        {
            "sub_actor": "friend",
            "name": "Rowan",
            "triggered": True,
            "delivered": 2,
            "dropped": [],
        }
    ]

    absent = _sub_actor_absent(_goal("friend"), never)
    assert (absent.held, absent.verdict, absent.reason) == (
        True,
        "not_applicable",
        "Not applicable: Rowan's trigger never fired in this call.",
    )
    silent = _sub_actor_absent(_goal("friend"), dropped)
    assert (silent.held, silent.verdict, silent.reason) == (
        None,
        "inconclusive",
        "Inconclusive: Rowan was triggered but did not take part: not ready in time.",
    )
    assert _sub_actor_absent(_goal("friend"), []).verdict == "not_applicable"
    assert _sub_actor_absent(_goal("friend"), heard) is None
    assert _sub_actor_absent(_goal(), never) is None


def test_a_scenario_with_sub_actors_is_skipped_with_the_reason_while_they_are_off(
    monkeypatch,
):
    with_one = SimpleNamespace(scenario_key="s", scenario_id="", sub_actors=("friend",))
    alone = SimpleNamespace(scenario_key="t", scenario_id="", sub_actors=())
    monkeypatch.delenv("HARNESS_SUB_ACTORS", raising=False)
    assert _skipped_receipt(with_one).reason == "sub-actors are switched off"
    assert _skipped_receipt(alone).reason is None
    monkeypatch.setenv("HARNESS_SUB_ACTORS", "1")
    assert _skipped_receipt(with_one).reason is None


def _receipt(**extra):
    from fi.alk.harness.outbound import build_result_receipt

    goal = {"name": "noticed", "held": True, "reason": "ok", "judged": True}
    return build_result_receipt(
        job_id="00000000-0000-4000-8000-000000000001",
        attempt_id="00000000-0000-4000-8000-000000000002",
        attempt_number=1,
        scenario_key="s",
        scenario_id="00000000-0000-4000-8000-000000000003",
        scenario_attempt=1,
        world_index=0,
        status=extra.pop("status", "passed"),
        sub_goals=[{**goal, **extra.pop("goal", {})}],
        evaluations=[],
        call=None,
        failure=None,
        **extra,
    )


def test_a_receipt_carries_the_verdict_and_reason_only_when_sent_and_its_digest_holds():
    from fi.alk.harness.outbound import whole_object_digest

    before = _receipt()
    assert "verdict" not in before["sub_goals"][0] and "reason" not in before
    after = _receipt(
        goal={"verdict": "not_applicable"}, reason="sub-actors are switched off"
    )
    assert (
        after["sub_goals"][0]["verdict"] == "not_applicable"
        and after["reason"] == "sub-actors are switched off"
    )
    for wire in (before, after):
        assert (
            whole_object_digest(
                {key: value for key, value in wire.items() if key != "digest"}
            )
            == wire["digest"]
        )


def test_the_judge_reads_a_sub_actor_s_turn_as_someone_other_than_the_customer():
    from fi.alk.harness.judge import _transcript, describe_call

    messages = [
        {"role": "user", "content": "One item please"},
        {"role": "sub_actor", "name": "Rowan", "content": "Make it two"},
        {"role": "assistant", "content": "One item, done."},
    ]
    assert "Rowan (not the customer): Make it two" in _transcript(messages)
    said = describe_call("", SimpleNamespace(), [], None, messages)
    assert "Also heard during the call: Rowan. They are not the customer" in said
    assert "Also heard" not in describe_call(
        "", SimpleNamespace(), [], None, messages[:1]
    )


def test_a_sub_goal_s_sub_actor_and_a_scenario_s_sub_actors_reach_the_run(tmp_path):
    from fi.alk.harness.catalogue import Catalogue, SubGoal, save_catalogue
    from fi.alk.harness.folder import write_folder
    from fi.alk.harness.scenario import Scenario
    from fi.alk.harness.scenario_source import load_scenarios

    catalogue = Catalogue(
        sub_goals=[
            SubGoal(
                name="noticed",
                what="checked with the customer",
                judged="x",
                sub_actor="friend",
            )
        ]
    )
    save_catalogue(catalogue, tmp_path)
    scenario = Scenario(
        name="with-a-friend",
        scenario_key="with-a-friend",
        instruction="You want one item.",
        sub_goals=["noticed"],
        sub_actors=[{"id": "friend", "instruction": "You want a second item."}],
    )
    write_folder(scenario, catalogue, tmp_path)
    [loaded] = load_scenarios(tmp_path)
    assert loaded.sub_actors == ("friend",)
    assert [(goal.name, goal.sub_actor) for goal in loaded.sub_goals] == [
        ("noticed", "friend")
    ]


def test_an_inconclusive_call_goes_out_with_its_reason():
    wire = _receipt(
        status="inconclusive",
        reason="Rowan was triggered but did not take part: voice failed.",
    )
    assert wire["status"] == "inconclusive"
    assert wire["reason"] == "Rowan was triggered but did not take part: voice failed."
