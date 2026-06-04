from __future__ import annotations

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
                "AGENT_LEARNING_REDTEAM_EXAMPLE_KEY",
                "AGENT_LEARNING_WORLD_FRAMEWORK_OPT_EXAMPLE_KEY",
                "AGENT_LEARNING_VOICE_STREAMING_OPT_EXAMPLE_KEY",
                "AGENT_LEARNING_REDTEAM_OPT_EXAMPLE_KEY",
                "AGENT_LEARNING_WORKSPACE_OBSERVABILITY_OPT_EXAMPLE_KEY",
                "AGENT_LEARNING_AGENT_INTEGRATION_OPT_EXAMPLE_KEY",
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
    if command == "suite":
        assert payload["summary"]["job_count"] == 15
        assert payload["summary"]["passed_count"] == 15
        assert payload["summary"]["score"] == pytest.approx(1.0)
        assert [child["command"] for child in payload["children"]] == [
            "run",
            "eval",
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
        ]
        assert {child["kind"] for child in payload["children"]} == {
            "agent-learning.run.v1",
            "agent-learning.eval.v1",
            "agent-learning.redteam.v1",
            "agent-learning.eval-optimization.v1",
            "agent-learning.optimization.v1",
        }
    if command in {"run", "eval", "redteam"}:
        assert payload["summary"]["case_count"] >= 1


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
    assert payload["summary"]["commands"] == {"run": 4}
    assert payload["summary"]["score"] == pytest.approx(1.0)

    expected = {
        "langchain-runnable": ("langchain", "ainvoke", "dict", "text"),
        "langgraph-state-graph": ("langgraph", "ainvoke", "dict", "text"),
        "pipecat-voice-pipeline": ("pipecat", "process", "dict", "voice"),
        "livekit-realtime-agent": ("livekit", "respond", "text", "voice"),
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
