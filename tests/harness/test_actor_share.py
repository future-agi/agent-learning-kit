"""The share of scenarios that include a speaking actor is a default the build's own request replaces."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from fi.alk.harness.actors import ACTORS_FILE, ActorShare, load_actors, save_actors
from fi.alk.harness.call_actor import CallActor, CallActorList, save_call_actors
from fi.alk.harness.catalogue import Catalogue, save_catalogue
from fi.alk.harness.contract import AgentContract, ToolSpec
from fi.alk.harness.scenario import Scenario
from fi.alk.harness.scenario_tools import CANONICAL_AXES, scenario_tools
from fi.alk.harness.world import GeneratedWorld
from fi.alk.harness.world.snapshot import save
from fi.simulate.simulation.call_actors import SHARE_RANGE, default_share

SHARE_REFUSAL = "include an actor or a fault"
ACTOR = [{"id": "second", "instruction": "Take over when asked."}]
PLACED = {axis: "plain" for axis in CANONICAL_AXES}


@pytest.fixture(autouse=True)
def _a_plan_that_holds_the_count(monkeypatch):
    """The one-level grid here is a placeholder, not a plan too thin for the count."""
    monkeypatch.setenv("ALK_HARNESS_CASES_PER_CELL", "1000")


def _environment(root: Path) -> AgentContract:
    class Notes(GeneratedWorld):
        name = "notes"
        tools = [{"name": "put"}]
        handlers = {"put": "def handle(args, db):\n    return {'ok': 1}\n"}

    world = Notes(":memory:")
    world.connection.executescript("CREATE TABLE note(id TEXT);")
    save(world, root, notes="test", sequences=[])
    save_catalogue(Catalogue(sub_goals=[]), root)
    save_call_actors(
        root,
        CallActorList(
            call_actors=[
                CallActor.model_validate(
                    {"id": "second", "name": "Rowan", "role": "someone who takes over", "channel": "audio_handoff"}
                )
            ]
        ),
    )
    contract = AgentContract(agent="notes", real_use_cases=["keep a note"], tools=[ToolSpec(name="put")])
    contract.modality = "voice"
    return contract


def _stage(tmp_path: Path, kept: list[Scenario], wanted: int = 10, **plan: Any) -> dict[str, Any]:
    contract = _environment(tmp_path)
    server, _ = scenario_tools(contract, tmp_path, tmp_path, wanted=wanted, can_save=False, start_from=kept)
    tools = {one.name: one.handler for one in server.tools}
    asyncio.run(tools["aim_for"]({"count": wanted, "axes": {axis: ["plain"] for axis in CANONICAL_AXES}, **plan}))
    return tools


def _submit(tools: dict[str, Any], name: str, with_actor: bool) -> str:
    args = {"name": name, "instruction": "Keep a note.", "coverage": PLACED, "actors": ACTOR if with_actor else []}
    return asyncio.run(tools["submit_scenario"](args))["content"][0]["text"]


def _written(count: int, with_actor: bool) -> list[Scenario]:
    return [Scenario(name=f"s{n}", actors=ACTOR if with_actor else []) for n in range(count)]


@pytest.fixture(autouse=True)
def _no_job(monkeypatch):
    monkeypatch.delenv("ALK_HARNESS_JOB_ID", raising=False)


def test_all_lets_every_scenario_carry_an_actor_and_refuses_nothing(tmp_path):
    tools = _stage(tmp_path, _written(9, with_actor=True), actor_share={"min": 1, "max": 1})
    assert SHARE_REFUSAL not in _submit(tools, "s9", with_actor=True)
    assert SHARE_REFUSAL not in _submit(tools, "alone", with_actor=False)


def test_none_needs_no_actor_and_refuses_nothing(tmp_path):
    tools = _stage(tmp_path, _written(9, with_actor=False), actor_share={"min": 0, "max": 0})
    assert SHARE_REFUSAL not in _submit(tools, "s9", with_actor=False)
    assert SHARE_REFUSAL not in _submit(tools, "with-one", with_actor=True)


def test_a_range_from_the_request_refuses_nothing(tmp_path):
    tools = _stage(tmp_path, _written(9, with_actor=False), actor_share={"min": 0.5, "max": 0.6})
    assert SHARE_REFUSAL not in _submit(tools, "s9", with_actor=False)


def test_with_a_cast_listed_the_default_share_never_refuses_a_scenario_where_the_person_is_alone(
    tmp_path, share_refused
):
    """The default share is a target: nine scenarios already written without an actor, and the tenth too, are never
    refused for the share, whatever share was drawn, so no actor is forced into a situation that has no one else."""
    share = default_share(str(tmp_path))
    assert SHARE_RANGE[0] <= share <= SHARE_RANGE[1]
    tools = _stage(tmp_path, _written(9, with_actor=False))
    assert not share_refused(lambda: _submit(tools, "s9", with_actor=False))






def test_an_actor_share_that_is_not_two_fractions_is_refused(tmp_path):
    contract = _environment(tmp_path)
    server, _ = scenario_tools(contract, tmp_path, tmp_path, wanted=10)
    aim = next(one.handler for one in server.tools if one.name == "aim_for")
    said = asyncio.run(aim({"count": 10, "actor_share": {"min": 0.2}}))
    assert said.get("is_error") and "actor_share takes" in said["content"][0]["text"]


def test_the_share_a_request_set_holds_in_a_later_session(tmp_path):
    contract = _environment(tmp_path)
    first, _ = scenario_tools(contract, tmp_path, tmp_path, wanted=10)
    aim = next(one.handler for one in first.tools if one.name == "aim_for")
    asyncio.run(aim({"count": 10, "actor_share": {"min": 0, "max": 0}}))
    assert load_actors(tmp_path).actor_share == ActorShare(min=0, max=0)
    server, _ = scenario_tools(contract, tmp_path, tmp_path, wanted=10, start_from=_written(9, with_actor=False))
    tools = {one.name: one.handler for one in server.tools}
    assert SHARE_REFUSAL not in _submit(tools, "s9", with_actor=False)


def test_an_actor_list_without_a_share_reads_and_saves_as_before(tmp_path):
    _environment(tmp_path)
    before = (tmp_path / ACTORS_FILE).read_text(encoding="utf-8")
    assert "actor_share" not in before and load_actors(tmp_path).actor_share is None
    save_actors(tmp_path, load_actors(tmp_path))
    assert (tmp_path / ACTORS_FILE).read_text(encoding="utf-8") == before




KIND_REFUSAL = "one kind takes at most"
FAULT = [{"action": "put", "mode": "timeout_after_commit"}]


def _with_an_approver(root: Path) -> None:
    from fi.alk.harness.actors import Actor

    listing = load_actors(root)
    approver = Actor(id="lead", name="Morgan Hale", role="the lead who approves", permitted_actions=["approval.put"])
    save_actors(root, listing.model_copy(update={"actors": [*listing.actors, approver]}))






def test_the_plan_spreads_actor_scenarios_across_every_kind_the_environment_supports(tmp_path):
    contract = _environment(tmp_path)
    _with_an_approver(tmp_path)
    server, _ = scenario_tools(contract, tmp_path, tmp_path, wanted=40, can_save=False)
    tools = {one.name: one.handler for one in server.tools}
    aim = {"count": 40, "axes": {axis: ["plain"] for axis in CANONICAL_AXES}, "actor_share": {"min": 0.5, "max": 0.5}}
    said = asyncio.run(tools["aim_for"](aim))["content"][0]["text"]
    assert "no one kind on more than 50% of them" in said
    assert "an actor who speaks (second)" in said
    assert "an actor who acts through the world (lead: approval.put)" in said
    assert "approval_required held until lead approves" in said
    assert "act through the world and no level is named for them yet: lead" in said


def test_the_kind_refusal_is_off_unless_switched_on(tmp_path, monkeypatch):
    monkeypatch.delenv("ALK_ACTOR_KIND_GATE", raising=False)
    tools = _stage(tmp_path, [Scenario(name=f"h{n}", actors=ACTOR) for n in range(10)], wanted=40,
                   actor_share={"min": 0.5, "max": 0.5})
    assert KIND_REFUSAL not in _submit(tools, "h10", with_actor=True)


def test_one_kind_of_actor_scenario_cannot_take_the_whole_share(tmp_path, monkeypatch):
    monkeypatch.setenv("ALK_ACTOR_KIND_GATE", "1")
    spouse_like = [Scenario(name=f"h{n}", actors=ACTOR) for n in range(10)]
    tools = _stage(tmp_path, spouse_like, wanted=40, actor_share={"min": 0.5, "max": 0.5})
    refused = _submit(tools, "h10", with_actor=True)
    assert KIND_REFUSAL in refused and "already carry second" in refused and "a fault on one of the agent's" in refused
    faulted = {"name": "f0", "instruction": "Keep a note.", "coverage": PLACED, "faults": FAULT}
    assert KIND_REFUSAL not in asyncio.run(tools["submit_scenario"](faulted))["content"][0]["text"]
    # Editing one already in the suite never changes what it tests, so it is never refused for its kind.
    assert KIND_REFUSAL not in _submit(tools, "h3", with_actor=True)


def test_the_most_one_kind_may_take_is_a_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("ALK_ACTOR_KIND_GATE", "1")
    monkeypatch.setenv("ALK_ACTOR_KIND_MOST_SHARE", "1")
    tools = _stage(tmp_path, [Scenario(name=f"h{n}", actors=ACTOR) for n in range(10)], wanted=40,
                   actor_share={"min": 0.5, "max": 0.5})
    assert KIND_REFUSAL not in _submit(tools, "h10", with_actor=True)
