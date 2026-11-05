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


def test_a_trial_s_state_changes_keep_attempted_refused_and_committed_apart_per_participant():
    from fi.alk.harness.call_runner import trial_state_changes

    log = [
        {"name": "deals.request_approval", "outcome": "committed"},
        {"actor_id": "coworker", "name": "deals.approve", "outcome": "refused"},
        {"actor_id": "manager", "name": "deals.approve", "outcome": "committed"},
        {"name": "deals.apply_discount", "outcome": "refused"},
        {"actor_id": "manager", "name": "deals.approve", "outcome": "duplicate"},
        {"name": "deals.apply_discount", "ok": True},
    ]
    listed = {"manager": Actor(id="manager", name="Mia"), "coworker": Actor(id="coworker", name="Sam")}
    changes = trial_state_changes(log, listed)
    assert [(c["who"], c["name"], c["attempted"], c["committed"], c["refused"], c["duplicate"]) for c in changes] == [
        ("agent", "the agent", 3, 2, 1, 0),
        ("coworker", "Sam", 1, 0, 1, 0),
        ("manager", "Mia", 2, 1, 0, 1),
    ]
    assert changes[0]["committed_actions"] == ["deals.request_approval", "deals.apply_discount"]


def test_a_worker_s_attempts_are_its_own_participant_with_each_refusal_and_why():
    from fi.alk.harness.call_runner import trial_state_changes

    log = [
        {"name": "orders.get", "outcome": "committed", "worker_id": "order_lookup"},
        {
            "name": "orders.update_status",
            "outcome": "refused",
            "worker_id": "order_lookup",
            "refusal_code": "not_permitted",
            "refusal": "orders.update_status is not permitted for order_lookup",
        },
        {"name": "refunds.issue", "outcome": "committed", "worker_id": "refund"},
        {"name": "send_reply", "outcome": "committed"},
    ]
    changes = trial_state_changes(log, {})
    assert [(c["who"], c["name"], c["attempted"], c["committed"], c["refused"]) for c in changes] == [
        ("agent", "the agent", 1, 1, 0),
        ("worker:order_lookup", "order_lookup", 2, 1, 1),
        ("worker:refund", "refund", 1, 1, 0),
    ]
    assert changes[1]["refused_actions"] == [
        {
            "action": "orders.update_status",
            "code": "not_permitted",
            "reason": "orders.update_status is not permitted for order_lookup",
        }
    ]


def _history_dispatcher(history, actors):
    from fi.alk.harness.actor_dispatch import ActorDispatcher

    sent = []

    def send(url, headers, arguments):
        sent.append((url, headers, arguments))
        return 200

    dispatcher = ActorDispatcher(
        actors,
        [],
        trial_id="trial-2",
        proxy_url="http://proxy/",
        token="t0ken",
        read_log=list,
        seed="trial-2",
        timeout=60.0,
        send=send,
        history=history,
    )
    return dispatcher, sent


def test_history_is_written_by_its_actors_in_order_before_the_run_with_keys_of_its_own():
    from fi.alk.harness.actors import HistoryEntry

    actors = {
        "priya": Actor(id="priya", name="Priya", permitted_actions=["slack.post"]),
        "omar": Actor(id="omar", name="Omar", permitted_actions=["slack.post"]),
    }
    history = [
        HistoryEntry(actor="priya", action="slack.post", arguments={"text": "Can we ship Friday?"}),
        HistoryEntry(actor="omar", action="slack.post", arguments={"text": "Only if QA signs off."}),
        HistoryEntry(actor="ghost", action="slack.post", arguments={}),
    ]
    for trial in range(2):
        dispatcher, sent = _history_dispatcher(history, actors)
        asyncio.run(dispatcher.write_history())
        # The same history on every trial, each entry under its own actor and a history key.
        assert [(h["X-ALK-Actor"], h["Idempotency-Key"], a["text"]) for _, h, a in sent] == [
            ("priya", "trial-2:priya:h1", "Can we ship Friday?"),
            ("omar", "trial-2:omar:h2", "Only if QA signs off."),
        ]
        assert dispatcher.fired[-1]["error"] == "not_listed"


def test_history_rows_are_the_trial_s_starting_point_never_its_state_changes():
    from fi.alk.harness.call_runner import trial_state_changes

    log = [
        {"actor_id": "priya", "name": "slack.post", "outcome": "committed", "idempotency_key": "t:priya:h1"},
        {"name": "slack.post", "outcome": "committed"},
    ]
    assert [c["who"] for c in trial_state_changes(log, {"priya": Actor(id="priya", name="Priya")})] == ["agent"]


def test_an_event_started_run_invokes_the_agent_once_with_no_caller(monkeypatch):
    from fi.alk.harness.actors import StartsWith

    agent = _Agent(["Posted the summary to #launch."])
    monkeypatch.setattr(conv, "Stage", lambda *a, **k: pytest.fail("no caller is simulated"))
    scenario = SimpleNamespace(
        instruction="", max_turns=4, starts_with=StartsWith(actor="notetaker", text="Meeting ended: launch sync")
    )
    transcript = asyncio.run(conv.converse(agent, scenario, SimpleNamespace()))
    assert agent.heard == ["Meeting ended: launch sync"]
    assert [one["role"] for one in transcript.canonical_messages()] == ["actor", "assistant"]
    assert transcript.ended == conv.FINISHED


def test_an_event_started_scenario_is_graded_even_when_the_agent_leaves_the_world_untouched(tmp_path):
    from fi.alk.harness.catalogue import Catalogue, SubGoal, save_catalogue
    from fi.alk.harness.folder import write_folder
    from fi.alk.harness.scenario import Scenario
    from fi.alk.harness.scenario_source import load_scenarios

    catalogue = Catalogue(sub_goals=[SubGoal(name="posted", what="posted the summary", judged="x")])
    save_catalogue(catalogue, tmp_path)
    scenario = Scenario(
        name="meeting-ended",
        scenario_key="meeting-ended",
        instruction="The launch sync ended.",
        sub_goals=["posted"],
        solution=[{"tool": "post", "arguments": {}}],
        starts_with={"actor": "notetaker", "text": "Meeting ended"},
    )
    write_folder(scenario, catalogue, tmp_path)
    (tmp_path / "contract.json").write_text('{"tools": [{"name": "post"}]}')
    [loaded] = load_scenarios(tmp_path)
    assert loaded.requires_tool_evidence is False


def test_faults_are_for_graders_only_and_a_world_with_them_gets_the_full_action_log(tmp_path):
    from fi.alk.harness.actors import EVALUATOR_ONLY
    from fi.alk.harness.bundle_author_v2 import _TOOL_TRACE_DDL, _action_log_seed

    assert "faults" in EVALUATOR_ONLY["scenario_fields"]
    folder = tmp_path / "scenarios" / "refund-timeout"
    folder.mkdir(parents=True)
    (folder / "scenario.json").write_text(
        json.dumps({"name": "refund-timeout", "faults": [{"action": "refunds.create", "mode": "timeout_after_commit"}]})
    )
    assert _action_log_seed(tmp_path).startswith(_TOOL_TRACE_DDL)
    brief = brief_text(actor_brief(_MANAGER, entry={"id": "manager"}, scenario={**_SCENARIO, "faults": [{"x": 1}]}))
    assert "timeout" not in brief and "faults" not in brief


def test_the_runner_writes_a_trial_s_faults_where_the_door_reads_them_and_clears_them(tmp_path):
    from fi.alk.harness.call_runner import _write_trial_state

    runtime = SimpleNamespace(metadata={"actor_proof_path": str(tmp_path / "actor-token")})
    _write_trial_state(runtime, [{"action": "refunds.create", "mode": "timeout_before_commit"}])
    assert json.loads((tmp_path / "trial.json").read_text())["faults"][0]["action"] == "refunds.create"
    _write_trial_state(runtime, [])
    assert json.loads((tmp_path / "trial.json").read_text()) == {"faults": []}
