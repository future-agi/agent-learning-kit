from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from agent_learning.cli import main


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = PROJECT_ROOT / "examples"


@pytest.mark.parametrize(
    ("command", "example", "kind", "required_env"),
    [
        (
            "run",
            "run_manifest.json",
            "agent-learning.run.v1",
            "AGENT_LEARNING_RUN_EXAMPLE_KEY",
        ),
        ("eval", "eval_suite.json", "agent-learning.eval.v1", None),
        ("eval", "artifact_task_eval_suite.json", "agent-learning.eval.v1", None),
        (
            "eval-artifact",
            "fixtures/task_artifacts/refund_task_run.json",
            "agent-learning.artifact-evaluation.v1",
            None,
        ),
        (
            "eval-task",
            "task_evidence.json",
            "agent-learning.artifact-evaluation.v1",
            None,
        ),
        (
            "redteam",
            "redteam_manifest.json",
            "agent-learning.redteam.v1",
            "AGENT_LEARNING_REDTEAM_EXAMPLE_KEY",
        ),
        (
            "optimize",
            "optimization_manifest.json",
            "agent-learning.optimization.v1",
            "AGENT_LEARNING_OPTIMIZE_EXAMPLE_KEY",
        ),
        (
            "optimize-eval",
            "eval_suite_optimization.json",
            "agent-learning.eval-optimization.v1",
            None,
        ),
        (
            "suite",
            "agent_learning_suite.json",
            "agent-learning.suite.v1",
            [
                "AGENT_LEARNING_RUN_EXAMPLE_KEY",
                "AGENT_LEARNING_MULTI_FRAMEWORK_EXAMPLE_KEY",
                "AGENT_LEARNING_CUSTOM_FRAMEWORK_OPT_EXAMPLE_KEY",
                "AGENT_LEARNING_SOCIAL_MEMORY_OPT_EXAMPLE_KEY",
                "AGENT_LEARNING_REDTEAM_EXAMPLE_KEY",
                "AGENT_LEARNING_WORLD_FRAMEWORK_OPT_EXAMPLE_KEY",
                "AGENT_LEARNING_VOICE_STREAMING_OPT_EXAMPLE_KEY",
                "AGENT_LEARNING_REDTEAM_OPT_EXAMPLE_KEY",
                "AGENT_LEARNING_WORKSPACE_OBSERVABILITY_OPT_EXAMPLE_KEY",
                "AGENT_LEARNING_AGENT_INTEGRATION_OPT_EXAMPLE_KEY",
                "AGENT_LEARNING_MULTI_AGENT_FRAMEWORK_HANDOFF_OPT_EXAMPLE_KEY",
                "AGENT_LEARNING_OPTIMIZER_GOVERNANCE_OPT_EXAMPLE_KEY",
                "AGENT_LEARNING_AGENT_CONTROL_PLANE_OPT_EXAMPLE_KEY",
                "AGENT_LEARNING_BROWSER_CUA_OPT_EXAMPLE_KEY",
                "AGENT_LEARNING_FRAMEWORK_CERT_OPT_EXAMPLE_KEY",
                "AGENT_LEARNING_AUTONOMOUS_REDTEAM_OPT_EXAMPLE_KEY",
                "AGENT_LEARNING_MULTIMODAL_IMAGE_OPT_EXAMPLE_KEY",
            ],
        ),
        (
            "run",
            "voice_streaming_realtime_manifest.json",
            "agent-learning.run.v1",
            "AGENT_LEARNING_VOICE_STREAMING_EXAMPLE_KEY",
        ),
    ],
)
def test_shipped_examples_execute_through_unified_cli(
    command: str,
    example: str,
    kind: str,
    required_env: str | list[str] | None,
    tmp_path,
    monkeypatch,
):
    for env_key in [required_env] if isinstance(required_env, str) else required_env or []:
        monkeypatch.setenv(env_key, f"real-local-{env_key.lower()}")

    output_path = tmp_path / f"{command}.json"
    junit_path = tmp_path / f"{command}.junit.xml"
    sarif_path = tmp_path / f"{command}.sarif.json"
    markdown_path = tmp_path / f"{command}.md"

    args = [
        command,
        str(EXAMPLES / example),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ]
    if command == "run":
        args.append("--no-eval")
    if command == "eval-artifact":
        args.extend(["--config", str(EXAMPLES / "artifact_task_eval_config.json")])
    if command == "eval-task":
        args.extend(["--config", str(EXAMPLES / "task_evidence_eval_config.json")])

    exit_code = main(args)

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == kind
    assert payload["status"] == "passed"
    assert payload["exit_code"] == 0
    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    assert json.loads(sarif_path.read_text(encoding="utf-8"))["version"] == "2.1.0"
    assert markdown_path.read_text(encoding="utf-8").strip()

    if command == "optimize":
        assert payload["summary"]["optimization_score"] >= 0.9
        assert payload["optimization"]["best_config"]
    if command == "optimize-eval":
        assert payload["summary"]["optimization_score"] == pytest.approx(1.0)
        assert payload["optimization"]["best_config"]
    if command == "eval" and example == "artifact_task_eval_suite.json":
        assert payload["summary"]["assertion_count"] == 8
        case = payload["eval_suite"]["cases"][0]
        assert case["provider_type"] == "artifact"
        assert {item["type"] for item in case["assertions"]} == {
            "json_path_equals",
            "json_path_gte",
        }
        assert '"task_completion": 1.0' in case["output"]
        assert '"verification_status": "approved"' in case["output"]
        assert '"canary_exfiltrated": false' in case["output"]
    if command == "eval-artifact":
        assert payload["summary"]["report_source"] == "report"
        assert payload["summary"]["source_kind"] == "agent-learning.run.v1"
        assert payload["summary"]["score"] >= 0.9
        assert payload["summary"]["metric_averages"]["task_completion"] >= 0.9
        assert payload["source"]["path"].endswith("refund_task_run.json")
    if command == "eval-task":
        assert payload["summary"]["report_source"] == "report"
        assert payload["summary"]["source_kind"] == "agent-learning.task-evidence.v1"
        assert payload["summary"]["score"] >= 0.9
        assert payload["summary"]["metric_averages"]["task_completion"] >= 0.9
        assert payload["summary"]["metric_averages"]["world_contract_quality"] >= 0.9
        assert payload["source"]["path"].endswith("task_evidence.json")
    if command == "suite":
        assert payload["summary"]["job_count"] == 21
        assert payload["summary"]["passed_count"] == 21
        assert payload["summary"]["score"] == pytest.approx(1.0)
        assert payload["summary"]["capability_gate_passed"] is True
        assert payload["summary"]["missing_required_capabilities"] == {}
        capabilities = payload["summary"]["capabilities"]
        required_capabilities = payload["summary"]["required_capabilities"]
        assert set(capabilities["commands"]) == {
            "eval",
            "eval_artifact",
            "optimize",
            "optimize_eval",
            "redteam",
            "run",
            "suite",
        }
        assert set(capabilities["result_kinds"]) == {
            "agent_learning.eval.v1",
            "agent_learning.artifact_evaluation.v1",
            "agent_learning.eval_optimization.v1",
            "agent_learning.optimization.v1",
            "agent_learning.redteam.v1",
            "agent_learning.run.v1",
            "agent_learning.suite.v1",
        }
        assert {
            "adversarial_attack_pack",
            "agent_control_plane",
            "agent_integration",
            "autonomy_loop",
            "browser_cua",
            "framework_capability",
            "framework_trace",
            "multi_agent_room",
            "multimodal_image",
            "optimizer_trace",
            "red_team_campaign",
            "streaming_trace",
            "voice",
            "world_orchestration_replay",
        } <= set(capabilities["environment_types"])
        assert {
            "agent_integration_manifest",
            "browser",
            "framework_capability_matrix",
            "framework_runtime",
            "optimizer_society_trace",
            "red_team_campaign",
            "streaming_trace",
            "voice",
            "world_contract",
        } <= set(capabilities["environment_state_keys"])
        assert {"artifact", "bland", "livekit", "retell", "twilio", "vapi"} <= set(
            capabilities["providers"]
        )
        assert {
            "autogen",
            "crewai",
            "custom_refund_orchestrator",
            "langchain",
            "langgraph",
            "livekit",
            "openai_agents",
            "pipecat",
        } <= set(
            capabilities["frameworks"]
        )
        assert {"chat", "phone", "sip", "voice", "webrtc", "websocket"} <= set(
            capabilities["channels"]
        )
        assert {
            "agent_integration_quality",
            "browser_action_outcome",
            "eval_assertions",
            "framework_capability_quality",
            "framework_runtime_contract",
            "framework_transcript_quality",
            "multi_agent_coordination_quality",
            "multimodal_faithfulness",
            "optimizer_trace_quality",
            "red_team_campaign_quality",
            "voice_trace_coverage",
            "world_contract_quality",
        } <= set(capabilities["metrics"])
        for capability, values in required_capabilities.items():
            assert set(values) <= set(capabilities[capability])
        assert [child["command"] for child in payload["children"]] == [
            "run",
            "suite",
            "optimize",
            "optimize",
            "eval",
            "eval",
            "eval_artifact",
            "redteam",
            "optimize_eval",
            "optimize",
            "optimize",
            "optimize",
            "optimize",
            "optimize",
            "optimize",
            "optimize",
            "optimize",
            "optimize",
            "optimize",
            "optimize",
            "optimize",
        ]
        assert {child["kind"] for child in payload["children"]} == {
            "agent-learning.run.v1",
            "agent-learning.suite.v1",
            "agent-learning.eval.v1",
            "agent-learning.artifact-evaluation.v1",
            "agent-learning.redteam.v1",
            "agent-learning.eval-optimization.v1",
            "agent-learning.optimization.v1",
        }
        nested = next(
            child
            for child in payload["children"]
            if child["id"] == "multi-framework-adapter-suite"
        )
        assert nested["kind"] == "agent-learning.suite.v1"
        assert nested["result"]["summary"]["commands"] == {"run": 5}
        assert [child["id"] for child in nested["result"]["children"]] == [
            "langchain-runnable",
            "langgraph-state-graph",
            "pipecat-voice-pipeline",
            "livekit-realtime-agent",
            "custom-refund-orchestrator",
        ]
        custom_framework_optimizer = next(
            child
            for child in payload["children"]
            if child["id"] == "custom-framework-adapter-optimizer"
        )
        assert custom_framework_optimizer["kind"] == "agent-learning.optimization.v1"
        assert (
            custom_framework_optimizer["result"]["optimization"]["best_config"]["agent"][
                "method"
            ]
            == "execute_task"
        )
        assert (
            custom_framework_optimizer["result"]["optimization"]["best_config"]["agent"][
                "input_mode"
            ]
            == "dict"
        )
        social_memory_optimizer = next(
            child
            for child in payload["children"]
            if child["id"] == "social-memory-framework-optimizer"
        )
        assert social_memory_optimizer["kind"] == "agent-learning.optimization.v1"
        assert social_memory_optimizer["result"]["optimization"]["optimizer_trace"][
            "optimizer"
        ] == "AgentSocialMemoryOptimizer"
        assert (
            social_memory_optimizer["result"]["optimization"]["best_config"]["agent"][
                "method"
            ]
            == "execute_task"
        )
    if command in {"run", "eval", "redteam"}:
        assert payload["summary"]["case_count"] >= 1


def test_task_evidence_suite_runs_eval_task_child(tmp_path):
    output_path = tmp_path / "task-evidence-suite.json"
    junit_path = tmp_path / "task-evidence-suite.junit.xml"
    sarif_path = tmp_path / "task-evidence-suite.sarif.json"
    markdown_path = tmp_path / "task-evidence-suite.md"

    exit_code = main([
        "suite",
        str(EXAMPLES / "task_evidence_suite.json"),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.suite.v1"
    assert payload["status"] == "passed"
    assert payload["summary"]["job_count"] == 1
    assert payload["summary"]["passed_count"] == 1
    assert payload["summary"]["capability_gate_passed"] is True
    assert payload["summary"]["missing_required_capabilities"] == {}
    assert payload["summary"]["commands"] == {"eval_task": 1}
    child = payload["children"][0]
    assert child["command"] == "eval_task"
    assert child["kind"] == "agent-learning.artifact-evaluation.v1"
    assert child["result"]["summary"]["source_kind"] == (
        "agent-learning.task-evidence.v1"
    )
    assert child["result"]["summary"]["score"] >= 0.9
    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    assert json.loads(sarif_path.read_text(encoding="utf-8"))["runs"][0]["results"] == []
    assert "agent-learning-task-evidence-suite" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_regression_artifact_suite_example_runs_artifact_lifecycle(tmp_path):
    output_path = tmp_path / "regression-artifact-suite.json"
    junit_path = tmp_path / "regression-artifact-suite.junit.xml"
    sarif_path = tmp_path / "regression-artifact-suite.sarif.json"
    markdown_path = tmp_path / "regression-artifact-suite.md"

    exit_code = main([
        "suite",
        str(EXAMPLES / "regression_artifact_suite.json"),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.suite.v1"
    assert payload["status"] == "passed"
    assert payload["summary"]["job_count"] == 5
    assert payload["summary"]["passed_count"] == 5
    assert payload["summary"]["capability_gate_passed"] is True
    assert payload["summary"]["missing_required_capabilities"] == {}
    assert [child["command"] for child in payload["children"]] == [
        "baseline",
        "compare",
        "report",
        "promote_to_regression",
        "replay",
    ]
    assert {child["kind"] for child in payload["children"]} == {
        "agent-simulate.baseline.v1",
        "agent-simulate.compare.v1",
        "agent-simulate.report.v1",
        "agent-simulate.regression_promotion.v1",
        "agent-simulate.replay.v1",
    }
    assert payload["children"][3]["result"]["summary"]["promoted_finding_count"] == 1
    assert payload["children"][4]["result"]["summary"]["replay_pass_rate"] == 1.0
    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    assert json.loads(sarif_path.read_text(encoding="utf-8"))["runs"][0]["results"] == []
    assert "agent-learning-regression-artifact-suite" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_eval_cli_bridge_exposes_vendored_evaluation_management_cli(
    tmp_path,
    capsys,
):
    exit_code = main(["eval-cli", "list", "categories", "--format", "json"])

    assert exit_code == 0
    categories = json.loads(capsys.readouterr().out)
    assert {"name": "safety", "count": 7} in categories
    assert {"name": "rag", "count": 6} in categories

    project_dir = tmp_path / "eval-project"
    exit_code = main([
        "eval-cli",
        "init",
        str(project_dir),
        "--template",
        "basic",
        "--force",
    ])

    assert exit_code == 0
    assert (project_dir / "fi-evaluation.yaml").exists()
    assert (project_dir / "data" / "test_cases.json").exists()
    assert (project_dir / "results" / ".gitignore").exists()


def test_agent_learn_init_optimize_scaffold_uses_unified_cli(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("AGENT_LEARNING_INIT_TEST_KEY", "real-local-init-key")
    project_dir = tmp_path / "agent-learning-project"
    init_output = tmp_path / "init.json"
    optimize_output = tmp_path / "optimize.json"

    exit_code = main([
        "init",
        str(project_dir),
        "--preset",
        "optimize",
        "--name",
        "refund-agent",
        "--required-env",
        "AGENT_LEARNING_INIT_TEST_KEY",
        "--force",
        "--output",
        str(init_output),
    ])

    assert exit_code == 0
    payload = json.loads(init_output.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.init.v1"
    assert payload["schema_version"] == "agent-learning.cli.v1"
    assert payload["summary"]["preset"] == "optimize"
    assert payload["summary"]["required_env"] == ["AGENT_LEARNING_INIT_TEST_KEY"]
    assert payload["init"]["next_commands"] == [
        f"agent-learn optimize {project_dir / 'manifests' / 'optimize.json'} --dry-run"
    ]

    readme = (project_dir / "README.md").read_text(encoding="utf-8")
    assert "Generated by `agent-learn init`." in readme
    assert "agent-learn replay manifests" in readme
    assert "agent-simulate" not in readme
    manifest_path = project_dir / "manifests" / "optimize.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["version"] == "agent-learning.optimization.v1"
    assert set(manifest["optimization"]["target"]["search_space"]) == {
        "agent.responses.0.tool_calls",
        "simulation.environments.0.data.transitions",
    }

    exit_code = main([
        "optimize",
        str(manifest_path),
        "--output",
        str(optimize_output),
    ])

    assert exit_code == 0
    optimized = json.loads(optimize_output.read_text(encoding="utf-8"))
    assert optimized["kind"] == "agent-learning.optimization.v1"
    assert optimized["status"] == "passed"
    assert optimized["summary"]["optimization_score"] >= 0.95
    best_config = optimized["optimization"]["best_config"]
    assert best_config["agent"]["responses"][0]["tool_calls"][0]["name"] == (
        "apply_world_transition"
    )
    assert best_config["simulation"]["environments"][0]["data"]["transitions"][0][
        "id"
    ] == "approve_refund"
    best_history = max(
        optimized["optimization"]["history"],
        key=lambda item: item["score"],
    )
    assert best_history["metrics"]["world_contract_quality"] == pytest.approx(1.0)


def test_agent_learn_init_all_scaffold_runs_trinity_suite(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("AGENT_LEARNING_INIT_ALL_KEY", "real-local-init-all-key")
    project_dir = tmp_path / "agent-learning-all-project"
    init_output = tmp_path / "init-all.json"
    suite_output = project_dir / "artifacts" / "suite.json"
    suite_junit = project_dir / "artifacts" / "suite.junit.xml"
    suite_sarif = project_dir / "artifacts" / "suite.sarif.json"
    suite_markdown = project_dir / "artifacts" / "suite.md"

    exit_code = main([
        "init",
        str(project_dir),
        "--preset",
        "all",
        "--name",
        "refund-agent",
        "--required-env",
        "AGENT_LEARNING_INIT_ALL_KEY",
        "--force",
        "--output",
        str(init_output),
    ])

    assert exit_code == 0
    payload = json.loads(init_output.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.init.v1"
    assert payload["summary"]["files_written_count"] == 12
    assert payload["init"]["next_commands"] == [
        (
            f"agent-learn suite {project_dir / 'manifests' / 'suite.json'} "
            f"--output {project_dir / 'artifacts' / 'suite.json'} "
            f"--junit {project_dir / 'artifacts' / 'suite.junit.xml'} "
            f"--sarif {project_dir / 'artifacts' / 'suite.sarif.json'} "
            f"--markdown {project_dir / 'artifacts' / 'suite.md'}"
        )
    ]
    assert {
        "run.json",
        "redteam.json",
        "optimize.json",
        "eval.json",
        "artifact_task_eval_suite.json",
        "artifact_task_eval_config.json",
        "eval_suite_optimization.json",
        "suite.json",
    } <= {
        path.name
        for path in (project_dir / "manifests").iterdir()
    }
    artifact_suite = json.loads(
        (project_dir / "manifests" / "artifact_task_eval_suite.json").read_text(
            encoding="utf-8",
        )
    )
    assert {item["type"] for item in artifact_suite["tests"][0]["assert"]} == {
        "json_path_equals",
        "json_path_gte",
    }

    exit_code = main([
        "suite",
        str(project_dir / "manifests" / "suite.json"),
        "--output",
        str(suite_output),
        "--junit",
        str(suite_junit),
        "--sarif",
        str(suite_sarif),
        "--markdown",
        str(suite_markdown),
    ])

    assert exit_code == 0
    suite = json.loads(suite_output.read_text(encoding="utf-8"))
    assert suite["kind"] == "agent-learning.suite.v1"
    assert suite["status"] == "passed"
    assert suite["summary"]["score"] == pytest.approx(1.0)
    assert suite["summary"]["job_count"] == 7
    assert suite["summary"]["passed_count"] == 7
    assert suite["summary"]["failed_count"] == 0
    assert suite["summary"]["capability_gate_passed"] is True
    assert {
        child["kind"]
        for child in suite["children"]
    } == {
        "agent-learning.run.v1",
        "agent-learning.eval.v1",
        "agent-learning.artifact-evaluation.v1",
        "agent-learning.redteam.v1",
        "agent-learning.eval-optimization.v1",
        "agent-learning.optimization.v1",
    }
    assert 'failures="0"' in suite_junit.read_text(encoding="utf-8")
    assert json.loads(suite_sarif.read_text(encoding="utf-8"))["version"] == "2.1.0"
    assert "refund-agent-trinity-suite" in suite_markdown.read_text(
        encoding="utf-8",
    )


def test_sdk_built_eval_suite_runs_through_cli_and_suite(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AGENT_LEARNING_SDK_EVAL_SUITE_KEY",
        "real-local-sdk-eval-suite-key",
    )
    example_path = EXAMPLES / "sdk_eval_suite.py"
    spec = importlib.util.spec_from_file_location("sdk_eval_suite", example_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    sdk_output = tmp_path / "sdk-eval-result.json"
    direct = module.run(sdk_output)
    manifest_path = sdk_output.with_suffix(".manifest.json")
    suite_path = sdk_output.with_suffix(".suite.json")
    assert direct["status"] == "passed"
    assert json.loads(suite_path.read_text(encoding="utf-8"))["required_env"] == []

    cli_output = tmp_path / "sdk-eval-cli.json"
    junit_path = tmp_path / "sdk-eval-cli.junit.xml"
    sarif_path = tmp_path / "sdk-eval-cli.sarif.json"
    markdown_path = tmp_path / "sdk-eval-cli.md"
    exit_code = main([
        "eval",
        str(manifest_path),
        "--output",
        str(cli_output),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])
    assert exit_code == 0
    cli_payload = json.loads(cli_output.read_text(encoding="utf-8"))
    assert cli_payload["kind"] == "agent-learning.eval.v1"
    assert cli_payload["status"] == "passed"
    assert cli_payload["summary"]["score"] == pytest.approx(1.0)
    assert cli_payload["summary"]["assertion_count"] == 2
    assert 'failures="0"' in junit_path.read_text(encoding="utf-8")
    assert json.loads(sarif_path.read_text(encoding="utf-8"))["runs"][0][
        "results"
    ] == []
    assert "sdk-local-eval-suite" in markdown_path.read_text(encoding="utf-8")

    suite_output = tmp_path / "sdk-eval-suite-result.json"
    suite_exit = main(["suite", str(suite_path), "--output", str(suite_output)])
    assert suite_exit == 0
    suite_payload = json.loads(suite_output.read_text(encoding="utf-8"))
    assert suite_payload["kind"] == "agent-learning.suite.v1"
    assert suite_payload["status"] == "passed"
    assert suite_payload["summary"]["score"] == pytest.approx(1.0)
    assert suite_payload["children"][0]["kind"] == "agent-learning.eval.v1"


def test_world_framework_memory_optimization_example_runs_evidence_gates(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "AGENT_LEARNING_WORLD_FRAMEWORK_OPT_EXAMPLE_KEY",
        "real-local-world-framework-key",
    )

    output_path = tmp_path / "world-framework-memory.json"
    junit_path = tmp_path / "world-framework-memory.junit.xml"
    sarif_path = tmp_path / "world-framework-memory.sarif.json"
    markdown_path = tmp_path / "world-framework-memory.md"

    exit_code = main([
        "optimize",
        str(EXAMPLES / "world_framework_memory_optimization.json"),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.optimization.v1"
    assert payload["status"] == "passed"
    assert payload["summary"]["optimization_score"] >= 0.84
    assert "simulation.environments" in payload["summary"]["search_paths"]

    env_types = [
        environment["type"]
        for environment in payload["optimization"]["best_config"]["simulation"][
            "environments"
        ]
    ]
    assert env_types == [
        "world_orchestration_replay",
        "framework_trace",
        "retrieval_memory",
        "agent_memory_lineage",
        "multi_agent_room",
    ]

    best_history = max(
        payload["optimization"]["history"],
        key=lambda item: item["score"],
    )
    metrics = best_history["metrics"]
    for metric in (
        "orchestration_flow_quality",
        "world_contract_quality",
        "retrieval_context_quality",
        "agent_memory_lineage_quality",
        "multi_agent_coordination_quality",
    ):
        assert metrics[metric] == pytest.approx(1.0)

    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    assert json.loads(sarif_path.read_text(encoding="utf-8"))["version"] == "2.1.0"
    assert "world-framework-memory-optimization" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_multi_framework_simulation_suite_runs_framework_adapters(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "AGENT_LEARNING_MULTI_FRAMEWORK_EXAMPLE_KEY",
        "real-local-multi-framework-key",
    )

    output_path = tmp_path / "multi-framework-suite.json"
    junit_path = tmp_path / "multi-framework-suite.junit.xml"
    sarif_path = tmp_path / "multi-framework-suite.sarif.json"
    markdown_path = tmp_path / "multi-framework-suite.md"

    exit_code = main([
        "suite",
        str(EXAMPLES / "multi_framework_simulation_suite.json"),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.suite.v1"
    assert payload["status"] == "passed"
    assert payload["summary"]["commands"] == {"run": 5}
    assert payload["summary"]["score"] == pytest.approx(1.0)

    expected = {
        "langchain-runnable": ("langchain", "ainvoke", "dict", "text"),
        "langgraph-state-graph": ("langgraph", "ainvoke", "dict", "text"),
        "pipecat-voice-pipeline": ("pipecat", "process", "dict", "voice"),
        "livekit-realtime-agent": ("livekit", "respond", "text", "voice"),
        "custom-refund-orchestrator": (
            "custom_refund_orchestrator",
            "execute_task",
            "dict",
            "text",
        ),
    }
    assert set(expected) == {child["id"] for child in payload["children"]}
    for child in payload["children"]:
        framework, method, input_mode, modality = expected[child["id"]]
        assert child["kind"] == "agent-learning.run.v1"
        assert child["status"] == "passed"
        case = child["result"]["report"]["results"][0]
        state = case["metadata"]["environment_state"]
        runtime = state["framework_runtime"]
        summary = runtime["summary"]
        assert runtime["framework"] == framework
        assert runtime["modality"] == modality
        assert summary["framework"] == framework
        assert summary["methods"] == [method]
        assert summary["input_modes"] == [input_mode]
        assert summary["tool_call_count"] == 1
        assert state["framework_trace"]["adapter_conformance"]["passed"] is True
        assistant_messages = [
            message for message in case["messages"] if message["role"] == "assistant"
        ]
        assert "framework_trace_status" in {
            call["name"]
            for message in assistant_messages
            for call in message.get("tool_calls", [])
        }
        assert "framework_status" in {
            message.get("tool_call_id")
            for message in case["messages"]
            if message["role"] == "tool"
        }

    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []
    assert "multi-framework-simulation-suite" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_custom_framework_optimization_example_runs_adapter_search(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "AGENT_LEARNING_CUSTOM_FRAMEWORK_OPT_EXAMPLE_KEY",
        "real-local-custom-framework-opt-key",
    )

    output_path = tmp_path / "custom-framework-optimization.json"
    junit_path = tmp_path / "custom-framework-optimization.junit.xml"
    sarif_path = tmp_path / "custom-framework-optimization.sarif.json"
    markdown_path = tmp_path / "custom-framework-optimization.md"

    exit_code = main([
        "optimize",
        str(EXAMPLES / "custom_framework_optimization.json"),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.optimization.v1"
    assert payload["status"] == "passed"
    assert payload["summary"]["optimization_score"] >= 0.95
    assert payload["summary"]["evaluation_score"] == pytest.approx(1.0)
    assert "agent" in payload["summary"]["search_paths"]

    best_agent = payload["optimization"]["best_config"]["agent"]
    assert best_agent["framework"] == "custom_refund_orchestrator"
    assert best_agent["method"] == "execute_task"
    assert best_agent["input_mode"] == "dict"

    best_history = max(
        payload["optimization"]["history"],
        key=lambda item: item["score"],
    )
    assert best_history["patch"].keys() == {"agent"}
    assert best_history["metrics"]["framework_runtime_contract"] == pytest.approx(1.0)
    assert best_history["metrics"]["framework_runtime_coverage"] == pytest.approx(1.0)
    assert best_history["metrics"]["framework_trace_coverage"] == pytest.approx(1.0)

    weakest_history = min(
        payload["optimization"]["history"],
        key=lambda item: item["score"],
    )
    assert weakest_history["metrics"]["framework_runtime_contract"] < 1.0

    case = best_history["report"]["results"][0]
    state = case["metadata"]["environment_state"]
    runtime = state["framework_runtime"]
    assert runtime["framework"] == "custom_refund_orchestrator"
    assert runtime["summary"]["methods"] == ["execute_task"]
    assert runtime["summary"]["input_modes"] == ["dict"]
    assert runtime["summary"]["tool_call_count"] == 1
    assert state["framework_trace"]["adapter_conformance"]["passed"] is True

    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []
    assert "custom-framework-adapter-optimization" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_social_memory_framework_optimization_example_synthesizes_patches(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "AGENT_LEARNING_SOCIAL_MEMORY_OPT_EXAMPLE_KEY",
        "real-local-social-memory-key",
    )

    output_path = tmp_path / "social-memory-framework-optimization.json"
    junit_path = tmp_path / "social-memory-framework-optimization.junit.xml"
    sarif_path = tmp_path / "social-memory-framework-optimization.sarif.json"
    markdown_path = tmp_path / "social-memory-framework-optimization.md"

    exit_code = main([
        "optimize",
        str(EXAMPLES / "social_memory_framework_optimization.json"),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.optimization.v1"
    assert payload["status"] == "passed"
    assert payload["summary"]["optimization_score"] >= 0.95
    assert payload["summary"]["evaluation_score"] == pytest.approx(1.0)
    assert {"agent", "simulation.environments"} <= set(
        payload["summary"]["search_paths"]
    )

    best_agent = payload["optimization"]["best_config"]["agent"]
    assert best_agent["framework"] == "custom_refund_orchestrator"
    assert best_agent["method"] == "execute_task"
    assert best_agent["input_mode"] == "dict"
    best_env = payload["optimization"]["best_config"]["simulation"]["environments"][0]
    assert best_env["data"]["spans"][0]["signals"] == ["planner", "tool", "policy"]

    trace = payload["optimization"]["optimizer_trace"]
    assert trace["optimizer"] == "AgentSocialMemoryOptimizer"
    assert {role["name"] for role in trace["roles"]} >= {
        "seed",
        "smriti",
        "sangha",
        "dharma_steward",
    }
    best_proposal = next(
        proposal
        for proposal in trace["proposals"]
        if proposal["candidate_id"] == trace["best_candidate_id"]
    )
    assert best_proposal["role"] == "sangha"
    assert set(best_proposal["patch"]) == {"agent", "simulation.environments"}
    assert trace["summary"]["has_synthesis"] is True

    best_history = max(
        payload["optimization"]["history"],
        key=lambda item: item["score"],
    )
    assert best_history["proposal_role"] == "sangha"
    assert best_history["metrics"]["framework_runtime_contract"] == pytest.approx(1.0)
    assert best_history["metrics"]["framework_runtime_coverage"] == pytest.approx(1.0)
    assert best_history["metrics"]["framework_trace_coverage"] == pytest.approx(1.0)

    case = best_history["report"]["results"][0]
    state = case["metadata"]["environment_state"]
    assert state["framework_runtime"]["summary"]["methods"] == ["execute_task"]
    assert state["framework_runtime"]["summary"]["input_modes"] == ["dict"]
    assert state["framework_runtime"]["summary"]["tool_call_count"] == 1
    assert state["framework_trace"]["adapter_conformance"]["passed"] is True

    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []
    assert "social-memory-framework-optimization" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_voice_streaming_realtime_manifest_runs_manifest_environments(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "AGENT_LEARNING_VOICE_STREAMING_EXAMPLE_KEY",
        "real-local-voice-streaming-key",
    )

    output_path = tmp_path / "voice-streaming.json"
    junit_path = tmp_path / "voice-streaming.junit.xml"
    sarif_path = tmp_path / "voice-streaming.sarif.json"
    markdown_path = tmp_path / "voice-streaming.md"

    exit_code = main([
        "run",
        str(EXAMPLES / "voice_streaming_realtime_manifest.json"),
        "--no-eval",
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.run.v1"
    assert payload["status"] == "passed"
    case = payload["report"]["results"][0]
    state = case["metadata"]["environment_state"]
    assert set(state) >= {"voice", "streaming_trace"}

    voice = state["voice"]
    assert voice["sample_rate_hz"] == 16000
    assert voice["last_transcript"] == "I need help with a refund on my order."
    assert voice["route_history"] == [
        {
            "route": "support",
            "reason": "refund support request",
            "target": {"queue": "refund_support", "priority": "high"},
        }
    ]
    assert voice["timing_distribution"]["stage_order"] == ["vad", "stt", "llm", "tts"]
    assert voice["timing_distribution"]["sample_count"] == 12
    assert voice["timing_distribution"]["stages"]["tts"]["p50_ms"] == 260.0
    assert voice["tts_history"][0]["text"].startswith("Your refund request")

    streaming = state["streaming_trace"]
    assert streaming["framework"] == "livekit"
    assert streaming["summary"]["event_count"] == 4
    assert streaming["summary"]["tool_delta_count"] == 1
    assert "tool_delta" in streaming["signals"]

    assistant_tool_names = {
        call["name"]
        for message in case["messages"]
        if message["role"] == "assistant"
        for call in message.get("tool_calls", [])
    }
    assert {
        "voice_status",
        "voice_timing",
        "transcribe_audio",
        "route_call",
        "streaming_trace_status",
        "list_stream_events",
        "inspect_stream_event",
        "speak",
    } <= assistant_tool_names
    tool_response_ids = {
        message.get("tool_call_id")
        for message in case["messages"]
        if message["role"] == "tool"
    }
    assert {
        "voice_status",
        "voice_timing",
        "transcribe_user",
        "route_support",
        "stream_status",
        "stream_tool_events",
        "inspect_stream_tool",
        "speak_answer",
    } <= tool_response_ids

    event_names = {(event["type"], event.get("name")) for event in case["events"]}
    assert ("voice_trace", "voice_status") in event_names
    assert ("voice_timing", "voice_timing_distribution") in event_names
    assert ("voice_route", "call_routed") in event_names
    assert ("voice", "tts_output") in event_names
    assert ("streaming_trace", "streaming_trace_status") in event_names
    assert ("streaming_trace", "streaming_events_listed") in event_names
    assert ("streaming_trace", "streaming_event_inspected") in event_names

    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []
    assert "voice-streaming-realtime-simulation" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_voice_streaming_realtime_optimization_example_runs_evidence_gates(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "AGENT_LEARNING_VOICE_STREAMING_OPT_EXAMPLE_KEY",
        "real-local-voice-streaming-opt-key",
    )

    output_path = tmp_path / "voice-streaming-optimization.json"
    junit_path = tmp_path / "voice-streaming-optimization.junit.xml"
    sarif_path = tmp_path / "voice-streaming-optimization.sarif.json"
    markdown_path = tmp_path / "voice-streaming-optimization.md"

    exit_code = main([
        "optimize",
        str(EXAMPLES / "voice_streaming_realtime_optimization.json"),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.optimization.v1"
    assert payload["status"] == "passed"
    assert payload["summary"]["optimization_score"] >= 0.99
    assert payload["summary"]["evaluation_score"] == pytest.approx(1.0)
    assert "simulation.environments" in payload["summary"]["search_paths"]

    best_config = payload["optimization"]["best_config"]
    env_types = [
        environment["type"]
        for environment in best_config["simulation"]["environments"]
    ]
    assert env_types == ["voice", "streaming_trace"]
    assert best_config["simulation"]["environments"][0]["data"]["sample_rate_hz"] == 16000
    assert (
        best_config["simulation"]["environments"][1]["data"]["state"]["route"]
        == "support"
    )

    best_history = max(
        payload["optimization"]["history"],
        key=lambda item: item["score"],
    )
    assert best_history["patch"].keys() == {"simulation.environments"}
    metrics = best_history["metrics"]
    for metric in (
        "voice_trace_coverage",
        "voice_interaction_quality",
        "voice_timing_distribution_quality",
        "voice_turn_taking",
        "tool_argument_schema",
        "streaming_trace_coverage",
        "streaming_interaction_quality",
    ):
        assert metrics[metric] == pytest.approx(1.0)

    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []
    assert "voice-streaming-realtime-optimization" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_redteam_campaign_optimization_example_runs_evidence_gates(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "AGENT_LEARNING_REDTEAM_OPT_EXAMPLE_KEY",
        "real-local-redteam-opt-key",
    )

    output_path = tmp_path / "redteam-campaign-optimization.json"
    junit_path = tmp_path / "redteam-campaign-optimization.junit.xml"
    sarif_path = tmp_path / "redteam-campaign-optimization.sarif.json"
    markdown_path = tmp_path / "redteam-campaign-optimization.md"

    exit_code = main([
        "optimize",
        str(EXAMPLES / "redteam_campaign_optimization.json"),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.optimization.v1"
    assert payload["status"] == "passed"
    assert payload["summary"]["optimization_score"] >= 0.9
    assert payload["summary"]["evaluation_score"] == pytest.approx(1.0)
    assert "simulation.environments" in payload["summary"]["search_paths"]

    best_config = payload["optimization"]["best_config"]
    env_types = [
        environment["type"]
        for environment in best_config["simulation"]["environments"]
    ]
    assert env_types == [
        "adversarial_attack_pack",
        "red_team_campaign",
        "red_team_readiness",
    ]

    best_campaign = best_config["simulation"]["environments"][1]["data"]
    assert best_campaign["required_attack_types"] == [
        "prompt_injection",
        "credential_exfiltration",
    ]
    assert best_campaign["required_surfaces"] == ["tool", "memory"]

    best_history = max(
        payload["optimization"]["history"],
        key=lambda item: item["score"],
    )
    assert best_history["patch"].keys() == {"simulation.environments"}
    metrics = best_history["metrics"]
    for metric in (
        "adversarial_resilience",
        "red_team_campaign_coverage",
        "red_team_campaign_quality",
        "red_team_readiness_coverage",
        "red_team_readiness_quality",
        "tool_selection_accuracy",
    ):
        assert metrics[metric] == pytest.approx(1.0)

    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []
    assert "redteam-campaign-optimization" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_redteam_autogen_optimization_example_regenerates_candidate_matrix(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "AGENT_LEARNING_REDTEAM_AUTOGEN_OPT_EXAMPLE_KEY",
        "real-local-redteam-autogen-opt-key",
    )

    output_path = tmp_path / "redteam-autogen-optimization.json"
    junit_path = tmp_path / "redteam-autogen-optimization.junit.xml"
    sarif_path = tmp_path / "redteam-autogen-optimization.sarif.json"
    markdown_path = tmp_path / "redteam-autogen-optimization.md"

    exit_code = main([
        "optimize",
        str(EXAMPLES / "redteam_autogen_optimization.json"),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.optimization.v1"
    assert payload["status"] == "passed"
    assert payload["summary"]["optimization_score"] >= 0.97
    assert payload["summary"]["evaluation_score"] == pytest.approx(1.0)
    assert payload["redteam"]["auto_generate"] is True
    assert set(payload["summary"]["search_paths"]) >= {
        "redteam.attacks",
        "redteam.surfaces",
    }

    best_config = payload["optimization"]["best_config"]
    assert best_config["redteam"]["attacks"] == [
        "prompt_injection",
        "credential_exfiltration",
    ]
    assert best_config["redteam"]["surfaces"] == ["tool", "memory"]

    best_history = max(
        payload["optimization"]["history"],
        key=lambda item: item["score"],
    )
    assert best_history["patch"] == {
        "redteam.attacks": [
            "prompt_injection",
            "credential_exfiltration",
        ],
        "redteam.surfaces": ["tool", "memory"],
    }
    metrics = best_history["metrics"]
    for metric in (
        "adversarial_resilience",
        "red_team_campaign_coverage",
        "red_team_campaign_quality",
        "tool_selection_accuracy",
    ):
        assert metrics[metric] == pytest.approx(1.0)

    case = best_history["report"]["results"][0]
    state = case["metadata"]["environment_state"]
    assert set(state) >= {"adversarial", "red_team_campaign"}
    campaign_summary = state["red_team_campaign"]["summary"]
    assert campaign_summary["attack_count"] == 4
    assert campaign_summary["coverage_cell_count"] == 4
    assert campaign_summary["missing_coverage_cells"] == []
    assert campaign_summary["missing_executed_cells"] == []

    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []
    assert "redteam-autogen-optimization" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_workspace_observability_optimization_example_runs_evidence_gates(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "AGENT_LEARNING_WORKSPACE_OBSERVABILITY_OPT_EXAMPLE_KEY",
        "real-local-workspace-observability-opt-key",
    )

    output_path = tmp_path / "workspace-observability-optimization.json"
    junit_path = tmp_path / "workspace-observability-optimization.junit.xml"
    sarif_path = tmp_path / "workspace-observability-optimization.sarif.json"
    markdown_path = tmp_path / "workspace-observability-optimization.md"

    exit_code = main([
        "optimize",
        str(EXAMPLES / "workspace_observability_optimization.json"),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.optimization.v1"
    assert payload["status"] == "passed"
    assert payload["summary"]["optimization_score"] >= 0.9
    assert payload["summary"]["evaluation_score"] == pytest.approx(1.0)
    assert "simulation.environments" in payload["summary"]["search_paths"]

    env_types = [
        environment["type"]
        for environment in payload["optimization"]["best_config"]["simulation"][
            "environments"
        ]
    ]
    assert env_types == ["workspace_run_manifest", "observability_replay"]

    best_history = max(
        payload["optimization"]["history"],
        key=lambda item: item["score"],
    )
    assert best_history["patch"].keys() == {"simulation.environments"}
    metrics = best_history["metrics"]
    for metric in (
        "workspace_run_coverage",
        "workspace_run_quality",
        "observability_replay_coverage",
        "observability_replay_quality",
        "tool_selection_accuracy",
    ):
        assert metrics[metric] == pytest.approx(1.0)

    case = best_history["report"]["results"][0]
    state = case["metadata"]["environment_state"]
    assert set(state) >= {"workspace_run_manifest", "observability_replay_pack"}
    workspace_summary = state["workspace_run_manifest"]["summary"]
    assert workspace_summary["failed_command_count"] == 0
    assert workspace_summary["open_red_team_finding_count"] == 0
    assert workspace_summary["secret_leak_count"] == 0
    assert workspace_summary["missing_required_evidence"] == []
    replay_summary = state["observability_replay_pack"]["summary"]
    assert replay_summary["case_count"] == 2
    assert replay_summary["failed_case_count"] == 1
    assert replay_summary["missing_trace_signals"] == []

    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []
    assert "workspace-observability-optimization" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_agent_integration_optimization_example_runs_provider_matrix(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "AGENT_LEARNING_AGENT_INTEGRATION_OPT_EXAMPLE_KEY",
        "real-local-agent-integration-opt-key",
    )

    output_path = tmp_path / "agent-integration-optimization.json"
    junit_path = tmp_path / "agent-integration-optimization.junit.xml"
    sarif_path = tmp_path / "agent-integration-optimization.sarif.json"
    markdown_path = tmp_path / "agent-integration-optimization.md"

    exit_code = main([
        "optimize",
        str(EXAMPLES / "agent_integration_optimization.json"),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.optimization.v1"
    assert payload["status"] == "passed"
    assert payload["summary"]["optimization_score"] >= 0.98
    assert payload["summary"]["evaluation_score"] == pytest.approx(1.0)
    assert "simulation.environments" in payload["summary"]["search_paths"]

    env_types = [
        environment["type"]
        for environment in payload["optimization"]["best_config"]["simulation"][
            "environments"
        ]
    ]
    assert env_types == ["agent_integration"]

    best_history = max(
        payload["optimization"]["history"],
        key=lambda item: item["score"],
    )
    assert best_history["patch"].keys() == {"simulation.environments"}
    metrics = best_history["metrics"]
    for metric in (
        "agent_integration_coverage",
        "agent_integration_quality",
        "tool_selection_accuracy",
    ):
        assert metrics[metric] == pytest.approx(1.0)

    case = best_history["report"]["results"][0]
    quality_metric = next(
        metric
        for metric in case["evaluation"]["agent_report"]["metrics"]
        if metric["name"] == "agent_integration_quality"
    )
    provider_channel_checks = {
        (check["expected"]["provider"], check["expected"]["channel"]): check["match"]
        for check in quality_metric["details"]["checks"]
        if check["check"] == "required_provider_channel"
    }
    assert provider_channel_checks[("vapi", "phone")] is True
    assert provider_channel_checks[("vapi", "webrtc")] is True
    assert provider_channel_checks[("bland", "phone")] is True
    assert provider_channel_checks[("bland", "sip")] is True
    assert provider_channel_checks[("bland", "webrtc")] is True

    state = case["metadata"]["environment_state"]
    assert set(state) == {"agent_integration_manifest"}
    summary = state["agent_integration_manifest"]["summary"]
    assert set(summary["observed_providers"]) >= {
        "agora",
        "bland",
        "deepgram",
        "elevenlabs",
        "livekit",
        "pipecat",
        "retell",
        "twilio",
        "vapi",
    }
    assert set(summary["observed_channels"]) >= {
        "chat",
        "voice",
        "webrtc",
        "phone",
        "sip",
        "websocket",
        "media_stream",
    }
    assert set(summary["trace_frameworks"]) >= {
        "autogen",
        "crewai",
        "langchain",
        "langgraph",
        "livekit",
        "openai_agents",
        "pipecat",
    }
    assert summary["verified_provider_count"] == 16
    assert summary["failed_session_count"] == 0
    assert summary["missing_required_providers"] == []
    assert summary["missing_required_channels"] == []
    assert summary["missing_required_trace_frameworks"] == []
    assert summary["providers_without_verified_credentials"] == []

    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []
    assert "agent-integration-optimization" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_multi_agent_framework_handoff_optimization_example_runs_captured_traces(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "AGENT_LEARNING_MULTI_AGENT_FRAMEWORK_HANDOFF_OPT_EXAMPLE_KEY",
        "real-local-multi-agent-framework-handoff-opt-key",
    )

    output_path = tmp_path / "multi-agent-framework-handoff-optimization.json"
    junit_path = tmp_path / "multi-agent-framework-handoff-optimization.junit.xml"
    sarif_path = tmp_path / "multi-agent-framework-handoff-optimization.sarif.json"
    markdown_path = tmp_path / "multi-agent-framework-handoff-optimization.md"

    exit_code = main([
        "optimize",
        str(EXAMPLES / "multi_agent_framework_handoff_optimization.json"),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.optimization.v1"
    assert payload["status"] == "passed"
    assert payload["summary"]["optimization_score"] >= 0.99
    assert payload["summary"]["evaluation_score"] == pytest.approx(1.0)
    assert payload["optimization"]["optimizer_trace"]["optimizer"] == (
        "AgentEvolutionOptimizer"
    )
    assert "simulation.environments" in payload["summary"]["search_paths"]

    best_config_envs = payload["optimization"]["best_config"]["simulation"][
        "environments"
    ]
    assert [environment["type"] for environment in best_config_envs] == [
        "framework_trace",
        "framework_trace",
        "framework_trace",
        "framework_trace",
        "multi_agent_room",
    ]
    assert [
        environment["data"]["framework"]
        for environment in best_config_envs
        if environment["type"] == "framework_trace"
    ] == ["openai_agents", "autogen", "crewai", "langgraph"]

    best_history = max(
        payload["optimization"]["history"],
        key=lambda item: item["score"],
    )
    assert best_history["patch"].keys() == {"simulation.environments"}
    metrics = best_history["metrics"]
    for metric in (
        "framework_transcript_quality",
        "multi_agent_trace_coverage",
        "multi_agent_coordination_quality",
        "task_completion",
        "trajectory_score",
    ):
        assert metrics[metric] == pytest.approx(1.0)

    case = best_history["report"]["results"][0]
    state = case["metadata"]["environment_state"]
    assert set(state) == {"framework_trace", "multi_agent"}

    transcript_metric = next(
        metric
        for metric in case["evaluation"]["agent_report"]["metrics"]
        if metric["name"] == "framework_transcript_quality"
    )
    observed = transcript_metric["details"]["observed"]
    assert set(observed["speaker_sequence"]) >= {
        "triage_agent",
        "retrieval_agent",
        "critic_agent",
        "planner",
        "researcher",
        "reviewer",
        "manager",
        "analyst",
        "qa",
        "retriever",
        "critic",
    }
    assert {handoff["to"] for handoff in observed["handoffs"]} >= {
        "retrieval_agent",
        "critic_agent",
        "researcher",
        "analyst",
        "retriever",
    }
    assert "ckpt_retrieval" in {
        checkpoint["id"].replace("-", "_")
        for checkpoint in observed["checkpoints"]
    }
    assert observed["errors"] == []

    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []
    assert "multi-agent-framework-handoff-optimization" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_optimizer_governance_optimization_example_runs_society_trace(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "AGENT_LEARNING_OPTIMIZER_GOVERNANCE_OPT_EXAMPLE_KEY",
        "real-local-optimizer-governance-opt-key",
    )

    output_path = tmp_path / "optimizer-governance-optimization.json"
    junit_path = tmp_path / "optimizer-governance-optimization.junit.xml"
    sarif_path = tmp_path / "optimizer-governance-optimization.sarif.json"
    markdown_path = tmp_path / "optimizer-governance-optimization.md"

    exit_code = main([
        "optimize",
        str(EXAMPLES / "optimizer_governance_optimization.json"),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.optimization.v1"
    assert payload["status"] == "passed"
    assert payload["summary"]["optimization_score"] >= 0.98
    assert payload["summary"]["evaluation_score"] == pytest.approx(1.0)
    assert "simulation.environments" in payload["summary"]["search_paths"]

    env_types = [
        environment["type"]
        for environment in payload["optimization"]["best_config"]["simulation"][
            "environments"
        ]
    ]
    assert env_types == ["optimizer_trace"]

    best_history = max(
        payload["optimization"]["history"],
        key=lambda item: item["score"],
    )
    assert best_history["patch"].keys() == {"simulation.environments"}
    metrics = best_history["metrics"]
    for metric in (
        "optimizer_trace_coverage",
        "optimizer_trace_quality",
        "tool_selection_accuracy",
    ):
        assert metrics[metric] == pytest.approx(1.0)

    case = best_history["report"]["results"][0]
    state = case["metadata"]["environment_state"]
    assert set(state) == {"optimizer_society_trace"}
    trace_summary = state["optimizer_society_trace"]["summary"]
    assert trace_summary["role_count"] == 5
    assert trace_summary["proposal_count"] == 5
    assert trace_summary["round_count"] == 3
    assert trace_summary["diagnostic_count"] == 2
    assert trace_summary["role_credit_count"] == 5
    assert trace_summary["duplicate_candidate_count"] == 0
    assert trace_summary["best_candidate_id"] == "c_steward"
    assert trace_summary["final_score"] == pytest.approx(0.99)
    for flag in (
        "has_role_graph",
        "has_critique",
        "has_synthesis",
        "has_steward",
        "has_governance",
        "has_role_diversity",
        "has_mediator",
        "has_contract_gate",
        "has_rollback",
        "has_locality",
        "has_dependency_audit",
    ):
        assert trace_summary[flag] is True
    assert trace_summary["governance_check_count"] == 6
    assert trace_summary["governance_pass_rate"] == pytest.approx(1.0)

    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []
    assert "optimizer-governance-optimization" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_agent_control_plane_optimization_example_runs_trust_and_control_gate(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "AGENT_LEARNING_AGENT_CONTROL_PLANE_OPT_EXAMPLE_KEY",
        "real-local-agent-control-plane-opt-key",
    )

    output_path = tmp_path / "agent-control-plane-optimization.json"
    junit_path = tmp_path / "agent-control-plane-optimization.junit.xml"
    sarif_path = tmp_path / "agent-control-plane-optimization.sarif.json"
    markdown_path = tmp_path / "agent-control-plane-optimization.md"

    exit_code = main([
        "optimize",
        str(EXAMPLES / "agent_control_plane_optimization.json"),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.optimization.v1"
    assert payload["status"] == "passed"
    assert payload["summary"]["optimization_score"] >= 0.98
    assert payload["summary"]["evaluation_score"] == pytest.approx(1.0)
    assert "simulation.environments" in payload["summary"]["search_paths"]

    env_types = [
        environment["type"]
        for environment in payload["optimization"]["best_config"]["simulation"][
            "environments"
        ]
    ]
    assert env_types == ["agent_trust_boundary", "agent_control_plane"]

    best_history = max(
        payload["optimization"]["history"],
        key=lambda item: item["score"],
    )
    assert best_history["patch"].keys() == {"simulation.environments"}
    metrics = best_history["metrics"]
    for metric in (
        "agent_trust_boundary_coverage",
        "agent_trust_boundary_quality",
        "agent_control_plane_coverage",
        "agent_control_plane_quality",
        "tool_selection_accuracy",
    ):
        assert metrics[metric] == pytest.approx(1.0)

    case = best_history["report"]["results"][0]
    state = case["metadata"]["environment_state"]
    assert set(state) == {"agent_trust_boundary_model", "agent_control_plane"}
    trust_summary = state["agent_trust_boundary_model"]["summary"]
    assert trust_summary["control_count"] == 11
    assert trust_summary["required_control_rate"] == pytest.approx(1.0)
    assert trust_summary["high_risk_unmitigated_count"] == 0
    assert trust_summary["gaps"] == []
    assert trust_summary["has_secret_handling"] is True
    control_summary = state["agent_control_plane"]["summary"]
    assert control_summary["control_count"] == 11
    assert control_summary["required_control_rate"] == pytest.approx(1.0)
    assert control_summary["exceeded_budget_count"] == 0
    assert control_summary["high_risk_uncontained_count"] == 0
    assert control_summary["gaps"] == []
    assert control_summary["has_kill_switch"] is True
    assert control_summary["has_drift_detection"] is True

    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []
    assert "agent-control-plane-optimization" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_browser_cua_optimization_example_runs_redteam_replay(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "AGENT_LEARNING_BROWSER_CUA_OPT_EXAMPLE_KEY",
        "real-local-browser-cua-opt-key",
    )

    output_path = tmp_path / "browser-cua-optimization.json"
    junit_path = tmp_path / "browser-cua-optimization.junit.xml"
    sarif_path = tmp_path / "browser-cua-optimization.sarif.json"
    markdown_path = tmp_path / "browser-cua-optimization.md"

    exit_code = main([
        "optimize",
        str(EXAMPLES / "browser_cua_optimization.json"),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.optimization.v1"
    assert payload["status"] == "passed"
    assert payload["summary"]["optimization_score"] >= 0.98
    assert payload["summary"]["evaluation_score"] == pytest.approx(1.0)
    assert "simulation.environments" in payload["summary"]["search_paths"]

    env_types = [
        environment["type"]
        for environment in payload["optimization"]["best_config"]["simulation"][
            "environments"
        ]
    ]
    assert env_types == ["browser_cua"]

    best_history = max(
        payload["optimization"]["history"],
        key=lambda item: item["score"],
    )
    assert best_history["patch"].keys() == {"simulation.environments"}
    metrics = best_history["metrics"]
    for metric in (
        "browser_action_safety",
        "browser_action_outcome",
        "browser_grounding_quality",
        "browser_mutation_resilience",
        "browser_trace_coverage",
        "tool_selection_accuracy",
    ):
        assert metrics[metric] == pytest.approx(1.0)

    case = best_history["report"]["results"][0]
    state = case["metadata"]["environment_state"]
    assert set(state) == {"browser"}
    browser = state["browser"]
    assert browser["checkout_complete"] is True
    assert browser["order_id"] == "ord_123"
    assert browser["url"] == "https://shop.example.test/confirmation"
    assert browser["mutation_pack"]["summary"]["mutation_count"] == 2
    assert browser["action_replay"][0]["mutation_id"] == "selector_drift_checkout"
    assert browser["action_replay"][0]["selector"] == "button[data-testid='place-order-safe']"
    assert browser["action_replay"][0]["success"] is True
    assert browser["action_replay"][0]["prompt_injection_touched"] is False

    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []
    assert "browser-cua-redteam-optimization" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_framework_certification_optimization_example_runs_framework_evidence(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "AGENT_LEARNING_FRAMEWORK_CERT_OPT_EXAMPLE_KEY",
        "real-local-framework-cert-opt-key",
    )

    output_path = tmp_path / "framework-certification-optimization.json"
    junit_path = tmp_path / "framework-certification-optimization.junit.xml"
    sarif_path = tmp_path / "framework-certification-optimization.sarif.json"
    markdown_path = tmp_path / "framework-certification-optimization.md"

    exit_code = main([
        "optimize",
        str(EXAMPLES / "framework_certification_optimization.json"),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.optimization.v1"
    assert payload["status"] == "passed"
    assert payload["summary"]["optimization_score"] >= 0.98
    assert payload["summary"]["evaluation_score"] == pytest.approx(1.0)
    assert "simulation.environments" in payload["summary"]["search_paths"]

    env_types = [
        environment["type"]
        for environment in payload["optimization"]["best_config"]["simulation"][
            "environments"
        ]
    ]
    assert env_types == [
        "framework_lifecycle",
        "framework_capability",
        "framework_probe",
        "framework_portability",
    ]

    best_history = max(
        payload["optimization"]["history"],
        key=lambda item: item["score"],
    )
    assert best_history["patch"].keys() == {"simulation.environments"}
    metrics = best_history["metrics"]
    for metric in (
        "framework_lifecycle_coverage",
        "framework_lifecycle_quality",
        "framework_capability_coverage",
        "framework_capability_quality",
        "framework_probe_coverage",
        "framework_probe_quality",
        "framework_portability_coverage",
        "framework_portability_quality",
        "tool_selection_accuracy",
    ):
        assert metrics[metric] == pytest.approx(1.0)

    case = best_history["report"]["results"][0]
    state = case["metadata"]["environment_state"]
    assert set(state) == {
        "framework_lifecycle_trace",
        "framework_capability_matrix",
        "framework_probe_suite",
        "framework_portability_matrix",
    }
    lifecycle = state["framework_lifecycle_trace"]["summary"]
    assert lifecycle["phase_count"] == 10
    assert lifecycle["recovered_error_count"] == 1
    capability = state["framework_capability_matrix"]["summary"]
    assert capability["supported_count"] == 9
    assert capability["missing_count"] == 0
    probe = state["framework_probe_suite"]["summary"]
    assert probe["passed_count"] == 12
    assert probe["failed_count"] == 0
    portability = state["framework_portability_matrix"]["summary"]
    assert portability["mapped_count"] == 10
    assert portability["missing_count"] == 0

    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []
    assert "framework-certification-optimization" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_autonomous_redteam_task_world_optimization_example_runs_full_harness(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "AGENT_LEARNING_AUTONOMOUS_REDTEAM_OPT_EXAMPLE_KEY",
        "real-local-autonomous-redteam-opt-key",
    )

    output_path = tmp_path / "autonomous-redteam-task-world-optimization.json"
    junit_path = tmp_path / "autonomous-redteam-task-world-optimization.junit.xml"
    sarif_path = tmp_path / "autonomous-redteam-task-world-optimization.sarif.json"
    markdown_path = tmp_path / "autonomous-redteam-task-world-optimization.md"

    exit_code = main([
        "optimize",
        str(EXAMPLES / "autonomous_redteam_task_world_optimization.json"),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.optimization.v1"
    assert payload["status"] == "passed"
    assert payload["summary"]["optimization_score"] == pytest.approx(1.0)
    assert payload["summary"]["evaluation_score"] == pytest.approx(1.0)
    assert "simulation.environments" in payload["summary"]["search_paths"]

    env_types = [
        environment["type"]
        for environment in payload["optimization"]["best_config"]["simulation"][
            "environments"
        ]
    ]
    assert env_types == [
        "structured_artifact",
        "domain_package",
        "world_attack_replay",
        "autonomy_loop",
    ]

    best_history = max(
        payload["optimization"]["history"],
        key=lambda item: item["score"],
    )
    assert best_history["patch"].keys() == {"simulation.environments"}
    assert best_history["score"] == pytest.approx(1.0)
    assert {
        name: score
        for name, score in best_history["metrics"].items()
        if score < 1.0
    } == {}
    for metric in (
        "artifact_semantics_quality",
        "artifact_grounding_quality",
        "domain_package_quality",
        "world_contract_coverage",
        "world_contract_quality",
        "adversarial_resilience",
        "autonomy_loop_coverage",
        "autonomy_loop_quality",
        "tool_argument_schema",
    ):
        assert best_history["metrics"][metric] == pytest.approx(1.0)

    case = best_history["report"]["results"][0]
    state = case["metadata"]["environment_state"]
    assert set(state) == {
        "adversarial",
        "autonomy_loop",
        "domain_packages",
        "structured_artifacts",
        "world_attack_replay",
        "world_contract",
    }
    assert state["structured_artifacts"]["ids"] == ["approval_policy"]
    assert state["domain_packages"]["ids"] == ["refund_case"]
    world_summary = state["world_attack_replay"]["summary"]
    assert world_summary["world_terminal_status"] == "success"
    assert world_summary["completed_required_transition_count"] == 2
    assert world_summary["invariant_violation_count"] == 0
    assert world_summary["attack_count"] == 2
    assert world_summary["canary_count"] == 1
    assert state["autonomy_loop"]["stages_observed"] == [
        "act",
        "memory",
        "observe",
        "orient",
        "plan",
        "reflect",
        "skill",
        "status",
        "verify",
    ]

    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []
    assert "autonomous-redteam-task-world-optimization" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_multimodal_image_optimization_example_runs_image_evidence(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv(
        "AGENT_LEARNING_MULTIMODAL_IMAGE_OPT_EXAMPLE_KEY",
        "real-local-multimodal-image-opt-key",
    )

    output_path = tmp_path / "multimodal-image-optimization.json"
    junit_path = tmp_path / "multimodal-image-optimization.junit.xml"
    sarif_path = tmp_path / "multimodal-image-optimization.sarif.json"
    markdown_path = tmp_path / "multimodal-image-optimization.md"

    exit_code = main([
        "optimize",
        str(EXAMPLES / "multimodal_image_optimization.json"),
        "--output",
        str(output_path),
        "--junit",
        str(junit_path),
        "--sarif",
        str(sarif_path),
        "--markdown",
        str(markdown_path),
    ])

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kind"] == "agent-learning.optimization.v1"
    assert payload["status"] == "passed"
    assert payload["summary"]["optimization_score"] == pytest.approx(1.0)
    assert payload["summary"]["evaluation_score"] == pytest.approx(1.0)
    assert "simulation.environments" in payload["summary"]["search_paths"]

    env_types = [
        environment["type"]
        for environment in payload["optimization"]["best_config"]["simulation"][
            "environments"
        ]
    ]
    assert env_types == ["multimodal_image"]

    best_history = max(
        payload["optimization"]["history"],
        key=lambda item: item["score"],
    )
    assert best_history["patch"].keys() == {"simulation.environments"}
    assert best_history["score"] == pytest.approx(1.0)
    assert {
        name: score
        for name, score in best_history["metrics"].items()
        if score < 1.0
    } == {}
    for metric in (
        "artifact_coverage",
        "artifact_grounding_quality",
        "artifact_semantics_quality",
        "agent_goal_accuracy",
        "multimodal_faithfulness",
        "tool_selection_accuracy",
    ):
        assert best_history["metrics"][metric] == pytest.approx(1.0)

    case = best_history["report"]["results"][0]
    state = case["metadata"]["environment_state"]
    assert state == {
        "images": {
            "ids": ["receipt_image"],
            "last_inspected": "receipt_image",
            "vision_harness": "receipt_grounding",
        }
    }

    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"] == []
    assert "multimodal-image-optimization" in markdown_path.read_text(
        encoding="utf-8"
    )
