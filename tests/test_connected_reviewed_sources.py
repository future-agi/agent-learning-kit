from __future__ import annotations

import asyncio
import io
import json
import shutil
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_environment_source_audiences import _approve
from test_genuine_agent_source_scenarios import _reviewed_order_knowledge
from test_reviewed_design_sources import _receipt

from fi.alk.harness import (
    authoring_entrypoint,
    cli,
    hosted_authoring_entrypoint,
    scenarios,
)
from fi.alk.harness.catalogue import Catalogue, SubGoal, save_catalogue
from fi.alk.harness.chat_policy import load_chat_policy
from fi.alk.harness.contract import AgentContract
from fi.alk.harness.hosted_chat_entrypoint import HostedChatRuntime
from fi.alk.harness.hosted_conversation import _workspace_archive
from fi.alk.harness.proofs import load_proofs
from fi.alk.harness.releases import ReleaseError
from fi.alk.harness.reviewed_build import PIN_FILE, prepare_reviewed_build
from fi.alk.harness.world.runtime import GeneratedWorld
from fi.alk.harness.world.snapshot import save
from fi.alk.studio import (
    EnvironmentRequirements,
    EnvironmentSource,
    SourceAudience,
    generate_source_scenarios,
    serialize_environment_snapshot,
)


def _launch(root, reviewed, *, count=1, uploads=False):
    root.mkdir()
    source = root / "agent"
    source.mkdir()
    fixture = Path(__file__).parents[1] / "examples/frameworks/openai_agents_agent.py"
    shutil.copyfile(fixture, source / "agent.py")
    snapshot = root / "reviewed-source-snapshot.json"
    snapshot.write_bytes(serialize_environment_snapshot(reviewed))
    receipt = _receipt(reviewed, uploads)
    job = {
        "job_id": "order-job",
        "run_id": "order-run",
        "execution": "hosted",
        "source": {
            "kind": "github",
            "visibility": "public",
            "repository": "acme/order-agent",
            "commit_sha": "a" * 40,
        },
        "runtime": {"isolation": "dedicated_vm"},
        "agent": {"connector": "auto"},
        "scenario_count": count,
        "metadata": {"agent_name": "order-support", "source_review_receipt": receipt},
    }
    if uploads:
        job["metadata"]["source_review_documents"] = [
            {
                "id": item.filename,
                "name": item.filename,
                "sha256": item.pin.source_sha256,
                "size": 1,
            }
            for item in reviewed.sources
        ]
    job_path = root / "reviewed-source-job.json"
    job_path.write_text(json.dumps(job))
    return job, job_path, snapshot, source, root / "workspace"


def _prepare(launch):
    job, job_path, snapshot, source, workspace = launch
    return prepare_reviewed_build(
        job,
        workspace=workspace,
        source=source,
        snapshot=snapshot,
        job_path=job_path,
    )


def _only(role, *, visible=True):
    item = (
        EnvironmentSource("orders", "orders.csv", "text/csv", b"id\n4821\n", role)
        if role == "seed_records"
        else EnvironmentSource(
            "kb-file",
            "shipping.txt",
            "text/plain",
            b"Order 4821 is shipped and will arrive on Tuesday.",
            "reference",
        )
    )
    return _approve(
        (item,), {item.source_id: SourceAudience(visible)}, infer_schema=True
    )


@pytest.mark.parametrize("role", ["reference", "seed_records"])
def test_connected_source_without_policy_is_grounded_and_unmeasured(tmp_path, role):
    reviewed = _only(role)
    generated = generate_source_scenarios(
        reviewed,
        environment_requirements=EnvironmentRequirements(task="Track shipment"),
        requested_count=3,
    )
    assert [(one.category, one.status) for one in generated.drafts] == [
        ("success", "reviewable")
    ] * 3
    assert all(
        one.expected_result == "unmeasured" and not one.checks
        for one in generated.evaluator_specs
    )
    case = generated.public_scenarios[0].dataset[0].persona
    assert "4821" in json.dumps(case)
    assert case["citations"]
    clean, approved = _prepare(_launch(tmp_path / "case", reviewed))
    assert approved.cases[0]["citations"] == case["citations"]
    assert "4821" in json.dumps(approved.cases[0])
    assert clean["metadata"] == {"agent_name": "order-support"}


def test_controller_refuses_unapproved_or_private_only_sources(tmp_path):
    launch = _launch(tmp_path / "blocked", _only("reference", visible=False))
    with pytest.raises(ReleaseError, match="source_scenarios_blocked"):
        _prepare(launch)
    launch = _launch(
        tmp_path / "invalid", _reviewed_order_knowledge(infer_schema=True), uploads=True
    )
    job, _, snapshot, _, _ = launch
    receipt = job["metadata"]["source_review_receipt"]
    binding = receipt["bindings"][0]
    binding["origin"]["document_id"] = "different-doc"
    with pytest.raises(ReleaseError, match="source_review_receipt_mismatch"):
        _prepare(launch)
    binding["origin"]["document_id"] = job["metadata"]["source_review_documents"][0][
        "id"
    ]
    clean, _ = _prepare(launch)
    job["metadata"] = clean["metadata"]
    with pytest.raises(ReleaseError, match="source_review_receipt_required"):
        _prepare(launch)
    snapshot.unlink()
    with pytest.raises(ReleaseError, match="source_snapshot_required"):
        _prepare(launch)


def _contract():
    return AgentContract(
        agent="order-support",
        system_prompt_excerpt="You help customers find their order status.",
        real_use_cases=["Track an order", "Check a shipment"],
    )


def _tool(servers, group, name):
    return next(one.handler for one in servers[group].tools if one.name == name)


def _read_case(servers, index=1):
    response = asyncio.run(
        _tool(servers, "reviewed_sources", "read_source")(
            {"section": "cases", "index": index}
        )
    )
    return json.loads(response["content"][0]["text"])


def _load_world(workspace):
    world = GeneratedWorld(":memory:")
    try:
        save(world, workspace)
    finally:
        world.close()
    save_catalogue(
        Catalogue(
            sub_goals=[
                SubGoal(
                    name="order-status",
                    judged="Reports the source-supported shipment status.",
                )
            ]
        ),
        workspace,
    )


def _arguments(case, name="Track a shipment"):
    return {
        "name": name,
        "use_case": "Track an order",
        "instruction": "Ask about the status of order 4821.",
        "tests": "Reports the available status of the shipment.",
        "fixture": {"origin": "seed", "reviewed_source": case["id"]},
        "sub_goals": ["order-status"],
        "solution": [],
    }


def test_connected_rejects_unrelated_scenario_with_approved_fixture(tmp_path):
    _, approved = _prepare(
        _launch(
            tmp_path / "case", _reviewed_order_knowledge("Tuesday", infer_schema=True)
        )
    )
    with pytest.raises(ReleaseError, match="source_case_unrelated"):
        approved.bind(
            {
                **_arguments(approved.cases[0]),
                "instruction": "Book a flight to Paris.",
                "tests": "Confirms the selected airline seat.",
            },
            [],
        )


def test_foreground_uses_approved_case_and_native_proof(tmp_path, monkeypatch):
    monkeypatch.setenv("ALK_HARNESS_MODEL", "claude-sonnet-4-6")
    policy = tmp_path / "policy.yaml"
    policy.write_text("schema_version: 1\nharness_chat:\n  enabled: true\n")
    results = []
    for n, eta in enumerate(("Tuesday", "Tuesday", "Thursday")):
        job, job_path, snapshot, source, workspace = _launch(
            tmp_path / str(n),
            _reviewed_order_knowledge(eta, infer_schema=True),
            uploads=True,
        )
        runtime = HostedChatRuntime(
            client=SimpleNamespace(turn_context={}),
            policy=load_chat_policy(policy),
            workspace=workspace,
            source=source,
            job=job,
            reviewed_snapshot=snapshot,
            job_path=job_path,
        )
        _load_world(workspace)
        stage = runtime._open_built(_contract(), None, None)
        case = _read_case(stage.spec.servers)
        assert "ORDER_GRADER_SECRET_9X" not in json.dumps(runtime.reviewed.evidence)
        assert "private-review-id" not in repr(runtime.job)
        assert {fact["value"] for fact in case["starting_facts"]} >= {"shipped", eta}
        assert any(span["source_id"] == "public-policy" for span in case["citations"])
        response = asyncio.run(
            _tool(stage.spec.servers, "scenarios", "submit_scenario")(_arguments(case))
        )
        assert not response.get("is_error"), response
        saved = scenarios.load_scenarios(workspace)
        runtime.reviewed.check_complete(saved, workspace)
        with pytest.raises(ReleaseError, match="source_suite_(invalid|unproved)"):
            runtime.reviewed.check_complete(
                [saved[0].model_copy(update={"tests": saved[0].tests + " edited"})],
                workspace,
            )
        proof = load_proofs(workspace)[saved[0].scenario_key]
        proof.pop("proved_at")
        results.append(
            (saved[0].model_dump(), proof, (workspace / PIN_FILE).read_bytes())
        )
        for path in workspace.rglob("*"):
            if path.is_file():
                assert b"private-review-id" not in path.read_bytes()
                assert b"ORDER_GRADER_SECRET_9X" not in path.read_bytes()
        with tarfile.open(
            fileobj=io.BytesIO(_workspace_archive(workspace)), mode="r:gz"
        ) as archive:
            assert PIN_FILE in archive.getnames()
            assert job_path.name not in archive.getnames()
            assert snapshot.name not in archive.getnames()
    assert results[0] == results[1]
    assert all(
        before != after for before, after in zip(results[0], results[2], strict=True)
    )
    assert results[0][0]["tests"] != results[2][0]["tests"]


def test_fallback_generates_safe_grounded_suite(tmp_path, monkeypatch):
    _, job_path, snapshot, source, workspace = _launch(
        tmp_path / "case", _only("reference")
    )
    monkeypatch.setattr(
        authoring_entrypoint, "initialize_hosted_compose", lambda *args: None
    )
    secrets = tmp_path / "secrets.json"
    secrets.write_text("{}")
    monkeypatch.setattr(hosted_authoring_entrypoint, "_SECRETS_PATH", secrets)
    monkeypatch.setattr(
        hosted_authoring_entrypoint,
        "_SIMULATOR_SECRETS_PATH",
        tmp_path / "absent-simulator",
    )
    monkeypatch.setattr(
        hosted_authoring_entrypoint, "_ADC_PATH", tmp_path / "absent-adc"
    )
    monkeypatch.setenv("ALK_HARNESS_CONTROLLER", "1")
    monkeypatch.setattr(scenarios, "HANDS_OUT_ABOVE", 1)

    async def understand(args):
        assert args.job.metadata == {"agent_name": "order-support"}
        (workspace / "contract.json").write_text(_contract().model_dump_json())
        return 0

    async def build(args):
        assert args.skip_source_provision
        assert args.reviewed is not None
        _load_world(workspace)
        return 0

    async def author(stage, *args, until, **kwargs):
        read = _tool(stage.spec.servers, "reviewed_sources", "read_source")
        result = await read({"section": "cases", "index": 1})
        case = json.loads(result["content"][0]["text"])
        response = await _tool(stage.spec.servers, "scenarios", "submit_scenario")(
            _arguments(case)
        )
        assert not response.get("is_error"), response
        assert until()

    monkeypatch.setattr(cli, "_understand", understand)
    monkeypatch.setattr(cli, "_build", build)
    monkeypatch.setattr(cli, "_converse", author)
    assert (
        hosted_authoring_entrypoint.main(
            [
                str(job_path),
                "--source",
                str(source),
                "--output",
                str(workspace),
                "--reviewed-snapshot",
                str(snapshot),
            ]
        )
        == 0
    )
    assert "Tuesday" in json.dumps(scenarios.load_scenarios(workspace)[0].fixture)
    assert json.loads((workspace / "job.json").read_bytes())["metadata"] == {
        "agent_name": "order-support"
    }


def test_parallel_writers_get_disjoint_case_allocations(tmp_path, monkeypatch):
    monkeypatch.setenv("ALK_HARNESS_MODEL", "test-model")
    launch = _launch(tmp_path / "parallel", _only("reference"), count=2)
    _, reviewed = _prepare(launch)
    workspace = launch[-1]
    _load_world(workspace)
    assigned = []

    class Writer:
        def __init__(self, spec, **kwargs):
            self.spec = spec

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def say(self, prompt, *, on_event):
            del prompt, on_event
            read = _tool(self.spec.servers, "reviewed_sources", "read_source")
            result = await read({"section": "cases", "index": 1})
            case = json.loads(result["content"][0]["text"])
            assert (await read({"section": "cases", "index": 2}))["is_error"]
            assigned.append(case["id"])
            submit = _tool(self.spec.servers, "scenarios", "submit_scenario")
            result = await submit(_arguments(case))
            assert not result.get("is_error"), result

    monkeypatch.setattr(scenarios, "Stage", Writer)
    suite = asyncio.run(
        scenarios.write_in_parallel(
            _contract(),
            out=workspace,
            wanted=2,
            reviewed=reviewed,
        )
    )
    reviewed.check_complete(suite, workspace)
    assert assigned == [case["id"] for case in reviewed.cases]
    assert len({scenario.name for scenario in suite}) == 2
