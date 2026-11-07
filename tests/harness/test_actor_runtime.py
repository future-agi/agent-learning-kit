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


def _history_dispatcher(history, actors):
    from fi.alk.harness.actor_dispatch import ActorDispatcher

    sent = []

    def send(url, headers, arguments):
        sent.append((url, headers, arguments))
        return 200

    from fi.alk.harness.actor_dispatch import HttpActs
    from fi.alk.harness.actors import PreRunSession

    dispatcher = ActorDispatcher(
        actors,
        [],
        act=HttpActs("http://proxy/", "t0ken", 60.0, send),
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


def test_an_actor_acting_in_a_service_needs_its_identity_there(tmp_path):
    from fi.alk.harness.actors import load_actors
    from fi.alk.harness.world.tools import world_actor_tools

    contract = SimpleNamespace(modality="chat", model_dump=lambda: {})
    add, _drop = world_actor_tools(tmp_path, contract, collections=lambda: ["approvals"])
    out = asyncio.run(add.handler({"id": "lead", "name": "Sam", "permitted_actions": ["chat.post_message", "approvals.put"]}))
    assert out.get("is_error") and "has no identity there" in out["content"][0]["text"] and "'chat'" in out["content"][0]["text"]
    assert load_actors(tmp_path).actors == []
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
    told = Scenario.model_validate({"name": "x", "instruction": "Alex Vance has already rejected it.", "persona": {"name": "Priya"}})
    assert ["instruction: it names Alex Vance"] == [one[: len("instruction: it names Alex Vance")] for one in _folded_actors(told, listed)]
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
    assert "Alex Vance (manager; comes in: its own; does: approvals.put (a record change))" in said
    assert "not in it: Sam Rivera (lead)" in said
    assert "Talked before the run: nobody (no `pre_run`)" in said and "Started by: the caller (no `starts_with`)" in said


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


def test_a_one_word_name_and_the_person_the_caller_speaks_for_are_never_read_as_folded(tmp_path):
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
        {"name": "x", "instruction": "You call for Dana Brooks; ask the manager about it.", "persona": {"name": "Priya"}}
    )
    assert _folded_actors(said, load_actors(tmp_path).by_id()) == []


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


def test_a_chat_actor_is_told_each_action_s_fields_and_acts_under_its_identity():
    from fi.alk.harness.run.conversation import ChatActors

    acted, briefs = [], []

    async def act(actor, action, arguments, *, key, parent, identity=""):
        acted.append((action, arguments, identity))
        return {"status": 200, "outcome": "committed"}

    cast = ChatActors(
        [{"id": "coworker", "name": "Jordan", "permitted_actions": ["chat.post_message", "approvals.put"],
          "identities": {"chat": "coworker", "approvals": "coworker"}, "trigger": {"kind": "always"}}],
        seed="t", model=None, act=act, about={"chat.post_message": "Post (fields: channel; text)"},
    )
    replies = iter([
        json.dumps({"action": "chat.post_message", "arguments": {"channel": "#sales", "text": "hi"}}),
        json.dumps({"action": "approvals.put", "arguments": {"deal_id": 7}}),
    ])

    async def write(actor, brief):
        briefs.append(brief)
        return next(replies)

    cast._write = write
    for turn in (1, 2):
        asyncio.run(cast.due_lines(agent_turns=turn, last_text="", tools_called=[], conversation=[]))
    assert "Post (fields: channel; text)" in json.dumps(briefs[0])
    assert acted == [("chat.post_message", {"channel": "#sales", "text": "hi"}, "coworker"), ("approvals.put", {"deal_id": 7}, "")]


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
