"""The actor record: everyone the agent deals with, as data, checked structurally and never by what it says."""

from __future__ import annotations

import json

import jsonschema
import pytest
from pydantic import ValidationError

from fi.alk.harness.actors import (
    ACTOR_RULES_FILE,
    ActorList,
    actor_rules,
    load_actors,
    save_actors,
    write_actor_rules,
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


def test_a_sealed_bundle_carries_the_rules_its_release_checks_actors_by(tmp_path):
    write_actor_rules(tmp_path)
    declared = json.loads((tmp_path / ACTOR_RULES_FILE).read_text())
    assert declared == json.loads(json.dumps(actor_rules()))
    assert "solution" in declared["evaluator_only"]["scenario_fields"]
    assert declared["reserved_ids"] == ["agent"]
    actor = declared["records"]["actors.json"]
    sub_actor = declared["records"]["sub_actors.json"]
    jsonschema.validate(_MANAGER, actor)
    partner = {"id": "partner", "name": "Sam", "channel": "audio_mix"}
    jsonschema.validate({**partner, "loudness_ratio": 0.5}, sub_actor)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**partner, "loudness_ratio": 2}, sub_actor)
    assert "goal" not in sub_actor["properties"]
    # How the world names an action and which call channels exist travel with the rules too.
    assert declared["world_actions"]["route"] == "endpoint"
    assert set(declared["channels"]) == {"audio_mix", "audio_handoff", "ivr"}
    # A background voice left unset draws from this range; a sub-actor never shares the caller's name.
    assert declared["loudness"] == {"channels": ["audio_mix"], "automatic": [0.333, 0.667]}
    assert declared["distinct_from_caller"] == ["name"]
    # A rule a person can break is described in plain words for the message that refuses it.
    assert (
        actor["properties"]["id"]["description"]
        == "1 to 64 lowercase letters, digits or underscores"
    )
