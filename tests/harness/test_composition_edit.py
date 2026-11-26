"""Editing a composition's suite: what counts as a change, what the working copy holds, and what a
session may write."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from fi.alk.environments import appointment_booking, clinic_scheduling
from fi.alk.environments.appointment_booking import build
from fi.alk.harness.builder import open_for_composition
from fi.alk.harness.catalogue import load_catalogue, save_catalogue
from fi.alk.harness.composition_edit import (
    CHANGE,
    CHANGE_MANIFEST,
    COMPOSITION_SCHEMA,
    Composition,
    SuiteProof,
    open_composition,
    plan_change,
    seal_change,
)
from fi.alk.harness.folder import read_folder, refresh_check, write_folder
from fi.alk.harness.mcp_servers import REGISTRY
from fi.alk.harness.releases import (
    ReleaseError,
    SuiteScenario,
    attached_tool_names,
    environment_directory,
    load_environment_release,
    load_suite_release,
    load_tools,
    seal_agent_binding,
    seal_suite_release,
)
from fi.alk.harness.scenario_tools import accept_scenario

DSN = os.environ.get("ALK_RELEASE_POSTGRES_DSN", "")
needs_servers = pytest.mark.skipif(
    not DSN or not Path(REGISTRY["filesystem"].argv[1]).is_file(),
    reason="set ALK_RELEASE_POSTGRES_DSN and install the filesystem MCP server",
)
DIGEST = "sha256:" + "a" * 64


@pytest.fixture
def composition(tmp_path) -> Composition:
    """The clinic's committed suite as a composition's, with its working copy."""
    source = Path(clinic_scheduling.__file__).parent / "suite"
    suite_root, workspace = tmp_path / "suite", tmp_path / "workspace"
    shutil.copytree(source, suite_root)
    workspace.mkdir()
    shutil.copytree(suite_root / "scenarios", workspace / "scenarios")
    for name in ("sub_goals.json", "simulator_prompt.md"):
        shutil.copyfile(suite_root / name, workspace / name)
    entries = [
        SuiteScenario(
            key=read_folder(suite_root, folder.name).scenario_key,
            path=f"scenarios/{folder.name}",
            origin="base",
        )
        for folder in sorted((suite_root / "scenarios").iterdir())
        if folder.is_dir()
    ]
    return Composition(
        workspace=workspace,
        document={},
        environments=[],
        environment_root=Path(),
        suite_root=suite_root,
        suite=SimpleNamespace(scenarios=entries),
        binding=None,
        policy=None,
    )


def test_an_untouched_working_copy_is_no_change(composition) -> None:
    assert plan_change(composition) is None


@pytest.mark.parametrize("filename", ["setup.py", "ready.py"])
def test_an_edit_to_a_scenarios_code_is_a_change(composition, filename) -> None:
    entry = composition.suite.scenarios[0]
    path = composition.workspace / entry.path / filename
    path.write_text(path.read_text() + "\n# edited\n")

    plan = plan_change(composition)

    assert plan.changed == [entry.key]
    shipped = plan.sources[entry.path] / filename
    assert shipped.read_text().endswith("# edited\n")


def test_an_edited_check_ships_with_every_scenario_that_names_it(composition) -> None:
    workspace, entry = composition.workspace, composition.suite.scenarios[0]
    catalogue = load_catalogue(workspace)
    scenario = read_folder(workspace, Path(entry.path).name)
    goal = next(
        goal
        for name in scenario.sub_goals
        if (goal := catalogue.named(name)) is not None and goal.deterministic()
    )
    goal.check += "\n# EDITED\n"
    save_catalogue(catalogue, workspace)
    refresh_check(workspace, goal)

    plan = plan_change(composition)

    naming = {
        one.key
        for one in composition.suite.scenarios
        if goal.name in read_folder(workspace, Path(one.path).name).sub_goals
    }
    assert set(plan.changed) == naming
    shipped = plan.sources[entry.path] / "checks" / f"{goal.name}.py"
    assert "# EDITED" in shipped.read_text()


def test_a_suite_whose_check_files_drift_from_its_catalogue_does_not_seal(
    tmp_path,
) -> None:
    environment, suite = tmp_path / "environment", tmp_path / "suite"
    build.build_environment(environment)
    catalogue = build.suite.catalogue()
    for scenario in build.suite.scenarios():
        write_folder(scenario, catalogue, suite)
    save_catalogue(catalogue, suite)
    (suite / "simulator_prompt.md").write_text(build.SIMULATOR_PROMPT)
    check = next((suite / "scenarios").glob("*/checks/*.py"))
    check.write_text(check.read_text() + "\n# stale\n")

    with pytest.raises(ReleaseError) as raised:
        seal_suite_release(
            suite, name="clinic", environment_root=environment, policy_profile={}
        )

    assert raised.value.code == "suite_check_files_invalid"
    assert check.name in raised.value.detail


@pytest.mark.parametrize("field", ["name", "sub_goals"])
def test_no_scenario_writes_outside_the_suite(composition, field) -> None:
    """A session keeps scenarios by name, so a name may not lead into `composition-change/`, which
    is what the platform publishes."""
    workspace = composition.workspace
    payload = read_folder(
        workspace, Path(composition.suite.scenarios[0].path).name
    ).model_dump()
    payload[field] = (
        "../../composition-change/suite"
        if field == "name"
        else ["../../../composition-change/change"]
    )

    result = accept_scenario(
        payload,
        world_root=workspace,
        catalogue=load_catalogue(workspace),
        kept=[],
        simulator_prompt="",
    )

    assert result["is_error"]
    assert "cannot be file names" in result["content"][0]["text"]
    assert not (workspace / CHANGE).exists()


# --- a composition on an environment with attached servers --------------------------------------


@pytest.fixture(scope="module")
def release(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("clinic")
    build.build_environment(root / "environment")
    build.build_suite(root / "suite", root / "environment", postgres_dsn=DSN)
    return root


def _workspace(release: Path, tmp_path: Path, connector: str) -> Path:
    environment, suite = release / "environment", release / "suite"
    sealed = load_environment_release(environment)
    workspace = tmp_path / "composition"
    directory = environment_directory(workspace / "releases", sealed.digest)
    directory.parent.mkdir(parents=True)
    shutil.copytree(environment, directory)
    shutil.copytree(suite, workspace / "releases" / "suite")
    binding = seal_agent_binding(
        {
            "mode": "canonical",
            "agent": {
                "agent_definition_id": "a",
                "agent_version_id": "1",
                "configuration_digest": DIGEST,
                "agent_config_digest": DIGEST,
                "provider_digest": DIGEST,
                "provider_target_id": DIGEST,
                "connector": connector,
            },
        },
        environment_root=environment,
        agent_tools=[
            *(tool.name for tool in load_tools(environment)),
            *attached_tool_names(environment),
        ],
    )
    (workspace / "binding.json").write_text(binding.model_dump_json())
    (workspace / "composition.json").write_text(
        json.dumps(
            {
                "schema_version": COMPOSITION_SCHEMA,
                "environment": [{"digest": sealed.digest}],
                "suite": {"digest": load_suite_release(suite).digest},
            }
        )
    )
    return workspace


@needs_servers
@pytest.mark.parametrize(
    ("connector", "modality"),
    [("retell_chat", "chat"), ("hosted_agent", "chat"), ("mcp_client", "chat"), ("retell", "voice")],
)
def test_a_composition_has_the_modality_its_runs_compose_with(
    release, tmp_path, connector, modality
) -> None:
    composition, _ = open_composition(_workspace(release, tmp_path, connector))
    assert composition.contract.modality == modality


@needs_servers
def test_scenarios_that_call_attached_servers_are_healthy_and_can_be_saved(
    release, tmp_path
) -> None:
    workspace = _workspace(release, tmp_path, "hosted_agent")
    composition, _ = open_composition(workspace)
    proof = SuiteProof(composition, lambda: DSN)
    try:
        assert proof.needed()
        stage = open_for_composition(composition, postgres_proof=proof.scenario)
        assert stage.health_at_open == []

        attached = attached_tool_names(Path(appointment_booking.__file__).parent)
        edited = next(
            one
            for one in stage.kept
            if any(step.tool in attached for step in one.solution)
        )
        catalogue = load_catalogue(workspace)
        assert proof.scenario(edited, catalogue) == ""
        unsolved = edited.model_copy(update={"solution": []})
        assert proof.scenario(unsolved, catalogue)

        edited.persona.initial_message = "Hello, quick question."
        write_folder(edited, catalogue, workspace)
        summary = seal_change(plan_change(composition), postgres_dsn=DSN)
    finally:
        proof.close()

    assert summary["changed"] == [edited.scenario_key]
    written = json.loads((workspace / CHANGE / CHANGE_MANIFEST).read_text())
    assert written == summary
    assert set(written) == {
        "schema_version",
        "parent",
        "suite",
        "binding_proof",
        "added",
        "changed",
        "removed",
        "unscorable",
        "no_longer_scored",
    }


@needs_servers
def test_a_working_copy_laid_out_without_the_servers_gets_them_on_open(
    release, tmp_path
) -> None:
    workspace = _workspace(release, tmp_path, "hosted_agent")
    open_composition(workspace)
    (workspace / "servers.json").unlink()
    shutil.rmtree(workspace / "attached")

    _, laid_out = open_composition(workspace)

    assert laid_out is False
    assert attached_tool_names(workspace)
    assert (workspace / "attached").is_dir()
