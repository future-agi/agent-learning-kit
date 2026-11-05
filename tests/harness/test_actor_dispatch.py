"""Scheduled world actors: each action once, at its trigger, under the actor's identity."""

from __future__ import annotations

import asyncio

import pytest

from fi.alk.harness.actor_dispatch import ActorDispatcher as _Dispatcher
from fi.alk.harness.actor_dispatch import HttpActs
from fi.alk.harness.actors import Actor, ActorList, ScenarioActor


def ActorDispatcher(actors, cast, *, proxy_url, token, timeout, send=None, decide=None, **rest):  # noqa: N802
    """The dispatcher over the HTTP surface, as the voice runner builds it; `decide` reads only the situation."""
    wrapped = None
    if decide is not None:

        def wrapped(situation, actor):
            return decide(situation)

    return _Dispatcher(actors, cast, act=HttpActs(proxy_url, token, timeout, send), decide=wrapped, **rest)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _dispatcher(cast, log, clock=None, seed="trial-1", reads=None):
    sent = []
    actors = {
        "reviewer": Actor(
            id="reviewer", name="Robin", permitted_actions=["records.approve"]
        ),
        "coworker": Actor(id="coworker", name="Sam", identities={"slack": "@sam"}),
    }

    def send(url, headers, arguments):
        sent.append((url, headers, arguments))
        return 200

    dispatcher = ActorDispatcher(
        actors,
        [ScenarioActor.model_validate(one) for one in cast],
        trial_id="trial-1",
        proxy_url="http://proxy/",
        token="t0ken",
        read_log=lambda: (reads.append(1) if reads is not None else None) or list(log),
        seed=seed,
        timeout=60.0,
        clock=clock or _Clock(),
        send=send,
    )
    return dispatcher, sent


async def _ticks(dispatcher, count=3):
    for _ in range(count):
        await dispatcher.tick()


def test_an_actor_acts_through_the_proxy_under_its_own_id_and_this_trial_s_token():
    dispatcher, sent = _dispatcher(
        [
            {
                "id": "reviewer",
                "actions": [{"action": "records.approve", "arguments": {"item": "d1"}}],
            }
        ],
        [],
    )
    asyncio.run(dispatcher.run())
    assert sent == [
        (
            "http://proxy/records/approve",
            {
                "X-ALK-Actor": "reviewer",
                "X-ALK-Actor-Token": "t0ken",
                "Idempotency-Key": "trial-1:reviewer:0",
            },
            {"item": "d1"},
        )
    ]


def test_an_action_on_the_agent_s_tool_call_fires_once_after_that_call_and_names_it_as_the_cause():
    log: list[dict] = []
    action = {
        "action": "records.approve",
        "trigger": {"kind": "on_tool", "tool": "request_approval"},
    }
    dispatcher, sent = _dispatcher([{"id": "reviewer", "actions": [action]}], log)

    async def scenario():
        await dispatcher.tick()
        assert sent == []
        # An actor's own row never counts as the agent's call.
        log.append(
            {"name": "request_approval", "actor_id": "coworker", "action_id": "x0"}
        )
        await dispatcher.tick()
        assert sent == []
        log.append({"name": "request_approval", "actor_id": "agent", "action_id": "a1"})
        log.append({"name": "request_approval", "actor_id": "agent", "action_id": "a2"})
        await dispatcher.tick()
        await dispatcher.tick()

    asyncio.run(scenario())
    assert len(sent) == 1 and sent[0][1]["X-ALK-Parent-Event"] == "a1"


def test_an_action_after_a_delay_waits_for_it():
    clock = _Clock()
    action = {
        "action": "records.approve",
        "trigger": {"kind": "after_seconds", "seconds": 5},
    }
    dispatcher, sent = _dispatcher(
        [{"id": "reviewer", "actions": [action]}], [], clock=clock
    )

    async def scenario():
        await dispatcher.tick()
        assert sent == []
        clock.now = 5
        await dispatcher.tick()

    asyncio.run(scenario())
    assert len(sent) == 1 and "X-ALK-Parent-Event" not in sent[0][1]


def test_an_actor_comes_in_when_the_agent_reaches_one_of_its_identities():
    log = [
        {
            "name": "post_message",
            "actor_id": "agent",
            "action_id": "a1",
            "arguments": {"to": "@robin"},
        },
        {
            "name": "post_message",
            "actor_id": "agent",
            "action_id": "a2",
            "arguments": {"to": "@sam"},
        },
    ]
    action = {"action": "slack.post_message", "trigger": {"kind": "contacted"}}
    dispatcher, sent = _dispatcher([{"id": "coworker", "actions": [action]}], log)
    asyncio.run(dispatcher.run())
    assert sent[0][1]["X-ALK-Parent-Event"] == "a2"


def test_an_identity_with_any_character_is_reached():
    from fi.alk.harness.actor_dispatch import contacts
    from fi.alk.harness.actors import Actor

    actor = Actor(id="lead", name="Lead", identities={"mail": "josé@example.org"})
    assert contacts({"to": "josé@example.org", "body": "hi"}, actor)
    assert not contacts({"to": "jose@example.org"}, actor)


def test_a_chance_action_is_drawn_once_and_the_same_on_a_rerun_of_the_trial():
    action = {"action": "records.approve", "trigger": {"kind": "probability", "p": 0.5}}
    cast = [{"id": "reviewer", "actions": [action] * 20}]
    first, sent_first = _dispatcher(cast, [], seed="s")
    again, sent_again = _dispatcher(cast, [], seed="s")
    asyncio.run(_ticks(first))
    asyncio.run(_ticks(again))
    keys = [headers["Idempotency-Key"] for _, headers, _ in sent_first]
    assert keys == [headers["Idempotency-Key"] for _, headers, _ in sent_again]
    assert 0 < len(keys) < 20 and len(keys) == len(set(keys))


def test_a_scenario_naming_an_actor_the_environment_does_not_list_sends_nothing_for_it():
    dispatcher, sent = _dispatcher(
        [{"id": "ghost", "actions": [{"action": "records.approve"}]}], []
    )
    asyncio.run(dispatcher.run())
    assert sent == []


def test_the_agent_s_id_is_never_an_actor_and_ids_are_unique():
    with pytest.raises(ValueError):
        Actor(id="agent", name="x")
    with pytest.raises(ValueError):
        ActorList(actors=[Actor(id="a", name="x"), Actor(id="a", name="y")])
    with pytest.raises(ValueError):
        ActorList(actors=[Actor(id="a", name="x", on_behalf_of="nobody")])


def test_a_trigger_with_a_field_it_does_not_have_is_refused_rather_than_firing_at_the_start():
    with pytest.raises(ValueError):
        ScenarioActor.model_validate(
            {
                "id": "reviewer",
                "actions": [
                    {
                        "action": "records.approve",
                        "trigger": {"on_tool": "request_approval"},
                    }
                ],
            }
        )


def test_a_check_reads_who_did_what_from_the_world_s_action_log():
    from fi.alk.harness.world.handle import HostedWorld, ReadOnlyWorld

    class Store:
        def query(self, sql, params):
            assert "_alk_tool_trace" in sql
            return [
                {"name": "update", "ok": False, "at": 1.0},
                {
                    "name": "approve",
                    "actor_id": "coworker",
                    "outcome": "refused",
                    "ok": False,
                    "at": 2.0,
                },
                {
                    "name": "approve",
                    "actor_id": "reviewer",
                    "outcome": "committed",
                    "ok": True,
                    "at": 3.0,
                },
            ]

    world = object.__new__(HostedWorld)
    world._store = Store()
    assert [row["actor_id"] for row in world.events(outcome="committed")] == [
        "reviewer"
    ]
    assert [row["name"] for row in world.events("agent", "refused")] == ["update"]
    assert len(world.events()) == 3
    view = object.__new__(ReadOnlyWorld)
    view._ReadOnlyWorld__world = world
    assert view.events("coworker") == world.events("coworker")


def test_a_chance_action_its_draw_left_out_is_settled_at_once_and_never_waited_on():
    action = {"action": "records.approve", "trigger": {"kind": "probability", "p": 0.0}}
    dispatcher, sent = _dispatcher([{"id": "reviewer", "actions": [action]}], [])
    assert dispatcher.pending == []
    asyncio.run(asyncio.wait_for(dispatcher.run(), 1.0))
    assert sent == []


def test_the_log_is_read_only_while_an_action_waits_on_what_the_agent_does():
    reads: list[int] = []
    timed = {
        "action": "records.approve",
        "trigger": {"kind": "after_seconds", "seconds": 5},
    }
    dispatcher, _sent = _dispatcher(
        [{"id": "reviewer", "actions": [timed]}], [], reads=reads
    )
    asyncio.run(_ticks(dispatcher))
    assert reads == []
    on_tool = {
        "action": "records.approve",
        "trigger": {"kind": "on_tool", "tool": "request_approval"},
    }
    dispatcher, _sent = _dispatcher(
        [{"id": "reviewer", "actions": [on_tool]}], [], reads=reads
    )
    asyncio.run(_ticks(dispatcher, 2))
    assert reads == [1, 1]


def test_an_action_s_request_is_bounded_by_the_call_s_budget(monkeypatch):
    from fi.alk.harness import actor_dispatch

    seen = []
    monkeypatch.setattr(
        actor_dispatch.HttpActs,
        "_send",
        lambda self, url, headers, arguments: seen.append(self.timeout) or {"status": 201},
    )
    dispatcher = ActorDispatcher(
        {"reviewer": Actor(id="reviewer", name="Robin")},
        [
            ScenarioActor.model_validate(
                {"id": "reviewer", "actions": [{"action": "records.approve"}]}
            )
        ],
        trial_id="t",
        proxy_url="http://proxy",
        token="x",
        read_log=list,
        seed="s",
        timeout=42.0,
    )
    asyncio.run(dispatcher.tick())
    assert seen == [42.0]


def _live(play, decisions, log, activation=None):
    sent, asked = [], []
    manager = Actor(
        id="manager",
        name="Mia",
        identities={"slack": "@mia"},
        permitted_actions=["deals.approve"],
        play=play,
        **({"activation": activation} if activation else {}),
    )

    def send(url, headers, arguments):
        sent.append((url, headers, arguments))
        return 200

    async def decide(situation):
        asked.append(situation)
        return decisions.pop(0) if decisions else '{"action": null, "why": "nothing to do"}'

    dispatcher = ActorDispatcher(
        {"manager": manager},
        [ScenarioActor.model_validate({"id": "manager", "actions": [{"action": "deals.approve"}]})],
        trial_id="trial-1",
        proxy_url="http://proxy/",
        token="t0ken",
        read_log=lambda: list(log),
        seed="trial-1",
        timeout=60.0,
        clock=_Clock(),
        send=send,
        decide=decide,
        briefs={"manager": "You are Mia, the store manager. Approve up to 15%."},
        world_actions={"deals.approve": "Approve a pending discount"},
    )
    return dispatcher, sent, asked


_ASKED = {"action_id": "a1", "name": "slack.post", "arguments": {"to": "@mia", "text": "Approve 12% for deal 7?"}}


def test_a_live_actor_decides_its_action_when_the_agent_reaches_it_and_the_world_still_checks_it():
    log = []
    dispatcher, sent, asked = _live(
        "live", ['Sure. {"action": "deals.approve", "arguments": {"deal": 7}, "why": "12% is within my limit"}'], log
    )
    asyncio.run(_ticks(dispatcher))
    # Nothing reached it yet, so it decides nothing; a live-only actor takes no scheduled action.
    assert sent == [] and asked == []
    log.append(_ASKED)
    asyncio.run(_ticks(dispatcher))
    assert sent == [
        (
            "http://proxy/deals/approve",
            {
                "X-ALK-Actor": "manager",
                "X-ALK-Actor-Token": "t0ken",
                "Idempotency-Key": "trial-1:manager:l1",
                "X-ALK-Parent-Event": "a1",
            },
            {"deal": 7},
        )
    ]
    # It decides from its brief, what it may do, and what the agent sent it; once per request.
    assert len(asked) == 1
    assert "Approve up to 15%" in asked[0] and "deals.approve: Approve a pending discount" in asked[0]
    assert "Approve 12% for deal 7?" in asked[0]
    assert dispatcher.fired[-1]["why"] == "12% is within my limit"


def test_a_live_actor_that_decides_nothing_or_answers_badly_sends_nothing_and_says_why():
    log = [_ASKED, {**_ASKED, "action_id": "a2"}]
    dispatcher, sent, _ = _live("live", ['{"action": null, "why": "needs the deal value"}', "not json"], log)
    asyncio.run(_ticks(dispatcher))
    assert sent == []
    assert [(one["decided"], one["why"]) for one in dispatcher.fired] == [(None, "needs the deal value"), (None, None)]


def test_a_live_actor_answers_every_time_it_is_reached_with_no_limit_of_its_own():
    log = [{**_ASKED, "action_id": f"a{n}"} for n in range(6)]
    dispatcher, sent, asked = _live("live", [], log)
    asyncio.run(_ticks(dispatcher, count=8))
    assert len(asked) == 6


def test_an_actor_played_both_ways_takes_its_scheduled_action_and_answers_live():
    log = [_ASKED]
    dispatcher, sent, _ = _live(
        "both", ['{"action": "deals.approve", "arguments": {"deal": 7}, "why": "ok"}'], log
    )
    asyncio.run(_ticks(dispatcher))
    keys = [headers["Idempotency-Key"] for _, headers, _ in sent]
    assert keys == ["trial-1:manager:0", "trial-1:manager:l1"]


def test_a_mocked_actor_never_calls_a_model():
    dispatcher, sent, asked = _live("mocked", [], [_ASKED])
    asyncio.run(_ticks(dispatcher))
    assert asked == [] and [h["Idempotency-Key"] for _, h, _ in sent] == ["trial-1:manager:0"]


def test_a_live_actor_comes_in_on_its_activation_trigger_once_when_no_one_reaches_it():
    log = [{"action_id": "c1", "name": "crm.update", "arguments": {}}]
    dispatcher, sent, asked = _live(
        "live", [], log, activation={"trigger": {"kind": "on_tool", "tool": "crm.update"}}
    )
    asyncio.run(_ticks(dispatcher))
    assert len(asked) == 1


def _approver(fallback, *, answers=None, instead_play="mocked"):
    """A manager the agent reaches on Slack, waited on for 30 s; `answers` is its scheduled approval, if any."""
    clock, sent = _Clock(), []
    actors = {
        "manager": Actor(
            id="manager",
            name="Mia",
            identities={"slack": "@mia"},
            permitted_actions=["deals.approve"],
            activation={"trigger": {"kind": "contacted"}, "timeout_seconds": 30, "fallback": fallback},
        ),
        "deputy": Actor(id="deputy", name="Dev", permitted_actions=["deals.approve"], play=instead_play),
    }
    cast = [{"id": "manager", "actions": answers or []}]
    if fallback == "deputy":
        cast.append({"id": "deputy", "actions": [{"action": "deals.approve", "trigger": {"kind": "after_seconds", "seconds": 999}}]})
    dispatcher = ActorDispatcher(
        actors,
        [ScenarioActor.model_validate(one) for one in cast],
        trial_id="trial-1",
        proxy_url="http://proxy/",
        token="t0ken",
        read_log=lambda: [_ASKED],
        seed="trial-1",
        timeout=60.0,
        clock=clock,
        send=lambda url, headers, arguments: sent.append((url, headers)) or 200,
        decide=lambda situation: '{"action": "deals.approve", "why": "covering for Mia"}',
    )
    return dispatcher, clock, sent


def _timeline(dispatcher):
    return [(one["actor"], one["play"], one.get("outcome")) for one in dispatcher.fired]


async def _wait_out(dispatcher, clock, seconds):
    await dispatcher.tick()
    clock.now += seconds
    await dispatcher.tick()
    await dispatcher.tick()


@pytest.mark.parametrize(
    "fallback, outcome", [("silent", "timed_out"), ("decline", "declined")]
)
def test_an_approver_that_does_not_answer_in_time_gets_its_fallback_recorded_and_nothing_sent(fallback, outcome):
    dispatcher, clock, sent = _approver(fallback)
    asyncio.run(_wait_out(dispatcher, clock, 29))
    assert _timeline(dispatcher) == [("manager", "waiting", None)]
    asyncio.run(_wait_out(dispatcher, clock, 1))
    assert _timeline(dispatcher) == [("manager", "waiting", None), ("manager", "timeout", outcome)]
    assert dispatcher.fired[0]["parent"] == "a1" and dispatcher.fired[1]["timeout_seconds"] == 30
    assert sent == []
    assert not dispatcher.pending and dispatcher.settled == {"manager"}


def test_an_approver_that_answers_in_time_has_no_fallback():
    approve = {"action": "deals.approve", "trigger": {"kind": "contacted"}}
    dispatcher, clock, sent = _approver("decline", answers=[approve])
    asyncio.run(_wait_out(dispatcher, clock, 60))
    assert _timeline(dispatcher) == [("manager", "waiting", None), ("manager", "mocked", "committed")]
    assert len(sent) == 1


@pytest.mark.parametrize("play", ["mocked", "live"])
def test_another_actor_answers_instead_when_the_approver_times_out(play):
    dispatcher, clock, sent = _approver("deputy", instead_play=play)
    asyncio.run(_wait_out(dispatcher, clock, 30))
    assert ("manager", "timeout", "answered_by") in _timeline(dispatcher)
    assert [headers["X-ALK-Actor"] for _url, headers in sent] == ["deputy"]
    assert sent[0][1]["X-ALK-Parent-Event"] == "a1"


def _channel(names, decide):
    """Live actors who can all post in one Slack channel, in a world whose log records each post."""
    log = []
    actors = {
        name: Actor(id=name, name=name.title(), play="live", permitted_actions=["slack.post_message"])
        for name in names
    }

    def send(url, headers, arguments):
        log.append({"name": "post_message", "actor_id": headers["X-ALK-Actor"], "action_id": f"r{len(log)}",
                    "arguments": arguments, "outcome": "committed"})
        return 200

    dispatcher = ActorDispatcher(
        actors,
        [ScenarioActor(id=name) for name in names],
        trial_id="trial-1",
        proxy_url="http://proxy/",
        token="t0ken",
        read_log=lambda: list(log),
        seed="trial-1",
        timeout=60.0,
        clock=_Clock(),
        send=send,
        decide=decide,
    )
    return dispatcher, log


def test_a_live_actor_acts_on_a_message_it_can_see_from_the_agent_or_another_actor_without_being_contacted():
    seen = []

    async def decide(situation):
        seen.append(situation)
        return '{"action": null, "why": "noted"}'

    dispatcher, log = _channel(["manager", "colleague"], decide)
    log.append({"name": "post_message", "actor_id": "agent", "action_id": "a1",
                "arguments": {"channel": "#deals", "text": "Can deal 7 get 12%?"}})
    asyncio.run(_ticks(dispatcher, count=2))
    assert len(seen) == 2 and all("Can deal 7 get 12%?" in one for one in seen)
    # An action somewhere it cannot act, addressed to no one it is, is not its business.
    log.append({"name": "update_deal", "actor_id": "agent", "action_id": "a2", "arguments": {"deal": 7}})
    asyncio.run(_ticks(dispatcher, count=2))
    assert len(seen) == 2


def test_many_actors_each_answering_once_with_the_agent_idle_are_never_stopped_by_a_count():
    # K-26: ten people each posting once is a conversation, not a loop; no count stops actors at run time.
    async def decide(situation):
        return '{"action": "slack.post_message", "arguments": {"text": "agreed"}, "why": "replying"}'

    dispatcher, log = _channel(["manager", "colleague", "lead"], decide)
    log.append({"name": "post_message", "actor_id": "agent", "action_id": "a1", "arguments": {"text": "Thoughts?"}})
    asyncio.run(_ticks(dispatcher, count=10))
    actor_rows = [row for row in log if row["actor_id"] != "agent"]
    assert len(actor_rows) > 6
    assert not any(one.get("problem") == "actor_loop" for one in dispatcher.fired)


def test_a_live_actor_never_answers_its_own_action():
    async def decide(situation):
        return '{"action": "slack.post_message", "arguments": {"text": "noted"}, "why": "replying"}'

    dispatcher, log = _channel(["manager"], decide)
    log.append({"name": "post_message", "actor_id": "agent", "action_id": "a1", "arguments": {"text": "Thoughts?"}})
    asyncio.run(_ticks(dispatcher, count=5))
    assert [row["actor_id"] for row in log] == ["agent", "manager"]


def test_a_scenario_s_own_trigger_decides_when_the_world_starts_waiting_on_an_actor():
    dispatcher, clock, sent = _approver("silent")
    assert dispatcher.triggers["manager"].kind == "contacted"
    actors = dict(dispatcher.actors)
    here = ActorDispatcher(
        actors,
        [ScenarioActor.model_validate({"id": "manager", "trigger": {"kind": "after_seconds", "seconds": 100}})],
        trial_id="trial-1", proxy_url="http://proxy/", token="t0ken", read_log=lambda: [_ASKED], seed="trial-1",
        timeout=60.0, clock=clock, send=lambda *args: 200,
    )
    asyncio.run(here.tick())
    assert here.waiting == {}
    clock.now += 100
    asyncio.run(here.tick())
    assert "manager" in here.waiting
