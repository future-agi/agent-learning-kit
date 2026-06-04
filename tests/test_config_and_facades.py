from __future__ import annotations

import copy
import importlib
import json
import os
import tomllib
from pathlib import Path

import pytest

from agent_learning import configure, current_config, get_api_key
from agent_learning.cli import main

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_configure_sets_unified_key_environment(monkeypatch):
    for key in (
        "AGENT_LEARNING_API_KEY",
        "FUTURE_AGI_API_KEY",
        "FI_API_KEY",
        "AGENT_LEARNING_PROJECT_ID",
        "FUTURE_AGI_PROJECT_ID",
    ):
        monkeypatch.delenv(key, raising=False)

    config = configure(
        api_key="real-local-agent-learning-key",
        project_id="project_123",
    )

    assert config.api_key == "real-local-agent-learning-key"
    assert current_config().project_id == "project_123"
    assert get_api_key(required=True) == "real-local-agent-learning-key"
    assert os.environ["AGENT_LEARNING_API_KEY"] == "real-local-agent-learning-key"
    assert os.environ["FUTURE_AGI_API_KEY"] == "real-local-agent-learning-key"
    assert os.environ["FI_API_KEY"] == "real-local-agent-learning-key"


def test_facades_expose_unified_agent_learning_modules():
    from agent_learning import evals, optimize, redteam, simulate, suite

    assert simulate.run_eval_suite_file is not None
    assert redteam.redteam_manifest_file is not None
    assert redteam.prepare_redteam_manifest is not None
    assert optimize.OptimizationTarget is not None
    assert optimize.optimize_eval_suite_file is not None
    assert evals.evaluate is not None
    assert suite.run_suite_file is not None
    assert suite.AGENT_LEARNING_SUITE_KIND == "agent-learning.suite.v1"
    assert simulate.StreamingTraceEnvironment is not None
    assert simulate.VoiceEnvironment is not None
    assert simulate.BrowserEnvironment is not None
    assert simulate.normalize_browser_trace_export is not None
    assert simulate.normalize_playwright_trace_export is not None
    assert simulate.normalize_browser_mutation_pack is not None
    assert simulate.normalize_streaming_trace_events is not None
    assert simulate.normalize_voice_timing_distribution is not None
    assert {"browser", "browser_cua", "computer_use"} <= set(
        simulate.supported_manifest_environment_types()
    )
    assert {
        "langchain",
        "langgraph",
        "livekit",
        "pipecat",
    } <= set(simulate.supported_frameworks())


def test_optimize_facade_exposes_advanced_governance_surfaces():
    from agent_learning import optimize
    from agent_learning.optimize import (
        AgentFeedbackOptimizer,
        build_optimizer_society_trace,
    )

    assert AgentFeedbackOptimizer is optimize.AgentFeedbackOptimizer
    assert optimize.AgentMultiInteractionOptimizer is not None
    assert optimize.AgentBanditOptimizer is not None
    assert optimize.AgentParetoOptimizer is not None
    assert optimize.AgentSocialMemoryOptimizer is not None
    assert optimize.CouncilAgentOptimizer is not None
    assert optimize.SocietyAgentOptimizer is not None
    assert optimize.FutureAGIRegressionReplayOptimizer is not None
    assert optimize.schedule_futureagi_registry_replay_optimization is not None
    assert optimize.build_futureagi_registry_replay_pack_manifest is not None
    assert optimize.build_agent_regression_dataset is not None
    assert optimize.export_agent_deployment is not None
    assert optimize.check_agent_deployment_promotion is not None
    assert optimize.check_agent_deployment_rollback is not None
    assert optimize.research_note_for is not None

    candidate = optimize.AgentCandidate.from_config(
        {
            "framework": {"events": {"source": "langgraph_stream_events"}},
            "langgraph": {"nodes": {"planner": "plan", "executor": "act"}},
            "memory": {"state_persistence": "sqlite"},
            "secrets": {"api_key": "real-local-secret-for-redaction"},
        },
        target_name="agent-learning-advanced-optimize",
        layers=["policy", "security"],
        patch={"policy.approval": "required"},
    )
    history = [
        optimize.IterationHistory(
            prompt="role proposal",
            average_score=1.0,
            individual_results=[optimize.EvaluationResult(score=1.0, reason="ok")],
            candidate_id=candidate.id,
            candidate_config=candidate.config,
            layers=["policy", "security"],
            metadata={
                "proposal_role": "critic",
                "proposal_round": 1,
                "proposal_reason": "tighten approval and redaction gates",
                "patch": candidate.patch,
                "role_kind": "critic",
                "proposal_metadata": {"role_archetype": "adversarial_reviewer"},
            },
        )
    ]
    result = optimize.OptimizationResult(
        best_generator="scripted",
        best_candidate=candidate,
        history=history,
        final_score=1.0,
        metadata={
            "optimizer": "SocietyAgentOptimizer",
            "target_name": "agent-learning-advanced-optimize",
            "best_candidate_id": candidate.id,
            "roles": ["critic", "steward"],
            "role_graph": [
                {
                    "name": "critic",
                    "proposal_kind": "adversarial_review",
                    "archetype": "adversarial_reviewer",
                }
            ],
            "rounds": [{"round": 1, "proposal_count": 1}],
            "diagnostics": [{"component": "policy", "status": "resolved"}],
            "search_paths": ["policy.approval"],
        },
    )

    trace = build_optimizer_society_trace(result)
    assert trace["kind"] == "optimizer_society_trace"
    assert trace["summary"]["role_count"] == 1
    assert trace["summary"]["proposal_count"] == 1
    assert trace["summary"]["final_score"] == pytest.approx(1.0)
    assert "governance" in trace["signals"]

    deployment = optimize.export_agent_deployment(result, framework="langgraph")
    assert deployment.framework == "langgraph"
    assert deployment.final_score == pytest.approx(1.0)
    assert deployment.config["secrets"] == "<redacted>"
    assert "secrets" in deployment.redactions
    assert "langgraph.apply.json" in deployment.files


def test_trinity_engines_are_vendored_in_agent_learning_kit():
    for module_name in ("fi.simulate", "fi.evals", "fi.opt"):
        module = importlib.import_module(module_name)
        module_path = Path(module.__file__).resolve()
        assert module_path.is_relative_to(PROJECT_ROOT / "src" / "fi")


def test_agent_learning_kit_does_not_depend_on_legacy_sdk_distributions():
    metadata = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    project = metadata["project"]
    dependencies = [*project.get("dependencies", [])]
    for extra_dependencies in project.get("optional-dependencies", {}).values():
        dependencies.extend(extra_dependencies)

    legacy_distributions = ("agent-simulate", "ai-evaluation", "agent-opt")
    normalized = "\n".join(dependencies).lower()
    for distribution in legacy_distributions:
        assert distribution not in normalized


def _portfolio_data() -> dict:
    return {
        "name": "agent-learning-portfolio",
        "selected_optimizer": "bandit",
        "final_score": 1.0,
        "improved": True,
        "rollback_decision": {"rollback_required": False},
        "feedback_cases": [{"id": "case"}],
        "diagnoses": [{"component": "multi_agent"}],
        "search_paths": [
            "optimizer.backend_portfolio.backends",
            "optimizer.backend_selector.policy",
        ],
        "backend_plan": [
            {"optimizer": "agent", "rank": 1},
            {"optimizer": "tpe", "rank": 2},
            {"optimizer": "bandit", "rank": 3},
        ],
        "backend_runs": [
            {
                "optimizer": "agent",
                "status": "completed",
                "final_score": 0.84,
                "improved": True,
            },
            {
                "optimizer": "tpe",
                "status": "completed",
                "final_score": 0.91,
                "improved": True,
            },
            {
                "optimizer": "bandit",
                "status": "completed",
                "final_score": 1.0,
                "improved": True,
            },
        ],
        "backend_lineage": [
            {
                "optimizer": "agent",
                "selection_relation": "equivalent",
                "patch_paths": ["optimizer.backend_portfolio.backends"],
            },
            {
                "optimizer": "tpe",
                "selection_relation": "supporting",
                "patch_paths": ["optimizer.backend_selector.policy"],
            },
            {
                "optimizer": "bandit",
                "selection_relation": "selected",
                "patch_paths": ["optimizer.backend_portfolio.backends"],
            },
        ],
        "ablation_report": {
            "selected_optimizer": "bandit",
            "selected_candidate_id": "candidate_bandit",
            "dependency": "backend_consensus",
            "consensus_backends": ["agent", "tpe"],
            "selected_backend_required": False,
        },
    }


def _bad_portfolio_data() -> dict:
    return {
        "name": "agent-learning-portfolio-bad",
        "selected_optimizer": "agent",
        "final_score": 0.2,
        "improved": False,
        "rollback_decision": {},
        "feedback_cases": [],
        "diagnoses": [],
        "search_paths": [],
        "backend_plan": [{"optimizer": "agent", "rank": 1}],
        "backend_runs": [
            {"optimizer": "agent", "status": "completed", "final_score": 0.2}
        ],
        "backend_lineage": [],
        "ablation_report": {
            "selected_optimizer": "agent",
            "selected_candidate_id": "candidate_agent",
            "dependency": "single_backend",
            "consensus_backends": [],
            "selected_backend_required": True,
        },
    }


def _optimization_manifest(required_env: str) -> dict:
    good = _portfolio_data()
    bad = _bad_portfolio_data()
    return {
        "version": "agent-learning.optimization.v1",
        "name": "agent-learning-kit-optimize",
        "required_env": [required_env],
        "scenario": {
            "name": "agent-learning-kit-optimize",
            "dataset": [
                {
                    "persona": {"name": "Riya", "role": "ci-owner"},
                    "situation": "Riya needs optimizer backend allocation evidence.",
                    "outcome": "The optimized manifest passes the portfolio gate.",
                }
            ],
        },
        "agent": {
            "type": "scripted",
            "content": "Optimizer portfolio inspected from Agent Learning Kit.",
            "tool_calls": [
                {"id": "status", "name": "optimizer_portfolio_status", "arguments": {}},
                {
                    "id": "list",
                    "name": "list_optimizer_backends",
                    "arguments": {"status": "completed"},
                },
                {
                    "id": "backend",
                    "name": "inspect_optimizer_backend",
                    "arguments": {"optimizer": "bandit"},
                },
                {
                    "id": "ablation",
                    "name": "inspect_optimizer_ablation",
                    "arguments": {},
                },
            ],
        },
        "simulation": {"engine": "local_text", "max_turns": 1, "min_turns": 1},
        "evaluation": {
            "agent_report": {
                "threshold": 0.9,
                "config": {
                    "required_tools": [
                        "optimizer_portfolio_status",
                        "list_optimizer_backends",
                        "inspect_optimizer_backend",
                        "inspect_optimizer_ablation",
                    ],
                    "available_tools": [
                        "optimizer_portfolio_status",
                        "list_optimizer_backends",
                        "inspect_optimizer_backend",
                        "inspect_optimizer_ablation",
                    ],
                    "required_optimizer_portfolio": [
                        "optimizer_portfolio",
                        "backend_plan",
                        "backend_run",
                        "backend_lineage",
                        "selected_optimizer",
                        "ablation",
                        "consensus",
                        "selected_relation",
                        "diagnostic",
                        "feedback",
                        "search_path",
                        "improvement",
                        "rollback_decision",
                        "agent",
                        "tpe",
                        "bandit",
                    ],
                    "optimizer_portfolio_quality": {
                        "required_backends": ["agent", "tpe", "bandit"],
                        "required_completed_backends": ["agent", "tpe", "bandit"],
                        "required_consensus_backends": ["agent", "tpe"],
                        "required_selection_relations": [
                            "selected",
                            "equivalent",
                            "supporting",
                        ],
                        "required_dependencies": ["backend_consensus"],
                        "required_search_paths": [
                            "optimizer.backend_portfolio.backends",
                            "optimizer.backend_selector.policy",
                        ],
                        "min_backend_plan_count": 3,
                        "min_backend_run_count": 3,
                        "min_completed_backends": 3,
                        "min_lineage_count": 3,
                        "min_consensus_backends": 2,
                        "min_feedback_cases": 1,
                        "min_diagnostics": 1,
                        "min_search_paths": 2,
                        "min_improved_backends": 3,
                        "min_final_score": 0.99,
                        "max_failed_backends": 0,
                        "require_selected_optimizer": True,
                        "require_backend_plan": True,
                        "require_backend_runs": True,
                        "require_backend_lineage": True,
                        "require_completed_backend": True,
                        "require_ablation": True,
                        "require_consensus": True,
                        "require_selected_relation": True,
                        "require_diagnostics": True,
                        "require_feedback": True,
                        "require_search_paths": True,
                        "require_improvement": True,
                        "require_rollback_decision": True,
                    },
                    "metric_weights": {
                        "optimizer_portfolio_coverage": 5.0,
                        "optimizer_portfolio_quality": 10.0,
                        "final_response_quality": 1.0,
                    },
                },
            }
        },
        "optimization": {
            "threshold": 0.9,
            "target": {
                "name": "agent-learning-optimizer-portfolio",
                "layers": ["harness", "multi_agent", "evaluator"],
                "base_config": {
                    "simulation": {
                        "environments": [
                            {"type": "optimizer_backend_portfolio", "data": bad}
                        ]
                    }
                },
                "search_space": {
                    "simulation.environments.0.data": [
                        bad,
                        copy.deepcopy(good),
                    ]
                },
            },
            "optimizer": {"max_candidates": 3, "diagnostic_score_threshold": 0.9},
        },
    }


def test_agent_learn_eval_runs_unified_command_and_writes_artifacts(tmp_path):
    suite_path = tmp_path / "suite.json"
    output_path = tmp_path / "result.json"
    junit_path = tmp_path / "result.junit.xml"
    sarif_path = tmp_path / "result.sarif.json"
    markdown_path = tmp_path / "result.md"
    suite_path.write_text(
        json.dumps(
            {
                "version": "agent-simulate.eval.v1",
                "name": "agent-learning-kit-eval",
                "providers": [{"id": "echo", "type": "echo"}],
                "prompts": [{"id": "support", "template": "{{question}}"}],
                "tests": [
                    {
                        "id": "policy",
                        "vars": {"question": "Where is the policy?"},
                        "assert": [{"type": "contains", "value": "policy"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main([
        "eval",
        str(suite_path),
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
    assert payload["status"] == "passed"
    assert payload["kind"] == "agent-learning.eval.v1"
    assert payload["summary"]["case_count"] == 1
    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    assert json.loads(sarif_path.read_text(encoding="utf-8"))["runs"][0]["results"] == []
    assert "agent-learning-kit-eval" in markdown_path.read_text(encoding="utf-8")


def test_agent_learn_run_executes_manifest_and_writes_unified_artifacts(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("AGENT_LEARNING_RUN_TEST_KEY", "real-local-run-key")
    manifest_path = tmp_path / "run.json"
    output_path = tmp_path / "run-result.json"
    junit_path = tmp_path / "run-result.junit.xml"
    sarif_path = tmp_path / "run-result.sarif.json"
    markdown_path = tmp_path / "run-result.md"
    manifest_path.write_text(
        json.dumps(
            {
                "version": "agent-learning.run.v1",
                "name": "agent-learning-kit-run",
                "required_env": ["AGENT_LEARNING_RUN_TEST_KEY"],
                "scenario": {
                    "name": "agent-learning-kit-run",
                    "dataset": [
                        {
                            "persona": {"name": "Maya", "role": "sdk-owner"},
                            "situation": "Maya needs a unified SDK smoke run.",
                            "outcome": "The unified run command returns a stable payload.",
                        }
                    ],
                },
                "agent": {
                    "type": "scripted",
                    "content": "The unified run command executed successfully.",
                },
                "simulation": {
                    "engine": "local_text",
                    "max_turns": 1,
                    "min_turns": 1,
                },
                "evaluation": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )

    exit_code = main([
        "run",
        str(manifest_path),
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
    assert payload["summary"]["case_count"] == 1
    assert payload["evaluation"] is None
    transcript = payload["report"]["results"][0]["transcript"]
    assert "unified run command executed" in transcript.lower()
    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    assert json.loads(sarif_path.read_text(encoding="utf-8"))["runs"][0]["results"] == []
    assert "agent-learning-kit-run" in markdown_path.read_text(encoding="utf-8")


def test_agent_learn_redteam_runs_unified_command_and_writes_artifacts(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("AGENT_LEARNING_REDTEAM_TEST_KEY", "real-local-redteam-key")
    source_path = (
        Path(__file__).resolve().parents[1] / "examples" / "redteam_manifest.json"
    )
    manifest = json.loads(source_path.read_text(encoding="utf-8"))
    manifest["required_env"] = ["AGENT_LEARNING_REDTEAM_TEST_KEY"]

    manifest_path = tmp_path / "redteam.json"
    output_path = tmp_path / "redteam-result.json"
    junit_path = tmp_path / "redteam-result.junit.xml"
    sarif_path = tmp_path / "redteam-result.sarif.json"
    markdown_path = tmp_path / "redteam-result.md"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    exit_code = main([
        "redteam",
        str(manifest_path),
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
    assert payload["kind"] == "agent-learning.redteam.v1"
    assert payload["status"] == "passed"
    assert payload["summary"]["case_count"] == 1
    assert payload["summary"]["redteam"] == payload["redteam"]
    assert payload["redteam"]["attack_types"] == [
        "prompt_injection",
        "credential_exfiltration",
    ]
    assert payload["redteam"]["error_finding_count"] == 0
    assert payload["summary"]["metric_averages"]["adversarial_resilience"] == 1.0
    assert payload["summary"]["metric_averages"]["environment_injection_resistance"] == 1.0
    assert payload["summary"]["metric_averages"]["red_team_campaign_quality"] == 1.0
    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert all(result["level"] != "error" for result in sarif["runs"][0]["results"])
    assert "agent-learning-redteam" in markdown_path.read_text(encoding="utf-8")


def test_agent_learn_optimize_runs_unified_command_and_writes_artifacts(
    tmp_path,
    monkeypatch,
):
    pytest.importorskip("fi.opt")
    monkeypatch.setenv("AGENT_LEARNING_OPTIMIZE_TEST_KEY", "real-local-opt-key")
    manifest_path = tmp_path / "optimize.json"
    output_path = tmp_path / "optimize-result.json"
    junit_path = tmp_path / "optimize-result.junit.xml"
    sarif_path = tmp_path / "optimize-result.sarif.json"
    markdown_path = tmp_path / "optimize-result.md"
    manifest_path.write_text(
        json.dumps(_optimization_manifest("AGENT_LEARNING_OPTIMIZE_TEST_KEY")),
        encoding="utf-8",
    )

    exit_code = main([
        "optimize",
        str(manifest_path),
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
    assert payload["evaluation"]["passed"] is True
    best_data = payload["optimization"]["best_config"]["simulation"]["environments"][0][
        "data"
    ]
    assert best_data["selected_optimizer"] == "bandit"
    assert payload["optimization"]["manifest_optimization"]["kind"] == (
        "manifest_optimization"
    )
    assert payload["optimization"]["optimizer_trace"]["kind"] == (
        "optimizer_society_trace"
    )
    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert all(result["level"] != "error" for result in sarif["runs"][0]["results"])
    assert "manifest_optimization_quality" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_agent_learn_optimize_eval_runs_unified_command_and_writes_artifacts(tmp_path):
    suite_path = tmp_path / "suite.json"
    output_path = tmp_path / "optimization.json"
    junit_path = tmp_path / "optimization.junit.xml"
    sarif_path = tmp_path / "optimization.sarif.json"
    markdown_path = tmp_path / "optimization.md"
    failing_response = "Private credentials only."
    passing_response = "Policy answer with policy and no leaked secrets."
    suite_path.write_text(
        json.dumps(
            {
                "version": "agent-simulate.eval.v1",
                "name": "agent-learning-kit-eval-optimization",
                "providers": [
                    {"id": "scripted", "type": "scripted", "response": failing_response}
                ],
                "prompts": [{"id": "support", "template": "{{question}}"}],
                "tests": [
                    {
                        "id": "policy",
                        "vars": {"question": "Where is the policy?"},
                        "assert": [
                            {"type": "contains", "value": "policy"},
                            {
                                "type": "not_contains",
                                "value": "private credentials",
                            },
                        ],
                    }
                ],
                "optimization": {
                    "threshold": 1.0,
                    "target": {
                        "name": "agent-learning-provider-response",
                        "layers": ["prompt", "evaluator"],
                        "base_config": {
                            "providers": [{"response": failing_response}]
                        },
                        "search_space": {
                            "providers.0.response": [
                                failing_response,
                                passing_response,
                            ]
                        },
                    },
                    "optimizer": {
                        "max_candidates": 2,
                        "include_seed": True,
                        "auto_diagnose": False,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    exit_code = main([
        "optimize-eval",
        str(suite_path),
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
    assert payload["kind"] == "agent-learning.eval-optimization.v1"
    assert payload["status"] == "passed"
    assert payload["optimization"]["best_config"]["providers"][0]["response"] == passing_response
    assert "failures=\"0\"" in junit_path.read_text(encoding="utf-8")
    sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert sarif["version"] == "2.1.0"
    assert all(result["level"] != "error" for result in sarif["runs"][0]["results"])
    assert "## Optimization" in markdown_path.read_text(encoding="utf-8")


def test_agent_learn_doctor_reports_module_availability(capsys):
    exit_code = main(["doctor"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["modules"]["simulate"]["available"] is True
    assert payload["modules"]["evaluation"]["available"] is True
    assert payload["modules"]["optimize"]["available"] is True
