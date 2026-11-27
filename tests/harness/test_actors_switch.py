"""The job's actors switch: off for a hosted job that does not turn actors on, the kit's own default for a local one."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fi.alk.harness import job as job_module
from fi.alk.harness.actors import ACTORS_FILE, ACTOR_SCENARIO_FIELDS, ActorList, as_played, save_actors
from fi.alk.harness.call_actor import CallActor, CallActorList, save_call_actors
from fi.alk.harness.catalogue import Catalogue, save_catalogue
from fi.alk.harness.contract import AgentContract, ToolSpec
from fi.alk.harness.job import (
    ACTORS_OFF,
    ActorsSwitch,
    actors_switch,
    current_actors_switch,
    job_actors_switch,
    publish_actors_switch,
)
from fi.alk.harness.scenario import Scenario
from fi.alk.harness.scenario_tools import CANONICAL_AXES, scenario_tools
from fi.alk.harness.world import GeneratedWorld
from fi.alk.harness.world.snapshot import save
from fi.alk.harness.world.tools import world_actor_tools
from fi.simulate.simulation.call_actors import SHARE_RANGE, default_share

ON = {"enabled": True, "share": [0.5, 0.6]}
OFF = {"enabled": False, "share": [0.0, 0.0]}
PLACED = {axis: "plain" for axis in CANONICAL_AXES}
ACTOR = [{"id": "second", "instruction": "Take over when asked."}]
SHARE_REFUSAL = "include an actor or a fault"


# --- reading the switch ---------------------------------------------------------------------------------------------


def test_an_absent_switch_is_off_for_a_hosted_job_and_the_kit_default_for_a_local_one():
    assert actors_switch({}, hosted=True) == ACTORS_OFF
    assert actors_switch(None, hosted=True) == ACTORS_OFF
    assert actors_switch({}, hosted=False) is None
    assert actors_switch({"actors": ON}, hosted=True) == ActorsSwitch(enabled=True, share=[0.5, 0.6])
    assert actors_switch({"actors": OFF}, hosted=False) == ACTORS_OFF


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "on",
        [True, [0.1, 0.2]],
        {"enabled": True},
        {"share": [0.1, 0.2]},
        {"enabled": 1, "share": [0.1, 0.2]},
        {"enabled": "true", "share": [0.1, 0.2]},
        {"enabled": True, "share": [0.1]},
        {"enabled": True, "share": [0.1, 0.2, 0.3]},
        {"enabled": True, "share": ["0.1", 0.2]},
        {"enabled": True, "share": [0.3, 0.2]},
        {"enabled": True, "share": [-0.1, 0.2]},
        {"enabled": True, "share": [0.1, 1.2]},
        {"enabled": True, "share": "0.1-0.2"},
    ],
)
@pytest.mark.parametrize("hosted", [True, False])
def test_a_malformed_switch_reads_as_off_and_is_logged(raw, hosted, caplog):
    assert actors_switch({"actors": raw}, hosted=hosted) == ACTORS_OFF
    assert "actors are off" in caplog.text


def test_a_job_s_switch_follows_its_execution_mode():
    hosted = job_module.HarnessJob.model_validate(
        {"job_id": "j", "run_id": "r", "execution": "hosted", "source": {"kind": "archive", "archive_artifact_id": "s"},
         "agent": {"connector": "http"}, "scenario_count": 1, "runtime": {"isolation": "dedicated_vm"}}
    )
    local = hosted.model_copy(update={"execution": job_module.ExecutionMode.LOCAL})
    assert job_actors_switch(hosted) == ACTORS_OFF
    assert job_actors_switch(local) is None


def test_the_published_switch_is_what_the_process_reads():
    assert current_actors_switch() is None
    publish_actors_switch({}, hosted=True)
    assert current_actors_switch() == ACTORS_OFF
    publish_actors_switch({"actors": ON}, hosted=True)
    assert current_actors_switch() == ActorsSwitch(enabled=True, share=[0.5, 0.6])
    publish_actors_switch({}, hosted=False)
    assert current_actors_switch() is None


# --- authoring ------------------------------------------------------------------------------------------------------


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


def _tools(tmp_path: Path, metadata: dict[str, Any], *, hosted: bool = True, kept: list[Scenario] | None = None):
    contract = _environment(tmp_path)
    publish_actors_switch(metadata, hosted=hosted)
    server, _ = scenario_tools(contract, tmp_path, tmp_path, wanted=10, can_save=False, start_from=kept or [])
    tools = {one.name: one for one in server.tools}
    asyncio.run(tools["aim_for"].handler({"count": 10, "axes": {axis: ["plain"] for axis in CANONICAL_AXES}}))
    return tools


def _written(count: int) -> list[Scenario]:
    return [Scenario(name=f"s{n}", actors=[]) for n in range(count)]


def _submit(tools, **fields) -> dict[str, Any]:
    return asyncio.run(tools["submit_scenario"].handler({"name": "s9", "instruction": "Keep a note.", "coverage": PLACED, **fields}))


@pytest.mark.parametrize(
    "metadata",
    [{}, {"actors": OFF}, {"actors": "on"}, {"actors": {"enabled": False, "share": [0.2, 0.3]}}],
    ids=["hosted-absent", "off", "malformed", "off-with-a-range"],
)
def test_with_actors_off_a_scenario_takes_no_actor_field_and_no_share_is_held(tmp_path, monkeypatch, metadata):
    monkeypatch.delenv("ALK_HARNESS_JOB_ID", raising=False)
    tools = _tools(tmp_path, metadata, kept=_written(9))

    for name in ("submit_scenario", "edit_scenario"):
        offered = set(tools[name].input_schema["properties"])
        assert not offered & set(ACTOR_SCENARIO_FIELDS), name
    for field, value in (("actors", ACTOR), ("faults", [{"action": "put", "mode": "unavailable"}]),
                         ("pre_run", [{"actor": "second"}]), ("starts_with", {"actor": "second"})):
        said = _submit(tools, **{field: value})
        assert said.get("is_error") and "Actors and faults are off" in said["content"][0]["text"], field
        edited = asyncio.run(tools["edit_scenario"].handler({"scenario_key": "s0", field: value}))
        assert edited.get("is_error") and "Actors and faults are off" in edited["content"][0]["text"], field
    # The share gate never fires: a suite of actor-free scenarios is short of nothing.
    assert SHARE_REFUSAL not in _submit(tools)["content"][0]["text"]


def test_with_actors_off_no_actor_tool_is_offered_and_no_actors_file_is_written(tmp_path):
    publish_actors_switch({}, hosted=True)
    assert world_actor_tools(tmp_path) == []
    save_actors(tmp_path, ActorList())
    assert not (tmp_path / ACTORS_FILE).exists()


def test_with_actors_on_the_platform_s_range_is_the_default_share(tmp_path, monkeypatch, share_refused):
    monkeypatch.delenv("ALK_HARNESS_JOB_ID", raising=False)
    tools = _tools(tmp_path, {"actors": ON}, kept=_written(9))
    assert {"actors", "faults", "pre_run", "starts_with"} <= set(tools["submit_scenario"].input_schema["properties"])
    assert [one.name for one in world_actor_tools(tmp_path)][:2] == ["add_actor", "drop_actor"]
    assert not share_refused(lambda: _submit(tools))


def test_a_local_job_with_no_switch_behaves_as_before(tmp_path, monkeypatch, share_refused):
    monkeypatch.delenv("ALK_HARNESS_JOB_ID", raising=False)
    tools = _tools(tmp_path, {}, hosted=False, kept=_written(9))
    share = default_share(str(tmp_path))
    assert SHARE_RANGE[0] <= share <= SHARE_RANGE[1]
    assert "actors" in tools["submit_scenario"].input_schema["properties"]
    assert not share_refused(lambda: _submit(tools))


# --- run time -------------------------------------------------------------------------------------------------------


def test_with_actors_off_a_run_plays_no_actor_pre_run_first_speaker_or_fault(tmp_path):
    save_actors(tmp_path, ActorList.model_validate({"actors": [{"id": "caller", "name": "The caller", "side": "caller", "goal": "Be brief."}]}))
    doc = {"instruction": "Keep a note.", "actors": ACTOR, "pre_run": [{"actor": "second"}],
           "starts_with": {"actor": "second"}, "faults": [{"action": "put", "mode": "unavailable"}]}

    played = as_played(doc, tmp_path, ACTORS_OFF)
    assert not set(played) & set(ACTOR_SCENARIO_FIELDS)
    # The caller's own actor record is not read either.
    assert played["instruction"] == "Keep a note."

    on = as_played(doc, tmp_path, None)
    assert on["actors"] == ACTOR and on["faults"] and "Be brief." in on["instruction"]


def test_a_hosted_process_with_no_published_switch_reads_actors_off(tmp_path, monkeypatch):
    # A child process that lost the published switch: in a hosted sandbox (its job file present) it fails closed.
    job_file = tmp_path / "job.json"
    monkeypatch.setattr(job_module, "HOSTED_JOB_PATH", job_file)
    assert current_actors_switch() is None
    job_file.write_text("{}")
    assert current_actors_switch() == ACTORS_OFF


def _hosted_job(tmp_path: Path) -> Path:
    path = tmp_path / "job.json"
    path.write_text(json.dumps({
        "job_id": "j", "run_id": "r", "execution": "hosted", "source": {"kind": "archive", "archive_artifact_id": "s"},
        "agent": {"connector": "http"}, "scenario_count": 1, "runtime": {"isolation": "dedicated_vm"}, "metadata": {},
    }))
    return path


def test_each_hosted_entrypoint_publishes_actors_off_for_a_job_with_no_switch(tmp_path):
    from fi.alk.harness import authoring_entrypoint, hosted_entrypoint
    from fi.alk.harness.chat_policy import load_chat_policy
    from fi.alk.harness.hosted_chat_entrypoint import HostedChatRuntime

    job = _hosted_job(tmp_path)
    hosted_entrypoint.load_job(job)
    assert current_actors_switch() == ACTORS_OFF

    publish_actors_switch({}, hosted=False)
    # Certify-only without its bundle output stops at its own check, after the switch is published.
    with pytest.raises(SystemExit):
        authoring_entrypoint.main([str(job), "--source", str(tmp_path), "--output", str(tmp_path), "--certify-only"])
    assert current_actors_switch() == ACTORS_OFF

    publish_actors_switch({}, hosted=False)
    policy = tmp_path / "policy.yaml"
    policy.write_text("schema_version: 1\nharness_chat:\n  enabled: true\n", encoding="utf-8")
    HostedChatRuntime(client=SimpleNamespace(turn_context={}, capabilities=SimpleNamespace(turn_context={})), policy=load_chat_policy(policy), workspace=tmp_path, source=tmp_path,
                      job={"metadata": {}, "scenario_count": 1})
    assert current_actors_switch() == ACTORS_OFF
