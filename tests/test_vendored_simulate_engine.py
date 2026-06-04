from __future__ import annotations

import asyncio
import importlib
import json
import textwrap
from pathlib import Path

import pytest

from agent_learning import simulate


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FI_ROOT = PROJECT_ROOT / "src" / "fi"


FRAMEWORK_AGENT_MODULE = """
class LocalLangGraphAgent:
    async def ainvoke(self, payload):
        assert payload["metadata"]["framework"] == "langgraph"
        assert payload["metadata"]["suite"] == "agent-learning-kit"
        assert payload["scenario_name"] == "vendored-simulate-runtime"
        assert {"apply_world_transition", "framework_trace_status"} <= {
            tool["name"] for tool in payload["tools"]
        }
        return {
            "content": (
                "The refund world contract completed and the framework adapter "
                "conformance passed."
            ),
            "tool_calls": [
                {
                    "id": "approve_refund",
                    "name": "apply_world_transition",
                    "arguments": {"id": "approve_refund"},
                },
                {
                    "id": "framework_status",
                    "name": "framework_trace_status",
                    "arguments": {},
                },
            ],
            "metadata": {"runtime_contract": {"passed": True}},
        }


def build_agent():
    return LocalLangGraphAgent()
"""


def _module_path(module_name: str) -> Path:
    module = importlib.import_module(module_name)
    return Path(module.__file__).resolve()


def _assert_vendored_under_agent_learning_kit(obj: object) -> None:
    module_path = _module_path(obj.__module__)
    assert module_path.is_relative_to(FI_ROOT)


def _manifest(required_env: str) -> dict:
    return {
        "version": "agent-simulate.cli.v1",
        "name": "vendored-simulate-runtime",
        "required_env": [required_env],
        "scenario": {
            "name": "vendored-simulate-runtime",
            "dataset": [
                {
                    "persona": {"name": "Maya", "role": "sdk-owner"},
                    "situation": "Maya needs a local vendored simulation run.",
                    "outcome": (
                        "The refund world contract completed and the framework "
                        "adapter conformance passed."
                    ),
                }
            ],
        },
        "agent": {
            "type": "framework",
            "framework": "langgraph",
            "target": "framework_agent.py:build_agent",
            "factory": True,
            "method": "ainvoke",
            "input_mode": "dict",
            "trace_runtime": True,
            "metadata": {"suite": "agent-learning-kit"},
        },
        "simulation": {
            "engine": "local_text",
            "max_turns": 1,
            "min_turns": 1,
            "environments": [
                {
                    "type": "world_contract",
                    "data": {
                        "name": "refund-world",
                        "actors": ["agent", "customer"],
                        "resources": ["refund"],
                        "initial_state": {
                            "policy": {"can_refund": True},
                            "refund": {"status": "pending"},
                        },
                        "transitions": [
                            {
                                "id": "approve_refund",
                                "actor": "agent",
                                "resource": "refund",
                                "action": "approve_refund",
                                "required": True,
                                "preconditions": {"refund.status": "pending"},
                                "effects": {"refund.status": "approved"},
                                "postconditions": {"refund.status": "approved"},
                                "signals": ["refund_resolution"],
                            }
                        ],
                        "invariants": [
                            {
                                "id": "policy_allows_refunds",
                                "must": {"policy.can_refund": True},
                            }
                        ],
                        "success_conditions": [
                            {
                                "id": "refund_approved",
                                "must": {"refund.status": "approved"},
                            }
                        ],
                    },
                },
                {
                    "type": "framework_trace",
                    "data": {
                        "framework": "langgraph",
                        "spans": [
                            {
                                "id": "agent_node",
                                "name": "agent_node",
                                "input": "refund request",
                                "output": "approved",
                                "tool_calls": [
                                    {"name": "apply_world_transition"}
                                ],
                                "signals": ["model", "tool", "state"],
                            }
                        ],
                        "adapter_required_signals": ["model", "tool", "state"],
                        "adapter_required_mappings": {
                            "tool": ["tool_name"],
                        },
                    },
                },
            ],
        },
        "evaluation": {"enabled": False},
    }


def _eval_suite() -> dict:
    return {
        "version": "agent-simulate.eval.v1",
        "name": "vendored-local-eval-suite",
        "providers": [
            {
                "id": "scripted",
                "type": "scripted",
                "response": "Policy answer: {{question}} is approved locally.",
            }
        ],
        "prompts": [{"id": "support", "template": "{{question}}"}],
        "tests": [
            {
                "id": "policy_lookup",
                "vars": {"question": "refund policy"},
                "assert": [
                    {"type": "contains", "value": "refund policy"},
                    {"type": "not_contains", "value": "network"},
                ],
            }
        ],
    }


def test_agent_learning_simulate_exports_are_vendored_from_src_fi() -> None:
    for module_name in (
        "fi.simulate",
        "fi.simulate.manifest",
        "fi.simulate.suite",
        "fi.simulate.simulation.engines.local_text",
    ):
        assert _module_path(module_name).is_relative_to(FI_ROOT)

    for exported in (
        simulate.AgentResponse,
        simulate.WorldContractEnvironment,
        simulate.FrameworkTraceEnvironment,
        simulate.LocalTextEngine,
        simulate.TestRunner,
    ):
        _assert_vendored_under_agent_learning_kit(exported)

    assert "langgraph" in simulate.supported_frameworks()


def test_public_manifest_api_runs_vendored_local_world_and_framework_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    required_env = "AGENT_LEARNING_KIT_SIMULATE_TEST_KEY"
    manifest = _manifest(required_env)
    manifest_path = tmp_path / "simulate.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "framework_agent.py").write_text(
        textwrap.dedent(FRAMEWORK_AGENT_MODULE),
        encoding="utf-8",
    )

    monkeypatch.delenv(required_env, raising=False)
    assert simulate.detect_manifest_command(manifest) == "run"
    assert simulate.missing_manifest_env(manifest) == [required_env]
    fi_simulate = importlib.import_module("fi.simulate")
    with pytest.raises(fi_simulate.ManifestError, match=required_env):
        simulate.validate_manifest_env(manifest)

    monkeypatch.setenv(required_env, "local-only-key")
    environments = simulate.build_manifest_environments(
        manifest["simulation"]["environments"],
        base_dir=tmp_path,
    )
    assert [environment.name for environment in environments] == [
        "world_contract",
        "framework_trace",
    ]
    assert {"world_contract", "framework_trace"} <= set(
        simulate.supported_manifest_environment_types()
    )
    assert {
        "multi_agent_room",
        "retrieval_memory",
        "streaming_trace",
        "voice",
        "voice_replay",
        "world_orchestration_replay",
    } <= set(simulate.supported_manifest_environment_types())

    result = asyncio.run(simulate.run_manifest_file(manifest_path, no_eval=True))

    assert result["status"] == "passed"
    assert result["exit_code"] == 0
    assert result["summary"]["case_count"] == 1
    case = result["report"]["results"][0]
    assert "refund world contract completed" in case["transcript"]

    state = case["metadata"]["environment_state"]
    assert state["world_contract"]["state"]["refund"]["status"] == "approved"
    assert state["world_contract"]["summary"]["terminal_status"] == "success"
    assert state["framework_trace"]["adapter_conformance"]["passed"] is True
    assert state["framework_runtime"]["framework"] == "langgraph"
    assert state["framework_runtime"]["summary"]["tool_call_count"] == 2

    event_names = {(event["type"], event.get("name")) for event in case["events"]}
    assert ("world_contract", "world_transition_applied") in event_names
    assert ("framework_trace", "framework_trace_status") in event_names
    assert [
        message["tool_call_id"]
        for message in case["messages"]
        if message["role"] == "tool"
    ] == ["approve_refund", "framework_status"]


def test_public_eval_suite_api_runs_local_prompt_provider(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(_eval_suite()), encoding="utf-8")

    loaded = simulate.load_eval_suite_file(suite_path)
    result = simulate.run_eval_suite_file(suite_path)

    assert loaded["version"] == "agent-simulate.eval.v1"
    assert result["kind"] == "agent-simulate.eval.v1"
    assert result["status"] == "passed"
    assert result["exit_code"] == 0
    assert result["summary"]["case_count"] == 1
    assert result["summary"]["assertion_count"] == 2
    assert result["eval_suite"]["cases"][0]["output"] == (
        "Policy answer: refund policy is approved locally."
    )
