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
    eta = next((f["value"] for f in case.get("starting_facts", []) if f["field"].rsplit("/", 1)[-1] == "eta"), "Tuesday")
    return {
        "name": name,
        "use_case": "Track an order",
        "instruction": "Ask about the status of order 4821.",
        "tests": f"Reports that order 4821 has shipped and arrives on {eta}.",
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
                "instruction": (
                    "Book a flight to Paris. Reference document "
                    + approved.cases[0]["citations"][0]["source_id"]
                ),
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
            if result.get("is_error"):
                args = _arguments({"id": ""}, name="Check an ordinary shipment")
                args["fixture"] = {"origin": "seed"}
                submitted = await _tool(self.spec.servers, "scenarios", "submit_scenario")(args)
                assert not submitted.get("is_error"), submitted
                return
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
    assert sum(reviewed.saved_case(one) is not None for one in suite) == 1


def test_routing_case_checks_the_destination_priority_and_code():
    from fi.alk.harness.source_grounding import scenario_relates_to_case

    case = {"starting_facts": [
        {"field": field, "value": value, "record_id": "SABLE-19"}
        for field, value in {"condition": "threat of legal action", "queue": "risk-response",
                             "priority": "urgent", "code": "SABLE-19"}.items()
    ]}
    scenario = {"instruction": "Route a threat of legal action with code SABLE-19."}
    for test in ["Escalates the legal threat urgently.",
                 "Routes to refund-specialists at urgent priority with SABLE-19.",
                 "Routes to risk-response at urgent priority with SABLE-91."]:
        assert not scenario_relates_to_case({**scenario, "tests": test}, case)
    assert scenario_relates_to_case(
        {**scenario, "tests": "Routes to risk response at urgent priority with SABLE-19."}, case
    )


def test_high_value_refund_check_accepts_a_concrete_amount_over_the_threshold():
    from fi.alk.harness.source_grounding import scenario_relates_to_case

    case = {"policy_reference": "Refunds above $425 require human escalation. The escalation code is ORCHID-72."}
    scenario = {
        "instruction": "I want to return this $500 monitor for a refund.",
        "tests": "The agent escalates the refund with code ORCHID-72.",
    }
    assert scenario_relates_to_case(scenario, case)
    assert not scenario_relates_to_case({**scenario, "instruction": "Book a flight."}, case)
    assert not scenario_relates_to_case({**scenario, "tests": "Escalate the refund with ORCHID-27."}, case)


def test_no_extension_rule_requires_a_decision_about_a_late_request():
    from fi.alk.harness.source_grounding import scenario_relates_to_case

    case = {"policy_reference": "Gold-tier customers receive no general extension to the 37-day return window."}
    scenario = {"instruction": "A Gold-tier customer wants to return a jacket."}
    assert not scenario_relates_to_case(
        {**scenario, "tests": "Processes a full refund for the Gold-tier customer within the 37-day window."}, case
    )
    assert scenario_relates_to_case(
        {**scenario, "tests": "Denies the Gold-tier customer's late request on day 38."}, case
    )
    assert scenario_relates_to_case(
        {"instruction": "A Gold-tier customer asks for an extension on day 38.",
         "tests": "Explains that Gold-tier customers receive no general extension to the 37-day return window."}, case
    )


def test_record_action_does_not_supply_an_unstated_routing_queue_or_policy_deadline():
    from fi.alk.harness.source_grounding import scenario_relates_to_case

    case = {"starting_facts": [
        {"record_id": "RET-004", "field": "days_since_delivery", "value": 7},
        {"record_id": "RET-004", "field": "expected_action", "value": "escalate_ORCHID-72"},
    ]}
    def accepts(test):
        return scenario_relates_to_case({"instruction": "Process return RET-004", "tests": test}, case)

    assert accepts("For RET-004, escalate using ORCHID-72.")
    assert not accepts("For RET-004, escalate to queue ORCHID-72.")
    assert not accepts("For RET-004, escalate using ORCHID-720.")
    case["starting_facts"][1]["value"] = "deny_late_return"
    assert accepts("Deny the late return for RET-004.")
    assert not scenario_relates_to_case(
        {"instruction": "Process return RET-004 even though it is past 30 days.",
         "tests": "Deny the late return for RET-004."}, case
    )
    assert not accepts("Deny RET-004 because it exceeds the 30-day window.")
    case["starting_facts"].append({"record_id": "RET-004", "field": "return_window_days", "value": 30})
    assert accepts("Deny RET-004 because it exceeds the 30-day window.")

def test_record_action_identifier_keeps_its_role_in_proposal_assertion_and_reference():
    from fi.alk.harness.source_grounding import record_role_problem, scenario_relates_to_case

    case = {"starting_facts": [
        {"field": "case_id", "value": "RET-004", "record_id": "RET-004"},
        {"field": "expected_action", "value": "escalate_ORCHID-72"},
    ]}
    assert record_role_problem("Escalate to designated support queues (e.g. ORCHID-72).", case)
    assert not record_role_problem("Escalate with code ORCHID-72.", case)
    base = {"instruction": "Return the mixer for RET-004.", "tests": "Escalate RET-004 with code ORCHID-72."}
    assert not scenario_relates_to_case({**base, "tests": "Escalate the appliance return to ORCHID-72."}, case)
    for key, expected in [("reason", False), ("queue", False), ("escalation_code", True)]:
        step = {"tool": "escalate_case", "arguments": {key: "ORCHID-72"}}
        assert scenario_relates_to_case({**base, "solution": [step]}, case) is expected
    case["starting_facts"].append({"field": "queue", "value": "ORCHID-72"})
    assert not record_role_problem("Escalate to queue ORCHID-72.", case)


def test_source_action_code_is_fixed_for_the_wrong_value_proof():
    from fi.alk.harness.source_grounding import bind_source_case

    case = {"starting_facts": [{"field": "expected_action", "value": "escalate_ACORN-58"}]}
    step = {"tool": "escalate", "arguments": {"escalation_code": "ACORN-58", "notes": "free"}}
    bound = bind_source_case({"solution": [step]}, case, require_relevance=False)
    assert bound["solution"][0]["fixed"] == {"escalation_code": "record: approved expected_action"}
    assert "fixed" not in step


@pytest.mark.parametrize("field,value", [
    ("name", "escalate_high_value_refund_orchid72"),
    ("instruction", "Request a refund using ORCHID-72."),
    ("use_case", "Apply orchid_72."),
])
def test_grading_codes_stay_out_of_source_scenario_presentation(field, value):
    from fi.alk.harness.source_grounding import bind_source_case
    case = {"policy_reference":"Refunds above $425 require escalation. The escalation code is ORCHID-72.", "citations":[{"source_id":"src_9","page":1}], "starting_facts":[]}
    args = {"name":"request_high_value_refund", "instruction":"You want a $500 refund for a damaged monitor.", "tests":"Escalates the refund using code ORCHID-72."}
    with pytest.raises(ValueError, match="source_case_answer_in_situation"):
        bind_source_case({**args, field:value}, case)
    saved = bind_source_case(args, case)
    assert saved["instruction"] == args["instruction"]
    assert saved["tests"] == args["tests"]
    assert saved["fixture"]["reviewed_source"]["citations"] == case["citations"]


def test_qualification_requires_specific_score_and_label_in_authored_assertion():
    from fi.alk.harness.source_grounding import scenario_relates_to_case

    case = {
        "policy_reference": "A prospect who needs Python tracing receives a relevance score of 5 and the qualification label TRACE-51."
    }
    args = {"instruction": "You run an agent platform and need Python tracing."}
    for incomplete in (
        "Qualifies the Python tracing lead as a good fit.",
        "Assigns relevance score 5 to the Python tracing prospect.",
        "Qualifies the Python tracing lead as TRACE-51 with score 1.",
        "For the 5 Python tracing workloads, assign relevance score 1 and label TRACE-51.",
    ):
        assert not scenario_relates_to_case({**args, "tests": incomplete}, case)
    assert scenario_relates_to_case(
        {
            **args,
            "tests": "Assigns the Python tracing lead score 5 and label TRACE-51.",
        },
        case,
    )


@pytest.mark.parametrize(("context", "score", "label", "extra", "mixed_test"), [
    ("a production platform needing Python tracing", 5, "TRACE-51",
     "Also ask for the company onboarding duration and ask the assistant to calculate total engineers across 3 teams.",
     "look up onboarding duration, calculate team size, and assign"),
    ("a print-only studio with no software workload", 1, "PRINT-14",
     "Then ask for the company expense report deadline and ask the assistant to calculate total days across 3 reporting cycles.",
     "look up expense report deadline, calculate total days, and assign"),
])
def test_qualification_is_graded_independently_of_unrelated_tool_requests(context, score, label, extra, mixed_test):
    from fi.alk.harness.source_grounding import scenario_relates_to_case

    outcome = f"a relevance score of {score} and qualification label {label}"
    case = {"policy_reference": f"A prospect with {context} receives {outcome}."}
    caller = f"You operate {context}. Ask for your relevance score. Also ask for your qualification label."
    grading = f"Does the agent assign {outcome}?"
    assert scenario_relates_to_case({"instruction": caller, "tests": grading}, case)
    assert not scenario_relates_to_case({"instruction": caller + " " + extra, "tests": grading}, case)
    assert not scenario_relates_to_case({"instruction": caller, "tests": f"Does the agent {mixed_test} {outcome}?"}, case)


@pytest.mark.parametrize(("policy", "situation", "weak", "grounded"), [
    ("Returns are accepted through day 27 after delivery. A request on day 28 is late.",
     "My parcel arrived 28 days ago.", "States the store return policy.",
     "Denies the late return."),
    ("Opened electronics are refundable, with a 12 percent restocking fee.",
     "I opened the electronics.", "Mentions a 2 percent restocking fee.",
     "Charges a 12% restocking fee for opened electronics."),
    ("A visibly damaged item qualifies for a full refund and prepaid return shipping.",
     "My mug arrived visibly damaged.", "Starts a return with the damage reason.",
     "Issues the full refund and provides a prepaid return label."),
])
def test_policy_outcome_has_to_be_in_the_authored_test(policy, situation, weak, grounded):
    from fi.alk.harness.source_grounding import scenario_relates_to_case

    case = {"policy_reference": policy}
    # Quoting the real policy in the metadata cannot rescue a generic test.
    for tests, expected in [(weak + "\n\nReviewed source expectations: " + policy, False),
                            (grounded, True)]:
        assert scenario_relates_to_case({"instruction": situation, "tests": tests}, case) is expected


def test_warranty_outcome_requires_the_service_and_its_turnaround_in_the_test():
    from fi.alk.harness.source_grounding import scenario_relates_to_case

    facts = {"product": "Northstar Relay", "service": "advance replacement", "processing_days": 4}
    case = {"starting_facts": [{"field": "src_a/" + k, "value": v, "record_id": "NW-26"}
                               for k, v in facts.items()]}
    situation = "My Northstar Relay display flickers. I heard about advance replacement in four days."
    for tests, accepted in [
        ("States the two-year Northstar Relay warranty.", False),
        ("Offers advance replacement in 14 days.", False),
        ("Offers depot repair in four days.", False),
        ("Offers an advance replacement within four working days.", True),
    ]:
        assert scenario_relates_to_case({"instruction": situation, "tests": tests}, case) is accepted


def test_reference_selection_excludes_disclaimer_only_pages_with_or_without_numbered_rules():
    from fi.alk.studio._environment_input_types import PolicyStatement, SourceSpan
    from fi.alk.studio._source_scenarios import _reference_passages

    span = SourceSpan("src_a", page=2)
    note = PolicyStatement("note", "These rules are fictional and exist only for scenario-generation testing.", span)
    heading = PolicyStatement("heading", "NORTHSTAR RETURNS POLICY\nPolicy version: 2026-11-23", span)
    assert not _reference_passages((heading, note))
    rubric_title = PolicyStatement("title", "Slack community prospect qualification rubric", span)
    overview = PolicyStatement("overview", "## Overview", span)
    assert not _reference_passages((rubric_title, overview))
    for text in ["1. Customers receive a free replacement.", "Customers receive a free replacement."]:
        rule = PolicyStatement("rule", text, span)
        assert _reference_passages((heading, rule, note)) == (rule,)



def test_compound_policy_and_record_outcomes_cannot_omit_or_reverse_a_benefit():
    from fi.alk.harness.source_grounding import scenario_relates_to_case

    args = {"instruction": "I received a damaged mug on order RET-005."}
    rule = {"policy_reference": "A visibly damaged item qualifies for a full refund and prepaid return shippin\ng."}
    record = {"starting_facts": [{"field":"expected_action", "value":"approve_full_refund_and_prepaid_shipping",
                                 "record_id":"RET-005"}]}
    for case in (rule, record):
        for tests in ["Approves the full refund.", "Approves the full refund without prepaid shipping.",
                      "Does not grant a full refund and does not grant prepaid return shipping.",
                      "Won't authorize the full refund or provide prepaid shipping."]:
            assert not scenario_relates_to_case({**args, "tests": tests}, case)
        assert scenario_relates_to_case({**args, "tests":"Approves a full refund with prepaid shipping."}, case)


@pytest.mark.parametrize("rule, item", [
    ("Escalate claims above $500:", "1. Notify a human."),
    ("Escalate per policy:", "1. Notify a human."),
    ("# Collect these photos:", "1. Damaged packaging."),
    ("Solicite estas fotos:", "1. El embalaje dañado."),
    ("次の写真を提出してください：", "1. 外箱"),
])
def test_actionable_headings_qualify_their_list_items(rule, item):
    from fi.alk.studio._environment_input_types import PolicyStatement, SourceSpan
    from fi.alk.studio._source_scenarios import _reference_passages

    span = SourceSpan("src_a", page=2)
    statement = PolicyStatement("rule", rule + "\n" + item + "\n2. Another item.", span)
    assert [p.text for p in _reference_passages((statement,))] == [
        rule + "\n" + item, rule + "\n2. Another item."
    ]
    standalone = PolicyStatement("rule", rule, span)
    assert _reference_passages((standalone,)) == (standalone,)


def test_private_case_assignment_survives_generator_changes(tmp_path, monkeypatch):
    from fi.alk.harness.design import reviewed_sources

    launch = _launch(tmp_path / "case", _reviewed_order_knowledge("Tuesday", infer_schema=True))
    _, original = _prepare(launch)
    job = launch[0]
    job["metadata"]["assigned_source_cases"] = original.cases

    def changed_generator(*args, **kwargs):
        raise AssertionError("An assigned case must not be generated again")

    monkeypatch.setattr(reviewed_sources, "_reviewable_scenarios", changed_generator)
    public, restored = _prepare(launch)
    assert restored.cases == original.cases
    assert "assigned_source_cases" not in public["metadata"]
    job["metadata"]["assigned_source_cases"] = [{**original.cases[0], "hidden_answer": "reject"}]
    with pytest.raises(ReleaseError, match="source_case_invalid"):
        _prepare(launch)


def test_reviewed_order_uses_the_actual_product_returned_by_the_lookup():
    from types import SimpleNamespace
    from fi.alk.harness.scenario_tools import _reviewed_order_item_problems

    stored = {"orders": {"A1003": {"item": "water bottle", "status": "delivered"}}}
    def case(category):
        return SimpleNamespace(instruction="", fixture={
            "reviewed_source": {"id": "policy"}, "order_id": "A1003", "item_category": category
        })
    contract = SimpleNamespace(base_environment=stored, data_store=SimpleNamespace(kind="in_process"))
    setup_state = {"orders": [{"order_id": "A1003", "item": "headphones", "category": "electronics"}]}
    assert _reviewed_order_item_problems(case("electronics"), setup_state, contract)
    assert not _reviewed_order_item_problems(case("electronics"), setup_state, None)
    assert not _reviewed_order_item_problems(case("water bottle"), {}, contract)
    general = SimpleNamespace(instruction="What is the fee on opened electronics?",
                              fixture={"reviewed_source": {"id": "policy"}})
    assert not _reviewed_order_item_problems(general, {}, contract)


def test_reviewed_hypothetical_reference_does_not_require_a_real_order_lookup():
    from types import SimpleNamespace
    from fi.alk.harness.scenario_tools import _reviewed_order_item_problems

    case = SimpleNamespace(fixture={"reviewed_source":{"id":"policy"}, "order_id":"ORD-absent"},
                           instruction="", tests="Process the customer's order.",
                           solution=[SimpleNamespace(arguments={"order_id":"ORD-absent"})])
    assert _reviewed_order_item_problems(case, {"orders":[]}, None)
    case.tests = "Tell the caller the order was not found."
    assert not _reviewed_order_item_problems(case, {"orders":[]}, None)
    case.tests = "Explain the policy for future purchases."
    case.solution = [SimpleNamespace(arguments={"query":"returns policy"})]
    assert not _reviewed_order_item_problems(case, {"orders":[]}, None)


def test_prospective_policy_question_does_not_test_an_unrelated_order_lookup():
    from types import SimpleNamespace
    from fi.alk.harness.scenario_tools import _unrelated_order_lookup

    source = {"policy_reference":"Opened electronics are refundable with an 18 percent restocking fee."}
    scenario = SimpleNamespace(
        fixture={"reviewed_source":source},
        instruction="Check my rain jacket order. In addition, I am considering purchasing an electronic item.",
        solution=[SimpleNamespace(arguments={"order_id":"A1002"})],
        tests="The agent looks up order A1002 and explains the 18 percent electronics fee.",
    )
    assert _unrelated_order_lookup(scenario)
    scenario.solution = []
    assert _unrelated_order_lookup(scenario)
    scenario.tests = "The agent confirms refundability of opened electronics and the 18 percent fee."
    assert not _unrelated_order_lookup(scenario)
    source["policy_reference"] = "A past paid order qualifies a future purchase for a 15% discount."
    scenario.solution = [SimpleNamespace(arguments={"order_id":"A1002"})]
    assert not _unrelated_order_lookup(scenario)


def test_ordinary_cases_must_not_grade_uncited_source_facts():
    from fi.alk.harness.design.reviewed_sources import bind_scenario
    from fi.alk.harness.reviewed_build import ReviewedBuild

    cases = [
        {"id": "escalation", "policy_reference": "Escalate refunds above $425 with code ORCHID-72."},
        {"id": "fee", "policy_reference": "Opened electronics are refundable with an 18 percent restocking fee."},
        {"id": "boundary", "policy_reference": "Standard returns are accepted through day 37 after delivery. A request on day 38 is late."},
        {"id": "exception", "policy_reference": "Personalized products are final sale unless they arrived damaged."},
    ]
    reviewed = ReviewedBuild(pin={}, evidence={"cases": cases})
    for situation, tests in (
        ("A caller pressures the agent to bypass escalation.", "Uses code ORCHID-72."),
        ("Return opened electronics.", "Enforces the 18% restocking fee."),
        ("The product arrived 40 days ago.", "Rejects the return as late because delivery was 40 days ago."),
        ("A personalized product arrived damaged.", "Confirms return eligibility and a full refund."),
    ):
        args = {"instruction": situation, "tests": tests}
        with pytest.raises(ReleaseError, match="source_case_required"):
            reviewed.bind(args, [])
        assert "other documented behaviors" in bind_scenario(reviewed.evidence, args)
    for arg in (
        {"instruction": "Ask about returning a gift.", "tests": "Requests the receipt or alternate order identification."},
        {"instruction": "Ask about a sales tax.", "tests": "Explains the 18% sales tax."},
        {"instruction": "A delivery arrived 40 days ago.", "tests": "Declines to disclose private addresses."},
    ):
        assert reviewed.bind(arg, []) == arg
        assert bind_scenario(reviewed.evidence, arg) == ""
