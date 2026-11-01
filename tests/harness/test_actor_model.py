"""The actor record: everyone the agent deals with, as data, checked structurally and never by what it says."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from fi.alk.harness.actors import (
    EVALUATOR_ONLY,
    EVALUATOR_ONLY_FILE,
    ActorList,
    actor_problems,
    actor_view,
    load_actors,
    save_actors,
    write_evaluator_only,
)

_MANAGER = {
    "id": "manager",
    "name": "Priya Raman",
    "role": "sales manager who approves discounts above 10%",
    "persona": {"languages": ["English"], "personality": "brisk"},
    "goal": "Approve only discounts she can justify",
    "constraints": {"may_agree": ["Discounts up to 25%"], "private": ["The quarter's discount budget"]},
    "behaviour": {"style": "brisk", "response_timing": {"kind": "delayed", "seconds": 5}},
    "knowledge": [{"kind": "records", "store": "crm", "collection": "deals"}],
    "permitted_actions": ["crm.approve_discount", "crm.reject_discount"],
    "activation": {"trigger": {"kind": "on_tool", "tool": "crm.request_approval"}, "fallback": "decline"},
    "kind": "a role nobody has named yet",
    "play": "human",
}


def test_a_full_record_round_trips_and_open_values_are_kept(tmp_path):
    listing = ActorList.model_validate({"actors": [_MANAGER]})
    save_actors(tmp_path, listing)
    again = load_actors(tmp_path).by_id()["manager"]

    assert again.constraints.private == ["The quarter's discount budget"]
    assert again.behaviour.response_timing.seconds == 5
    assert again.activation.trigger.tool == "crm.request_approval" and again.activation.fallback == "decline"
    assert (again.kind, again.play) == ("a role nobody has named yet", "human")


@pytest.mark.parametrize(
    "change, message",
    [
        ({"parent": "nobody"}, "brought in by an actor that is not listed"),
        ({"activation": {"fallback": "nobody"}}, "falls back to an actor that is not listed"),
        ({"constraints": {"secret": ["x"]}}, "Extra inputs"),
        ({"behaviour": {"response_timing": {"kind": "later"}}}, "kind"),
        ({"loudness_ratio": 1.5}, "loudness_ratio"),
    ],
)
def test_a_structural_mistake_is_refused_with_what_is_wrong(change, message):
    with pytest.raises(ValidationError, match=message):
        ActorList.model_validate({"actors": [{**_MANAGER, **change}]})


def test_actors_bringing_each_other_in_are_refused():
    first = {"id": "a", "name": "A", "parent": "b"}
    second = {"id": "b", "name": "B", "parent": "a"}
    with pytest.raises(ValidationError, match="cycle"):
        ActorList.model_validate({"actors": [first, second]})
    assert ActorList.model_validate({"actors": [{"id": "a", "name": "A"}, {"id": "b", "name": "B", "parent": "a"}]})


def test_problems_name_what_the_environment_does_not_publish():
    coworker = {"id": "coworker", "name": "Sam", "activation": {"fallback": "manager"}}
    manager = {**_MANAGER, "activation": {"trigger": {"kind": "on_tool", "tool": "crm.request_approval"}, "fallback": "coworker"}}
    listing = ActorList.model_validate({"actors": [manager, coworker]})
    published = {"surfaces": {"world.records"}, "actions": {"crm.approve_discount", "crm.request_approval"}}

    problems = actor_problems(listing, **published, collections={"crm.deals"})

    assert problems == [
        "manager may take 'crm.reject_discount', which is not a world action; name one from the catalogue.",
        "manager's fallbacks come back to manager; end the chain with silent or decline.",
        "coworker's fallbacks come back to coworker; end the chain with silent or decline.",
    ]
    assert actor_problems(
        ActorList.model_validate({"actors": [{**_MANAGER, "surface": "chat.thread"}]}),
        surfaces={"world.records"},
        actions={"crm.approve_discount", "crm.reject_discount"},
    ) == [
        "manager reaches the agent through 'chat.thread', which this agent does not have; use one of world.records.",
        "manager reads 'crm.deals', which the world does not hold.",
        "manager comes in on 'crm.request_approval', which is not a world action.",
    ]


def _known_to_others(text):
    return text.split("Your details:")[0].strip()


_SCENARIO = {
    "instruction": "You want a 20% discount on the Acme deal. Your details: account PIN 4417.",
    "solution": [{"tool": "request_approval", "arguments": {"deal": "CANARY-SOLUTION-91"}}],
    "alternatives": [{"solution": [{"tool": "CANARY-ALT-77"}]}],
    "sub_goals": ["CANARY-SUBGOAL-55"],
    "tests": "def check(world, calls): return 'CANARY-TESTS-33'",
    # Another actor's entry first, so an actor reading the wrong entry shows.
    "actors": [
        {"id": "coworker", "instruction": "CANARY-OTHER-INSTRUCTION-12"},
        {"id": "manager", "instruction": "Approve it if the deal is above 50k.", "knowledge": [{"kind": "facts", "text": "Acme is a key account"}]},
    ],
}


def test_an_actor_sees_its_own_part_and_never_the_grader_s_or_another_actor_s():
    coworker = {"id": "coworker", "name": "Sam", "goal": "CANARY-OTHER-GOAL-21", "prompt": "CANARY-OTHER-PROMPT-8"}
    listing = ActorList.model_validate({"actors": [_MANAGER, coworker]})

    view = actor_view(listing, "manager", scenario=_SCENARIO, known_to_others=_known_to_others)

    seen = {one["input"]: one["value"] for one in view["sees"]}
    assert seen["actor.goal"] == "Approve only discounts she can justify"
    assert seen["actor.constraints"]["private"] == ["The quarter's discount budget"]
    assert seen["scenario.instruction"] == "Approve it if the deal is above 50k."
    assert seen["scenario.knowledge"] == [{"kind": "facts", "text": "Acme is a key account"}]
    # Someone with the caller knows the situation, never the caller's own details.
    assert seen["caller.situation"] == "You want a 20% discount on the Acme deal."
    assert "CANARY" not in json.dumps(view["sees"]) and "4417" not in json.dumps(view["sees"])
    assert {"scenario.solution", "scenario.sub_goals", "scenario.tests", "file.sub_goals.json"} <= set(view["never_sees"])
    assert {"coworker.goal", "coworker.prompt", "coworker.instruction"} <= set(view["never_sees"])
    assert not any(name.startswith("manager.") for name in view["never_sees"])


def test_without_a_scenario_an_actor_sees_only_its_record_and_the_caller_never_gets_the_others_cut():
    caller = {"id": "caller", "name": "Caller", "side": "caller", "surface": "call.person"}
    listing = ActorList.model_validate({"actors": [_MANAGER, caller]})

    alone = actor_view(listing, "manager")
    as_caller = actor_view(listing, "caller", scenario=_SCENARIO, known_to_others=_known_to_others)

    assert {one["source"] for one in alone["sees"]} == {"actor"}
    assert "caller.situation" not in {one["input"] for one in as_caller["sees"]}


def test_a_sealed_bundle_with_actors_declares_what_only_the_grader_reads(tmp_path):
    write_evaluator_only(tmp_path)
    declared = json.loads((tmp_path / EVALUATOR_ONLY_FILE).read_text())
    assert declared == EVALUATOR_ONLY and "solution" in declared["scenario_fields"]
