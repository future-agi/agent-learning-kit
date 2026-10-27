"""Sub-actors in a chat: a second participant the agent and the person both see."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from fi.alk.harness.contract import AgentContract
from fi.alk.harness.run import conversation
from fi.alk.harness.scenario import Scenario
from fi.alk.harness.sub_actor import SubActor, SubActorList, save_sub_actors

_FRIEND = SubActor(
    id="friend", name="Rowan", role="a friend in the chat", channel="thread_message"
)


class _Stage:
    """The person and every sub-actor, scripted: each session takes the next line of its own script."""

    scripts: dict[str, list[str]] = {}
    briefs: dict[str, list[str]] = {}

    def __init__(self, spec, name=""):
        self.name = name
        self.spec = spec
        self.spent_usd = 0.0
        self.simulator_tokens = (0, 0)
        _Stage.briefs.setdefault(name, []).append(spec.system_prompt)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def say(self, text):
        _Stage.briefs.setdefault(self.name + ":heard", []).append(text)
        line = _Stage.scripts[self.name].pop(0)
        if isinstance(line, float):
            await asyncio.sleep(line)
            line = "too late"
        if isinstance(line, Exception):
            raise line
        return SimpleNamespace(text=line, tokens_in=10, tokens_out=5, cost_usd=0.001)


class _Target:
    def __init__(self, named=True):
        self.world = SimpleNamespace(calls=[])
        self.spent_usd = 0.0
        self.said = []
        self.heard = []
        if named:
            self.interject = lambda name, text: self.heard.append((name, text))

    async def open(self):
        return None

    async def close(self):
        return None

    async def say(self, text):
        self.said.append(text)
        return f"reply {len(self.said)}"


def _run(tmp_path, monkeypatch, *, person, writes, trigger=None, named=True, on=True):
    if on:
        monkeypatch.setenv("HARNESS_SUB_ACTORS", "1")
    else:
        monkeypatch.delenv("HARNESS_SUB_ACTORS", raising=False)
    monkeypatch.setattr(conversation, "Stage", _Stage)
    _Stage.scripts = {"customer": list(person), "sub-actor": list(writes)}
    _Stage.briefs = {}
    save_sub_actors(tmp_path, SubActorList(sub_actors=[_FRIEND]))
    scenario = Scenario(
        name="s",
        instruction="You want one item.",
        max_turns=6,
        sub_actors=[
            {
                "id": "friend",
                "instruction": "You want a second item.",
                "trigger": trigger or {"kind": "mid_journey", "turn": 2},
            }
        ],
    )
    target = _Target(named)
    transcript = asyncio.run(
        conversation.converse(target, scenario, AgentContract(), world_root=tmp_path)
    )
    return transcript, target


def test_a_participant_writes_after_its_trigger_and_both_the_agent_and_the_person_see_it(
    tmp_path, monkeypatch
):
    transcript, target = _run(
        tmp_path,
        monkeypatch,
        person=["Hi, one item please", "Yes", "That's all [DONE]"],
        writes=["Add a second one for me"],
    )
    assert [(one.speaker, one.text) for one in transcript.exchanges] == [
        ("customer", "Hi, one item please"),
        ("agent", "reply 1"),
        ("customer", "Yes"),
        ("agent", "reply 2"),
        ("sub_actor", "Add a second one for me"),
        ("customer", "That's all"),
    ]
    assert target.heard == [("Rowan", "Add a second one for me")]
    assert target.said == ["Hi, one item please", "Yes"]
    assert (
        _Stage.briefs["customer:heard"][-1]
        == "reply 2\n\n[Rowan]: Add a second one for me"
    )
    assert (
        "Rowan (a friend in the chat) is in this chat with you"
        in _Stage.briefs["customer"][0]
    )
    brief = _Stage.briefs["sub-actor"][0]
    assert (
        "You want a second item." in brief
        and "You want one item." in brief
        and "The person: Hi, one item please\nThe agent: reply 1\nThe person: Yes\nThe agent: reply 2"
        in brief
    )
    assert "Rowan (not the customer): Add a second one for me" in transcript.spoken()
    body = json.loads(transcript.artifact())
    assert body["messages"][4] == {
        "role": "sub_actor",
        "name": "Rowan",
        "sub_actor": "friend",
        "content": "Add a second one for me",
        "tokens": 15,
        "at": transcript.exchanges[4].at,
    }
    # The message's tokens are in the call's simulator usage once; the person's own add nothing here.
    assert (transcript.simulator_input_tokens, transcript.simulator_output_tokens) == (
        10,
        5,
    )
    assert transcript.spent_usd == 0.001
    record = body["sub_actors"]
    assert record["summary"] == [
        {
            "sub_actor": "friend",
            "name": "Rowan",
            "triggered": True,
            "delivered": 1,
            "dropped": [],
        }
    ]
    assert [(one["id"], one["instruction"]) for one in record["resolved"]] == [
        ("friend", "You want a second item.")
    ]
    assert [event["kind"] for event in record["events"]] == [
        "triggered",
        "delivered",
        "finished",
    ]


def test_a_target_that_cannot_take_a_named_message_hears_it_before_the_person_s_next_one(
    tmp_path, monkeypatch
):
    _transcript, target = _run(
        tmp_path,
        monkeypatch,
        person=["Hi", "Yes", "Fine", "Bye [DONE]"],
        writes=["Me too"],
        named=False,
    )
    assert target.said == ["Hi", "Yes", "[Rowan]: Me too\n\nFine"]


def test_a_message_that_fails_is_recorded_with_the_reason_and_the_chat_goes_on(
    tmp_path, monkeypatch
):
    transcript, target = _run(
        tmp_path,
        monkeypatch,
        person=["Hi", "Yes", "Bye [DONE]"],
        writes=[RuntimeError("model down")],
    )
    assert (
        target.heard == []
        and [one.speaker for one in transcript.exchanges].count("sub_actor") == 0
    )
    assert transcript.sub_actors["summary"] == [
        {
            "sub_actor": "friend",
            "name": "Rowan",
            "triggered": True,
            "delivered": 0,
            "dropped": ["message failed: RuntimeError"],
        }
    ]


def test_a_participant_writes_at_most_its_limit_and_one_never_triggered_reads_so(
    tmp_path, monkeypatch
):
    transcript, _target = _run(
        tmp_path,
        monkeypatch,
        person=["Hi", "a", "b", "c", "d", "Bye [DONE]"],
        writes=["one", "two", "three", "four"],
        trigger={"kind": "always"},
    )
    assert [one.text for one in transcript.exchanges if one.speaker == "sub_actor"] == [
        "one",
        "two",
        "three",
    ]
    never, _ = _run(
        tmp_path,
        monkeypatch,
        person=["Hi", "Bye [DONE]"],
        writes=["unused"],
        trigger={"kind": "mid_journey", "turn": 5},
    )
    assert never.sub_actors["summary"] == [
        {
            "sub_actor": "friend",
            "name": "Rowan",
            "triggered": False,
            "delivered": 0,
            "dropped": [],
        }
    ]


def test_with_the_switch_off_nobody_else_enters_the_chat(tmp_path, monkeypatch):
    transcript, target = _run(
        tmp_path,
        monkeypatch,
        person=["Hi", "Yes", "Bye [DONE]"],
        writes=["unused"],
        on=False,
    )
    assert target.heard == [] and transcript.sub_actors == {}
    assert "is in this chat with you" not in _Stage.briefs["customer"][0]
    assert "sub_actors" not in json.loads(transcript.artifact())


def test_the_hosted_chat_names_the_participant_in_the_message_itself():
    from fi.alk.harness.chat_call_runner import _HostedChatTarget

    target = object.__new__(_HostedChatTarget)
    target._messages = []
    target.interject("Rowan Lee", "Me too")
    assert target._messages == [
        {"role": "user", "name": "Rowan_Lee", "content": "[Rowan Lee]: Me too"}
    ]


def test_an_empty_opening_never_hands_the_agent_the_note_about_the_others(
    tmp_path, monkeypatch
):
    _transcript, target = _run(
        tmp_path, monkeypatch, person=["", "", "", "That's all [DONE]"], writes=[]
    )
    # Every ask came back empty, so the run opens with a greeting; the note about the others in
    # the chat never reaches the agent.
    assert target.said[0] == "Hello."
    assert not any("Rowan" in said for said in target.said)


def test_a_message_not_ready_in_time_is_dropped_and_the_chat_goes_on(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(conversation, "HANDOFF_LINE_SECONDS", 0.05)
    transcript, target = _run(
        tmp_path,
        monkeypatch,
        person=["Hi, one item please", "Yes", "That's all [DONE]"],
        writes=[1.0],
    )
    assert [one.speaker for one in transcript.exchanges] == [
        "customer",
        "agent",
        "customer",
        "agent",
        "customer",
    ]
    assert target.heard == []
    summary = json.loads(transcript.artifact())["sub_actors"]["summary"]
    assert summary[0]["triggered"] and summary[0]["delivered"] == 0
    assert summary[0]["dropped"] == ["not ready in time"]
