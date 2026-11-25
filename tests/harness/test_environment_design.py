"""Designing an environment: resuming, customizing a template, documents and the local command."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from fi.alk import environments
from fi.alk.environments import designed
from fi.alk.environments.appointment_booking import suite as appointment_suite
from fi.alk.harness.design import __main__ as design_command
from fi.alk.harness.design import documents, hosted, parse
from fi.alk.harness.design import workspace as design_workspace
from fi.alk.harness import catalogue as design_catalogue
from fi.alk.harness import folder as design_folder
from fi.alk.harness.design.contract import design_contract
from fi.alk.harness.design.documents import _replaced, coerce, parse_documents
from fi.alk.harness.design.hosted import OUTPUT
from fi.alk.harness.design.schema import unseeded_schema
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
    seal_design,
)
from fi.alk.harness.hosted_chat_entrypoint import HostedChatRuntime
from fi.alk.harness.hosted_conversation import _workspace_archive
from fi.alk.harness.releases import (
    WORLD_IMPLEMENTATION,
    PolicyProfile,
    ReleaseError,
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


# --- where a design is sealed --------------------------------------------------------------------


def test_a_design_never_writes_over_a_folder_it_did_not_make(
    tmp_path, monkeypatch
) -> None:
    workspace, out = tmp_path / "workspace", tmp_path / "project"
    open_design(
        workspace, name="desk", template=_template("banking", tmp_path, monkeypatch)
    )
    out.mkdir()
    (out / "README.md").write_text("mine")

    with pytest.raises(ReleaseError) as raised:
        seal_design(workspace, out, postgres_dsn="postgresql://nowhere/x")

    assert raised.value.code == "design_output_exists"
    assert (out / "README.md").read_text() == "mine"


def test_a_design_that_does_not_seal_leaves_its_output_as_it_was(
    tmp_path, monkeypatch
) -> None:
    workspace, out = tmp_path / "workspace", tmp_path / "out"
    open_design(
        workspace, name="desk", template=_template("banking", tmp_path, monkeypatch)
    )
    out.mkdir()
    (out / RESULT).write_text("{}")
    check = next((workspace / "scenarios").glob("*/checks/*.py"))
    check.write_text(check.read_text() + "\n# stale\n")

    with pytest.raises(ReleaseError) as raised:
        seal_design(workspace, out, postgres_dsn="postgresql://nowhere/x")

    assert raised.value.code == "design_check_files_invalid"
    assert [path.name for path in out.iterdir()] == [RESULT]
    assert not list(tmp_path.glob(".out.*"))


def test_a_sealed_design_lands_whole(tmp_path, monkeypatch) -> None:
    workspace, out = tmp_path / "workspace", tmp_path / "new" / "out"
    open_design(
        workspace, name="desk", template=_template("banking", tmp_path, monkeypatch)
    )
    sealed = []

    def seal_suite(root, **_options):
        sealed.append(root)
        assert not out.exists()

    monkeypatch.setattr(design_workspace, "seal_suite_release", seal_suite)

    seal_design(workspace, out, postgres_dsn="")

    assert sorted(path.name for path in out.iterdir()) == ["environment", "suite"]
    assert load_environment_release(out / "environment").name == "desk"
    assert [path.name for path in out.parent.iterdir()] == ["out"]


def _run_arguments(tmp_path: Path, output: Path) -> list[str]:
    return [
        "run",
        "--workspace",
        str(tmp_path / "workspace"),
        "--name",
        "desk",
        "--output",
        str(output),
        "--postgres-dsn",
        "postgresql://nowhere/x",
    ]


@pytest.mark.parametrize("output", ["held", "file"])
def test_the_local_command_refuses_a_foreign_output_before_it_designs(
    tmp_path, monkeypatch, output
) -> None:
    async def never(_args):
        pytest.fail("the design started")

    monkeypatch.setattr(design_command, "_design", never)
    (tmp_path / "held").mkdir()
    (tmp_path / "held" / "notes.txt").write_text("mine")
    (tmp_path / "file").write_text("mine")

    with pytest.raises(SystemExit):
        design_command.main(_run_arguments(tmp_path, tmp_path / output))

    assert (tmp_path / "held" / "notes.txt").read_text() == "mine"


@pytest.mark.parametrize(
    ("given", "used"), [(None, "process"), ("", "process"), ("namespace", "namespace")]
)
def test_the_local_command_isolates_the_code_it_designs(
    tmp_path, monkeypatch, given, used
) -> None:
    seen = []

    async def design(_args):
        seen.append(os.environ["ALK_HANDLER_ISOLATION"])
        return 0

    monkeypatch.setattr(design_command, "_design", design)
    monkeypatch.setenv("ALK_HANDLER_ISOLATION", given or "")
    if given is None:
        monkeypatch.delenv("ALK_HANDLER_ISOLATION")

    assert design_command.main(_run_arguments(tmp_path, tmp_path / "out")) == 0
    assert seen == [used]


# --- documents -----------------------------------------------------------------------------------


def test_a_document_that_takes_too_long_is_recorded_and_the_rest_are_read(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a-slow.txt").write_text("slow")
    (source / "b-fine.txt").write_text("fine")
    run = subprocess.run

    def timed(argv, **options):
        if "a-slow" in argv[3]:
            raise subprocess.TimeoutExpired(argv, options["timeout"])
        return run(argv, **options)

    monkeypatch.setattr(documents.subprocess, "run", timed)

    slow, fine = parse_documents(source, tmp_path / "out")

    assert (slow.parts, slow.error) == ([], "took more than 120 seconds to read")
    assert fine.error == "" and fine.parts


def test_a_sheet_called_parts_does_not_replace_the_index(tmp_path, monkeypatch) -> None:
    table = {"columns": ["sku"], "rows": [["A"]], "truncated": False}
    monkeypatch.setattr(parse, "parse", lambda _source: ("xlsx", [("Parts", table)]))

    assert parse.main(["stock.xlsx", str(tmp_path)]) == 0

    (part,) = json.loads((tmp_path / "parts.json").read_text())["parts"]
    assert part["title"] == "Parts"
    assert json.loads((tmp_path / f"{part['name']}.json").read_text()) == table


def test_a_long_csv_field_is_read(tmp_path) -> None:
    path = tmp_path / "long.csv"
    path.write_text("id,text\n1," + "x" * 200_000 + "\n")

    assert parse.main([str(path), str(tmp_path / "out")]) == 0


def test_a_csv_field_past_the_limit_is_the_documents_error(tmp_path, capsys) -> None:
    path = tmp_path / "long.csv"
    path.write_text("id,text\n1," + "x" * (parse.MOST_CHARS + 1) + "\n")

    assert parse.main([str(path), str(tmp_path / "out")]) == 3
    assert "long.csv" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("value", "declared", "number"),
    [
        ("1,200", "INTEGER", 1200),
        ("1,00,000", "INTEGER", 100000),
        ("$1,200.50", "REAL", 1200.5),
        ("US$ 30", "INTEGER", 30),
        ("USD 30", "INTEGER", 30),
        ("-€5", "INTEGER", -5),
        ("€-5", "INTEGER", -5),
        ("₹2.50", "REAL", 2.5),
        (".5", "REAL", 0.5),
        ("40%", "INTEGER", 40),
        ("1e3", "REAL", 1000.0),
    ],
)
def test_a_number_as_people_write_it_is_coerced(value, declared, number) -> None:
    assert coerce(value, declared) == number


@pytest.mark.parametrize("value", ["1,5", "Room 12", "BAY 12", "1,2,3", "about 5", "USD"])
def test_text_that_only_holds_a_number_is_not_one(value) -> None:
    with pytest.raises(ValueError, match="is not a number"):
        coerce(value, "INTEGER")
    assert coerce(value, "TEXT") == value


def test_made_up_text_is_never_replaced_again() -> None:
    matched: set[str] = set()
    replacements = {"Ann": "Bob", "Bob": "Cal", "Anna Lee": "Ann Ray"}

    text, count = _replaced("Ann met Bob and Anna Lee.", replacements, matched)

    assert text == "Bob met Cal and Ann Ray."
    assert (count, matched) == (3, set(replacements))


def test_a_collation_the_seed_drops_is_listed(tmp_path) -> None:
    database = tmp_path / "world.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT COLLATE NOCASE)"
        )
        connection.execute("CREATE TABLE tags (id TEXT PRIMARY KEY, name TEXT)")
        connection.execute("CREATE UNIQUE INDEX one_name ON tags (name COLLATE NOCASE)")

    assert unseeded_schema(database) == [
        "tags: index one_name collates NOCASE",
        "users: collation NOCASE",
    ]
