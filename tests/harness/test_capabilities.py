"""Capability evidence: what a build's certification shows the agent and its world support."""

import json

import pytest

from fi.alk.harness.capabilities import CATALOGUE, capability_evidence
from fi.alk.harness.certification import (
    CertificationAuthoring,
    CertificationChecks,
    CertificationCompiler,
    CertificationRuntime,
    CertificationSource,
    CertificationStatus,
    CheckStatus,
    GenericHarnessArtifactStore,
    HarnessCertification,
)
from fi.alk.harness.repair_controller import RepairHistory


def _digest(char: str) -> str:
    return "sha256:" + char * 64


def _certificate(**checks) -> HarnessCertification:
    return HarnessCertification.create(
        status=CertificationStatus.CERTIFIED,
        source=CertificationSource(
            repository="https://example.invalid/agent.git",
            commit="abc123",
            digest=_digest("a"),
            schema_hash=_digest("b"),
        ),
        authoring=CertificationAuthoring(
            contract_hash=_digest("c"), world_ir_hash=_digest("d"), scenario_set_hash=_digest("e")
        ),
        compiler=CertificationCompiler(version="compiler-v1", bundle_digest=_digest("f")),
        runtime=CertificationRuntime(snapshot="snapshot-r1", validation_attempts=1),
        checks=CertificationChecks(
            **{
                "static": CheckStatus.PASSED,
                "schema_and_seed": CheckStatus.PASSED,
                "processes": CheckStatus.PASSED,
                "chat_ingress": CheckStatus.PASSED,
                "source_invariants": CheckStatus.PASSED,
                "scenario_setup_ready": "5/5",
                "tool_contract": "3/3",
                "reset_equivalence": CheckStatus.PASSED,
                "world_isolation": CheckStatus.PASSED,
                **checks,
            }
        ),
        repairs=RepairHistory(
            decisions=(),
            results=(),
            compiler_candidates_used=0,
            environment_patches_used=0,
            scenario_patches_used=0,
        ),
        limitations=("external side effects were not exercised",),
    )


def _findings(certificate: HarnessCertification) -> dict[str, str]:
    return {
        key: value["finding"]
        for key, value in capability_evidence(certificate)["capabilities"].items()
    }


def test_every_catalogue_capability_has_evidence_and_unchecked_ones_are_not_run():
    evidence = capability_evidence(_certificate())
    assert set(evidence["capabilities"]) == {entry["id"] for entry in CATALOGUE}
    assert len(CATALOGUE) == 15
    findings = _findings(_certificate())
    assert {key: findings[key] for key in ("start_health", "one_turn", "tool_routing", "state_evidence", "world_reset")} == {
        key: "passed" for key in ("start_health", "one_turn", "tool_routing", "state_evidence", "world_reset")
    }
    assert findings["streaming"] == findings["browser"] == findings["cost_measurement"] == "not_run"
    assert findings["tool_discovery"] == "passed"
    assert evidence["inputs"] == {"source_digest": _digest("a"), "bundle_digest": _digest("f")}
    assert evidence["order"] == [entry["id"] for entry in CATALOGUE]
    assert evidence["order"].index("start_health") < evidence["order"].index("one_turn")


@pytest.mark.parametrize(
    "checks, finding",
    [
        ({"action_behavior": "18/20"}, "passed_with_limits"),
        ({"tool_contract": "0/3"}, "failed"),
        ({"tool_contract": "0/0"}, "not_run"),
    ],
)
def test_tool_routing_reads_the_counts(checks, finding):
    assert _findings(_certificate(**checks))["tool_routing"] == finding


def test_a_failed_check_fails_its_capability_and_a_voice_only_check_leaves_it_untested():
    assert _findings(_certificate(processes=CheckStatus.FAILED))["start_health"] == "failed"
    assert _findings(_certificate(chat_ingress=CheckStatus.NOT_APPLICABLE))["one_turn"] == "not_run"


def test_the_evidence_is_written_beside_the_certificate(tmp_path):
    certificate = _certificate(action_behavior="18/20")
    GenericHarnessArtifactStore(tmp_path).write_certification(certificate)
    written = json.loads((tmp_path / "capability-evidence.json").read_text())
    assert written == capability_evidence(certificate)
    assert written["capabilities"]["tool_routing"]["checks"] == [
        {"id": "tool_contract", "result": "3/3"},
        {"id": "action_behavior", "result": "18/20"},
    ]


def test_scenario_requirements_are_sealed_beside_the_certificate_when_a_bundle_exists(tmp_path):
    bundle = tmp_path / "environment-bundle"
    (bundle / "scenarios" / "refund").mkdir(parents=True)
    (bundle / "contract.json").write_text(json.dumps({"modality": "chat", "conversational": True}))
    (bundle / "sub_goals.json").write_text(
        json.dumps({"sub_goals": [{"name": "paid", "check": "def check(world, calls):\n    return world.state('t')\n"}]})
    )
    (bundle / "scenarios" / "refund" / "scenario.json").write_text(
        json.dumps({"max_turns": 4, "solution": [{"tool": "refund"}], "sub_goals": ["paid"]})
    )
    store = tmp_path / "generic-harness"
    GenericHarnessArtifactStore(store).write_certification(_certificate())

    written = json.loads((store / "scenario-requirements.json").read_text())
    assert written["scenarios"]["refund"]["tools"]["required"] == ["refund"]
    assert written["scenarios"]["refund"]["sub_goal_evidence"]["world"] == ["paid"]
    assert not (tmp_path / "elsewhere").exists()


def test_no_requirements_file_without_a_bundle(tmp_path):
    GenericHarnessArtifactStore(tmp_path / "generic-harness").write_certification(_certificate())
    assert not (tmp_path / "generic-harness" / "scenario-requirements.json").exists()


def test_the_tools_and_actions_that_failed_are_named_on_their_checks(tmp_path):
    from fi.alk.harness.action_certification import (
        ActionCertificationReport,
        ActionProbeMode,
        ActionProbeResult,
        ActionProbeStatus,
    )
    from fi.alk.harness.tool_certification import (
        ToolAvailability,
        ToolCertification,
        ToolCertificationReport,
        ToolProbeMode,
    )

    def tool(name, availability):
        return ToolCertification(
            tool=name, entrypoint_mode="http", probe_mode=ToolProbeMode.READ_ONLY, availability=availability,
            schema_validated=True, implementation_validated=True, reason="",
        )

    def action(name, status):
        return ActionProbeResult(
            action=name, mode=ActionProbeMode.DISPOSABLE_WORLD, status=status, input_schema_validated=True,
            output_schema_validated=True, reset_verified=True, reason="",
        )

    store = GenericHarnessArtifactStore(tmp_path)
    store.write_certification(_certificate(action_behavior="1/2"))
    unrecorded = json.loads((tmp_path / "capability-evidence.json").read_text())
    assert all("failed" not in check for check in unrecorded["capabilities"]["tool_routing"]["checks"])

    store.write_tool_certification(
        ToolCertificationReport.create([tool("lookup", ToolAvailability.CERTIFIED), tool("refund", ToolAvailability.REJECTED)])
    )
    store.write_action_certification(
        ActionCertificationReport.create(
            [action("issue_refund", ActionProbeStatus.FAILED), action("read_order", ActionProbeStatus.REFUSED)]
        )
    )
    store.write_certification(_certificate(tool_contract="1/2", action_behavior="1/2"))
    checks = json.loads((tmp_path / "capability-evidence.json").read_text())["capabilities"]["tool_routing"]["checks"]
    assert checks == [
        {"id": "tool_contract", "result": "1/2", "failed": ["refund"]},
        {"id": "action_behavior", "result": "1/2", "failed": ["issue_refund"]},
    ]
