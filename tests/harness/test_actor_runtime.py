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


def _cast(trigger, monkeypatch, lines=("Why Friday?",), limits=None):
    cast = conv.ChatActors(
        [{**_MANAGER, "trigger": trigger, "brief": "You are Mia."}], seed="run", model=None, limits=limits
    )
    written = list(lines)

    async def write(actor, brief):
        # Plain text is a line; nothing is a wait.
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


def test_an_actor_whose_model_fails_says_nothing_and_the_conversation_goes_on(monkeypatch):
    from fi.alk.harness.actor_model import ActorModelFailed

    cast = _cast({"kind": "always"}, monkeypatch)

    async def failing(actor, brief):
        raise ActorModelFailed("manager's model gemini-3.8-flash did not answer: api_error: 403")

    monkeypatch.setattr(cast, "_write", failing)
    agent = _Agent(["Hello, what can I do?", "Moved to Friday."])
    person = _Person(["Hi, I want to move my order.", conv.DONE])
    transcript = _run(monkeypatch, cast, agent, person)
    assert [one for one in transcript.canonical_messages() if one["role"] == "actor"] == []
    assert not any("403" in one for one in agent.heard)
    assert cast.summary() == [
        {
            "actor": "manager",
            "name": "Mia",
            "triggered": True,
            "delivered": 0,
            "dropped": ["its model did not answer"],
        }
    ]


def test_an_actor_the_agent_answers_goes_back_and_forth_with_it_until_its_live_turns_run_out(monkeypatch):
    # One person's turn: the actor writes, the agent answers, the actor answers that, up to its own live turns.
    lines = ["20% is too high.", "Why not 14%?", "16% then, final.", "Fine, approved.", "One more thing."]
    cast = _cast({"kind": "always"}, monkeypatch, lines=lines, limits={"live_turns_per_actor": 3})
    agent = _Agent(["Hello, which deal?", "Submitted 20%.", "Margins allow 16%.", "I can do 16%.", "Noted.", "x"])
    person = _Person(["Deal 9, 20% please.", conv.DONE])
    transcript = _run(monkeypatch, cast, agent, person)
    actor_lines = [one["content"] for one in transcript.canonical_messages() if one["role"] == "actor"]
    # The first line, then three answers to the agent, and no more: the fifth is never asked for.
    assert actor_lines == lines[:4]
    assert cast.follow_ups == {"manager": 3}
    assert [one for one in agent.heard if one.startswith("Mia: ")] == [f"Mia: {line}" for line in lines[:4]]


def test_the_person_takes_the_turn_after_the_actor_and_hears_every_exchange(monkeypatch):
    lines = ["20% is too high.", "16% then.", ""]
    cast = _cast({"kind": "always"}, monkeypatch, lines=lines)
    agent = _Agent(["Hello.", "Submitted 20%.", "Margins allow 16%.", "Done at 16%.", "Bye."])
    person = _Person(["Deal 9, 20% please.", "Thanks.", conv.DONE])
    transcript = _run(monkeypatch, cast, agent, person)
    speakers = [one.speaker for one in transcript.exchanges]
    # The actor stopped (a wait), so the person spoke next, not another actor round.
    assert speakers[:7] == ["customer", "agent", "actor", "agent", "actor", "agent", "customer"]
    told = person.told[1]
    assert "Mia wrote in the conversation: 20% is too high." in told and "Mia wrote in the conversation: 16% then." in told


@pytest.mark.parametrize("limits, rounds", [({"live_turns_per_trial": 2}, 2), ({"caller_takes_back_after_turns": 2}, 1)])
def test_actor_answers_stop_at_the_trial_s_limits_and_the_person_still_speaks(monkeypatch, limits, rounds):
    cast = _cast({"kind": "always"}, monkeypatch, lines=[f"line {n}" for n in range(20)], limits=limits)
    agent = _Agent([f"reply {n}" for n in range(30)])
    person = _Person(["Start.", conv.DONE])
    transcript = _run(monkeypatch, cast, agent, person)
    assert sum(cast.follow_ups.values()) == rounds
    # The person was asked after the actors stopped, and ended the chat on that turn.
    assert len(person.told) == 2 and "wrote in the conversation" in person.told[1]
    assert transcript.exchanges[-1].speaker == "agent" and transcript.ended == conv.FINISHED


def test_an_actor_whose_word_never_comes_never_writes_and_is_recorded_not_triggered(monkeypatch):
    cast = _cast({"kind": "on_keyword", "words": ["refund"]}, monkeypatch)
    agent = _Agent(["Hello.", "Done, moved."])
    person = _Person(["Move my order to Friday.", conv.DONE])
    transcript = _run(monkeypatch, cast, agent, person)
    assert all(one["role"] != "actor" for one in transcript.canonical_messages())
    assert cast.summary()[0]["triggered"] is False and cast.summary()[0]["delivered"] == 0


@pytest.mark.parametrize("limits, expected", [(None, 9), ({"caller_takes_back_after_turns": 4}, 4)])
def test_a_chat_actor_is_free_and_only_the_environment_s_limit_stops_it(monkeypatch, limits, expected):
    # No hard-coded cap: an actor decides on every agent turn, within the limits the environment sets.
    cast = _cast({"kind": "always"}, monkeypatch, lines=[f"line {n}" for n in range(10)], limits=limits)

    async def lines():
        out = []
        for turn in range(1, 10):
            out += await cast.due_lines(agent_turns=turn, last_text="Anything else?", tools_called=[],
                                        conversation=[])
        return out

    written = asyncio.run(lines())
    assert len(written) == expected


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


def test_an_actor_never_reads_the_harness_s_own_tables_even_when_its_record_names_them(tmp_path):
    from fi.alk.harness.actors import read_knowledge

    rows = {
        "_alk_tool_trace": [{"tool": "apply_discount", "args": "the agent's call"}],
        "harness_seed_sentinel": [{"id": "ready"}],
        "deals": [{"id": 7}],
    }
    read = read_knowledge(
        [
            {"kind": "records", "store": "world_db", "collection": "_alk_tool_trace"},
            {"kind": "records", "store": "world_db", "collection": "harness_seed_sentinel"},
            {"kind": "records", "store": "world_db", "collection": "deals"},
        ],
        tmp_path,
        rows.get,
    )
    assert [one["read"] for one in read] == [[], [], [{"id": 7}]]


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


def test_an_actor_s_action_is_shown_by_its_full_name_where_its_permissions_name_one():
    from fi.alk.harness.call_runner import actor_rows, trial_state_changes

    log = [
        {"actor_id": "manager", "name": "put", "outcome": "committed", "ok": True},
        {"actor_id": "manager", "name": "change", "outcome": "committed", "ok": True},
        {"actor_id": "both", "name": "change", "outcome": "committed", "ok": True},
        {"actor_id": "manager", "name": "post_message", "outcome": "committed", "ok": True},
    ]
    listed = {
        "manager": Actor(id="manager", name="Mia", permitted_actions=["approvals.put", "approvals.change", "chat.post_message"]),
        # Two collections it may change: the verb alone can't say which, so it is kept as logged.
        "both": Actor(id="both", name="Bo", permitted_actions=["approvals.change", "deals.change"]),
    }
    changes = {c["who"]: c["committed_actions"] for c in trial_state_changes(log, listed)}
    assert changes["manager"] == ["approvals.put", "approvals.change", "chat.post_message"]
    assert changes["both"] == ["change"]
    assert [row["name"] for row in actor_rows(log, listed)] == ["approvals.put", "approvals.change", "change", "chat.post_message"]


def test_an_actor_s_ambiguous_action_takes_its_full_name_from_what_it_chose():
    from fi.alk.harness.call_runner import actor_rows, trial_state_changes

    log = [
        {"actor_id": "manager", "name": "put", "outcome": "committed", "ok": True},
        {"actor_id": "manager", "name": "put", "outcome": "committed", "ok": True},
    ]
    # Both collections log their puts as `put`: only the actor's own choices say which was which.
    listed = {"manager": Actor(id="manager", name="Mia", permitted_actions=["approvals.put", "channel_members.put"])}
    decisions = [
        {"actor": "manager", "play": "mocked", "action": "approvals.put", "outcome": "committed"},
        {"actor": "manager", "play": "live", "action": "channel_members.put", "outcome": "committed"},
    ]
    changes = {c["who"]: c["committed_actions"] for c in trial_state_changes(log, listed, decisions)}
    assert changes["manager"] == ["approvals.put", "channel_members.put"]
    assert [row["name"] for row in actor_rows(log, listed, decisions)] == ["approvals.put", "channel_members.put"]
    # Without its choices the verb alone can't say which, so it is kept.
    assert {c["who"]: c["committed_actions"] for c in trial_state_changes(log, listed)}["manager"] == ["put", "put"]


def _history_dispatcher(history, actors):
    from fi.alk.harness.actor_dispatch import ActorDispatcher

    sent = []

    def send(url, headers, arguments):
        sent.append((url, headers, arguments))
        return 200

    from tests.harness._door_acts import door_acts
    from fi.alk.harness.actors import PreRunSession

    dispatcher = ActorDispatcher(
        actors,
        [],
        act=door_acts(send, "http://proxy"),
        trial_id="trial-2",
        read_log=list,
        seed="trial-2",
        pre_run=[PreRunSession(thread=history)],
    )
    return dispatcher, sent


def test_history_is_written_by_its_actors_in_order_before_the_run_with_keys_of_its_own():
    from fi.alk.harness.actors import ThreadMessage

    actors = {
        "priya": Actor(id="priya", name="Priya", permitted_actions=["slack.post"]),
        "omar": Actor(id="omar", name="Omar", permitted_actions=["slack.post"]),
    }
    history = [
        ThreadMessage(actor="priya", action="slack.post", arguments={"text": "Can we ship Friday?"}),
        ThreadMessage(actor="omar", action="slack.post", arguments={"text": "Only if QA signs off."}),
        ThreadMessage(actor="ghost", action="slack.post", arguments={}),
    ]
    for trial in range(2):
        dispatcher, sent = _history_dispatcher(history, actors)
        asyncio.run(dispatcher.replay())
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


def test_an_actor_s_start_message_is_theirs_by_name_and_counted_as_their_line(monkeypatch):
    from fi.alk.harness.actors import StartsWith

    agent = _Agent(["Applied and posted."])
    monkeypatch.setattr(conv, "Stage", lambda *a, **k: pytest.fail("no caller is simulated"))
    cast = conv.ChatActors([{**_MANAGER, "trigger": {"kind": "always"}}], seed="run", model=None)
    scenario = SimpleNamespace(
        instruction="", max_turns=4, starts_with=StartsWith(actor="manager", text="Apply the agreed 20% to deal 8.")
    )
    transcript = asyncio.run(conv.converse(agent, scenario, SimpleNamespace(), actors=cast))
    assert transcript.canonical_messages()[0] == {
        "role": "actor", "actor": "manager", "name": "Mia", "content": "Apply the agreed 20% to deal 8."
    }
    assert cast.summary() == [{"actor": "manager", "name": "Mia", "triggered": True, "delivered": 1, "dropped": []}]


def test_a_task_driven_run_needs_no_actor_and_the_scenario_sends_the_task(monkeypatch):
    from fi.alk.harness.actors import StartsWith

    agent = _Agent(["Summary posted to #launch."])
    monkeypatch.setattr(conv, "Stage", lambda *a, **k: pytest.fail("no caller is simulated"))
    scenario = SimpleNamespace(instruction="", max_turns=4, starts_with=StartsWith(text="Summarize the launch thread"))
    transcript = asyncio.run(conv.converse(agent, scenario, SimpleNamespace()))
    assert agent.heard == ["Summarize the launch thread"]
    assert [one["role"] for one in transcript.canonical_messages()] == ["user", "assistant"]
    assert transcript.exchanges[0].speaker == "task" and transcript.exchanges[0].actor is None


def test_a_task_driven_start_is_valid_without_any_actor_listed(tmp_path):
    from fi.alk.harness.scenario import Scenario
    from fi.alk.harness.scenario_tools import _unlisted_actors

    scenario = Scenario(name="summarize", instruction="x", starts_with={"text": "Summarize the launch thread"})
    assert _unlisted_actors(scenario, tmp_path) == []
    named = Scenario(name="ended", instruction="x", starts_with={"actor": "notetaker", "text": "Meeting ended"})
    assert _unlisted_actors(named, tmp_path) == ["starts_with: 'notetaker' is not an actor; add it with add_actor first"]


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


def test_the_runner_writes_a_trial_s_faults_into_the_world_database_never_a_file(tmp_path, monkeypatch):
    import sys

    from fi.alk.harness import call_runner as cr

    ran: list = []

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=()):
            ran.append((sql.split()[0], params))

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=lambda *a, **k: _Connection()))
    database = SimpleNamespace(protocol="postgres", address="postgresql://world")
    runtime = SimpleNamespace(endpoints={"db": database}, metadata={})
    cr._write_trial_state(runtime, [{"action": "refunds.create", "mode": "timeout_before_commit"}])
    cr._write_trial_state(runtime, [])
    inserts = [json.loads(params[0]) for verb, params in ran if verb == "INSERT"]
    assert inserts == [[{"action": "refunds.create", "mode": "timeout_before_commit"}], []]
    assert not list(tmp_path.rglob("trial.json"))

def test_a_way_of_speaking_sent_as_the_actor_s_surface_is_said_back_never_saved(tmp_path, monkeypatch):
    from fi.alk.harness.actors import load_actors
    from fi.alk.harness.world.tools import world_actor_tools

    add, _drop = world_actor_tools(tmp_path, SimpleNamespace(modality="voice", model_dump=lambda: {}))
    out = asyncio.run(add.handler({"id": "dana", "name": "Dana", "role": "a coworker", "surface": "audio_mix"}))
    assert out.get("is_error") and "send it as `channel`" in out["content"][0]["text"]
    assert load_actors(tmp_path).actors == []
    ok = asyncio.run(add.handler({"id": "dana", "name": "Dana", "role": "a coworker", "channel": "audio_mix"}))
    assert not ok.get("is_error")
    assert load_actors(tmp_path).by_id()["dana"].channel == "audio_mix"


def test_an_actor_needs_its_identity_where_its_action_s_tool_names_who_acts(tmp_path):
    from fi.alk.harness.actors import load_actors
    from fi.alk.harness.world.tools import world_actor_tools

    names_who = {
        "tools": [{"name": "post_message", "args": ["channel", "text", "actor_id"]}, {"name": "get_deal", "args": ["deal_id"]}],
        "tool_entrypoints": [
            {"tool": "post_message", "endpoint": "chat/post_message"},
            {"tool": "get_deal", "endpoint": "crm/get_deal"},
        ],
    }
    contract = SimpleNamespace(modality="chat", model_dump=lambda: names_who)
    add, _drop = world_actor_tools(tmp_path, contract, collections=lambda: ["approvals"])
    # A tool that does not name who acts needs no identity.
    reader = asyncio.run(add.handler({"id": "reader", "name": "Kim", "permitted_actions": ["crm.get_deal"]}))
    assert not reader.get("is_error")
    out = asyncio.run(add.handler({"id": "lead", "name": "Sam", "permitted_actions": ["chat.post_message", "approvals.put"]}))
    assert out.get("is_error") and "has no identity there" in out["content"][0]["text"] and "'chat'" in out["content"][0]["text"]
    assert [one.id for one in load_actors(tmp_path).actors] == ["reader"]
    ok = asyncio.run(
        add.handler(
            {"id": "lead", "name": "Sam", "permitted_actions": ["chat.post_message", "approvals.put"], "identities": {"chat": "lead"}}
        )
    )
    assert not ok.get("is_error") and load_actors(tmp_path).by_id()["lead"].identities == {"chat": "lead"}


def test_a_person_folded_into_the_caller_is_refused_and_a_kept_scenario_reads_its_cast_back(tmp_path):
    from fi.alk.harness.actors import load_actors
    from fi.alk.harness.scenario import Scenario
    from fi.alk.harness.scenario_tools import _folded_actors, cast_read_back

    save_actors(
        tmp_path,
        ActorList(
            actors=[
                Actor(id="manager", name="Alex Vance", permitted_actions=["approvals.put"]),
                Actor(id="lead", name="Sam Rivera", permitted_actions=["approvals.put"]),
            ]
        ),
    )
    listed = load_actors(tmp_path).by_id()
    # What the instruction's words mention is never refused: a person in the world's data may be named in passing.
    told = Scenario.model_validate(
        {"name": "x", "instruction": "Ask about the refund Alex Vance approved last month.", "persona": {"name": "Priya"}}
    )
    assert _folded_actors(told, listed) == []
    assert "not in it: Alex Vance (manager), Sam Rivera (lead)" in cast_read_back(told, tmp_path)
    played_as = Scenario.model_validate({"name": "x", "instruction": "Negotiate.", "persona": {"name": "alex vance"}})
    assert _folded_actors(played_as, listed)[0].startswith("persona: the caller is played as Alex Vance")
    kept = Scenario.model_validate(
        {
            "name": "x",
            "instruction": "Ask whether Alex Vance approved it.",
            "persona": {"name": "Priya"},
            "actors": [{"id": "manager", "actions": [{"action": "approvals.put", "arguments": {"deal_id": 7}}]}],
        }
    )
    assert _folded_actors(kept, listed) == []
    said = cast_read_back(kept, tmp_path)
    assert "Alex Vance (manager; plays: mocked, only the actions listed here; comes in: its own; does: approvals.put (a record change))" in said
    assert "not in it: Sam Rivera (lead)" in said
    assert "Talked before the run: nobody (no `pre_run`)" in said and "Started by: the caller (no `starts_with`)" in said
    # An action with no trigger of its own fires at the start, so it is read back as already done.
    assert "Already done before the agent starts (`always`): manager (approvals.put)" in said
    assert "has to handle" not in said
    asked = kept.model_copy(
        update={"actors": [{"id": "manager", "actions": [{"action": "approvals.put", "trigger": {"kind": "on_tool", "tool": "ask"}}]}]}
    )
    assert "Already done" not in cast_read_back(Scenario.model_validate(asked.model_dump()), tmp_path)
    # A decision already made at the start, and a part spoken on the call, are each read back with what to check.
    decided = kept.model_copy(
        update={
            "actors": [
                {
                    "id": "manager",
                    "actions": [{"action": "approvals.put", "arguments": {}, "trigger": {"kind": "always"}}],
                },
                {"id": "lead", "instruction": "Say the order number is 1043."},
            ]
        }
    )
    said = cast_read_back(Scenario.model_validate(decided.model_dump()), tmp_path)
    assert "Already done before the agent starts (`always`): manager (approvals.put)" in said
    assert "For lead: name what the agent has to handle" in said


def test_the_read_back_says_how_each_actor_plays_and_that_a_chat_actor_s_decision_stays_out_of_the_caller_s_part(tmp_path):
    from fi.alk.harness.scenario import Scenario
    from fi.alk.harness.scenario_tools import cast_read_back

    save_actors(
        tmp_path,
        ActorList(
            actors=[
                Actor(id="manager", name="Alex Vance", channel="chat_message", role="Sales manager"),
                Actor(id="lead", name="Sam Rivera", play="live", permitted_actions=["approvals.put"]),
                Actor(id="ceo", name="Jo Park", play="both", permitted_actions=["approvals.put"]),
            ]
        ),
    )
    said = cast_read_back(
        Scenario.model_validate(
            {
                "name": "x",
                "instruction": "Ask for 20% on deal 9.",
                "actors": [{"id": "manager", "instruction": "Negotiate down to 16%."}, {"id": "lead"}, {"id": "ceo"}],
            }
        ),
        tmp_path,
    )
    assert "manager; plays: speaks, deciding in the moment on each agent turn its channel allows;" in said
    assert "lead; plays: live, decides its own actions in the moment;" in said
    assert "ceo; plays: both, the actions listed here and its own decisions;" in said
    assert "manager answer the agent until their live turns run out" in said
    assert "keep what they decide out of the caller's instruction" in said
    # Without a chat actor, no such note.
    quiet = cast_read_back(
        Scenario.model_validate({"name": "x", "instruction": "Ask.", "actors": [{"id": "lead"}]}), tmp_path
    )
    assert "answer the agent until" not in quiet


def test_an_actor_brought_in_when_contacted_needs_an_identity_the_agent_could_name(tmp_path):
    from fi.alk.harness.actors import load_actors
    from fi.alk.harness.scenario import Scenario
    from fi.alk.harness.scenario_tools import _activation_problems

    save_actors(
        tmp_path,
        ActorList(
            actors=[
                Actor(id="sup", name="Dana Brooks", permitted_actions=["approvals.put"],
                      activation={"trigger": {"kind": "contacted"}}),
                Actor(id="mgr", name="Alex Vance", permitted_actions=["approvals.put"], identities={"crm": "mgr"},
                      activation={"trigger": {"kind": "contacted"}}),
            ]
        ),
    )
    listed = load_actors(tmp_path).by_id()
    unreachable = Scenario.model_validate({"name": "x", "instruction": "Cancel it.", "actors": [{"id": "sup"}]})
    assert "sup comes in when contacted but has no identity" in " ".join(_activation_problems(unreachable, listed))
    reachable = Scenario.model_validate({"name": "x", "instruction": "Cancel it.", "actors": [{"id": "mgr"}]})
    assert _activation_problems(reachable, listed) == []


def test_the_person_the_caller_speaks_for_may_share_the_caller_s_name(tmp_path):
    from fi.alk.harness.actors import load_actors
    from fi.alk.harness.scenario import Scenario
    from fi.alk.harness.scenario_tools import _folded_actors

    save_actors(
        tmp_path,
        ActorList(
            actors=[
                Actor(id="caller", name="The caller", on_behalf_of="owner"),
                Actor(id="owner", name="Dana Brooks"),
                Actor(id="boss", name="Manager"),
            ]
        ),
    )
    said = Scenario.model_validate(
        {"name": "x", "instruction": "You call for Dana Brooks.", "persona": {"name": "Dana Brooks"}}
    )
    assert _folded_actors(said, load_actors(tmp_path).by_id()) == []


def test_saving_the_actors_writes_the_rules_that_check_them_beside_them(tmp_path):
    from fi.alk.harness.actors import ACTOR_RULES_FILE, actor_rules

    save_actors(tmp_path, ActorList(actors=[Actor(id="sup", name="Dana", permitted_actions=["approvals.change"])]))
    written = json.loads((tmp_path / ACTOR_RULES_FILE).read_text())
    assert written["own_actions"] == ["activation.fallback"] and written == json.loads(json.dumps(actor_rules()))


def test_the_sealed_rules_name_the_record_changes_the_platform_lists_beside_the_routes():
    from fi.alk.harness.actors import actor_rules, record_changes

    rules = actor_rules()["record_changes"]
    assert rules["from"] == "generic-harness/source-model.json" and rules["verbs"] == ["put", "change"]
    # The platform lists exactly what add_actor accepts.
    tables = ["approvals", *rules["never"], rules["never_prefix"] + "tool_trace"]
    assert sorted(record_changes(tables)) == sorted(f"approvals.{verb}" for verb in rules["verbs"])


def test_an_actor_may_be_given_record_changes_on_the_world_s_own_collections(tmp_path):
    from fi.alk.harness.actors import load_actors
    from fi.alk.harness.world.tools import world_actor_tools

    contract = SimpleNamespace(modality="chat", model_dump=lambda: {})
    tables = ["approvals", "deals", "harness_seed_sentinel", "_alk_tool_trace"]
    add, _drop = world_actor_tools(tmp_path, contract, collections=lambda: tables)
    assert "approvals.put" in add.description and "_alk_tool_trace" not in add.description
    out = asyncio.run(add.handler({"id": "lead", "name": "Sam", "permitted_actions": ["approvals.put"]}))
    text = out["content"][0]["text"]
    assert "not among them" not in text and "deals.change" in text and "harness_seed_sentinel" not in text
    assert load_actors(tmp_path).by_id()["lead"].permitted_actions == ["approvals.put"]
    out = asyncio.run(add.handler({"id": "lead", "permitted_actions": ["_alk_actors.put"]}))
    assert "_alk_actors.put is not among them" in out["content"][0]["text"]


def test_a_voice_actor_s_brief_reads_the_world_records_it_may_see(tmp_path, monkeypatch):
    from fi.alk.harness import call_runner as cr
    from fi.alk.harness.call_actor import CallActor, CallActorList, save_call_actors

    save_call_actors(tmp_path, CallActorList(call_actors=[CallActor(id="near", name="Rowan", role="a coworker", channel="audio_mix")]))
    from fi.alk.harness.actors import load_actors

    listing = load_actors(tmp_path)
    listing.actors[0] = listing.actors[0].model_copy(
        update={"knowledge": [{"kind": "records", "store": "crm", "collection": "deals", "where": {"id": 7}}]}
    )
    save_actors(tmp_path, listing)
    doc = {
        "instruction": "You want the discount applied.",
        "persona": {"name": "Sam", "languages": ["English"]},
        "actors": [{"id": "near", "instruction": "You want them to hurry.", "trigger": {"kind": "always"}}],
    }
    rows = {"deals": [{"id": 7, "value": 900}, {"id": 8, "value": 50}]}
    [resolved] = cr._resolved_call_actors(doc, tmp_path, "deepgram", "", "run", rows.get)
    assert '"value": 900' in resolved["brief"] and '"value": 50' not in resolved["brief"]


class _InProcessWorld:
    """A generated world whose tools run in process: each call commits and is recorded."""

    def __init__(self):
        from fi.alk.harness.world.runtime import Call

        self.Call = Call
        self.calls = []
        self.committed = []

    def _record(self, call):
        self.calls.append(call)
        return call

    def handle_tool_call(self, call):
        self.committed.append(dict(call["arguments"]))
        self._record(self.Call(name=call["name"], arguments=dict(call["arguments"]), result={"ok": True}))
        return SimpleNamespace(content='{"ok": true}')


def _chat_target(faults):
    from fi.alk.harness import chat_call_runner as chat

    contract = SimpleNamespace(tool_entrypoints=[{"tool": "update_deal", "endpoint": "crm/update_deal"}])
    world = _InProcessWorld()
    target = chat._HostedChatTarget(
        wrapper=None, contract=contract, world=world, scenario_key="s", scenario_id="s-1", faults=faults
    )
    return target, world


def test_a_chat_agent_s_in_process_call_timed_out_before_commit_never_changes_the_world():
    target, world = _chat_target([{"action": "crm.update_deal", "mode": "timeout_before_commit"}])
    assert target._world_call("c1", "update_deal", {"discount": 10}) == "Error: the request timed out"
    assert world.committed == [] and world.calls[0].error == "timeout_before_commit" and not world.calls[0].ok
    # The retry is the agent's second call: it commits once.
    assert target._world_call("c2", "update_deal", {"discount": 10}) == '{"ok": true}'
    assert world.committed == [{"discount": 10}]


def test_a_chat_agent_s_in_process_call_timed_out_after_commit_commits_and_a_retry_commits_again():
    target, world = _chat_target([{"action": "crm.update_deal", "mode": "timeout_after_commit", "on_call": 1}])
    assert target._world_call("c1", "update_deal", {"discount": 10}) == "Error: the request timed out"
    assert world.committed == [{"discount": 10}] and world.calls[0].error == "response_lost"
    target._world_call("c2", "update_deal", {"discount": 10})
    # Not deduplicated: the check can fail the duplicate the careless retry made.
    assert world.committed == [{"discount": 10}, {"discount": 10}]


def test_a_chat_call_with_no_fault_is_answered_as_the_world_answers():
    target, world = _chat_target([])
    assert target._world_call("c1", "update_deal", {"discount": 5}) == '{"ok": true}'


@pytest.mark.parametrize(
    "path, allowed",
    [
        ("docs/policy.md", True),
        ("kb/pricing.csv", True),
        ("sub_goals.json", False),
        ("data/certification.json", False),
        ("contract.json", False),
        ("world.sqlite", False),
        ("generic-harness/world-ir.json", False),
        ("scenarios/a/scenario.json", False),
        ("../outside.txt", False),
        (".env", False),
    ],
)
def test_an_actor_may_read_the_environment_s_own_documents_never_the_evaluator_s_or_the_harness_s(path, allowed):
    from fi.alk.harness.actors import actor_rules, knowledge_file_allowed

    assert knowledge_file_allowed(path) is allowed
    assert "not_knowledge" in actor_rules()


def test_a_chat_actor_that_declines_reaches_the_agent_as_a_labelled_line_never_in_its_own_words(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    cast = conv.ChatActors(
        [{**_MANAGER, "trigger": {"kind": "always"}, "brief": "You are Mia.", "timeout_seconds": 30,
          "fallback": "decline"}],
        seed="run", model=None, clock=lambda: clock.now,
    )

    async def wait(actor, brief):
        clock.now += 20
        return ""

    monkeypatch.setattr(cast, "_write", wait)
    agent = _Agent(["Hello.", "Let me ask my manager.", "Okay."])
    person = _Person(["Move my order.", "Please do.", "Thanks."])
    _run(monkeypatch, cast, agent, person)
    assert "Mia did not answer: declined" in agent.heard
    assert "Mia: Mia did not answer: declined" not in agent.heard


def test_a_field_s_unit_reaches_the_actor_and_the_contract_line_every_stage_reads():
    from fi.alk.harness.actor_dispatch import described_with_units
    from fi.alk.harness.contract import ToolSpec

    spec = ToolSpec.model_validate(
        {"name": "refund", "args": ["refund_amount", "order_id"], "units": {"refund_amount": "cents, USD"},
         "description": "Refund an order"}
    )
    assert spec.arg_units == {"refund_amount": "cents, USD"}
    # Every field by name, so a live actor sends the arguments the world takes, with the unit where there is one.
    assert described_with_units(spec.model_dump()) == "Refund an order (fields: refund_amount in cents, USD; order_id)"
    assert described_with_units({"description": "Refund an order"}) == "Refund an order"


def test_an_actor_is_told_a_record_change_s_fields_from_the_world_s_data_schema():
    from fi.alk.harness.actor_dispatch import action_descriptions

    contract = {
        "data_schema": {
            "approvals": {
                "id": "SERIAL PRIMARY KEY",
                "deal_id": "INTEGER NOT NULL REFERENCES deals(id)",
                "discount_percent": "INTEGER NOT NULL",
                "status": "TEXT NOT NULL",
                "posted_at": "TIMESTAMPTZ NOT NULL DEFAULT now()",
            }
        },
        "tools": [{"name": "post_message", "args": ["channel", "text"], "description": "Post in a channel"}],
        "tool_entrypoints": [{"tool": "post_message", "endpoint": "/chat/post_message"}],
    }
    about = action_descriptions(contract)
    # The fields the world fills itself are left out of a put; a change can match on any of them.
    assert about["approvals.put"] == (
        "Add a record to approvals. Fields: deal_id (integer); discount_percent (integer); status (text)"
    )
    assert "`where` picks them and `set` gives the new values" in about["approvals.change"]
    assert "id; deal_id; discount_percent; status; posted_at" in about["approvals.change"]
    assert about["chat.post_message"] == "Post in a channel (fields: channel; text)"
    # No data schema, no record descriptions.
    assert "approvals.put" not in action_descriptions({**contract, "data_schema": {}})


def test_a_chat_actor_is_told_each_action_s_fields_and_acts_under_its_identity():
    from fi.alk.harness.run.conversation import ChatActors

    acted, briefs = [], []

    async def act(actor, action, arguments, *, key, parent, identity="", form="tool"):
        acted.append((action, arguments, identity, form))
        return {"status": 200, "outcome": "committed"}

    cast = ChatActors(
        [{"id": "coworker", "name": "Jordan", "permitted_actions": ["chat.post_message", "approvals.put"],
          "identities": {"chat": "coworker", "approvals": "coworker"}, "trigger": {"kind": "always"}}],
        seed="t", model=None, act=act, about={"chat.post_message": "Post (fields: channel; text)"},
    )
    replies = iter([
        json.dumps({"action": "chat.post_message", "arguments": {"channel": "#sales", "text": "hi"}}),
        json.dumps({"action": "approvals.put", "arguments": {"deal_id": 7}}),
        json.dumps({"action": "approvals.change", "arguments": {"where": {"deal_id": 7}, "set": {"status": "x"}}}),
    ])

    async def write(actor, brief):
        briefs.append(brief)
        return next(replies)

    cast._write = write
    for turn in (1, 2, 3):
        asyncio.run(cast.due_lines(agent_turns=turn, last_text="", tools_called=[], conversation=[]))
    assert "Post (fields: channel; text)" in json.dumps(briefs[0])
    # A permitted record change goes to the world's records, as a scheduled one does; one it may not make stays a
    # tool call, for the door to refuse.
    assert acted == [
        ("chat.post_message", {"channel": "#sales", "text": "hi"}, "coworker", "tool"),
        ("approvals.put", {"deal_id": 7}, "", "record"),
        ("approvals.change", {"where": {"deal_id": 7}, "set": {"status": "x"}}, "", "tool"),
    ]


def test_a_unit_for_a_field_the_tool_does_not_have_is_a_contract_problem():
    from fi.alk.harness.contract import ToolSpec, validate_contract
    from fi.alk.harness.contract import AgentContract

    contract = AgentContract.model_validate({"agent": "shop", "tools": [ToolSpec(
        name="refund", args=["order_id"], arg_units={"refund_amount": "cents"}).model_dump()]})
    assert "tool[refund]:units-for-unknown-args:refund_amount" in validate_contract(contract)
    named = AgentContract.model_validate({"agent": "shop", "tools": [ToolSpec(
        name="refund", args=["refund_amount"], arg_units={"refund_amount": "cents, USD"}).model_dump()]})
    assert "units: refund_amount in cents, USD" in named.brief()


def test_a_task_driven_run_s_task_reaches_the_platform_as_the_person_s_message():
    from fi.alk.harness.platform import SPEAKERS

    assert SPEAKERS["task"] == "user"


def test_a_start_message_from_no_one_is_read_back_with_the_actors_who_could_send_it(tmp_path):
    from fi.alk.harness.scenario import Scenario
    from fi.alk.harness.scenario_tools import cast_read_back

    save_actors(tmp_path, ActorList(actors=[Actor(id="manager", name="Alex Vance", permitted_actions=["chat.post_message"])]))
    unsent = Scenario.model_validate({"name": "x", "instruction": "A deal was approved.", "starts_with": {"text": "Apply it."}})
    said = cast_read_back(unsent, tmp_path)
    assert "Started by: the scenario's own task" in said
    assert "The start message comes from no one" in said and "Alex Vance (manager)" in said
    sent = Scenario.model_validate(
        {"name": "x", "instruction": "A deal was approved.", "starts_with": {"actor": "manager", "text": "Apply it."}}
    )
    assert "comes from no one" not in cast_read_back(sent, tmp_path)


def test_an_actor_whose_role_names_one_caller_is_read_back_as_written_for_one_scenario(tmp_path):
    from fi.alk.harness.scenario import Scenario
    from fi.alk.harness.scenario_tools import cast_read_back

    save_actors(
        tmp_path,
        ActorList(actors=[Actor(id="spouse", name="Sam Whitfield", role="Spouse of caller Dana Whitfield", channel="audio_mix")]),
    )
    other_caller = Scenario.model_validate(
        {"name": "x", "instruction": "Ask about the claim.", "persona": {"name": "Chloe Park"}, "actors": [{"id": "spouse"}]}
    )
    said = cast_read_back(other_caller, tmp_path, {"dana whitfield", "chloe park"})
    assert "Sam Whitfield's role names Dana Whitfield, a caller in this suite" in said
    assert "never by one scenario's names" in said
    # The same caller's own scenario is caught too: the actor is shared by every scenario.
    assert "names Dana Whitfield" in cast_read_back(
        Scenario.model_validate({**other_caller.model_dump(), "persona": {"name": "Dana Whitfield"}}), tmp_path
    )
    # A role that names people by their part, or a single shared first name, is fine.
    save_actors(tmp_path, ActorList(actors=[Actor(id="spouse", name="Sam Whitfield", role="The caller's spouse, Dana's husband", channel="audio_mix")]))
    assert "role names" not in cast_read_back(other_caller, tmp_path, {"dana whitfield", "chloe park"})


def test_a_caller_instruction_that_states_what_an_actor_decides_is_read_back_as_settled(tmp_path):
    from fi.alk.harness.actors import Constraints
    from fi.alk.harness.scenario import Scenario
    from fi.alk.harness.scenario_tools import cast_read_back

    save_actors(
        tmp_path,
        ActorList(
            actors=[
                Actor(
                    id="manager",
                    name="Alex Vance",
                    play="live",
                    permitted_actions=["approvals.change"],
                    constraints=Constraints(may_agree=["Up to 18% on deals over $50,000"]),
                )
            ]
        ),
    )
    settled = Scenario.model_validate(
        {"name": "x", "instruction": "The manager agreed 18% on deal 9; apply it.", "actors": [{"id": "manager"}]}
    )
    said = cast_read_back(settled, tmp_path)
    assert "The caller's instruction already states 18%, which Alex Vance decides" in said
    asked = Scenario.model_validate(
        {"name": "x", "instruction": "Ask for 25% on deal 9.", "actors": [{"id": "manager"}]}
    )
    assert "already states" not in cast_read_back(asked, tmp_path)


def test_an_actor_with_no_identity_in_a_service_still_acts_there_as_itself():
    from fi.alk.harness.run.conversation import ChatActors

    acted = []

    async def act(actor, action, arguments, *, key, parent, identity="", form="tool"):
        acted.append((action, identity))
        return {"status": 200, "outcome": "committed"}

    cast = ChatActors(
        [{"id": "manager", "name": "Alex", "permitted_actions": ["chat.post_message"], "trigger": {"kind": "always"}}],
        seed="t", model=None, act=act, about={},
    )

    async def write(actor, brief):
        return json.dumps({"action": "chat.post_message", "arguments": {"channel": "#sales", "text": "Approved."}})

    cast._write = write
    asyncio.run(cast.due_lines(agent_turns=1, last_text="", tools_called=[], conversation=[]))
    # With no identity the world would fill in its own default, and that default is the agent.
    assert acted == [("chat.post_message", "manager")]


def test_a_decision_written_only_as_a_record_after_the_agent_s_call_is_read_back_as_unseen_by_the_agent(tmp_path):
    from fi.alk.harness.scenario import Scenario
    from fi.alk.harness.scenario_tools import cast_read_back

    save_actors(tmp_path, ActorList(actors=[Actor(id="manager", name="Alex Vance", permitted_actions=["approvals.put"])]))
    on_request = {"action": "approvals.put", "arguments": {"status": "rejected"},
                  "trigger": {"kind": "on_tool", "tool": "request_discount"}}
    silent = Scenario.model_validate({"name": "x", "instruction": "Ask for 25%.", "actors": [{"id": "manager", "actions": [on_request]}]})
    assert "Alex Vance decide only by writing a record after the agent's call" in cast_read_back(silent, tmp_path)
    # An actor who also tells the agent is heard; no note.
    told = Scenario.model_validate(
        {"name": "x", "instruction": "Ask for 25%.",
         "actors": [{"id": "manager", "instruction": "Say you rejected it.", "actions": [on_request]}]}
    )
    assert "decide only by writing a record" not in cast_read_back(told, tmp_path)
