"""Actors as the plan defines them: one activation, fallbacks, limits as settings, pre-run sessions, the record."""

from __future__ import annotations

import asyncio

import pytest

from fi.alk.harness.actor_dispatch import ActorDispatcher, HttpActs, world_summary
from fi.alk.harness.actors import (
    Activation,
    Actor,
    ActorList,
    Limits,
    PreRunSession,
    ScenarioActor,
    Trigger,
    cycles,
    is_pre_run,
)
from fi.simulate.simulation.call_actors import Moment, due, read_decision


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _sent_to(sent, result=None):
    def send(url, headers, arguments):
        sent.append((url, headers, arguments))
        return {"status": 200, "result": result(len(sent)) if result else None}

    return send


@pytest.mark.parametrize(
    "key, pre_run",
    [("t:priya:h1", True), ("t:hr_manager:0", False), ("t:hr_manager:l2", False), ("t:h:h12", True), (None, False)],
)
def test_only_a_key_ending_in_h_and_a_number_is_a_pre_run_row(key, pre_run):
    # An actor whose id starts with "h" (hr_manager) is never read as history.
    assert is_pre_run(key) is pre_run


def test_hr_manager_s_actions_stay_in_the_trial_s_changes():
    from fi.alk.harness.call_runner import trial_state_changes

    log = [{"actor_id": "hr_manager", "name": "approve", "outcome": "committed", "idempotency_key": "t:hr_manager:0"}]
    changes = trial_state_changes(log, {"hr_manager": Actor(id="hr_manager", name="Hana")})
    assert [one["who"] for one in changes] == ["agent", "hr_manager"]
    # The agent did nothing: "no change" is its result, shown, never left out.
    assert changes[0]["no_change"] is True and changes[0]["attempted"] == 0


def test_conditional_is_due_once_a_matching_record_exists_on_every_runtime():
    # A record of the collection that does not match is not enough.
    rows: list[dict] = [{"status": "approved", "id": 0}]
    trigger = {"kind": "conditional", "collection": "approvals", "where": {"status": "pending"}}
    for turns in (None, 3):
        moment = Moment(agent_turns=turns, records=lambda collection: list(rows))
        assert not due(trigger, moment, seed="s", actor_id="a")
    rows.append({"status": "pending", "id": 1})
    for turns in (None, 3):
        assert due(trigger, Moment(agent_turns=turns, records=lambda collection: list(rows)), seed="s", actor_id="a")


def test_always_is_at_once_beside_the_call_and_after_the_first_turn_on_it():
    assert due({"kind": "always"}, Moment(agent_turns=None), seed="s", actor_id="a")
    assert not due({"kind": "always"}, Moment(agent_turns=0), seed="s", actor_id="a")
    assert due({"kind": "always"}, Moment(agent_turns=1), seed="s", actor_id="a")


def test_a_fallback_may_be_one_of_the_actor_s_own_permitted_actions():
    ActorList(actors=[Actor(id="manager", name="Mia", permitted_actions=["crm.reject"],
                            activation=Activation(timeout_seconds=30, fallback="crm.reject"))])
    with pytest.raises(ValueError):
        ActorList(actors=[Actor(id="manager", name="Mia", activation=Activation(fallback="crm.reject"))])


def test_a_permitted_action_fallback_is_sent_under_the_actor_and_labelled_as_the_timeout_s():
    sent: list = []
    clock = _Clock()
    manager = Actor(id="manager", name="Mia", permitted_actions=["crm.reject"], identities={"crm": "mia"},
                    activation=Activation(trigger=Trigger(kind="contacted"), timeout_seconds=30, fallback="crm.reject"))
    log = [{"actor_id": "agent", "name": "request", "action_id": "a1", "arguments": {"to": "mia"}}]
    dispatcher = ActorDispatcher(
        {"manager": manager}, [ScenarioActor(id="manager")], act=HttpActs("http://p", "t", 5, _sent_to(sent)),
        trial_id="t", read_log=lambda: log, seed="t", clock=clock,
    )

    async def run():
        await dispatcher.tick()
        clock.now = 31
        await dispatcher.tick()

    asyncio.run(run())
    assert [(one["play"], one.get("outcome")) for one in dispatcher.fired] == [
        ("waiting", None), ("timeout", "fallback_action")
    ]
    assert sent[0][0] == "http://p/crm/reject" and sent[0][1]["X-ALK-Parent-Event"] == "a1"


def test_an_open_wait_and_an_actor_that_never_came_in_are_in_the_record():
    clock = _Clock()
    actors = {
        "manager": Actor(id="manager", name="Mia", identities={"crm": "mia"},
                         activation=Activation(trigger=Trigger(kind="contacted"), timeout_seconds=30)),
        "auditor": Actor(id="auditor", name="Ada", activation=Activation(trigger=Trigger(kind="on_tool", tool="close"))),
    }
    log = [{"actor_id": "agent", "name": "request", "action_id": "a1", "arguments": {"to": "mia"}}]
    dispatcher = ActorDispatcher(
        actors, [ScenarioActor(id="manager"), ScenarioActor(id="auditor")],
        act=HttpActs("http://p", "t", 5, _sent_to([])), trial_id="t", read_log=lambda: log, seed="t", clock=clock,
    )
    asyncio.run(dispatcher.tick())
    record = dispatcher.record()
    assert {"actor": "manager", "play": "timeout", "outcome": "unresolved_at_end", "parent": "a1"} in record
    assert any(one["actor"] == "auditor" and one.get("problem") == "not_activated" for one in record)


def test_pre_run_sessions_replay_in_order_and_a_reply_takes_the_id_the_world_gave_its_message():
    sent: list = []
    actors = {
        "priya": Actor(id="priya", name="Priya", permitted_actions=["slack.post"]),
        "ravi": Actor(id="ravi", name="Ravi", permitted_actions=["slack.post", "slack.reply"]),
    }
    sessions = [
        PreRunSession.model_validate(
            {
                "actors": ["priya", "ravi"],
                "thread": [
                    {"actor": "priya", "action": "slack.post", "arguments": {"channel": "#leads", "text": "20% for Acme?"}},
                    {"actor": "ravi", "action": "slack.reply", "arguments": {"text": "Too much."},
                     "refers": {"thread_ts": {"message": 1, "field": "ts"}}},
                ],
            }
        ),
        PreRunSession.model_validate(
            {"actors": ["ravi"], "thread": [{"actor": "ravi", "action": "slack.post", "arguments": {"channel": "#eng"}}]}
        ),
    ]
    dispatcher = ActorDispatcher(
        actors, [], act=HttpActs("http://p", "t", 5, _sent_to(sent, lambda n: {"ts": f"ts-{n}"})),
        trial_id="t", read_log=list, seed="t", pre_run=sessions,
    )
    asyncio.run(dispatcher.replay())
    assert [(h["X-ALK-Actor"], h["Idempotency-Key"]) for _, h, _ in sent] == [
        ("priya", "t:priya:h1"), ("ravi", "t:ravi:h2"), ("ravi", "t:ravi:h3")
    ]
    assert sent[1][2] == {"text": "Too much.", "thread_ts": "ts-1"}
    assert all(one["play"] == "history" for one in dispatcher.fired)


def test_an_old_scenario_s_typed_history_reads_as_one_pre_run_session():
    from fi.alk.harness.scenario import pre_run_of

    doc = pre_run_of({"history": [{"actor": "priya", "action": "slack.post", "arguments": {"text": "hi"}}]})
    assert "history" not in doc
    assert doc["pre_run"][0]["thread"][0]["actor"] == "priya"


def test_limits_are_settings_with_channel_defaults_and_a_scenario_override():
    voice = Limits().resolved("voice")
    chat = Limits().resolved("chat")
    assert (voice["max_active"], voice["actor_seconds_per_call"]) == (2, 60.0)
    assert (chat["max_active"], chat["caller_takes_back_after_turns"]) == (None, None)
    assert chat["max_messages"] == 500
    merged = Limits(max_active=3).merged(Limits(max_active=None)).resolved("voice")
    assert merged["max_active"] is None


def test_the_voice_cast_takes_no_limit_when_the_setting_says_none():
    from fi.simulate.simulation.call_actors import Cast

    cast = Cast([{"id": "a", "name": "A", "trigger": {"kind": "always"}}], "s", limits={"actor_seconds_per_call": None,
                "max_active": None, "caller_takes_back_after_turns": None})
    assert cast.remaining() == float("inf")
    cast.entering(Moment(agent_turns=1))
    for turn in range(1, 20):
        cast.took_turn()
    assert cast.may_speak(cast.actors[0], 20)


def test_a_cycle_of_actors_triggering_each_other_is_found():
    actors = {
        "a": Actor(id="a", name="A", permitted_actions=["approvals.change"]),
        "b": Actor(id="b", name="B", permitted_actions=["approvals.put"]),
    }
    entries = [
        ScenarioActor(id="a", trigger=Trigger(kind="conditional", collection="approvals")),
        ScenarioActor(id="b", trigger=Trigger(kind="conditional", collection="approvals")),
    ]
    assert cycles(actors, entries) == [["a", "b"]]
    assert cycles(actors, entries[:1]) == []


def test_every_actor_acting_through_the_world_is_in_the_call_s_actor_summary():
    listed = {"manager": Actor(id="manager", name="Mia"), "auditor": Actor(id="auditor", name="Ada")}
    rows = world_summary(
        [
            {"actor": "manager", "play": "live", "action": "crm.approve", "outcome": "committed"},
            {"actor": "auditor", "play": "problem", "problem": "no_world_door", "why": "no world door"},
            {"actor": "nearby", "play": "live", "action": "x", "outcome": "committed"},
        ],
        listed,
        speaking={"nearby"},
    )
    assert [(one["actor"], one["triggered"], one["delivered"], one.get("problem")) for one in rows] == [
        ("manager", True, 1, None),
        ("auditor", False, 0, "no_world_door"),
    ]


def test_a_decision_is_a_line_an_action_both_or_a_wait():
    both = read_decision('{"line": "Approved.", "action": "crm.approve", "arguments": {"deal": 7}, "why": "ok"}')
    assert (both.line, both.action, both.arguments) == ("Approved.", "crm.approve", {"deal": 7})
    assert read_decision('{"line": null, "action": null}').waits
    assert read_decision("Just a line.").line == "Just a line."


def test_actor_model_calls_are_metered_into_the_simulator_usage():
    from types import SimpleNamespace

    from fi.alk.harness.actor_meter import ActorMeter
    from fi.alk.harness.run.conversation import Transcript, _with_actor_usage

    meter = ActorMeter()
    meter.add("manager", "flash", SimpleNamespace(tokens_in=10, tokens_out=4, cost_usd=0.01))
    transcript = Transcript(simulator_input_tokens=100, simulator_output_tokens=50)
    _with_actor_usage(transcript, SimpleNamespace(meter=meter))
    assert (transcript.simulator_input_tokens, transcript.simulator_output_tokens) == (110, 54)
    assert meter.rows()[0]["actor"] == "manager"


def test_an_approval_line_reads_request_decision_and_the_agent_s_writes_around_it_from_the_log():
    from fi.alk.harness.call_runner import approvals

    log = [
        {"actor_id": "agent", "name": "request_discount", "action_id": "a1", "at": 1.0, "outcome": "committed"},
        {"actor_id": "agent", "name": "apply_discount", "action_id": "a2", "at": 2.0, "outcome": "refused"},
        {"actor_id": "manager", "name": "approve", "action_id": "m1", "at": 3.0, "outcome": "committed",
         "parent_event_id": "a1"},
        {"actor_id": "agent", "name": "apply_discount", "action_id": "a3", "at": 4.0, "outcome": "committed"},
    ]
    [line] = approvals(log)
    assert line["request"]["action"] == "request_discount" and line["decision"]["by"] == "manager"
    assert line["before_decision"] == [{"action": "apply_discount", "outcome": "refused"}]
    assert line["after_decision"] == [{"action": "apply_discount", "outcome": "committed"}]
    assert line["acted_before_decision"] is False


def test_a_world_tool_that_asks_who_acts_gets_the_actor_s_identity_there():
    sent: list = []
    actor = Actor(id="priya", name="Priya", permitted_actions=["slack.post"], identities={"slack": "U-priya"})
    dispatcher = ActorDispatcher(
        {"priya": actor},
        [ScenarioActor.model_validate({"id": "priya", "actions": [{"action": "slack.post", "arguments": {"text": "hi"}}]})],
        act=HttpActs("http://p", "t", 5, _sent_to(sent)), trial_id="t", read_log=list, seed="t",
        names_actor={"slack.post"},
    )
    asyncio.run(dispatcher.tick())
    assert sent[0][2] == {"text": "hi", "actor_id": "U-priya"}


def test_a_session_whose_actor_record_changed_is_written_again_before_sealing(tmp_path):
    import json

    from fi.alk.harness.actors import save_actors, session_fingerprint
    from fi.alk.harness.pre_run import refresh_stale

    priya = Actor(id="priya", name="Priya", play="live", permitted_actions=["slack.post"])
    save_actors(tmp_path, ActorList(actors=[priya]))
    session = PreRunSession(actors=["priya"], where={"action": "slack.post", "arguments": {"channel": "#leads"}},
                            about="Acme wants 20%")
    fresh = session.model_copy(update={"thread": [{"actor": "priya", "action": "slack.post", "arguments": {"text": "old"}}],
                                       "fingerprint": session_fingerprint(session, {"priya": priya})})
    folder = tmp_path / "scenarios" / "acme"
    folder.mkdir(parents=True)
    (folder / "scenario.json").write_text(json.dumps({"name": "acme", "pre_run": [fresh.model_dump(mode="json")]}))
    written = []

    async def write(brief, actor):
        written.append(actor.id)
        return '{"text": "new"}' if len(written) == 1 else '{"text": null}'

    assert asyncio.run(refresh_stale(tmp_path, write)) == []
    # Priya's record changes: her session is stale and is written again from her new record.
    save_actors(tmp_path, ActorList(actors=[priya.model_copy(update={"goal": "Hold the price"})]))
    assert asyncio.run(refresh_stale(tmp_path, write)) == ["acme"]
    sealed = json.loads((folder / "scenario.json").read_text())["pre_run"][0]
    assert [one["arguments"]["text"] for one in sealed["thread"]] == ["new"]
    assert sealed["fingerprint"] != fresh.fingerprint


def test_a_speaking_actor_s_decided_action_is_sent_through_the_door_by_the_run_never_by_the_voice_engine(tmp_path):
    import json

    from fi.alk.harness.call_runner import _reported_actions
    from fi.simulate.simulation.engines.livekit import _action_reporter

    handed = tmp_path / "actor-actions.jsonl"
    report = _action_reporter(str(handed))
    report({"actor": "manager", "action": "crm.approve", "arguments": {"deal": 7}, "why": "within my limit"})
    sent: list = []
    manager = Actor(id="manager", name="Mia", channel="audio_handoff", role="manager", permitted_actions=["crm.approve"])
    dispatcher = ActorDispatcher(
        {"manager": manager}, [ScenarioActor(id="manager")], act=HttpActs("http://p", "tok", 5, _sent_to(sent)),
        trial_id="t", read_log=list, seed="t", reported=_reported_actions(handed),
    )
    asyncio.run(dispatcher.tick())
    asyncio.run(dispatcher.tick())
    # Sent once, under the actor's id and this trial's token, by the harness side.
    assert [(url, h["X-ALK-Actor"], h["X-ALK-Actor-Token"], a) for url, h, a in sent] == [
        ("http://p/crm/approve", "manager", "tok", {"deal": 7})
    ]
    assert dispatcher.fired[-1]["channel"] == "voice" and json.loads(handed.read_text().splitlines()[0])["actor"] == "manager"


def test_a_voice_actor_with_actions_decides_its_line_and_its_action_in_one_turn(monkeypatch):
    from fi.simulate.simulation.engines import call_actor_voice

    reported: list = []

    async def complete(model, context):
        return '{"line": "Approved, go ahead.", "action": "crm.approve", "arguments": {"deal": 7}, "why": "ok"}'

    async def voiced(tts, text):
        return None

    monkeypatch.setattr(call_actor_voice, "complete_text", complete)
    monkeypatch.setattr(call_actor_voice, "voiced", voiced)
    voice = call_actor_voice.CallActorVoice(
        actor={"id": "manager", "name": "Mia", "channel": "audio_handoff", "permitted_actions": ["crm.approve"]},
        model=None, tts=None, situation="", language="", report_action=reported.append,
    )
    text, audio, _done = asyncio.run(voice.line(["The agent: May I apply 12%?"]))
    assert text == "Approved, go ahead." and audio is None
    assert reported[0]["action"] == "crm.approve" and reported[0]["arguments"] == {"deal": 7}


def test_a_built_environment_lists_the_caller_once_and_an_empty_caller_changes_no_scenario(tmp_path):
    from fi.alk.harness.actors import CALLER, ensure_caller, load_actors, with_caller_actor

    assert ensure_caller(tmp_path) is True
    assert ensure_caller(tmp_path) is False
    assert [one.id for one in load_actors(tmp_path).actors] == [CALLER]
    doc = {"instruction": "Move my order.", "persona": {"name": "Asha"}}
    assert with_caller_actor(doc, tmp_path) == doc
