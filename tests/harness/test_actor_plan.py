"""Actors as the plan defines them: one activation, fallbacks, limits as settings, pre-run sessions, the record."""

from __future__ import annotations

import asyncio
import json

import pytest

from fi.alk.harness.actor_dispatch import ActorDispatcher, world_summary

from tests.harness._door_acts import door_acts
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
    [
        ("t:priya:h1", True),
        ("t:hr_manager:0", False),
        ("t:hr_manager:l2", False),
        ("t:h:h12", True),
        (None, False),
    ],
)
def test_only_a_key_ending_in_h_and_a_number_is_a_pre_run_row(key, pre_run):
    # An actor whose id starts with "h" (hr_manager) is never read as history.
    assert is_pre_run(key) is pre_run


def test_hr_manager_s_actions_stay_in_the_trial_s_changes():
    from fi.alk.harness.call_runner import trial_state_changes

    log = [
        {
            "actor_id": "hr_manager",
            "name": "approve",
            "outcome": "committed",
            "idempotency_key": "t:hr_manager:0",
        }
    ]
    changes = trial_state_changes(
        log, {"hr_manager": Actor(id="hr_manager", name="Hana")}
    )
    assert [one["who"] for one in changes] == ["agent", "hr_manager"]
    # The agent did nothing: "no change" is its result, shown, never left out.
    assert changes[0]["no_change"] is True and changes[0]["attempted"] == 0


def test_conditional_is_due_once_a_matching_record_exists_on_every_runtime():
    # A record of the collection that does not match is not enough.
    rows: list[dict] = [{"status": "approved", "id": 0}]
    trigger = {
        "kind": "conditional",
        "collection": "approvals",
        "where": {"status": "pending"},
    }
    for turns in (None, 3):
        moment = Moment(agent_turns=turns, records=lambda collection: list(rows))
        assert not due(trigger, moment, seed="s", actor_id="a")
    rows.append({"status": "pending", "id": 1})
    for turns in (None, 3):
        assert due(
            trigger,
            Moment(agent_turns=turns, records=lambda collection: list(rows)),
            seed="s",
            actor_id="a",
        )


def test_always_is_at_once_beside_the_call_and_after_the_first_turn_on_it():
    assert due({"kind": "always"}, Moment(agent_turns=None), seed="s", actor_id="a")
    assert not due({"kind": "always"}, Moment(agent_turns=0), seed="s", actor_id="a")
    assert due({"kind": "always"}, Moment(agent_turns=1), seed="s", actor_id="a")


def test_a_fallback_may_be_one_of_the_actor_s_own_permitted_actions():
    ActorList(
        actors=[
            Actor(
                id="manager",
                name="Mia",
                permitted_actions=["crm.reject"],
                activation=Activation(timeout_seconds=30, fallback="crm.reject"),
            )
        ]
    )
    with pytest.raises(ValueError):
        ActorList(
            actors=[
                Actor(
                    id="manager",
                    name="Mia",
                    activation=Activation(fallback="crm.reject"),
                )
            ]
        )


def test_a_permitted_action_fallback_is_sent_under_the_actor_and_labelled_as_the_timeout_s():
    sent: list = []
    clock = _Clock()
    manager = Actor(
        id="manager",
        name="Mia",
        permitted_actions=["crm.reject"],
        identities={"crm": "mia"},
        activation=Activation(
            trigger=Trigger(kind="contacted"),
            timeout_seconds=30,
            fallback="crm.reject",
            fallback_arguments={"deal_id": 7, "reason": "no answer"},
        ),
    )
    log = [
        {
            "actor_id": "agent",
            "name": "request",
            "action_id": "a1",
            "arguments": {"to": "mia"},
        }
    ]
    dispatcher = ActorDispatcher(
        {"manager": manager},
        [ScenarioActor(id="manager")],
        act=door_acts(_sent_to(sent)),
        trial_id="t",
        read_log=lambda: log,
        seed="t",
        clock=clock,
    )

    async def run():
        await dispatcher.tick()
        clock.now = 31
        await dispatcher.tick()

    asyncio.run(run())
    assert [(one["play"], one.get("outcome")) for one in dispatcher.fired] == [
        ("waiting", None),
        ("timeout", "fallback_action"),
    ]
    assert (
        sent[0][0] == "http://p/crm/reject" and sent[0][1]["X-ALK-Parent-Event"] == "a1"
    )
    # The fallback is taken with the arguments it was given, so a decline writes its record.
    assert sent[0][2] == {"deal_id": 7, "reason": "no answer"}


def test_an_open_wait_and_an_actor_that_never_came_in_are_in_the_record():
    clock = _Clock()
    actors = {
        "manager": Actor(
            id="manager",
            name="Mia",
            identities={"crm": "mia"},
            activation=Activation(
                trigger=Trigger(kind="contacted"), timeout_seconds=30
            ),
        ),
        "auditor": Actor(
            id="auditor",
            name="Ada",
            activation=Activation(trigger=Trigger(kind="on_tool", tool="close")),
        ),
    }
    log = [
        {
            "actor_id": "agent",
            "name": "request",
            "action_id": "a1",
            "arguments": {"to": "mia"},
        }
    ]
    dispatcher = ActorDispatcher(
        actors,
        [ScenarioActor(id="manager"), ScenarioActor(id="auditor")],
        act=door_acts(_sent_to([])),
        trial_id="t",
        read_log=lambda: log,
        seed="t",
        clock=clock,
    )
    asyncio.run(dispatcher.tick())
    record = dispatcher.record()
    assert {
        "actor": "manager",
        "play": "timeout",
        "outcome": "unresolved_at_end",
        "parent": "a1",
    } in record
    # A trigger that never came is recorded as not triggered, never as the scenario's problem.
    assert any(
        one["actor"] == "auditor" and one.get("play") == "not_triggered"
        for one in record
    )
    assert not any(one.get("play") == "problem" for one in record)


def test_pre_run_sessions_replay_in_order_and_a_reply_takes_the_id_the_world_gave_its_message():
    sent: list = []
    actors = {
        "priya": Actor(id="priya", name="Priya", permitted_actions=["slack.post"]),
        "ravi": Actor(
            id="ravi", name="Ravi", permitted_actions=["slack.post", "slack.reply"]
        ),
    }
    sessions = [
        PreRunSession.model_validate(
            {
                "actors": ["priya", "ravi"],
                "thread": [
                    {
                        "actor": "priya",
                        "action": "slack.post",
                        "arguments": {"channel": "#leads", "text": "20% for Acme?"},
                    },
                    {
                        "actor": "ravi",
                        "action": "slack.reply",
                        "arguments": {"text": "Too much."},
                        "refers": {"thread_ts": {"message": 1, "field": "ts"}},
                    },
                ],
            }
        ),
        PreRunSession.model_validate(
            {
                "actors": ["ravi"],
                "thread": [
                    {
                        "actor": "ravi",
                        "action": "slack.post",
                        "arguments": {"channel": "#eng"},
                    }
                ],
            }
        ),
    ]
    dispatcher = ActorDispatcher(
        actors,
        [],
        act=door_acts(_sent_to(sent, lambda n: {"ts": f"ts-{n}"})),
        trial_id="t",
        read_log=list,
        seed="t",
        pre_run=sessions,
    )
    asyncio.run(dispatcher.replay())
    assert [(h["X-ALK-Actor"], h["Idempotency-Key"]) for _, h, _ in sent] == [
        ("priya", "t:priya:h1"),
        ("ravi", "t:ravi:h2"),
        ("ravi", "t:ravi:h3"),
    ]
    assert sent[1][2] == {"text": "Too much.", "thread_ts": "ts-1"}
    assert all(one["play"] == "history" for one in dispatcher.fired)


def test_an_old_scenario_s_typed_history_reads_as_one_pre_run_session():
    from fi.alk.harness.scenario import pre_run_of

    doc = pre_run_of(
        {
            "history": [
                {"actor": "priya", "action": "slack.post", "arguments": {"text": "hi"}}
            ]
        }
    )
    assert "history" not in doc
    assert doc["pre_run"][0]["thread"][0]["actor"] == "priya"


def test_limits_are_settings_with_channel_defaults_and_a_scenario_override():
    voice = Limits().resolved("voice")
    chat = Limits().resolved("chat")
    assert (voice["max_active"], voice["actor_seconds_per_call"]) == (2, 60.0)
    assert (chat["max_active"], chat["caller_takes_back_after_turns"]) == (None, None)
    assert chat["max_messages"] == 20
    merged = Limits(max_active=3).merged(Limits(max_active=None)).resolved("voice")
    assert merged["max_active"] is None


def test_the_voice_cast_takes_no_limit_when_the_setting_says_none():
    from fi.simulate.simulation.call_actors import Cast

    cast = Cast(
        [{"id": "a", "name": "A", "trigger": {"kind": "always"}}],
        "s",
        limits={
            "actor_seconds_per_call": None,
            "max_active": None,
            "caller_takes_back_after_turns": None,
        },
    )
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
        ScenarioActor(
            id="a", trigger=Trigger(kind="conditional", collection="approvals")
        ),
        ScenarioActor(
            id="b", trigger=Trigger(kind="conditional", collection="approvals")
        ),
    ]
    assert cycles(actors, entries) == [["a", "b"]]
    # Alone, `a` waits for a record only it may write.
    assert cycles(actors, entries[:1]) == [["a"]]


def test_an_actor_waiting_for_a_record_only_it_may_write_is_a_cycle_of_one():
    waits = ScenarioActor(id="approver", trigger=Trigger(kind="conditional", collection="approvals"))
    alone = {"approver": Actor(id="approver", name="Ana", permitted_actions=["approvals.put"])}
    assert cycles(alone, [waits]) == [["approver"]]
    # Another actor who may write it, and comes in on its own, brings it in.
    shared = {**alone, "clerk": Actor(id="clerk", name="Cal", permitted_actions=["approvals.put"])}
    assert cycles(shared, [waits, ScenarioActor(id="clerk")]) == []
    # One that may not write it waits for the agent or the seed, which this check does not count.
    assert cycles({"approver": Actor(id="approver", name="Ana")}, [waits]) == []


def test_a_scenario_whose_actor_waits_for_a_record_only_it_may_write_is_refused_with_why(tmp_path):
    from fi.alk.harness.actors import save_actors
    from fi.alk.harness.scenario import Scenario
    from fi.alk.harness.scenario_tools import _unlisted_actors

    save_actors(tmp_path, ActorList(actors=[Actor(id="approver", name="Ana", permitted_actions=["approvals.put"])]))
    scenario = Scenario(
        name="s",
        instruction="x",
        actors=[{"id": "approver", "trigger": {"kind": "conditional", "collection": "approvals"}}],
    )
    assert any(
        "approver waits for a record only it can write" in one for one in _unlisted_actors(scenario, tmp_path)
    )


def test_every_actor_acting_through_the_world_is_in_the_call_s_actor_summary():
    listed = {
        "manager": Actor(id="manager", name="Mia"),
        "auditor": Actor(id="auditor", name="Ada"),
    }
    rows = world_summary(
        [
            {
                "actor": "manager",
                "play": "live",
                "action": "crm.approve",
                "outcome": "committed",
            },
            {
                "actor": "auditor",
                "play": "problem",
                "problem": "no_world_door",
                "why": "no world door",
            },
            {"actor": "nearby", "play": "live", "action": "x", "outcome": "committed"},
        ],
        listed,
        speaking={"nearby"},
    )
    assert [
        (one["actor"], one["triggered"], one["delivered"], one.get("problem"))
        for one in rows
    ] == [
        ("manager", True, 1, None),
        ("auditor", False, 0, "no_world_door"),
    ]


def test_a_decision_is_a_line_an_action_both_or_a_wait():
    both = read_decision(
        '{"line": "Approved.", "action": "crm.approve", "arguments": {"deal": 7}, "why": "ok"}'
    )
    assert (both.line, both.action, both.arguments) == (
        "Approved.",
        "crm.approve",
        {"deal": 7},
    )
    assert read_decision('{"line": null, "action": null}').waits
    assert read_decision("Just a line.").line == "Just a line."


def test_actor_model_calls_are_metered_into_the_simulator_usage():
    from types import SimpleNamespace

    from fi.alk.harness.actor_meter import ActorMeter
    from fi.alk.harness.run.conversation import Transcript, _close_actors

    meter = ActorMeter()
    meter.add(
        "manager", "flash", SimpleNamespace(tokens_in=10, tokens_out=4, cost_usd=0.01)
    )
    transcript = Transcript(simulator_input_tokens=100, simulator_output_tokens=50)
    _close_actors(transcript, SimpleNamespace(meter=meter, end=lambda: None))
    assert (transcript.simulator_input_tokens, transcript.simulator_output_tokens) == (
        110,
        54,
    )
    assert meter.rows()[0]["actor"] == "manager"


def test_an_approval_line_reads_request_decision_and_the_agent_s_writes_around_it_from_the_log():
    from fi.alk.harness.call_runner import approvals

    log = [
        {
            "actor_id": "agent",
            "name": "request_discount",
            "action_id": "a1",
            "at": 1.0,
            "outcome": "committed",
        },
        {
            "actor_id": "agent",
            "name": "apply_discount",
            "action_id": "a2",
            "at": 2.0,
            "outcome": "refused",
        },
        {
            "actor_id": "manager",
            "name": "approve",
            "action_id": "m1",
            "at": 3.0,
            "outcome": "committed",
            "parent_event_id": "a1",
        },
        {
            "actor_id": "agent",
            "name": "apply_discount",
            "action_id": "a3",
            "at": 4.0,
            "outcome": "committed",
        },
    ]
    [line] = approvals(log)
    assert (
        line["request"]["action"] == "request_discount"
        and line["decision"]["by"] == "manager"
    )
    assert line["before_decision"] == [
        {"action": "apply_discount", "outcome": "refused"}
    ]
    assert line["after_decision"] == [
        {"action": "apply_discount", "outcome": "committed"}
    ]
    assert line["acted_before_decision"] is False


def test_a_world_tool_that_asks_who_acts_gets_the_actor_s_identity_there():
    sent: list = []
    actor = Actor(
        id="priya",
        name="Priya",
        permitted_actions=["slack.post"],
        identities={"slack": "U-priya"},
    )
    dispatcher = ActorDispatcher(
        {"priya": actor},
        [
            ScenarioActor.model_validate(
                {
                    "id": "priya",
                    "actions": [{"action": "slack.post", "arguments": {"text": "hi"}}],
                }
            )
        ],
        act=door_acts(_sent_to(sent)),
        trial_id="t",
        read_log=list,
        seed="t",
        names_actor={"slack.post"},
    )
    asyncio.run(dispatcher.tick())
    assert sent[0][2] == {"text": "hi", "actor_id": "U-priya"}


def test_a_world_tool_that_asks_who_acts_gets_the_actor_s_own_id_when_it_has_no_identity_there():
    sent: list = []
    actor = Actor(id="priya", name="Priya", permitted_actions=["slack.post"])
    dispatcher = ActorDispatcher(
        {"priya": actor},
        [ScenarioActor.model_validate({"id": "priya", "actions": [{"action": "slack.post", "arguments": {"text": "hi"}}]})],
        act=door_acts(_sent_to(sent)),
        trial_id="t",
        read_log=list,
        seed="t",
        names_actor={"slack.post"},
    )
    asyncio.run(dispatcher.tick())
    # Left empty, the tool would record the call as the agent's.
    assert sent[0][2] == {"text": "hi", "actor_id": "priya"}


def test_an_actor_s_identity_goes_with_its_service_action_but_never_with_a_record_change():
    sent = []
    actor = Actor(
        id="priya",
        name="Priya",
        permitted_actions=["chat.post_message", "approvals.put"],
        identities={"chat": "coworker", "approvals": "coworker"},
    )
    dispatcher = ActorDispatcher(
        {"priya": actor},
        [
            ScenarioActor.model_validate(
                {
                    "id": "priya",
                    "actions": [
                        {"action": "chat.post_message", "arguments": {"text": "hi"}},
                        {"action": "approvals.put", "arguments": {"deal_id": 7}},
                    ],
                }
            )
        ],
        act=door_acts(_sent_to(sent)),
        trial_id="t",
        read_log=list,
        seed="t",
    )
    asyncio.run(dispatcher.tick())
    # The body stays as the action gave it, so a strict service is never sent a field it does not take.
    assert [one[2] for one in sent] == [{"text": "hi"}, {"deal_id": 7}]
    assert [one[1].get("X-ALK-Actor-Identity") for one in sent] == ["coworker", None]


def test_a_session_whose_actor_record_changed_is_written_again_before_sealing(tmp_path):
    import json

    from fi.alk.harness.actors import save_actors, session_fingerprint
    from fi.alk.harness.pre_run import refresh_stale

    priya = Actor(
        id="priya", name="Priya", play="live", permitted_actions=["slack.post"]
    )
    save_actors(tmp_path, ActorList(actors=[priya]))
    session = PreRunSession(
        actors=["priya"],
        where={"action": "slack.post", "arguments": {"channel": "#leads"}},
        about="Acme wants 20%",
    )
    fresh = session.model_copy(
        update={
            "thread": [
                {"actor": "priya", "action": "slack.post", "arguments": {"text": "old"}}
            ],
            "fingerprint": session_fingerprint(session, {"priya": priya}),
        }
    )
    folder = tmp_path / "scenarios" / "acme"
    folder.mkdir(parents=True)
    (folder / "scenario.json").write_text(
        json.dumps({"name": "acme", "pre_run": [fresh.model_dump(mode="json")]})
    )
    written = []

    async def write(brief, actor):
        written.append(actor.id)
        return '{"text": "new"}' if len(written) == 1 else '{"text": null}'

    assert asyncio.run(refresh_stale(tmp_path, write)) == []
    # Priya's record changes: her session is stale and is written again from her new record.
    save_actors(
        tmp_path,
        ActorList(actors=[priya.model_copy(update={"goal": "Hold the price"})]),
    )
    assert asyncio.run(refresh_stale(tmp_path, write)) == ["acme"]
    sealed = json.loads((folder / "scenario.json").read_text())["pre_run"][0]
    assert [one["arguments"]["text"] for one in sealed["thread"]] == ["new"]
    assert sealed["fingerprint"] != fresh.fingerprint


def test_a_speaking_actor_s_decided_action_is_sent_through_the_door_by_the_run_never_by_the_voice_engine(
    tmp_path,
):
    import json

    from fi.alk.harness.call_runner import _reported_actions
    from fi.simulate.simulation.engines.livekit import _action_reporter

    handed = tmp_path / "actor-actions.jsonl"
    report = _action_reporter(str(handed))
    report(
        {
            "actor": "manager",
            "action": "crm.approve",
            "arguments": {"deal": 7},
            "why": "within my limit",
        }
    )
    sent: list = []
    manager = Actor(
        id="manager",
        name="Mia",
        channel="audio_handoff",
        role="manager",
        permitted_actions=["crm.approve"],
    )
    dispatcher = ActorDispatcher(
        {"manager": manager},
        [ScenarioActor(id="manager")],
        act=door_acts(_sent_to(sent)),
        trial_id="t",
        read_log=list,
        seed="t",
        reported=_reported_actions(handed),
    )
    asyncio.run(dispatcher.tick())
    asyncio.run(dispatcher.tick())
    # Sent once, under the actor's id, by the harness side.
    assert [(url, h["X-ALK-Actor"], a) for url, h, a in sent] == [("http://p/crm/approve", "manager", {"deal": 7})]
    assert (
        dispatcher.fired[-1]["channel"] == "voice"
        and json.loads(handed.read_text().splitlines()[0])["actor"] == "manager"
    )


def test_a_voice_actor_with_actions_decides_its_line_and_its_action_in_one_turn(
    monkeypatch,
):
    from fi.simulate.simulation.engines import call_actor_voice

    reported: list = []

    async def complete(model, context):
        return '{"line": "Approved, go ahead.", "action": "crm.approve", "arguments": {"deal": 7}, "why": "ok"}'

    async def voiced(tts, text):
        return None

    monkeypatch.setattr(call_actor_voice, "complete_text", complete)
    monkeypatch.setattr(call_actor_voice, "voiced", voiced)
    voice = call_actor_voice.CallActorVoice(
        actor={
            "id": "manager",
            "name": "Mia",
            "channel": "audio_handoff",
            "permitted_actions": ["crm.approve"],
        },
        model=None,
        tts=None,
        situation="",
        language="",
        report_action=reported.append,
    )
    text, audio, _done = asyncio.run(voice.line(["The agent: May I apply 12%?"]))
    assert text == "Approved, go ahead." and audio is None
    assert reported[0]["action"] == "crm.approve" and reported[0]["arguments"] == {
        "deal": 7
    }


def test_a_built_environment_lists_the_caller_once_and_an_empty_caller_changes_no_scenario(
    tmp_path,
):
    from fi.alk.harness.actors import (
        CALLER,
        ensure_caller,
        load_actors,
        with_caller_actor,
    )

    assert ensure_caller(tmp_path) is True
    assert ensure_caller(tmp_path) is False
    assert [one.id for one in load_actors(tmp_path).actors] == [CALLER]
    doc = {"instruction": "Move my order.", "persona": {"name": "Asha"}}
    assert with_caller_actor(doc, tmp_path) == doc


def test_a_voice_actor_with_permitted_actions_reaches_the_engine_able_to_act(tmp_path):
    from fi.alk.harness.actors import save_actors
    from fi.alk.harness.call_runner import _resolved_call_actors
    from fi.simulate.simulation.engines.call_actor_voice import CallActorVoice

    manager = Actor(
        id="manager",
        name="Mia",
        channel="audio_handoff",
        role="manager",
        play="live",
        permitted_actions=["crm.approve"],
        persona={"gender": "female"},
    )
    save_actors(tmp_path, ActorList(actors=[manager]))
    doc = {
        "instruction": "Move my order.",
        "persona": {"name": "Asha", "gender": "male"},
        "actors": [
            {
                "id": "manager",
                "instruction": "Approve if within limits.",
                "trigger": {"kind": "always"},
            }
        ],
    }
    [voice] = [
        one
        for one in _resolved_call_actors(doc, tmp_path, "deepgram", "", "run")
        if one.get("entered", True)
    ]
    assert voice["permitted_actions"] == ["crm.approve"]
    assert CallActorVoice(
        actor=voice,
        model=None,
        tts=None,
        situation="",
        language="",
        report_action=lambda one: None,
    ).acts


def test_a_voice_actor_s_own_on_tool_activation_waits_on_the_call_the_log_records(
    tmp_path,
):
    from fi.alk.harness.actors import save_actors
    from fi.alk.harness.call_runner import _resolved_call_actors

    manager = Actor(
        id="manager",
        name="Mia",
        channel="audio_handoff",
        role="manager",
        persona={"gender": "female"},
        activation={"trigger": {"kind": "on_tool", "tool": "crm.apply_discount"}},
    )
    save_actors(tmp_path, ActorList(actors=[manager]))
    (tmp_path / "contract.json").write_text(
        json.dumps(
            {
                "tool_entrypoints": [
                    {"tool": "apply_discount", "endpoint": "/crm/apply_discount"}
                ]
            }
        )
    )
    doc = {
        "instruction": "Move my order.",
        "persona": {"name": "Asha", "gender": "male"},
        "actors": [{"id": "manager", "instruction": "Approve if within limits."}],
    }
    [voice] = [
        one
        for one in _resolved_call_actors(doc, tmp_path, "deepgram", "", "run")
        if one.get("entered", True)
    ]
    # The engine counts the world action in full, never the route's last part another route can share.
    assert voice["trigger"]["tool"] == "crm.apply_discount"


def test_a_speaking_live_actor_is_decided_by_its_channel_only_never_also_by_the_dispatcher(
    tmp_path,
):
    from fi.alk.harness.actor_dispatch import build_dispatcher
    from fi.alk.harness.actors import save_actors

    decided: list = []
    manager = Actor(
        id="manager",
        name="Mia",
        channel="audio_handoff",
        role="manager",
        play="live",
        permitted_actions=["crm.approve"],
        identities={"crm": "mia"},
    )
    save_actors(tmp_path, ActorList(actors=[manager]))
    log = [
        {
            "actor_id": "agent",
            "name": "request",
            "action_id": "a1",
            "arguments": {"to": "mia"},
        }
    ]
    sent: list = []
    dispatcher = build_dispatcher(
        {"actors": [{"id": "manager"}]},
        tmp_path,
        act=door_acts(_sent_to(sent)),
        read_log=lambda: log,
        records=None,
        trial_id="t",
    )
    dispatcher.decide = lambda situation, actor: (
        decided.append(actor.id) or '{"action": "crm.approve"}'
    )
    asyncio.run(dispatcher.tick())
    assert dispatcher.live == [] and decided == [] and sent == []


@pytest.mark.parametrize(
    ("trigger_tool", "tool", "endpoint", "logged", "comes_in"),
    [
        ("crm.apply_discount", "apply_discount", "/crm/apply_discount", "crm.apply_discount", True),
        ("issue_refund", "issue_refund", "/refunds/create", "refunds.create", True),
        ("cancel_order", "cancel_order", "/orders/{id}/cancel", "orders.81.cancel", True),
        # Another route with the same last part is not the tool the actor waits on.
        ("crm.update", "update_deal", "/crm/update", "ticket.update", False),
    ],
    ids=["action-id", "tool-name", "templated-route", "same-last-part"],
)
def test_an_actor_s_own_on_tool_activation_comes_in_on_the_call_the_log_records(
    tmp_path, trigger_tool, tool, endpoint, logged, comes_in
):
    from fi.alk.harness.actor_dispatch import build_dispatcher
    from fi.alk.harness.actors import save_actors

    decided: list = []
    reviewer = Actor(
        id="reviewer",
        name="Rey",
        play="live",
        permitted_actions=["crm.note"],
        activation={"trigger": {"kind": "on_tool", "tool": trigger_tool}},
    )
    save_actors(tmp_path, ActorList(actors=[reviewer]))
    (tmp_path / "contract.json").write_text(
        json.dumps({"tool_entrypoints": [{"tool": tool, "endpoint": endpoint}]})
    )
    log = [
        {"actor_id": "agent", "name": logged.rsplit(".", 1)[-1], "action": logged, "action_id": "a1", "arguments": {}}
    ]
    dispatcher = build_dispatcher(
        {"actors": [{"id": "reviewer"}]},
        tmp_path,
        act=door_acts(_sent_to([])),
        read_log=lambda: log,
        records=None,
        trial_id="t",
    )
    dispatcher.decide = lambda situation, actor: decided.append(actor.id) or "{}"
    asyncio.run(dispatcher.tick())
    assert decided == (["reviewer"] if comes_in else [])


def test_one_decider_serves_every_runtime_on_the_actor_s_model_and_meters_each_call(
    monkeypatch,
):
    from types import SimpleNamespace

    from fi.alk.harness import session
    from fi.alk.harness.actor_meter import ActorMeter
    from fi.alk.harness.actor_model import decider

    asked: list = []

    class _Stage:
        def __init__(self, spec, name):
            asked.append(spec.model)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def say(self, text):
            return SimpleNamespace(text="ok", tokens_in=3, tokens_out=2, cost_usd=0.0)

    monkeypatch.setattr(session, "Stage", _Stage)
    meter = ActorMeter()
    decide = decider(meter, "env-model")
    assert asyncio.run(decide("brief", "mia", "own-model")) == "ok"
    asyncio.run(decide("brief", "sam", None))
    assert asked == ["own-model", "env-model"]
    assert [(one["actor"], one["model"]) for one in meter.rows()] == [
        ("mia", "own-model"),
        ("sam", "env-model"),
    ]


def test_an_actor_s_model_is_routed_like_the_harness_s_own_and_a_failed_call_is_never_its_line(monkeypatch):
    from types import SimpleNamespace

    from fi.alk.harness import session
    from fi.alk.harness.actor_model import ActorModelFailed, decider

    asked: list = []
    answers = iter(
        [
            SimpleNamespace(text="Approved at 18%.", error="", tokens_in=1, tokens_out=1, cost_usd=0.0),
            SimpleNamespace(text="Failed to authenticate. API Error: 403", error="api_error: 403", tokens_in=0, tokens_out=0, cost_usd=0.0),
        ]
    )

    class _Stage:
        def __init__(self, spec, name):
            asked.append(spec.model)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def say(self, text):
            return next(answers)

    monkeypatch.setattr(session, "Stage", _Stage)
    monkeypatch.setenv("ALK_HARNESS_MODEL", "vertex_ai/gemini-3.7-flash")
    decide = decider(None, "gemini-3.7-flash")
    assert asyncio.run(decide("brief", "mia", "gemini-3.8-flash")) == "Approved at 18%."
    with pytest.raises(ActorModelFailed):
        asyncio.run(decide("brief", "mia", None))
    assert asked == ["vertex_ai/gemini-3.8-flash", "vertex_ai/gemini-3.7-flash"]


def test_a_cycle_another_actor_breaks_is_no_cycle_whichever_actor_ids_sort_first():
    def waits_on(collection):
        return Trigger(kind="conditional", collection=collection)

    actors = {
        # The manager comes in once an approval exists; two actors may write one.
        "m": Actor(id="m", name="M", permitted_actions=["deals.change"]),
        # This one waits on a deal only the manager writes: on its own, a cycle with the manager.
        "a": Actor(id="a", name="A", permitted_actions=["approvals.change"]),
        # This one waits on an invoice someone with no trigger of their own writes, so it comes in, and so does m.
        "b": Actor(id="b", name="B", permitted_actions=["approvals.put"]),
        "x": Actor(id="x", name="X", permitted_actions=["invoices.put"]),
    }
    entries = [
        ScenarioActor(id="m", trigger=waits_on("approvals")),
        ScenarioActor(id="a", trigger=waits_on("deals")),
        ScenarioActor(id="b", trigger=waits_on("invoices")),
        ScenarioActor(id="x"),
    ]
    assert cycles(actors, entries) == []
    # Without the actor that breaks it, the manager and `a` wait on each other.
    assert cycles(actors, [one for one in entries if one.id != "b"]) == [["a", "m"]]


def test_actors_triggered_by_the_agent_s_calls_never_form_a_cycle_whatever_they_may_do():
    actors = {
        "rep": Actor(id="rep", name="R", permitted_actions=["crm.request"]),
        "mgr": Actor(id="mgr", name="M", permitted_actions=["crm.approve"]),
    }
    entries = [
        ScenarioActor(id="rep", trigger=Trigger(kind="on_tool", tool="crm.approve")),
        ScenarioActor(id="mgr", trigger=Trigger(kind="on_tool", tool="crm.request")),
    ]
    assert cycles(actors, entries) == []


def test_a_per_trial_session_is_written_inside_each_trial_and_each_trial_keeps_its_own_thread():
    actors = {
        "priya": Actor(
            id="priya", name="Priya", play="live", permitted_actions=["slack.post"]
        )
    }
    session = PreRunSession(
        actors=["priya"],
        where={"action": "slack.post", "arguments": {"channel": "#leads"}},
        about="Acme wants 20%",
        per_trial=True,
    )
    threads = []
    for trial, said in (("t1", "Twenty?"), ("t2", "Too much.")):
        sent: list = []
        turns = iter([json.dumps({"text": said}), json.dumps({"text": None})])

        async def write(brief, actor, turns=turns):
            return next(turns)

        dispatcher = ActorDispatcher(
            actors,
            [],
            act=door_acts(_sent_to(sent)),
            trial_id=trial,
            read_log=list,
            seed=trial,
            pre_run=[session],
            write_session=write,
            session_briefs={"priya": "You are Priya."},
        )
        asyncio.run(dispatcher.replay())
        threads.append(
            [
                one["arguments"]["text"]
                for one in dispatcher.fired
                if one["play"] == "history"
            ]
        )
        assert sent[0][1]["Idempotency-Key"] == f"{trial}:priya:h1"
    assert threads == [["Twenty?"], ["Too much."]]


@pytest.mark.parametrize(
    "fallback, line, acted",
    [
        ("silent", None, []),
        ("decline", "Mia did not answer: declined", []),
        ("crm.reject", None, ["crm.reject"]),
    ],
)
def test_a_chat_actor_that_does_not_answer_in_time_gets_its_fallback(
    fallback, line, acted
):
    from fi.alk.harness.run import conversation as conv

    clock = _Clock()
    sent: list = []

    async def act(actor_id, action, arguments, *, key, parent, **kwargs):
        sent.append(action)
        return {"outcome": "committed"}

    cast = conv.ChatActors(
        [
            {
                "id": "mia",
                "name": "Mia",
                "trigger": {"kind": "always"},
                "brief": "You are Mia.",
                "timeout_seconds": 30,
                "fallback": fallback,
                "permitted_actions": ["crm.reject"],
            }
        ],
        seed="t",
        model=None,
        act=act,
        clock=clock,
    )

    async def wait(actor, brief):
        return '{"line": null, "action": null, "why": "thinking"}'

    cast._write = wait

    async def turns():
        first = await cast.due_lines(
            agent_turns=1, last_text="", tools_called=[], conversation=[]
        )
        clock.now = 31
        return first, await cast.due_lines(
            agent_turns=2, last_text="", tools_called=[], conversation=[]
        )

    first, second = asyncio.run(turns())
    assert first == []
    assert [text for _, text in second] == ([line] if line else [])
    assert sent == acted
    assert any(
        one["kind"] == "timeout" and one["fallback"] == fallback for one in cast.events
    )


def test_a_voice_actor_that_does_not_answer_in_time_is_recorded_with_its_fallback_never_voiced():
    from fi.simulate.simulation.call_actors import Cast

    clock = _Clock()
    actors = [
        {
            "id": "mia",
            "name": "Mia",
            "trigger": {"kind": "always"},
            "timeout_seconds": 30,
            "fallback": "decline",
        },
        {
            "id": "dev",
            "name": "Dev",
            "trigger": {"kind": "on_keyword", "words": ["never"]},
        },
        {
            "id": "ana",
            "name": "Ana",
            "trigger": {"kind": "always"},
            "timeout_seconds": 30,
            "fallback": "dev",
        },
    ]
    cast = Cast(
        actors,
        "s",
        clock,
        limits={
            "max_active": None,
            "caller_takes_back_after_turns": None,
            "actor_seconds_per_call": None,
        },
    )
    cast.entering(Moment(agent_turns=1))
    clock.now = 31
    expired = cast.fallbacks()
    assert [(actor["id"], outcome) for actor, _, outcome in expired] == [
        ("mia", "declined"),
        ("ana", "answered_by"),
    ]
    declined = next(
        one
        for one in cast.events
        if one.get("actor") == "mia" and one.get("kind") == "timeout"
    )
    assert declined["text"] == "Mia did not answer: declined"
    # The actor named as the fallback comes in next, whatever its own trigger.
    assert [one["id"] for one in cast.entering(Moment(agent_turns=2))] == ["dev"]
    timeout = next(one for one in cast.events if one.get("actor") == "ana" and one.get("kind") == "timeout")
    # Ana's timeout reads answered only once Dev has spoken.
    assert timeout["outcome"] == "unresolved"
    cast.delivered(actors[1], "I can approve that.", 2, 3.0, 4.0)
    assert (timeout["outcome"], timeout["reason"]) == ("answered_by", "no answer in time: answered_by")


@pytest.mark.parametrize("blocked", ["line_limit", "call_limit", "already_left", "not_on_the_call"])
def test_a_voice_stand_in_that_cannot_speak_never_reads_as_having_answered(blocked):
    from fi.simulate.simulation.call_actors import Cast

    clock = _Clock()
    actors = [
        {"id": "ana", "name": "Ana", "trigger": {"kind": "always"}, "timeout_seconds": 30, "fallback": "dev"},
        {"id": "dev", "name": "Dev", "trigger": {"kind": "on_keyword", "words": ["never"]},
         **({"max_lines": 0} if blocked == "line_limit" else {})},
    ]
    cast = Cast(
        actors,
        "s",
        clock,
        limits={
            "max_active": None,
            "caller_takes_back_after_turns": 0 if blocked == "call_limit" else None,
            "actor_seconds_per_call": None,
        },
    )
    cast.entering(Moment(agent_turns=1))
    if blocked == "already_left":
        cast._state["dev"]["done"] = True
    if blocked == "not_on_the_call":
        actors[0]["fallback"] = "ghost"
    clock.now = 31
    cast.fallbacks()
    timeout = next(one for one in cast.events if one.get("actor") == "ana" and one.get("kind") == "timeout")
    assert timeout["outcome"] == ("unresolved" if blocked in ("already_left", "not_on_the_call") else "limit_blocked")


def test_a_voice_stand_in_stopped_by_its_line_limit_after_it_came_in_reads_limit_blocked():
    from fi.simulate.simulation.call_actors import Cast

    clock = _Clock()
    actors = [
        {"id": "ana", "name": "Ana", "trigger": {"kind": "always"}, "timeout_seconds": 30, "fallback": "dev"},
        {"id": "dev", "name": "Dev", "trigger": {"kind": "on_keyword", "words": ["never"]}, "max_lines": 1},
    ]
    limits = {"max_active": None, "caller_takes_back_after_turns": None, "actor_seconds_per_call": None}
    cast = Cast(actors, "s", clock, limits=limits)
    cast.entering(Moment(agent_turns=1))
    clock.now = 31
    cast.fallbacks()
    [dev] = cast.entering(Moment(agent_turns=2))
    cast._state["dev"]["lines"] = 1
    assert not cast.may_speak(dev, 3)
    timeout = next(one for one in cast.events if one.get("actor") == "ana" and one.get("kind") == "timeout")
    assert timeout["outcome"] == "limit_blocked"


def test_a_voice_actor_that_answered_in_time_has_no_fallback():
    from fi.simulate.simulation.call_actors import Cast

    clock = _Clock()
    actor = {
        "id": "mia",
        "name": "Mia",
        "trigger": {"kind": "always"},
        "timeout_seconds": 30,
        "fallback": "decline",
    }
    cast = Cast(
        [actor],
        "s",
        clock,
        limits={
            "max_active": None,
            "caller_takes_back_after_turns": None,
            "actor_seconds_per_call": None,
        },
    )
    cast.entering(Moment(agent_turns=1))
    cast.delivered(actor, "Approved.", 1, 1.0, 2.0)
    clock.now = 31
    assert cast.fallbacks() == []


def test_a_scenario_never_lists_the_caller_among_its_actors(tmp_path):
    from fi.alk.harness.actors import ensure_caller
    from fi.alk.harness.scenario import Scenario
    from fi.alk.harness.scenario_tools import _unlisted_actors

    ensure_caller(tmp_path)
    scenario = Scenario(name="s", instruction="x", actors=[{"id": "caller"}])
    assert any(
        "the caller is the person" in one
        for one in _unlisted_actors(scenario, tmp_path)
    )


def test_a_pre_run_session_with_no_place_is_refused_with_its_fix_before_any_model_is_asked(
    tmp_path,
):
    from fi.alk.harness.actors import save_actors
    from fi.alk.harness.pre_run import session_problems

    save_actors(
        tmp_path,
        ActorList(
            actors=[
                Actor(
                    id="priya",
                    name="Priya",
                    play="live",
                    permitted_actions=["slack.post"],
                )
            ]
        ),
    )
    said = session_problems(
        [{"actors": ["priya"], "about": "Acme", "where": {}}], tmp_path
    )
    assert said == [
        'pre_run 1: `where` needs `action`, the world action that writes into this place, beside the arguments '
        'naming the place, such as {"action": "chat.post_message", "arguments": {"channel": "#sales"}}; these '
        "actors may write with slack.post"
    ]
    assert (
        session_problems(
            [{"actors": ["priya"], "about": "Acme", "where": {"action": "slack.post"}}],
            tmp_path,
        )
        == []
    )


@pytest.mark.parametrize("channel", ["voice", "chat"])
def test_the_sealed_limit_defaults_are_the_ones_a_run_uses(channel):
    from fi.alk.harness.actors import actor_rules

    sealed = actor_rules()["limits"][channel]
    used = Limits().resolved(channel)
    assert {key: used[key] for key in sealed} == sealed


def test_a_speaking_actor_that_names_a_model_talks_on_it_and_is_counted_as_simulator_usage():
    from fi.simulate.agent.definition import LLMConfig
    from fi.simulate.simulation.engines.livekit import actor_llm

    class Model:
        def __init__(self, config):
            self.config, self.listeners = config, []

        def on(self, event, callback):
            self.listeners.append((event, callback))

    class Collector:
        def collect(self, metrics):
            pass

    config = LLMConfig(provider="google", model="caller-model")
    collector = Collector()
    shared = object()
    own = actor_llm({"id": "mia", "model": "actor-model"}, shared, config, collector, build=Model)
    assert own.config.model == "actor-model" and own.config.provider == "google"
    assert own.listeners == [("metrics_collected", collector.collect)]
    assert actor_llm({"id": "dev"}, shared, config, collector, build=Model) is shared
    assert actor_llm({"id": "ana", "model": "caller-model"}, shared, config, collector, build=Model) is shared


def test_an_approval_that_timed_out_reads_the_fallback_as_its_decision_and_every_agent_write_after_the_request():
    from fi.alk.harness.call_runner import approvals

    log = [
        {"action_id": "a1", "actor_id": "agent", "name": "approvals.request", "outcome": "committed", "at": 1.0},
        {"action_id": "a2", "actor_id": "agent", "name": "orders.cancel", "outcome": "committed", "at": 5.0},
    ]
    decisions = [{"actor": "manager", "play": "timeout", "fallback": "decline", "outcome": "declined", "parent": "a1"}]
    (line,) = approvals(log, decisions)
    assert line["decision"] == {"by": "manager", "action": "timeout: decline", "outcome": "declined", "at": None}
    # With no time for the decision, every write after the request came before it.
    assert line["before_decision"] == [{"action": "orders.cancel", "outcome": "committed"}]
    assert line["acted_before_decision"] is True


def test_a_wait_still_open_when_the_conversation_ends_is_recorded_and_a_timeout_reads_in_the_summary():
    from fi.alk.harness.run import conversation as conv

    clock = _Clock()
    cast = conv.ChatActors(
        [
            {"id": "mia", "name": "Mia", "trigger": {"kind": "always"}, "timeout_seconds": 30, "fallback": "silent"},
            {"id": "dev", "name": "Dev", "trigger": {"kind": "always"}, "timeout_seconds": 300, "fallback": "silent"},
        ],
        seed="t", model=None, clock=clock,
    )

    async def wait(actor, brief):
        return '{"line": null, "action": null, "why": "thinking"}'

    cast._write = wait

    async def turns():
        await cast.due_lines(agent_turns=1, last_text="", tools_called=[], conversation=[])
        clock.now = 31
        await cast.due_lines(agent_turns=2, last_text="", tools_called=[], conversation=[])

    asyncio.run(turns())
    conv._close_actors(conv.Transcript(), cast)
    dropped = {row["actor"]: row["dropped"] for row in cast.summary()}
    assert dropped == {"mia": ["no answer in time: timed_out"], "dev": ["no answer in time: unresolved_at_end"]}


def test_a_voice_wait_open_at_hang_up_is_recorded_and_a_declined_request_is_a_labelled_unspoken_row():
    from fi.simulate.simulation.call_actors import Cast
    from fi.simulate.simulation.engines.call_actor_voice import with_call_actor_turns

    clock = _Clock()
    actors = [
        {"id": "mia", "name": "Mia", "trigger": {"kind": "always"}, "timeout_seconds": 30, "fallback": "decline"},
        {"id": "dev", "name": "Dev", "trigger": {"kind": "always"}, "timeout_seconds": 300, "fallback": "silent"},
    ]
    cast = Cast(actors, "s", clock, limits={"max_active": None, "caller_takes_back_after_turns": None,
                                            "actor_seconds_per_call": None})
    cast.entering(Moment(agent_turns=1))
    clock.now = 31
    cast.fallbacks()
    cast.hang_up()
    dropped = {row["actor"]: row["dropped"] for row in cast.summary()}
    assert "no answer in time: declined" in dropped["mia"]
    assert "no answer in time: unresolved_at_end" in dropped["dev"]
    rows = with_call_actor_turns([{"role": "user", "content": "hi", "created_at": 1.0}], cast.events)
    assert [(row["role"], row["content"], row.get("spoken")) for row in rows] == [
        ("user", "hi", None), ("actor", "Mia did not answer: declined", False),
    ]


def test_a_speaking_actor_on_the_caller_s_model_is_counted_by_the_session_the_engine_already_meters():
    """An actor without a model of its own talks on the session's model. Its calls count because LiveKit's activity
    forwards that model's metrics to the session, and the engine collects the session's. Either link gone, and those
    calls stop counting as simulator usage."""
    import inspect

    from livekit.agents.voice import agent_activity

    from fi.simulate.simulation.engines import livekit as engine

    assert 'self.llm.on("metrics_collected", self._on_metrics_collected)' in inspect.getsource(agent_activity)
    assert "lambda event: self._usage_collector.collect(event.metrics)" in inspect.getsource(engine)
    assert engine.actor_llm({"id": "mia"}, "shared", None, None) == "shared"


def test_the_models_an_actor_may_name_are_sealed_with_the_actor_rules():
    from fi.alk.harness.actors import ACTOR_MODELS, actor_rules

    assert actor_rules()["actor_models"] == list(ACTOR_MODELS) and ACTOR_MODELS


def test_a_conversation_actors_write_among_themselves_stops_at_twenty_messages_by_default():
    from fi.alk.harness.pre_run import generate

    actors = {
        one: Actor(id=one, name=one.title(), play="live", permitted_actions=["slack.post"])
        for one in ("priya", "dev")
    }
    session = PreRunSession(
        actors=["priya", "dev"],
        where={"action": "slack.post", "arguments": {"channel": "#leads"}},
        about="Acme wants 20%",
    )
    count = iter(range(1000))

    async def write(brief, actor):
        return json.dumps({"text": f"{actor.id} message {next(count)}"})

    written = asyncio.run(generate(session, actors, {}, write))
    assert len(written.thread) == 20


def test_an_actor_repeating_its_own_line_is_a_loop_and_ends_the_conversation():
    from fi.alk.harness.pre_run import generate

    actors = {
        one: Actor(id=one, name=one.title(), play="live", permitted_actions=["slack.post"])
        for one in ("priya", "dev")
    }
    session = PreRunSession(
        actors=["priya", "dev"],
        where={"action": "slack.post", "arguments": {"channel": "#leads"}},
        about="Acme wants 20%",
    )

    async def write(brief, actor):
        return json.dumps({"text": f"{actor.id}: can we do 20%?"})

    written = asyncio.run(generate(session, actors, {}, write))
    # Each says its line once; the second round repeats both, so no one writes and the session ends.
    assert [one.arguments["text"] for one in written.thread] == ["priya: can we do 20%?", "dev: can we do 20%?"]


def test_a_proof_starts_from_the_world_after_the_pre_run_threads_and_credits_them_to_their_actors(tmp_path):
    from types import SimpleNamespace

    from fi.alk.harness.actors import save_actors
    from fi.alk.harness.prove import _WorldActors
    from fi.alk.harness.scenario import Scenario

    save_actors(tmp_path, ActorList(actors=[Actor(id="priya", name="Priya", play="live",
                                                  permitted_actions=["slack.post", "slack.reply"])]))
    scenario = Scenario(name="acme", instruction="x", pre_run=[{
        "actors": ["priya"], "where": {"action": "slack.post", "arguments": {"channel": "#leads"}},
        "about": "Acme wants 20%",
        "thread": [
            {"actor": "priya", "action": "slack.post", "arguments": {"channel": "#leads", "text": "Acme wants 20%"}},
            {"actor": "priya", "action": "slack.reply", "arguments": {"text": "Can we?"},
             "refers": {"thread_ts": {"message": 1, "field": "ts"}}},
        ],
    }])

    class World:
        endpoints = ()

        def __init__(self):
            self.calls = []

        def call(self, name, arguments):
            self.calls.append(SimpleNamespace(name=name, arguments=arguments, ok=True,
                                              result={"ts": f"ts{len(self.calls) + 1}"}))

    world, by_actors = World(), {}
    acting = _WorldActors(scenario, tmp_path, world, by_actors)
    acting.replay_pre_run()
    assert [(one.name, one.arguments.get("thread_ts")) for one in world.calls] == [("post", None), ("reply", "ts1")]
    assert by_actors == {0: "priya", 1: "priya"}
    assert [row["actor_id"] for row in acting.events()] == ["priya", "priya"]


def test_a_pre_run_session_nobody_can_write_is_refused_with_the_fix(tmp_path):
    from fi.alk.harness.actors import save_actors
    from fi.alk.harness.pre_run import session_problems

    save_actors(
        tmp_path,
        ActorList(
            actors=[
                Actor(id="mia", name="Mia", play="mocked", permitted_actions=["chat.post_message"]),
                Actor(id="sam", name="Sam", play="live", permitted_actions=["chat.post_message"]),
            ]
        ),
    )
    place = {"action": "chat.post_message", "arguments": {"channel": "#sales"}}
    said = session_problems([{"actors": ["mia"], "about": "deal 8", "where": place}], tmp_path)
    assert said and "nobody here writes" in said[0]
    assert session_problems([{"actors": ["mia", "sam"], "about": "deal 8", "where": place}], tmp_path) == []
    # An actor that plays both writes as well as following its scheduled actions.
    save_actors(
        tmp_path,
        ActorList(actors=[Actor(id="mia", name="Mia", play="both", permitted_actions=["chat.post_message"])]),
    )
    assert session_problems([{"actors": ["mia"], "about": "deal 8", "where": place}], tmp_path) == []
    given = [{"actor": "mia", "action": "chat.post_message", "arguments": {"channel": "#sales", "text": "20% ok?"}}]
    assert session_problems([{"actors": ["mia"], "about": "deal 8", "where": place, "thread": given}], tmp_path) == []


def test_an_actor_that_only_waits_on_its_own_trigger_is_watched_until_the_agent_s_call_comes(tmp_path):
    """No scheduled action and no scenario trigger: the actor comes in on its own activation, so the trial's run must
    keep watching the log for it, not stop at once because nothing else is pending."""
    from fi.alk.harness.actor_dispatch import build_dispatcher
    from fi.alk.harness.actors import save_actors

    decided: list = []
    watcher = Actor(
        id="watcher",
        name="Priya",
        play="both",
        permitted_actions=["chat.post_message"],
        activation={"trigger": {"kind": "on_tool", "tool": "crm.apply_discount"}},
    )
    save_actors(tmp_path, ActorList(actors=[watcher]))
    (tmp_path / "contract.json").write_text(
        json.dumps({"tool_entrypoints": [{"tool": "apply_discount", "endpoint": "/crm/apply_discount"}]})
    )
    log: list = []
    dispatcher = build_dispatcher(
        {"actors": [{"id": "watcher"}]},
        tmp_path,
        act=door_acts(_sent_to([])),
        read_log=lambda: log,
        records=None,
        trial_id="t",
    )
    dispatcher.decide = lambda situation, actor: decided.append(actor.id) or "{}"

    async def trial():
        running = asyncio.create_task(dispatcher.run())
        await asyncio.sleep(0.1)
        # The agent's call arrives after the run started.
        log.append({"actor_id": "agent", "name": "apply_discount", "action_id": "a1", "arguments": {}})
        try:
            await asyncio.wait_for(running, timeout=5)
        except asyncio.TimeoutError:
            pass

    asyncio.run(trial())
    assert decided == ["watcher"]


def test_an_actor_woken_by_the_agent_s_call_sees_that_call_when_it_decides(tmp_path):
    """An actor whose own on_tool trigger fired must see the call that woke it, even when it cannot take that action
    itself; otherwise it waits for something that already happened."""
    from fi.alk.harness.actor_dispatch import build_dispatcher
    from fi.alk.harness.actors import save_actors

    situations: list = []
    watcher = Actor(
        id="watcher",
        name="Priya",
        play="both",
        permitted_actions=["chat.post_message"],
        activation={"trigger": {"kind": "on_tool", "tool": "crm.apply_discount"}},
    )
    save_actors(tmp_path, ActorList(actors=[watcher]))
    (tmp_path / "contract.json").write_text(
        json.dumps({"tool_entrypoints": [{"tool": "apply_discount", "endpoint": "/crm/apply_discount"}]})
    )
    log: list = [
        {"actor_id": "agent", "name": "get_deal", "action_id": "a0", "arguments": {"deal_id": 8}},
        {"actor_id": "agent", "name": "apply_discount", "action_id": "a1", "arguments": {"deal_id": 8, "percent": 10}},
    ]
    dispatcher = build_dispatcher(
        {"actors": [{"id": "watcher"}]},
        tmp_path,
        act=door_acts(_sent_to([])),
        read_log=lambda: log,
        records=None,
        trial_id="t",
    )
    dispatcher.decide = lambda situation, actor: situations.append(situation) or "{}"

    async def trial():
        try:
            await asyncio.wait_for(dispatcher.run(), timeout=5)
        except asyncio.TimeoutError:
            pass

    asyncio.run(trial())
    assert len(situations) == 1
    assert '"action": "apply_discount"' in situations[0]
    # Only the call it watches, not every call the agent made.
    assert '"action": "get_deal"' not in situations[0]


def test_seeing_the_watched_call_does_not_wake_an_actor_before_its_trigger_s_count(tmp_path):
    """An on_tool trigger on the second call keeps the actor out after the first, though it sees that call."""
    from fi.alk.harness.actor_dispatch import build_dispatcher
    from fi.alk.harness.actors import save_actors

    decided: list = []
    watcher = Actor(
        id="watcher",
        name="Priya",
        play="both",
        permitted_actions=["chat.post_message"],
        activation={"trigger": {"kind": "on_tool", "tool": "crm.apply_discount", "on_call": 2}},
    )
    save_actors(tmp_path, ActorList(actors=[watcher]))
    (tmp_path / "contract.json").write_text(
        json.dumps({"tool_entrypoints": [{"tool": "apply_discount", "endpoint": "/crm/apply_discount"}]})
    )
    log: list = [{"actor_id": "agent", "name": "apply_discount", "action_id": "a1", "arguments": {}}]
    dispatcher = build_dispatcher(
        {"actors": [{"id": "watcher"}]},
        tmp_path,
        act=door_acts(_sent_to([])),
        read_log=lambda: log,
        records=None,
        trial_id="t",
    )
    dispatcher.decide = lambda situation, actor: decided.append(actor.id) or "{}"

    async def trial():
        running = asyncio.create_task(dispatcher.run())
        await asyncio.sleep(0.3)
        assert decided == []
        log.append({"actor_id": "agent", "name": "apply_discount", "action_id": "a2", "arguments": {}})
        try:
            await asyncio.wait_for(running, timeout=5)
        except asyncio.TimeoutError:
            pass

    asyncio.run(trial())
    assert decided == ["watcher"]


def _watcher_dispatcher(tmp_path, log, sent):
    from fi.alk.harness.actor_dispatch import build_dispatcher
    from fi.alk.harness.actors import save_actors

    watcher = Actor(
        id="watcher",
        name="Priya",
        play="both",
        permitted_actions=["chat.post_message"],
        activation={"trigger": {"kind": "on_tool", "tool": "crm.apply_discount"}},
    )
    save_actors(tmp_path, ActorList(actors=[watcher]))
    (tmp_path / "contract.json").write_text(
        json.dumps({"tool_entrypoints": [{"tool": "apply_discount", "endpoint": "/crm/apply_discount"}]})
    )
    return build_dispatcher(
        {"actors": [{"id": "watcher"}]},
        tmp_path,
        act=door_acts(_sent_to(sent)),
        read_log=lambda: log,
        records=None,
        trial_id="t",
    )


_POST = json.dumps({"action": "chat.post_message", "arguments": {"text": "noted"}, "why": "the discount was applied"})


def test_a_live_turn_the_trial_s_end_cut_short_is_taken_when_the_trial_settles(tmp_path):
    log = [{"actor_id": "agent", "name": "apply_discount", "action_id": "a1", "arguments": {}}]
    sent: list = []
    dispatcher = _watcher_dispatcher(tmp_path, log, sent)
    started = asyncio.Event()
    calls: list = []

    async def decide(situation, actor):
        calls.append(actor.id)
        if len(calls) == 1:
            started.set()
            await asyncio.sleep(60)  # the conversation ends during this first decision
        return _POST

    dispatcher.decide = decide

    async def trial():
        running = asyncio.create_task(dispatcher.run())
        await asyncio.wait_for(started.wait(), timeout=5)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        await dispatcher.settle(seconds=5)

    asyncio.run(trial())
    assert calls == ["watcher", "watcher"]
    assert len(sent) == 1
    assert [(one.get("action"), one.get("turn")) for one in dispatcher.record() if one.get("play") == "live"] == [
        ("chat.post_message", 1)
    ]


def test_an_agent_call_just_before_the_end_still_wakes_its_actor_when_the_trial_settles(tmp_path):
    log: list = []
    sent: list = []
    dispatcher = _watcher_dispatcher(tmp_path, log, sent)
    dispatcher.decide = lambda situation, actor: _POST

    async def trial():
        running = asyncio.create_task(dispatcher.run())
        await asyncio.sleep(0.05)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        # The agent's call reached the log after the actors' last look, as the conversation ended.
        log.append({"actor_id": "agent", "name": "apply_discount", "action_id": "a1", "arguments": {}})
        await dispatcher.settle(seconds=5)

    asyncio.run(trial())
    assert len(sent) == 1


def test_a_turn_still_unfinished_when_the_settle_runs_out_is_recorded_not_lost(tmp_path):
    log = [{"actor_id": "agent", "name": "apply_discount", "action_id": "a1", "arguments": {}}]
    dispatcher = _watcher_dispatcher(tmp_path, log, [])

    async def decide(situation, actor):
        await asyncio.sleep(60)

    dispatcher.decide = decide

    async def trial():
        running = asyncio.create_task(dispatcher.run())
        await asyncio.sleep(0.3)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        await dispatcher.settle(seconds=0.3)

    asyncio.run(trial())
    rows = [one for one in dispatcher.record() if one.get("actor") == "watcher"]
    assert [(one["play"], one.get("why")) for one in rows] == [
        ("live", "the trial ended before its turn finished")
    ]


def test_a_speaking_chat_actor_s_record_fallback_is_taken_with_its_arguments_and_writes_its_record():
    from fi.alk.harness.actors import acting_fields
    from fi.alk.harness.run import conversation as conv

    actor = Actor.model_validate({
        "id": "manager", "name": "Mia", "role": "manager", "channel": "chat_message",
        "permitted_actions": ["approvals.change"],
        "activation": {"timeout_seconds": 1, "fallback": "approvals.change",
                       "fallback_arguments": {"where": {"status": "pending"}, "set": {"status": "declined"}}},
    })
    sent: list = []

    async def act(actor_id, action, arguments, **kwargs):
        sent.append((action, arguments, kwargs.get("form")))
        return {"status": 200, "outcome": "committed"}

    clock = _Clock()
    entry = {"id": "manager", "name": "Mia", "trigger": {"kind": "always"}, **acting_fields(actor)}
    cast = conv.ChatActors([entry], seed="t", model=None, act=act, clock=clock)
    cast.waits.due(entry)
    clock.now = 5
    asyncio.run(cast._fall_back())
    assert sent == [("approvals.change", {"where": {"status": "pending"}, "set": {"status": "declined"}}, "record")]
    timeout = next(one for one in cast.events if one["kind"] == "timeout")
    assert timeout["action_outcome"] == "committed"


def test_a_speaking_chat_actor_s_tool_fallback_is_taken_as_the_actor_never_as_the_agent():
    from fi.alk.harness.actors import acting_fields
    from fi.alk.harness.run import conversation as conv

    actor = Actor.model_validate({
        "id": "manager", "name": "Mia", "role": "manager", "channel": "chat_message",
        "permitted_actions": ["crm.reject_discount"],
        "activation": {"timeout_seconds": 1, "fallback": "crm.reject_discount", "fallback_arguments": {"deal_id": 8}},
    })
    sent: list = []

    async def act(actor_id, action, arguments, **kwargs):
        sent.append((action, kwargs.get("identity", "")))
        return {"status": 200, "outcome": "committed"}

    clock = _Clock()
    entry = {"id": "manager", "name": "Mia", "trigger": {"kind": "always"}, **acting_fields(actor)}
    cast = conv.ChatActors([entry], seed="t", model=None, act=act, clock=clock)
    cast.waits.due(entry)
    clock.now = 5
    asyncio.run(cast._fall_back())
    # Without an identity the world fills in its own default, and that default is the agent.
    assert sent == [("crm.reject_discount", "manager")]


def _manager_chat(history, sent, briefs):
    from fi.alk.harness.run import conversation as conv

    async def act(actor_id, action, arguments, **kwargs):
        sent.append((action, arguments))
        return {"status": 200, "outcome": "committed"}

    cast = conv.ChatActors(
        [{"id": "manager", "name": "Mia", "trigger": {"kind": "always"}, "brief": "You are Mia, the manager.",
          "permitted_actions": ["approvals.change"]}],
        seed="t", model=None, act=act, history=history,
    )

    async def write(actor, brief):
        briefs.append(brief)
        return ('{"line": "Approved.", "action": "approvals.change", "arguments": '
                '{"where": {"deal_id": 8}, "set": {"status": "approved"}}, "why": "approve"}')

    cast._write = write
    return cast


def test_a_chat_actor_decides_knowing_its_own_earlier_actions_in_the_trial():
    # Its scheduled rejection, written before the conversation, is shown to it as its own.
    rows = [{"actor_id": "manager", "name": "put", "outcome": "committed",
             "arguments": {"deal_id": 8, "status": "rejected"}}]
    sent, briefs = [], []
    cast = _manager_chat(lambda key: rows if key == "manager" else [], sent, briefs)
    asyncio.run(cast.due_lines(agent_turns=1, last_text="Is it approved?", tools_called=[], conversation=[]))
    assert '"by": "you"' in briefs[0] and '"rejected"' in briefs[0]
    assert "do not reverse a decision you already made" in briefs[0]


def test_a_chat_actor_told_no_by_the_world_hears_what_it_said():
    rows = [{"actor_id": "manager", "name": "post_message", "outcome": "refused",
             "arguments": {"channel": "sales"}, "error": '{"detail": "manager is not in sales"}'},
            {"actor_id": "manager", "name": "put", "outcome": "committed", "arguments": {}, "error": "ignored"}]
    sent, briefs = [], []
    cast = _manager_chat(lambda key: rows if key == "manager" else [], sent, briefs)
    asyncio.run(cast.due_lines(agent_turns=1, last_text="Is it approved?", tools_called=[], conversation=[]))
    assert "manager is not in sales" in briefs[0]
    assert briefs[0].count('"world_said": ') == 1


def test_a_chat_actor_told_no_corrects_its_action_once_straight_away_and_never_loops():
    from fi.alk.harness.run import conversation as conv

    def cast_for(accepts):
        rows, briefs = [], []

        async def act(actor_id, action, arguments, **kwargs):
            ok = arguments.get("channel") in accepts
            rows.append({"actor_id": actor_id, "name": action, "arguments": arguments,
                         "outcome": "committed" if ok else "refused",
                         **({} if ok else {"error": f'{{"detail": "lead is not in {arguments.get("channel")}"}}'})})
            return {"status": 200 if ok else 403, "outcome": "committed" if ok else "refused"}

        cast = conv.ChatActors(
            [{"id": "lead", "name": "Lee", "trigger": {"kind": "always"}, "permitted_actions": ["chat.post_message"]}],
            seed="t", model=None, act=act, history=lambda key: [row for row in rows if row["actor_id"] == key],
        )

        async def write(actor, brief):
            briefs.append(brief)
            channel = "#sales" if "lead is not in sales" in brief else "sales"
            return '{"action": "chat.post_message", "arguments": {"channel": "%s", "text": "approved"}, "why": "x"}' % channel

        cast._write = write
        asyncio.run(cast.due_lines(agent_turns=1, last_text="Was it approved?", tools_called=[], conversation=[]))
        return rows, briefs

    rows, briefs = cast_for({"#sales"})
    assert [(row["arguments"]["channel"], row["outcome"]) for row in rows] == [("sales", "refused"), ("#sales", "committed")]
    # Refused again, it is not asked a third time in the same turn.
    rows, briefs = cast_for(set())
    assert [row["outcome"] for row in rows] == ["refused", "refused"] and len(briefs) == 2
    assert "is what the world told you" in briefs[1] and "is what the world told you" not in briefs[0]
    assert "exactly as your brief, the conversation or the world writes it" in briefs[0]


def test_a_chat_actor_told_no_that_answers_with_words_instead_still_says_them():
    from fi.alk.harness.run import conversation as conv

    rows = []

    async def act(actor_id, action, arguments, **kwargs):
        rows.append({"actor_id": actor_id, "name": action, "arguments": arguments, "outcome": "refused",
                     "error": "not allowed"})
        return {"status": 403, "outcome": "refused"}

    cast = conv.ChatActors(
        [{"id": "lead", "name": "Lee", "trigger": {"kind": "always"}, "permitted_actions": ["chat.post_message"]}],
        seed="t", model=None, act=act, history=lambda key: list(rows),
    )
    replies = iter([
        '{"action": "chat.post_message", "arguments": {"channel": "sales"}, "why": "x"}',
        '{"line": "I could not post it, but it was approved.", "action": null, "why": "tell them"}',
    ])

    async def write(actor, brief):
        return next(replies)

    cast._write = write
    out = asyncio.run(cast.due_lines(agent_turns=1, last_text="Approved?", tools_called=[], conversation=[]))
    assert [line for _, line in out] == ["I could not post it, but it was approved."] and len(rows) == 1


def test_a_chat_actor_never_sends_an_action_it_already_committed_again():
    rows: list = []
    sent, briefs = [], []

    def history(key):
        return rows

    cast = _manager_chat(history, sent, briefs)

    async def turns():
        for turn in (1, 2, 3):
            await cast.due_lines(agent_turns=turn, last_text="Still not approved?", tools_called=[], conversation=[])
            # The door logs what the actor sent.
            rows[:] = [{"actor_id": "manager", "name": "change", "outcome": "committed", "arguments": arguments}
                       for _, arguments in sent]

    asyncio.run(turns())
    assert len(sent) == 1
    assert [one["kind"] for one in cast.events if one.get("kind") == "repeated"] == ["repeated", "repeated"]


def test_the_dispatcher_shows_a_live_actor_its_own_rows_as_its_own():
    from fi.simulate.simulation.call_actors import decision_brief

    brief = decision_brief({"name": "Mia"}, situation="", said=[], conversation=[], language="",
                           seen=['{"by": "you", "action": "put"}'], actions={}, speaks=False)
    assert "your own earlier actions" in brief
    plain = decision_brief({"name": "Mia"}, situation="", said=[], conversation=[], language="",
                           seen=['{"by": "agent", "action": "get"}'], actions={}, speaks=False)
    assert "your own earlier actions" not in plain
