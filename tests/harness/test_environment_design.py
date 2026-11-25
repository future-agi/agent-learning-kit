"""Designing an environment: resuming, customizing a template, documents and the local command."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import io
import json
import shutil
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from fi.alk import environments
from fi.alk.environments import designed
from fi.alk.environments.appointment_booking import suite as appointment_suite
from fi.alk.harness.design import hosted, workspace as design_workspace
from fi.alk.harness import catalogue as design_catalogue
from fi.alk.harness import folder as design_folder
from fi.alk.harness.design.contract import design_contract
from fi.alk.harness.design.hosted import OUTPUT
from fi.alk.harness.design.tools import (
    APPROVE,
    FINISHED_MARKER,
    PENDING_QUESTION,
    RESULT,
    settle_proposal,
)
from fi.alk.harness.design.workspace import (
    Proposal,
    load_design,
    open_design,
    save_design,
)
from fi.alk.harness.hosted_chat_entrypoint import HostedChatRuntime
from fi.alk.harness.hosted_conversation import _workspace_archive
from fi.alk.harness.releases import (
    WORLD_IMPLEMENTATION,
    PolicyProfile,
    ReleaseRef,
    load_environment_release,
)
from fi.alk.harness.world.probe import _run_sequence
from fi.alk.harness.world.runtime import GeneratedWorld
from fi.alk.harness.world.tools import world_tools

PROPOSAL = Proposal(one_liner="A desk", summary="It books.", use_cases=["booking"])


# --- a hosted design that parked and is continued ------------------------------------------------


def _parked_checkpoint(workspace: Path) -> bytes:
    """A design that asked for approval, parked, and whose ending turn checkpointed it."""
    design = open_design(workspace, name="desk")
    save_design(workspace, design.model_copy(update={"pending_proposal": PROPOSAL}))
    (workspace / PENDING_QUESTION).write_text(
        json.dumps({"message_id": "m1", "question": "Approve?", "options": []})
    )

    async def nothing() -> None:
        return None

    runtime = SimpleNamespace(
        workspace=workspace, backend_name="", _checkpoint=nothing
    )
    asyncio.run(HostedChatRuntime._park_design(runtime, []))
    return _workspace_archive(workspace)


def _hosted_args(tmp_path: Path, workspace: Path) -> argparse.Namespace:
    job = tmp_path / "job.json"
    job.write_text(
        json.dumps({"metadata": {"design": {"name": "desk", "resume_from": "job-1"}}})
    )
    return argparse.Namespace(
        job=str(job),
        releases=str(tmp_path / "releases"),
        workspace=str(workspace),
        documents=str(tmp_path / "documents"),
    )


def test_a_continued_design_waits_for_its_answer_and_finishes(tmp_path, monkeypatch):
    archive = _parked_checkpoint(tmp_path / "first")
    workspace = tmp_path / "second"
    with tarfile.open(fileobj=io.BytesIO(archive)) as packed:
        packed.extractall(workspace, filter="data")
    assert (workspace / FINISHED_MARKER).is_file()
    stages = []

    def answered_while_waiting(_seconds: float) -> None:
        # The person answers, and the builder finishes the design.
        settle_proposal(workspace, APPROVE)
        stages.append(load_design(workspace).stage)
        (workspace / PENDING_QUESTION).unlink()
        (workspace / OUTPUT / RESULT).write_text(json.dumps({"status": "completed"}))
        (workspace / FINISHED_MARKER).write_text(json.dumps({"status": "completed"}))

    monkeypatch.setattr(hosted.time, "sleep", answered_while_waiting)

    assert hosted.run_hosted(_hosted_args(tmp_path, workspace)) == 0
    assert stages == ["world"]


def test_a_design_this_attempt_completed_is_reported_again(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    open_design(workspace, name="desk")
    (workspace / OUTPUT).mkdir()
    (workspace / OUTPUT / RESULT).write_text(json.dumps({"status": "completed"}))
    (workspace / FINISHED_MARKER).write_text(json.dumps({"status": "completed"}))
    monkeypatch.setattr(
        hosted.time, "sleep", lambda _seconds: pytest.fail("it is already finished")
    )

    assert hosted.run_hosted(_hosted_args(tmp_path, workspace)) == 0


# --- customizing a template ----------------------------------------------------------------------

PACKAGES = Path(environments.__file__).parent
DESIGNED = sorted(path.parent.name for path in PACKAGES.glob("*/environment.json"))


@pytest.mark.parametrize("package", DESIGNED)
def test_every_declared_sequence_runs_on_the_committed_world(package) -> None:
    here = PACKAGES / package
    failed = []
    for index, sequence in enumerate(designed._spec(here).get("sequences") or []):
        world = GeneratedWorld(":memory:", kind="sqlite")
        world.store.apply((here / "world.sql").read_text(encoding="utf-8"))
        world.handlers = {
            path.stem: path.read_text(encoding="utf-8")
            for path in (here / "handlers").glob("*.py")
        }
        result = _run_sequence(world, sequence, index)
        world.close()
        if not result.passed:
            failed.append(f"{result.name}: {result.detail}")
    assert failed == []


def _template(package: str, root: Path, monkeypatch) -> tuple[Path, Path]:
    """The package's environment sealed, and its suite as committed (opening a template reads the
    suite's files; proving them is the suite's own test)."""
    here, environment, suite = PACKAGES / package, root / "environment", root / "suite"
    build = importlib.import_module(f"fi.alk.environments.{package}.build")
    if package == "appointment_booking":
        build.build_environment(environment)
        catalogue = appointment_suite.catalogue()
        for scenario in appointment_suite.scenarios():
            design_folder.write_folder(scenario, catalogue, suite)
        design_catalogue.save_catalogue(catalogue, suite)
        (suite / "simulator_prompt.md").write_text(build.SIMULATOR_PROMPT)
        profile = PolicyProfile()
    else:
        designed.build_environment(here, environment)
        shutil.copytree(here / "suite", suite)
        profile = PolicyProfile.model_validate_json(
            (suite / designed.POLICY_PROFILE).read_bytes()
        )
    release = load_environment_release(environment)
    sealed = SimpleNamespace(
        digest="sha256:" + "b" * 64,
        name=f"{package}_base",
        environment=ReleaseRef(digest=release.digest, name=release.name),
        policy_profile=profile,
        scenarios=[
            SimpleNamespace(path=f"scenarios/{folder.name}")
            for folder in sorted((suite / "scenarios").iterdir())
            if folder.is_dir()
        ],
    )
    monkeypatch.setattr(design_workspace, "load_suite_release", lambda _root: sealed)
    return environment, suite


def _approved(workspace: Path):
    design = load_design(workspace).model_copy(
        update={"proposal": PROPOSAL, "stage": "world"}
    )
    save_design(workspace, design)
    server, world = world_tools(
        design_contract(workspace, design),
        workspace,
        implementation=WORLD_IMPLEMENTATION,
    )
    return {spec.name: spec.handler for spec in server.tools}, world


def _said(result) -> str:
    return result["content"][0]["text"]


@pytest.mark.parametrize("package", ["appointment_booking", *DESIGNED])
def test_a_template_opens_and_its_world_saves_unmodified(
    package, tmp_path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    open_design(
        workspace, name="desk", template=_template(package, tmp_path, monkeypatch)
    )
    handlers, _world = _approved(workspace)

    result = asyncio.run(handlers["save_world"]({"notes": ""}))

    assert not result.get("is_error"), _said(result)


def test_a_customization_answers_for_what_it_changes(tmp_path, monkeypatch) -> None:
    """Rows left over from building, and a world check that cannot fail, are still refused."""
    workspace = tmp_path / "workspace"
    open_design(
        workspace, name="desk", template=_template("banking", tmp_path, monkeypatch)
    )
    handlers, world = _approved(workspace)
    columns = [
        row["name"] for row in world.store.query('PRAGMA table_info("disputes")')
    ]
    world.connection.execute(
        f"INSERT INTO disputes ({', '.join(columns)}) SELECT 'D-LEFT', "
        + ", ".join(columns[1:])
        + " FROM disputes LIMIT 1"
    )
    world.connection.commit()

    leftover = asyncio.run(handlers["save_world"]({"notes": ""}))

    assert leftover["is_error"]
    assert "left over from building: disputes" in _said(leftover)

    world.connection.execute(f"DELETE FROM disputes WHERE {columns[0]} = 'D-LEFT'")
    world.connection.commit()
    # A template check rewritten to look at nothing is the customization's own.
    name = next(iter(designed._spec(PACKAGES / "banking")["world_checks"]))
    asyncio.run(
        handlers["add_world_check"](
            {"name": name, "code": "def check(world):\n    world.state()\n    return None\n"}
        )
    )

    blind = asyncio.run(handlers["save_world"]({"notes": ""}))

    assert blind["is_error"]
    assert f"stayed green with the world emptied and every tool silenced, so they are not verifying anything: {name}." in _said(blind)
