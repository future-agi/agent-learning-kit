from __future__ import annotations

import asyncio
import json
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from fi.alk.harness.catalogue import Catalogue, SubGoal, save_catalogue
from fi.alk.harness.design import hosted
from fi.alk.harness.design.__main__ import main
from fi.alk.harness.design.hosted import OUTPUT, ready_marker
from fi.alk.harness.design.proof import ViewProof
from fi.alk.harness.design.reviewed_sources import EVIDENCE, document_manifest
from fi.alk.harness.design.session import open_for_designing
from fi.alk.harness.design.workspace import (
    Proposal,
    load_design,
    open_design,
    release_view,
    save_design,
)
from fi.alk.harness.releases import TOOLS, ReleaseError
from fi.alk.harness.world.runtime import GeneratedWorld
from fi.alk.harness.world.snapshot import save
from fi.alk.studio._environment_input_snapshots import serialize_environment_snapshot
from test_environment_inputs import _approve_proposals, _reviewed, _sources


def _snapshot(tmp_path: Path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    for source in _sources():
        (uploads / source.filename).write_bytes(source.content)
    _, reviewed = _reviewed()
    private = tmp_path / "private.json"
    private.write_bytes(serialize_environment_snapshot(reviewed))
    return uploads, private, reviewed


def test_raw_uploads_wait_for_source_review_in_both_entrypoints(tmp_path):
    uploads, _, _ = _snapshot(tmp_path)
    output = tmp_path / "output"
    workspace = tmp_path / "design"
    assert (
        main(
            [
                "run",
                "--workspace",
                str(workspace),
                "--output",
                str(output),
                "--name",
                "refunds",
                "--documents",
                str(uploads),
                "--postgres-dsn",
                "unused",
            ]
        )
        == 2
    )
    assert (
        json.loads((output / "result.json").read_bytes())["failure"]["code"]
        == "source_review_required"
    )
    assert load_design(workspace).stage == "interview"
    assert not (workspace / EVIDENCE).exists()

    hosted = tmp_path / "hosted"
    job = tmp_path / "job.json"
    documents = [
        {"name": document.file, "sha256": document.sha256, "size": document.size}
        for document in document_manifest(uploads)
    ]
    job.write_text(
        json.dumps(
            {"metadata": {"design": {"name": "refunds", "documents": documents}}}
        )
    )
    assert (
        main(
            [
                "hosted",
                "--job",
                str(job),
                "--releases",
                str(tmp_path),
                "--workspace",
                str(hosted),
            ]
        )
        == 2
    )
    result = json.loads((hosted / OUTPUT / "result.json").read_bytes())
    assert result["failure"]["code"] == "source_review_required"
    assert not ready_marker(hosted).exists()


def test_hosted_control_starts_from_approved_snapshot_without_raw_mount(
    tmp_path, monkeypatch
):
    uploads, private, reviewed = _snapshot(tmp_path)
    workspace = tmp_path / "workspace"
    documents = [
        {"name": doc.file, "sha256": doc.sha256, "size": doc.size}
        for doc in document_manifest(uploads)
    ]
    job = tmp_path / "job.json"
    job.write_text(
        json.dumps(
            {
                "scenario_count": 3,
                "metadata": {"design": {"name": "refunds", "documents": documents}},
            }
        )
    )

    def finish(_seconds):
        assert ready_marker(workspace).is_file()
        assert load_design(workspace).reviewed_source.digest == reviewed.snapshot_digest
        assert load_design(workspace).scenario_count == 3
        (workspace / OUTPUT).mkdir()
        (workspace / OUTPUT / hosted.RESULT).write_text('{"status":"completed"}')
        (workspace / hosted.FINISHED_MARKER).touch()

    monkeypatch.setattr(
        hosted, "time", SimpleNamespace(monotonic=lambda: 0, sleep=finish)
    )
    assert (
        main(
            [
                "hosted",
                "--job",
                str(job),
                "--releases",
                str(tmp_path),
                "--workspace",
                str(workspace),
                "--documents",
                str(tmp_path / "unmounted"),
                "--reviewed-snapshot",
                str(private),
            ]
        )
        == 0
    )
    assert (workspace / EVIDENCE).is_file()


def test_snapshot_is_pinned_and_projection_survives_resume(tmp_path):
    uploads, private, reviewed = _snapshot(tmp_path)
    workspace = tmp_path / "workspace"
    design = open_design(
        workspace,
        name="refunds",
        documents=document_manifest(uploads),
        reviewed_snapshot=private,
    )
    assert design.stage == "interview"
    assert design.reviewed_source.id == reviewed.snapshot_id
    assert design.reviewed_source.digest == reviewed.snapshot_digest
    before = (workspace / EVIDENCE).read_bytes()
    assert open_design(workspace, name="refunds", reviewed_snapshot=private) == design
    assert open_design(workspace, name="refunds") == design
    assert (workspace / EVIDENCE).read_bytes() == before

    tampered = tmp_path / "tampered.json"
    tampered.write_bytes(
        private.read_bytes().replace(b"HIDDEN_SENTINEL_74Q", b"FORGED_SENTINEL")
    )
    with pytest.raises(ReleaseError, match="source_snapshot_invalid"):
        open_design(workspace, name="refunds", reviewed_snapshot=tampered)
    _, different = _approve_proposals(_sources()[:1])
    tampered.write_bytes(serialize_environment_snapshot(different))
    with pytest.raises(ReleaseError, match="source_snapshot_changed"):
        open_design(workspace, name="refunds", reviewed_snapshot=tampered)
    assert (workspace / EVIDENCE).read_bytes() == before
    assert load_design(workspace) == design

    (workspace / EVIDENCE).write_bytes(before + b" ")
    with pytest.raises(ReleaseError, match="source_snapshot_invalid"):
        open_design(workspace, name="refunds")


async def _approve(*_args):
    return "approve"


def _stage(workspace: Path):
    def dsn():
        return "unused"

    return open_for_designing(
        workspace,
        approve=_approve,
        postgres_dsn=dsn,
        output=workspace / OUTPUT,
        view_proof=ViewProof(workspace, dsn),
    )


def _tool(stage, server: str, name: str, args: dict):
    return asyncio.run(
        next(
            tool.handler(args)
            for tool in stage.spec.servers[server].tools
            if tool.name == name
        )
    )


def test_source_free_design_keeps_the_existing_interview(tmp_path, monkeypatch):
    monkeypatch.setenv("ALK_HARNESS_MODEL", "test-model")
    open_design(tmp_path, name="plain")
    stage, tools = _stage(tmp_path)
    try:
        assert "Read" in stage.spec.builtins
        assert "reviewed_sources" not in stage.spec.servers
        response = _tool(
            stage,
            "design",
            "propose_design",
            {
                "one_liner": "Refund a fee",
                "summary": "A refund is approved for an eligible order.",
                "use_cases": ["Approve an eligible refund"],
            },
        )
        assert not response.get("is_error"), response
        assert load_design(tmp_path).stage == "world"
    finally:
        tools.world.close()


def test_native_scenario_proof_keeps_public_facts_and_citations(tmp_path, monkeypatch):
    monkeypatch.setenv("ALK_HARNESS_MODEL", "test-model")
    uploads, private, _ = _snapshot(tmp_path)
    workspace = tmp_path / "workspace"
    design = open_design(
        workspace,
        name="refunds",
        requirements="Approve an eligible refund",
        documents=document_manifest(uploads),
        reviewed_snapshot=private,
        scenario_count=6,
    )
    proposal = Proposal(
        one_liner="Refund a fee",
        summary="A refund is approved for an eligible order.",
        use_cases=["Approve an eligible refund"],
    )
    save_design(
        workspace, design.model_copy(update={"stage": "suite", "proposal": proposal})
    )
    (workspace / TOOLS).write_text(
        json.dumps(
            [
                {
                    "name": "lookup_eligibility",
                    "description": "Check eligibility for a refund.",
                    "parameters": {"type": "object", "properties": {}},
                    "mutates_state": False,
                },
                {
                    "name": "approve_refund",
                    "description": "Approve a refund for a given status.",
                    "parameters": {
                        "type": "object",
                        "properties": {"status": {"type": "string"}},
                        "required": ["status"],
                    },
                    "mutates_state": True,
                },
            ]
        )
    )

    class RefundWorld(GeneratedWorld):
        name = "refunds"
        tools = [{"name": "lookup_eligibility"}, {"name": "approve_refund"}]
        handlers = {
            "lookup_eligibility": "def handle(args, db):\n"
            "    return {'status': db.one(\"SELECT status FROM orders WHERE id = 'fee'\")['status']}\n",
            "approve_refund": "def handle(args, db):\n"
            "    db.execute('UPDATE orders SET refunded = 1 WHERE status = ?', (args['status'],))\n"
            "    return {'approved': True}\n",
        }

    with closing(RefundWorld(":memory:")) as world:
        world.connection.executescript(
            "CREATE TABLE orders (id TEXT PRIMARY KEY, status TEXT, refunded INTEGER);"
            "INSERT INTO orders VALUES ('fee', 'eligible', 0);"
        )
        save(world, workspace)
    save_catalogue(
        Catalogue(
            sub_goals=[
                SubGoal(
                    name="refund-approved",
                    what="the eligible order was refunded",
                    check="def check(world, calls):\n"
                    "    row = world.state()['orders'][0]\n"
                    "    return None if row['status'] == 'eligible' and row['refunded'] else 'refund not approved'\n",
                )
            ]
        ),
        workspace,
    )

    stage, tools = _stage(workspace)
    try:
        assert not {"Read", "Glob", "Grep"}.intersection(stage.spec.builtins)
        assert "documents" not in stage.spec.servers
        assert not stage.spec.workers
        assert "HIDDEN_SENTINEL_74Q" not in stage.spec.system_prompt
        evidence = json.loads((workspace / EVIDENCE).read_bytes())
        case_index, expected = next(
            (index, case)
            for index, case in enumerate(evidence["cases"], start=1)
            if any(fact["field"] == "status" for fact in case["facts"])
        )
        shown = _tool(
            stage,
            "reviewed_sources",
            "read_source",
            {"section": "cases", "index": case_index},
        )
        assert json.loads(shown["content"][0]["text"]) == expected
        records = _tool(
            stage, "reviewed_sources", "read_source", {"section": "records", "index": 1}
        )
        assert "C17" not in records["content"][0]["text"]
        result = _tool(
            stage,
            "scenarios",
            "submit_scenario",
            {
                "name": "approve-eligible-refund",
                "use_case": proposal.use_cases[0],
                "instruction": "Ask for an eligible order to be refunded.",
                "tests": "Checks eligibility before approving the refund.",
                "fixture": {"origin": "seed", "reviewed_source": expected["id"]},
                "solution": [
                    {"tool": "lookup_eligibility", "arguments": {}},
                    {"tool": "approve_refund", "arguments": {"status": "eligible"}},
                ],
                "sub_goals": ["refund-approved"],
            },
        )
        assert not result.get("is_error"), result
        _, suite = release_view(workspace, tmp_path / "release")
        saved = json.loads(
            (suite / "scenarios/approve-eligible-refund/scenario.json").read_bytes()
        )
        grounding = saved["fixture"]["reviewed_source"]
        assert any(fact["value"] == "eligible" for fact in grounding["facts"])
        assert {"source_id": "public-policy", "page": 1, "row": None} in grounding[
            "citations"
        ]
        for artifact in (*workspace.rglob("*"), *suite.parent.rglob("*")):
            if artifact.is_file():
                content = artifact.read_bytes()
                assert b"HIDDEN_SENTINEL_74Q" not in content
                assert b"Alice" not in content
    finally:
        tools.world.close()
