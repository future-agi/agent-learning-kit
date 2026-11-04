"""Scheduled world actors: each action once, at its trigger, under the actor's identity."""

from __future__ import annotations

import asyncio

import pytest

from fi.alk.harness.actor_dispatch import ActorDispatcher
from fi.alk.harness.actors import Actor, ActorList, ScenarioActor


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _dispatcher(cast, log, clock=None, seed="trial-1", reads=None):
    sent = []
    actors = {
        "manager": Actor(id="manager", name="Priya", permitted_actions=["crm.approve_discount"]),
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
        [{"id": "manager", "actions": [{"action": "crm.approve_discount", "arguments": {"deal": "d1"}}]}], []
    )
    asyncio.run(dispatcher.run())
    assert sent == [
        (
            "http://proxy/crm/approve_discount",
            {"X-ALK-Actor": "manager", "X-ALK-Actor-Token": "t0ken", "Idempotency-Key": "trial-1:manager:0"},
            {"deal": "d1"},
        )
    ]


def test_an_action_on_the_agent_s_tool_call_fires_once_after_that_call_and_names_it_as_the_cause():
    log: list[dict] = []
    action = {"action": "crm.approve_discount", "trigger": {"kind": "on_tool", "tool": "crm.request_approval"}}
    dispatcher, sent = _dispatcher([{"id": "manager", "actions": [action]}], log)

    async def scenario():
        await dispatcher.tick()
        assert sent == []
        # An actor's own row never counts as the agent's call.
        log.append({"name": "request_approval", "actor_id": "coworker", "action_id": "x0"})
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
    action = {"action": "crm.approve_discount", "trigger": {"kind": "after_seconds", "seconds": 5}}
    dispatcher, sent = _dispatcher([{"id": "manager", "actions": [action]}], [], clock=clock)

    async def scenario():
        await dispatcher.tick()
        assert sent == []
        clock.now = 5
        await dispatcher.tick()

    asyncio.run(scenario())
    assert len(sent) == 1 and "X-ALK-Parent-Event" not in sent[0][1]


def test_an_actor_comes_in_when_the_agent_reaches_one_of_its_identities():
    log = [
        {"name": "post_message", "actor_id": "agent", "action_id": "a1", "arguments": {"to": "@priya"}},
        {"name": "post_message", "actor_id": "agent", "action_id": "a2", "arguments": {"to": "@sam"}},
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
    action = {"action": "crm.approve_discount", "trigger": {"kind": "probability", "p": 0.5}}
    cast = [{"id": "manager", "actions": [action] * 20}]
    first, sent_first = _dispatcher(cast, [], seed="s")
    again, sent_again = _dispatcher(cast, [], seed="s")
    asyncio.run(_ticks(first))
    asyncio.run(_ticks(again))
    keys = [headers["Idempotency-Key"] for _, headers, _ in sent_first]
    assert keys == [headers["Idempotency-Key"] for _, headers, _ in sent_again]
    assert 0 < len(keys) < 20 and len(keys) == len(set(keys))


def test_a_scenario_naming_an_actor_the_environment_does_not_list_sends_nothing_for_it():
    dispatcher, sent = _dispatcher([{"id": "ghost", "actions": [{"action": "crm.approve_discount"}]}], [])
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
            {"id": "manager", "actions": [{"action": "crm.approve_discount", "trigger": {"on_tool": "request_approval"}}]}
        )


def test_a_check_reads_who_did_what_from_the_world_s_action_log():
    from fi.alk.harness.world.handle import HostedWorld, ReadOnlyWorld

    class Store:
        def query(self, sql, params):
            assert "_alk_tool_trace" in sql
            return [
                {"name": "update_deal", "ok": False, "at": 1.0},
                {"name": "approve_discount", "actor_id": "coworker", "outcome": "refused", "ok": False, "at": 2.0},
                {"name": "approve_discount", "actor_id": "manager", "outcome": "committed", "ok": True, "at": 3.0},
            ]

    world = object.__new__(HostedWorld)
    world._store = Store()
    assert [row["actor_id"] for row in world.events(outcome="committed")] == ["manager"]
    assert [row["name"] for row in world.events("agent", "refused")] == ["update_deal"]
    assert len(world.events()) == 3
    view = object.__new__(ReadOnlyWorld)
    view._ReadOnlyWorld__world = world
    assert view.events("coworker") == world.events("coworker")


def test_a_chance_action_its_draw_left_out_is_settled_at_once_and_never_waited_on():
    action = {"action": "crm.approve_discount", "trigger": {"kind": "probability", "p": 0.0}}
    dispatcher, sent = _dispatcher([{"id": "manager", "actions": [action]}], [])
    assert dispatcher.pending == []
    asyncio.run(asyncio.wait_for(dispatcher.run(), 1.0))
    assert sent == []


def test_the_log_is_read_only_while_an_action_waits_on_what_the_agent_does():
    reads: list[int] = []
    timed = {"action": "crm.approve_discount", "trigger": {"kind": "after_seconds", "seconds": 5}}
    dispatcher, _sent = _dispatcher([{"id": "manager", "actions": [timed]}], [], reads=reads)
    asyncio.run(_ticks(dispatcher))
    assert reads == []
    on_tool = {"action": "crm.approve_discount", "trigger": {"kind": "on_tool", "tool": "crm.request_approval"}}
    dispatcher, _sent = _dispatcher([{"id": "manager", "actions": [on_tool]}], [], reads=reads)
    asyncio.run(_ticks(dispatcher, 2))
    assert reads == [1, 1]


def test_an_action_s_request_is_bounded_by_the_call_s_budget(monkeypatch):
    from fi.alk.harness import actor_dispatch

    seen = []
    monkeypatch.setattr(actor_dispatch, "_send", lambda url, headers, arguments, timeout: seen.append(timeout) or 201)
    dispatcher = ActorDispatcher(
        {"manager": Actor(id="manager", name="Priya")},
        [ScenarioActor.model_validate({"id": "manager", "actions": [{"action": "crm.approve_discount"}]})],
        trial_id="t",
        proxy_url="http://proxy",
        token="x",
        read_log=list,
        seed="s",
        timeout=42.0,
    )
    asyncio.run(dispatcher.tick())
    assert seen == [42.0]
