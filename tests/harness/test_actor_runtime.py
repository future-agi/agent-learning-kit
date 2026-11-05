"""One actor record at run time: the brief it plays from, the caller as an actor, and actors writing in a chat."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from fi.alk.harness.actors import (
    Actor,
    ActorList,
    actor_brief,
    brief_text,
    save_actors,
    with_caller_actor,
)
from fi.alk.harness.run import conversation as conv
from fi.simulate.simulation.call_actors import line_brief

_SCENARIO = {
    "instruction": "You want the order moved to Friday.\n\nYour details:\n- Order: 4471",
    "solution": [{"tool": "move", "arguments": {"order": "4471"}}],
    "sub_goals": ["moved"],
    "persona": {"name": "Sam", "languages": ["English"]},
}
_MANAGER = {
    "id": "manager",
    "name": "Mia",
    "role": "the store manager",
    "goal": "Approve only moves within a week",
    "constraints": {"never": ["approve refunds"]},
    "knowledge": [{"kind": "facts", "text": "Friday deliveries are full after noon."}],
    "permitted_actions": ["orders.approve"],
    "channel": "chat_message",
}


def test_an_actor_s_brief_is_its_view_and_never_what_only_the_evaluator_reads():
    entry = {"id": "manager", "instruction": "Ask why the move is needed."}
    items = actor_brief(_MANAGER, entry=entry, scenario=_SCENARIO)
    inputs = [one["input"] for one in items]
    assert inputs == [
        "actor.name",
        "actor.role",
        "actor.goal",
        "actor.constraints",
        "actor.knowledge",
        "scenario.instruction",
        "caller.situation",
    ]
    text = brief_text(items)
    # The caller's situation reaches anyone else only up to the caller's own details.
    assert "moved to Friday" in text and "4471" not in text
    assert "Friday deliveries are full after noon." in text
    for hidden in ("solution", "sub_goals", "moved\"", "move\""):
        assert hidden not in text


def test_the_line_an_actor_writes_is_played_from_its_brief():
    brief = brief_text(actor_brief(_MANAGER, entry={"id": "manager"}, scenario=_SCENARIO))
    written = line_brief(
        {**_MANAGER, "brief": brief}, situation="unused", said=[], conversation=[], language="English"
    )
    assert brief in written and "unused" not in written


def test_an_actor_never_sees_a_file_only_the_evaluator_reads():
    with pytest.raises(ValidationError, match="only the evaluator"):
        Actor.model_validate(
            {"id": "a", "name": "A", "knowledge": [{"kind": "files", "paths": ["data/sub_goals.json"]}]}
        )
    Actor.model_validate({"id": "a", "name": "A", "knowledge": [{"kind": "files", "paths": ["docs/policy.md"]}]})


def test_the_caller_is_an_actor_whose_record_shapes_the_person_under_each_scenario(tmp_path):
    assert with_caller_actor(dict(_SCENARIO), tmp_path) == _SCENARIO
    save_actors(
        tmp_path,
        ActorList.model_validate(
            {
                "actors": [
                    {
                        "id": "caller",
                        "name": "Sam",
                        "persona": {"name": "Sam", "personality": "brisk", "languages": ["Hindi"]},
                        "goal": "Keep the call short",
                        "knowledge": [{"kind": "facts", "text": "You paid by card."}],
                        "permitted_actions": [],
                    }
                ]
            }
        ),
    )
    played = with_caller_actor(dict(_SCENARIO), tmp_path)
    # The scenario's persona wins where both set a field; the record fills the rest.
    assert played["persona"] == {"name": "Sam", "personality": "brisk", "languages": ["English"]}
    assert played["instruction"].startswith(_SCENARIO["instruction"])
    assert "Keep the call short" in played["instruction"] and "You paid by card." in played["instruction"]
    # Anyone else is given the situation before the details, so the caller's own record stays the caller's.
    others = brief_text(actor_brief(_MANAGER, scenario=played))
    assert "Keep the call short" not in others
    # The caller already has its whole situation, so it is not given it again cut short.
    own = [one["input"] for one in actor_brief({"id": "caller", "name": "Sam"}, scenario=played)]
    assert "caller.situation" not in own


class _Agent:
    def __init__(self, replies):
        self.replies = list(replies)
        self.heard = []
        self.world = SimpleNamespace(calls=[])
        self.spent_usd = 0.0

    async def open(self):
        return None

    async def close(self):
        return None

    async def say(self, text):
        self.heard.append(text)
        return self.replies.pop(0) if self.replies else "ok"


class _Person:
    def __init__(self, lines):
        self.lines = list(lines)
        self.told = []
        self.spent_usd = 0.0
        self.simulator_tokens = (0, 0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def say(self, text):
        self.told.append(text)
        return SimpleNamespace(text=self.lines.pop(0) if self.lines else conv.DONE)


def _run(monkeypatch, cast, agent, person):
    monkeypatch.setattr(conv, "Stage", lambda *a, **k: person)
    monkeypatch.setattr(conv, "customer_prompt", lambda *a, **k: "")
    scenario = SimpleNamespace(instruction="Move my order.", max_turns=4)
    return asyncio.run(conv.converse(agent, scenario, SimpleNamespace(), actors=cast))


def _cast(trigger, monkeypatch, lines=("Why Friday?",)):
    cast = conv.ChatActors(
        [{**_MANAGER, "trigger": trigger, "brief": "You are Mia."}], seed="run", model=None
    )
    written = list(lines)

    async def write(brief):
        return written.pop(0) if written else ""

    monkeypatch.setattr(cast, "_write", write)
    return cast


def test_an_actor_writes_in_the_chat_when_its_trigger_fires_and_the_agent_reads_it_as_theirs(monkeypatch):
    cast = _cast({"kind": "on_keyword", "words": ["manager"]}, monkeypatch)
    agent = _Agent(["Hello, what can I do?", "I need my manager to approve that.", "Mia, it is for a delivery slot."])
    person = _Person(["Hi, I want to move my order.", "Please move it to Friday.", "Thanks."])
    transcript = _run(monkeypatch, cast, agent, person)
    # Not on the agent's first turn: only once the word is said.
    assert agent.heard[2] == "Mia: Why Friday?"
    messages = transcript.canonical_messages()
    actor_turns = [one for one in messages if one["role"] == "actor"]
    assert actor_turns == [{"role": "actor", "actor": "manager", "name": "Mia", "content": "Why Friday?"}]
    assert any("Mia wrote in the conversation: Why Friday?" in one for one in person.told)
    assert [one["role"] for one in json.loads(transcript.artifact())["messages"]].count("actor") == 1
    assert cast.summary() == [
        {"actor": "manager", "name": "Mia", "triggered": True, "delivered": 1, "dropped": []}
    ]


def test_an_actor_whose_word_never_comes_never_writes_and_is_recorded_not_triggered(monkeypatch):
    cast = _cast({"kind": "on_keyword", "words": ["refund"]}, monkeypatch)
    agent = _Agent(["Hello.", "Done, moved."])
    person = _Person(["Move my order to Friday.", conv.DONE])
    transcript = _run(monkeypatch, cast, agent, person)
    assert all(one["role"] != "actor" for one in transcript.canonical_messages())
    assert cast.summary()[0]["triggered"] is False and cast.summary()[0]["delivered"] == 0


def test_an_actor_that_cannot_reach_a_chat_is_kept_with_the_reason(monkeypatch):
    cast = conv.ChatActors(
        [{"id": "near", "name": "Rowan", "entered": False, "reason": "not_a_chat_channel"}], seed="s", model=None
    )
    assert cast.summary() == [
        {
            "actor": "near",
            "name": "Rowan",
            "triggered": False,
            "delivered": 0,
            "dropped": [],
            "entered": False,
            "reason": "not_a_chat_channel",
        }
    ]


def test_an_actor_s_knowledge_reads_the_environment_s_files_and_records_but_never_what_only_the_evaluator_reads(tmp_path):
    from fi.alk.harness.actors import read_knowledge

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "policy.md").write_text("Discounts above 15% need the regional manager.")
    (tmp_path / "sub_goals.json").write_text('{"secret": "the answer key"}')
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("not the environment's")
    rows = {"deals": [{"id": 7, "owner": "mia", "value": 900}, {"id": 8, "owner": "sam", "value": 50}]}
    read = read_knowledge(
        [
            {"kind": "facts", "text": "You manage the north store."},
            {"kind": "files", "paths": ["docs/policy.md", "sub_goals.json", "../outside.txt"]},
            {"kind": "records", "store": "crm", "collection": "deals", "where": {"owner": "mia"}},
        ],
        tmp_path,
        rows.get,
    )
    text = brief_text(actor_brief({"id": "manager", "name": "Mia", "knowledge": read}))
    assert "Discounts above 15% need the regional manager." in text
    assert '"value": 900' in text and '"value": 50' not in text
    assert "the answer key" not in text and "not the environment's" not in text
    assert "You manage the north store." in text


def test_records_an_actor_may_read_are_only_named_where_the_world_cannot_be_read(tmp_path):
    from fi.alk.harness.actors import read_knowledge

    entry = {"kind": "records", "store": "crm", "collection": "deals"}
    assert read_knowledge([entry], tmp_path) == [entry]
    text = brief_text(actor_brief({"id": "m", "name": "Mia", "knowledge": [entry]}))
    assert "You can read the deals records in crm" in text
