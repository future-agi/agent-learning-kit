from __future__ import annotations

import asyncio
import json
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from fi.alk.harness.catalogue import Catalogue, SubGoal, load_catalogue, save_catalogue
from fi.alk.harness.design import hosted
from fi.alk.harness.design.__main__ import main
from fi.alk.harness.design.hosted import OUTPUT, ready_marker
from fi.alk.harness.design.proof import ViewProof
from fi.alk.harness.design.reviewed_sources import (
    EVIDENCE,
    bind_scenario,
    check_grounded_suite,
    document_manifest,
)
from fi.alk.harness.design.session import open_for_designing
from fi.alk.harness.design.workspace import (
    DesignTemplate,
    Provenance,
    marker_matches,
    seal_design,
    Proposal,
    load_design,
    open_design,
    release_view,
    save_design,
)
from fi.alk.harness.folder import document_for, read_all, write_folder
from fi.alk.harness.releases import (
    POLICY,
    TOOLS,
    ReleaseError,
    ReleaseRef,
    load_environment_release,
    load_suite_release,
)
from fi.alk.harness.scenario import Scenario
from fi.alk.harness.source_grounding import safe_source_context
from fi.alk.harness.world.runtime import GeneratedWorld
from fi.alk.harness.world.snapshot import save
from fi.alk.studio._environment_input_snapshots import serialize_environment_snapshot
import test_environment_inputs
from test_environment_inputs import _approve_proposals, _reviewed, _sources


def _receipt(reviewed, uploads):
    bindings = []
    for source in reviewed.sources:
        binding = {
            "source_id": source.pin.source_id,
            "source_sha256": source.pin.source_sha256,
            "parser_revision": source.pin.parser_revision,
            "role": source.pin.role,
        }
        origin = (
            {"kind": "upload", "document_id": source.filename}
            if uploads
            else {
                "kind": "knowledge_base",
                "kb_id": "kb-secret",
                "file_id": source.filename,
            }
        )
        binding["origin"] = {**origin, "original_sha256": source.pin.source_sha256}
        bindings.append(binding)
    return {
        "review_id": "private-review-id",
        "snapshot_id": reviewed.snapshot_id,
        "snapshot_digest": reviewed.snapshot_digest,
        "bindings": bindings,
    }


def _snapshot(tmp_path: Path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    for source in _sources():
        (uploads / source.filename).write_bytes(source.content)
    _, reviewed = _reviewed()
    private = tmp_path / "private.json"
    private.write_bytes(serialize_environment_snapshot(reviewed))
    return uploads, private, reviewed, _receipt(reviewed, uploads)


def test_raw_uploads_wait_for_source_review_in_both_entrypoints(tmp_path):
    uploads, _, _, _ = _snapshot(tmp_path)
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
        {
            "id": document.id,
            "name": document.file,
            "sha256": document.sha256,
            "size": document.size,
        }
        for document in document_manifest(uploads)
    ]
    job.write_text(
        json.dumps(
            {
                "metadata": {
                    "design": {
                        "attempt_id": "current",
                        "name": "refunds",
                        "documents": documents,
                    }
                }
            }
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
    uploads, private, reviewed, receipt = _snapshot(tmp_path)
    workspace = tmp_path / "workspace"
    documents = [
        {"id": doc.id, "name": doc.file, "sha256": doc.sha256, "size": doc.size}
        for doc in document_manifest(uploads)
    ]
    job = tmp_path / "job.json"
    job.write_text(
        json.dumps(
            {
                "scenario_count": 3,
                "metadata": {
                    "design": {
                        "attempt_id": "current",
                        "name": "refunds",
                        "documents": documents,
                    },
                    "source_review_receipt": receipt,
                },
            }
        )
    )

    workspace.mkdir()
    (workspace / OUTPUT).mkdir()
    (workspace / OUTPUT / hosted.RESULT).write_text(
        '{"status":"failed","attempt_id":"old"}'
    )
    (workspace / hosted.FINISHED_MARKER).write_text('{"attempt_id":"old"}')
    ready_marker(workspace).write_text('{"attempt_id":"old"}')

    def finish(_seconds):
        assert marker_matches(ready_marker(workspace), "current")
        assert load_design(workspace).reviewed_source.digest == reviewed.snapshot_digest
        assert load_design(workspace).scenario_count == 3
        (workspace / OUTPUT / hosted.RESULT).write_text(
            '{"status":"completed","attempt_id":"current"}'
        )
        (workspace / hosted.FINISHED_MARKER).write_text('{"attempt_id":"current"}')

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


def test_chat_ignores_readiness_and_completion_from_previous_attempt(
    tmp_path, monkeypatch
):
    ready_marker(tmp_path).write_text('{"attempt_id":"old"}')
    (tmp_path / hosted.FINISHED_MARKER).write_text('{"attempt_id":"old"}')
    waited = False

    async def wait(_seconds):
        nonlocal waited
        waited = True
        ready_marker(tmp_path).write_text('{"attempt_id":"current"}')

    monkeypatch.setattr(hosted.asyncio, "sleep", wait)
    asyncio.run(hosted.wait_for_design(tmp_path, "current"))
    assert waited


def test_snapshot_is_pinned_and_projection_survives_resume(tmp_path):
    uploads, private, reviewed, receipt = _snapshot(tmp_path)
    workspace = tmp_path / "workspace"
    design = open_design(
        workspace,
        name="refunds",
        documents=document_manifest(uploads),
        reviewed_snapshot=private,
        source_review_receipt=receipt,
    )
    assert design.stage == "interview"
    assert design.reviewed_source.id == reviewed.snapshot_id
    assert design.reviewed_source.digest == reviewed.snapshot_digest
    before = (workspace / EVIDENCE).read_bytes()
    assert (
        open_design(
            workspace,
            name="refunds",
            reviewed_snapshot=private,
            source_review_receipt=receipt,
        )
        == design
    )
    with pytest.raises(ReleaseError, match="source_snapshot_required"):
        open_design(workspace, name="refunds")
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
    assert (
        open_design(
            workspace,
            name="refunds",
            reviewed_snapshot=private,
            source_review_receipt=receipt,
        )
        == design
    )
    assert (workspace / EVIDENCE).read_bytes() == before


def test_accepted_snapshot_requires_an_exact_receipt_even_for_kb_only(tmp_path):
    uploads, private, reviewed, receipt = _snapshot(tmp_path)
    docs = document_manifest(uploads)
    with pytest.raises(ReleaseError, match="source_review_receipt_required"):
        open_design(tmp_path / "missing", name="scratch", reviewed_snapshot=private)

    wrong_upload = json.loads(json.dumps(receipt))
    wrong_upload["bindings"][0]["origin"]["document_id"] = "some-other-document"
    with pytest.raises(ReleaseError, match="source_review_receipt_mismatch"):
        open_design(
            tmp_path / "wrong",
            name="scratch",
            documents=docs,
            reviewed_snapshot=private,
            source_review_receipt=wrong_upload,
        )

    knowledge = _receipt(reviewed, None)
    workspace = tmp_path / "knowledge"
    design = open_design(
        workspace,
        name="scratch",
        reviewed_snapshot=private,
        source_review_receipt=knowledge,
    )
    assert design.documents == []
    assert design.reviewed_source.id == reviewed.snapshot_id
    assert b"kb-secret" not in (workspace / EVIDENCE).read_bytes()
    assert b"private-review-id" not in (workspace / "design.json").read_bytes()
    with pytest.raises(ReleaseError, match="source_snapshot_required"):
        open_design(workspace, name="scratch", source_review_receipt=knowledge)
    missing_binding = {**knowledge, "bindings": knowledge["bindings"][:-1]}
    with pytest.raises(ReleaseError, match="source_review_receipt_mismatch"):
        open_design(
            workspace,
            name="scratch",
            reviewed_snapshot=private,
            source_review_receipt=missing_binding,
        )


def test_equal_uploaded_bytes_can_have_distinct_approved_source_ids(tmp_path):
    first = _sources()[0]
    second = replace(first, source_id="second-policy", filename="second.pdf")
    _, reviewed = _approve_proposals((first, second))
    private = tmp_path / "private.json"
    private.write_bytes(serialize_environment_snapshot(reviewed))
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    for original in (first, second):
        (uploads / original.filename).write_bytes(original.content)
    receipt = _receipt(reviewed, uploads)
    workspace = tmp_path / "workspace"
    design = open_design(
        workspace,
        name="scratch",
        documents=document_manifest(uploads),
        reviewed_snapshot=private,
        source_review_receipt=receipt,
    )
    assert design.reviewed_source.id == reviewed.snapshot_id
    receipt["bindings"][1]["source_id"] = receipt["bindings"][0]["source_id"]
    with pytest.raises(ReleaseError, match="source_review_receipt_mismatch"):
        open_design(
            workspace,
            name="scratch",
            reviewed_snapshot=private,
            source_review_receipt=receipt,
        )


def test_blocking_diagnostics_stop_authoring_and_keep_private_detail_out(tmp_path):
    _, reviewed = _approve_proposals((_sources()[0],), hidden=("public-policy",))
    private = tmp_path / "private.json"
    private.write_bytes(serialize_environment_snapshot(reviewed))
    receipt_file = tmp_path / "receipt.json"
    receipt_file.write_text(json.dumps(_receipt(reviewed, None)))
    workspace = tmp_path / "workspace"
    output = tmp_path / "output"
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
                "--reviewed-snapshot",
                str(private),
                "--reviewed-receipt",
                str(receipt_file),
                "--postgres-dsn",
                "unused",
            ]
        )
        == 2
    )
    result = json.loads((output / "result.json").read_bytes())
    assert result["failure"] == {
        "code": "source_scenarios_blocked",
        "message": "10 source scenarios are blocked by validation.",
    }
    assert not (workspace / EVIDENCE).exists()


def test_changed_approved_value_changes_native_bound_fixture(tmp_path, monkeypatch):
    original = _sources()
    bound = []
    for status in ("eligible", "expired"):
        changed = replace(
            original[-1],
            content=b'[{"buyer_id":"C17","status":"' + status.encode() + b'"}]',
        )
        monkeypatch.setattr(
            test_environment_inputs, "_sources", lambda: (*original[:-1], changed)
        )
        _, reviewed = _reviewed()
        private = tmp_path / f"{status}.json"
        private.write_bytes(serialize_environment_snapshot(reviewed))
        workspace = tmp_path / status
        open_design(
            workspace,
            name="refunds",
            requirements="Handle a refund",
            scenario_count=2,
            reviewed_snapshot=private,
            source_review_receipt=_receipt(reviewed, None),
        )
        evidence = json.loads((workspace / EVIDENCE).read_bytes())
        case = next(
            case
            for case in evidence["cases"]
            if any(fact["field"] == "status" for fact in case["starting_facts"])
        )
        args = {
            "name": "refund",
            "instruction": f"Ask whether this {status} order can receive a refund.",
            "tests": "Check the approved refund source for order C17.",
            "fixture": {"origin": "seed", "reviewed_source": case["id"]},
        }
        assert bind_scenario(evidence, args) == ""
        bound.append(
            document_for(Scenario.model_validate(args))["fixture"]["reviewed_source"]
        )
    assert [
        next(
            fact["value"]
            for fact in case["starting_facts"]
            if fact["field"] == "status"
        )
        for case in bound
    ] == ["eligible", "expired"]
    assert bound[0]["id"] != bound[1]["id"]
    assert {"source_id": "orders", "page": None, "row": 1} in bound[1]["citations"]


def test_design_rejects_unrelated_scenario_with_approved_fixture():
    evidence = {
        "cases": [
            {
                "id": "source-case-order",
                "category": "success",
                "purpose": "Test a compliant outcome.",
                "starting_facts": [
                    {
                        "source_id": "orders",
                        "record_id": "4821",
                        "field": "status",
                        "value": "shipped",
                    }
                ],
                "policy_reference": "Shipped orders may be tracked.",
                "citations": [{"source_id": "shipping-policy", "page": 1, "row": None}],
            }
        ]
    }
    args = {
        "name": "flight",
        "instruction": "Book a flight to Paris.",
        "tests": "Confirms the selected airline seat.",
        "fixture": {"reviewed_source": "source-case-order"},
    }
    assert bind_scenario(evidence, args).startswith("The scenario is unrelated")


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
                "records": ["Orders and refund requests"],
                "tools": ["Check eligibility"],
                "rules": ["Approve eligible orders"],
                "people": ["Returns supervisor"],
            },
        )
        assert not response.get("is_error"), response
        approved = load_design(tmp_path)
        assert approved.stage == "world"
        assert approved.proposal.tools == ["Check eligibility"]
        assert approved.proposal.people == ["Returns supervisor"]
    finally:
        tools.world.close()


def test_native_scenario_proof_keeps_public_facts_and_citations(tmp_path, monkeypatch):
    monkeypatch.setenv("ALK_HARNESS_MODEL", "test-model")
    uploads, private, _, receipt = _snapshot(tmp_path)
    workspace = tmp_path / "workspace"
    design = open_design(
        workspace,
        name="refunds",
        requirements="Approve an eligible refund",
        documents=document_manifest(uploads),
        reviewed_snapshot=private,
        source_review_receipt=receipt,
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
    (workspace / POLICY).write_text('{"rules":[]}')
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
            if case["category"] == "success"
            and "approved" in case["policy_reference"]
            and any(fact["field"] == "status" for fact in case["starting_facts"])
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
                "negative_controls": [
                    {
                        "name": "refunds-ineligible-orders",
                        "why": "Targets orders with the wrong eligibility status.",
                        "steps": [
                            {
                                "tool": "approve_refund",
                                "arguments": {"status": "ineligible"},
                            }
                        ],
                    }
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
        assert any(fact["value"] == "eligible" for fact in grounding["starting_facts"])
        assert {"source_id": "public-policy", "page": 1, "row": None} in grounding[
            "citations"
        ]
        assert "approved" in grounding["policy_reference"]
        assert all("situation" not in case for case in evidence["cases"])

        catalogue = load_catalogue(workspace)
        native = read_all(workspace)[0]
        with pytest.raises(ReleaseError, match="design_source_required"):
            check_grounded_suite(workspace, evidence, design)
        extra = native.model_copy(update={"name": "extra", "scenario_key": "extra"})
        write_folder(extra, catalogue, workspace)
        with pytest.raises(ReleaseError, match="design_source_duplicate"):
            check_grounded_suite(workspace, evidence, design)
        extra.fixture = {"origin": "seed", "reviewed_source": {"id": "unknown"}}
        write_folder(extra, catalogue, workspace)
        with pytest.raises(ReleaseError, match="design_source_invalid"):
            check_grounded_suite(workspace, evidence, design)
        extra.fixture = {"origin": "seed"}
        write_folder(extra, catalogue, workspace)
        design = load_design(workspace).model_copy(
            update={
                "template": DesignTemplate(
                    environment=ReleaseRef(
                        name="template", digest="sha256:" + "a" * 64
                    ),
                    suite=ReleaseRef(name="template_base", digest="sha256:" + "b" * 64),
                    scenario_keys=["extra"],
                ),
                "provenance": [
                    Provenance(
                        target="table:orders", document="previous", sha256="example"
                    )
                ],
            }
        )
        save_design(workspace, design)
        for index, case in enumerate(evidence["cases"]):
            if case == grounding:
                continue
            key = f"approved-{index}"
            rebound = native.model_copy(
                update={
                    "name": key,
                    "scenario_key": key,
                        "instruction": f"Handle a refund for {safe_source_context(case)}.",
                        "tests": "Checks the refund outcome.",
                        "variables": {},
                    "fixture": {"origin": "seed", "reviewed_source": case["id"]},
                }
            ).model_dump(mode="json")
            assert bind_scenario(evidence, rebound) == ""
            write_folder(
                Scenario.model_validate(rebound),
                catalogue,
                workspace,
            )

        release = tmp_path / "sealed"
        environment, suite = seal_design(workspace, release, postgres_dsn="")
        pin = design.reviewed_source.model_dump(include={"id", "digest"})
        assert (
            environment.metadata["reviewed_source"]
            == suite.metadata["reviewed_source"]
            == pin
        )
        assert environment.metadata["provenance"][0]["target"] == "table:orders"
        assert suite.metadata["template_suite"] == design.template.suite.digest
        assert load_environment_release(release / "environment") == environment
        assert load_suite_release(release / "suite") == suite
        for artifact in (*workspace.rglob("*"), *release.rglob("*")):
            if artifact.is_file():
                content = artifact.read_bytes()
                assert b"HIDDEN_SENTINEL_74Q" not in content
                assert b"Alice" not in content
                assert b"private-review-id" not in content
    finally:
        tools.world.close()


def test_wrong_record_role_is_rejected_before_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("ALK_HARNESS_MODEL", "test-model")
    open_design(tmp_path, name="returns")
    stage, tools = _stage(tmp_path)
    evidence = {"cases": [{"starting_facts": [
        {"field": "expected_action", "value": "escalate_ACORN-58"}
    ]}]}
    monkeypatch.setattr("fi.alk.harness.design.tools.load_evidence", lambda *_: evidence)
    args = {"one_liner": "Manage returns", "summary": "Routes to queues (e.g. ACORN-58).",
            "use_cases": ["Escalate a return"]}
    try:
        failed = _tool(stage, "design", "propose_design", args)
        assert failed["is_error"]
        assert load_design(tmp_path).pending_proposal is None
        corrected = {**args, "summary": "Escalate with code ACORN-58."}
        assert not _tool(stage, "design", "propose_design", corrected).get("is_error")
        assert load_design(tmp_path).stage == "world"
    finally:
        tools.world.close()


def test_proposal_only_attributes_a_matching_public_clause_to_its_page():
    from fi.alk.harness.design.reviewed_sources import proposal_rule_sources

    evidence = {"cases": [{
        "policy_reference": "1. Damaged items qualify for prepaid return shippin\ng\n.",
        "citations": [{"source_id":"policy", "page":2}, {"source_id":"orders", "row":1}],
    }]}
    rules = ["[agent_policy] Damaged items qualify for prepaid return shipping.",
             "Damaged items never qualify for prepaid return shipping."]
    assert proposal_rule_sources(rules, evidence) == [{"rule_index":0, "source_id":"policy", "page":2}]


def test_build_approval_requires_resolving_every_dependency(tmp_path):
    from fi.alk.harness.design.tools import settle_proposal

    open_design(tmp_path, name="returns")
    proposal = Proposal(
        one_liner="Handle returns",
        summary="Answer return requests",
        use_cases=["Damaged item"],
        dependencies=["Inventory service access"],
    )
    for dependencies, expected in ((proposal.dependencies, False), ([], True)):
        pending = proposal.model_copy(update={"dependencies": dependencies})
        design = load_design(tmp_path).model_copy(update={"pending_proposal": pending})
        save_design(tmp_path, design)
        assert settle_proposal(tmp_path, "Build this") is expected
        assert (load_design(tmp_path).proposal is not None) is expected


def test_revealed_policy_answer_is_corrected_before_first_design_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("ALK_HARNESS_MODEL", "test-model")
    open_design(tmp_path, name="returns")
    stage, tools = _stage(tmp_path)
    rule = "Refunds above $425 require human escalation. The escalation code is ORCHID-72."
    monkeypatch.setattr("fi.alk.harness.design.tools.load_evidence", lambda *_: {"cases":[{"policy_reference":rule}]})
    proposal = {"one_liner":"Handle returns", "summary":"Evaluate refunds", "rules":[rule],
                "use_cases":["Escalate above $425 with code ORCHID-72"]}
    try:
        assert _tool(stage, "design", "propose_design", proposal)["is_error"]
        assert load_design(tmp_path).pending_proposal is None
        proposal["use_cases"] = ["High-value refund escalation"]
        assert not _tool(stage, "design", "propose_design", proposal).get("is_error")
        assert load_design(tmp_path).proposal.rules == [rule]
    finally:
        tools.world.close()
