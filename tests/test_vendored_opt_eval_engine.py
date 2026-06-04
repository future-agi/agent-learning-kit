from __future__ import annotations

import copy
import importlib
from pathlib import Path

import pytest

from agent_learning import evals as agent_evals
from agent_learning import optimize as agent_optimize
from fi.opt import diagnose_agent_report_evaluation


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENDORED_FI_ROOT = PROJECT_ROOT / "src" / "fi"


def _assert_vendored_module(module_name: str):
    module = importlib.import_module(module_name)
    module_path = Path(module.__file__).resolve()
    assert module_path.is_relative_to(VENDORED_FI_ROOT)
    return module


def _agent_report(strategy: str) -> dict:
    if strategy == "tool_grounded":
        messages = [
            {"role": "user", "content": "Resolve the policy case."},
            {
                "role": "assistant",
                "content": "I will look up the policy first.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "lookup_policy",
                        "arguments": {"case_id": "case-7"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "policy allows resolution",
            },
            {"role": "assistant", "content": "Policy case resolved."},
        ]
        events = [{"type": "state_update", "payload": {"case": {"resolved": True}}}]
    else:
        messages = [
            {"role": "user", "content": "Resolve the policy case."},
            {"role": "assistant", "content": "I cannot resolve the case yet."},
        ]
        events = []

    return {
        "results": [
            {
                "persona": {
                    "situation": "Resolve the policy case.",
                    "outcome": "Policy case resolved.",
                },
                "messages": messages,
                "events": events,
            }
        ]
    }


def _agent_report_eval_config() -> dict:
    return {
        "required_tools": ["lookup_policy"],
        "available_tools": ["lookup_policy", "close_case"],
        "expected_state": {"case": {"resolved": True}},
        "success_criteria": ["policy case resolved"],
        "metric_weights": {
            "task_completion": 2.0,
            "tool_selection_accuracy": 3.0,
            "state_goal_accuracy": 3.0,
        },
    }


def _score_agent_report(strategy: str):
    evaluation = agent_evals.evaluate_agent_report(
        _agent_report(strategy),
        config=_agent_report_eval_config(),
        threshold=0.9,
    )
    metric_scores = {
        metric.name: metric.score
        for metric in evaluation.cases[0].metrics
    }
    return evaluation, metric_scores


def _manifest() -> dict:
    return {
        "name": "agent-learning-kit-vendored-manifest",
        "agent": {"type": "scripted", "content": "Base policy responder."},
        "simulation": {
            "engine": "local_text",
            "environments": [
                {
                    "type": "policy_case",
                    "data": {"selected_strategy": "seed"},
                }
            ],
        },
        "evaluation": {"agent_report": {"config": _agent_report_eval_config()}},
        "optimization": {
            "threshold": 0.9,
            "target": {
                "name": "policy-resolution-strategy",
                "layers": ["harness", "evaluator"],
                "base_config": {
                    "simulation": {
                        "environments": [
                            {"data": {"selected_strategy": "seed"}}
                        ]
                    }
                },
                "search_space": {
                    "simulation.environments.0.data.selected_strategy": [
                        "seed",
                        "tool_grounded",
                    ]
                },
            },
            "optimizer": {
                "max_candidates": 3,
                "include_seed": True,
                "auto_diagnose": False,
            },
        },
    }


def _eval_suite() -> dict:
    return {
        "version": "agent-simulate.eval.v1",
        "name": "agent-learning-kit-vendored-eval-suite",
        "providers": [
            {
                "id": "scripted-policy-agent",
                "type": "scripted",
                "response": "Policy response for {{question}}",
                "config": {
                    "routing": {"mode": "seed"},
                    "headers": {"x-local-test": "1"},
                },
            }
        ],
        "prompts": [{"id": "support", "template": "{{question}}"}],
        "tests": [
            {
                "id": "policy_case",
                "vars": {"question": "Resolve the policy case."},
                "assert": [{"type": "contains", "value": "policy"}],
            }
        ],
        "optimization": {
            "threshold": 0.9,
            "target": {
                "name": "policy-provider-routing",
                "layers": ["prompt", "evaluator"],
                "base_config": {
                    "providers": [{"config": {"routing": {"mode": "seed"}}}]
                },
                "search_space": {
                    "providers.0.config.routing.mode": [
                        "seed",
                        "tool_grounded",
                    ]
                },
            },
            "optimizer": {
                "max_candidates": 3,
                "include_seed": True,
                "auto_diagnose": False,
            },
        },
    }


def test_agent_learning_facades_resolve_to_vendored_fi_engines():
    fi_evals = _assert_vendored_module("fi.evals")
    fi_autoeval = _assert_vendored_module("fi.evals.autoeval")
    fi_local_evals = _assert_vendored_module("fi.evals.local")
    fi_opt = _assert_vendored_module("fi.opt")
    fi_optimizers = _assert_vendored_module("fi.opt.optimizers")
    fi_opt_simulate = _assert_vendored_module("fi.opt.integrations.simulate")
    fi_agent_metrics = _assert_vendored_module("fi.evals.metrics.agents")

    assert set(fi_evals.__all__) <= set(agent_evals.__all__)
    for name in (
        "StreamingConfig",
        "StreamingEvalResult",
        "ChunkResult",
        "EarlyStopPolicy",
        "ExecutionMode",
        "BaseEvaluation",
        "EvalBuilder",
        "blocking_evaluator",
        "async_evaluator",
        "custom_eval",
        "simple_eval",
        "EvalTemplateManager",
        "Protect",
        "protect",
        "list_evaluations",
        "Toxicity",
        "PromptInjection",
        "TaskCompletion",
    ):
        assert getattr(agent_evals, name) is getattr(fi_evals, name)
    assert set(fi_autoeval.__all__) <= set(agent_evals.__all__)
    assert set(fi_local_evals.__all__) <= set(agent_evals.__all__)
    for name in (
        "AutoEvalPipeline",
        "AutoEvalConfig",
        "AppAnalyzer",
        "EvalRecommender",
        "get_template_names",
        "to_yaml_string",
        "from_yaml_string",
    ):
        assert getattr(agent_evals, name) is getattr(fi_autoeval, name)
    for name in (
        "RoutingMode",
        "LOCAL_CAPABLE_METRICS",
        "can_run_locally",
        "select_routing_mode",
        "LocalEvaluator",
        "HybridEvaluator",
        "LocalLLMFactory",
    ):
        assert getattr(agent_evals, name) is getattr(fi_local_evals, name)
    assert set(fi_opt.__all__) <= set(agent_optimize.__all__)
    assert set(fi_optimizers.__all__) <= set(agent_optimize.__all__)
    for name in (
        "AgentMutationBundle",
        "FrameworkMutationRule",
        "DEFAULT_AGENT_MUTATION_LIBRARY",
        "AgentComponentSpec",
        "FailureMode",
        "COMPONENT_SPECS",
        "FAILURE_ROUTES",
        "diagnose_agent_report_evaluation",
        "SimulateManifestOptimizationProblem",
        "SimulateEvalSuiteOptimizationProblem",
        "problem_from_simulate_manifest",
        "problem_from_eval_suite",
        "optimize_simulate_manifest",
        "deep_merge",
        "set_path",
    ):
        assert getattr(agent_optimize, name) is getattr(fi_opt, name)
    for name in (
        "RandomSearchOptimizer",
        "BayesianSearchOptimizer",
        "GEPAOptimizer",
        "PromptWizardOptimizer",
        "AgentOptimizer",
        "AgentTPEOptimizer",
        "CouncilAgentOptimizer",
    ):
        assert getattr(agent_optimize, name) is getattr(fi_optimizers, name)
    assert agent_optimize.ManifestOptimizationProblem is fi_opt.ManifestOptimizationProblem
    assert agent_optimize.EvalSuiteOptimizationProblem is fi_opt.EvalSuiteOptimizationProblem
    assert agent_optimize.ManifestOptimizationProblem is (
        fi_opt_simulate.ManifestOptimizationProblem
    )
    assert agent_evals.AgentReportEvaluator is fi_agent_metrics.AgentReportEvaluator


def test_manifest_optimizer_uses_vendored_eval_engine_for_local_scoring():
    manifest = _manifest()
    original = copy.deepcopy(manifest)
    evaluated_strategies = []

    def evaluate_manifest(candidate_manifest, candidate):
        strategy = candidate_manifest["simulation"]["environments"][0]["data"][
            "selected_strategy"
        ]
        evaluation, metric_scores = _score_agent_report(strategy)
        evaluated_strategies.append((strategy, evaluation.score))
        return {
            "score": evaluation.score,
            "reason": f"strategy={strategy}; passed={evaluation.passed}",
            "metadata": {
                "candidate_id": candidate.id,
                "evaluation_passed": evaluation.passed,
                "metric_scores": metric_scores,
                "selected_strategy": strategy,
            },
        }

    problem = agent_optimize.ManifestOptimizationProblem.from_manifest(
        manifest,
        evaluate_manifest=evaluate_manifest,
    )

    result = problem.optimize()

    assert manifest == original
    assert "optimization" not in problem.base_manifest
    assert [strategy for strategy, _ in evaluated_strategies] == [
        "seed",
        "tool_grounded",
    ]
    assert result.final_score == pytest.approx(
        max(score for _, score in evaluated_strategies)
    )
    assert result.best_candidate.get_path(
        "simulation.environments.0.data.selected_strategy"
    ) == "tool_grounded"
    assert result.metadata["search_paths"] == [
        "simulation.environments.0.data.selected_strategy"
    ]

    best_history = max(result.history, key=lambda item: item.average_score)
    assert best_history.metadata["candidate_manifest"]["agent"]["content"] == (
        "Base policy responder."
    )
    assert best_history.metadata["candidate_patch"] == {
        "simulation.environments.0.data.selected_strategy": "tool_grounded"
    }
    assert best_history.metadata["evaluation_passed"] is True
    assert best_history.metadata["metric_scores"]["state_goal_accuracy"] == 1.0


def test_eval_suite_optimizer_runs_local_agent_report_eval_without_services():
    suite = _eval_suite()
    original = copy.deepcopy(suite)
    routed_modes = []

    def run_suite(candidate_suite, candidate):
        provider = candidate_suite["providers"][0]
        routing_mode = provider["config"]["routing"]["mode"]
        assert provider["id"] == "scripted-policy-agent"
        assert provider["config"]["headers"] == {"x-local-test": "1"}

        evaluation, metric_scores = _score_agent_report(routing_mode)
        routed_modes.append(routing_mode)
        return {
            "score": evaluation.score,
            "reason": f"routing={routing_mode}; passed={evaluation.passed}",
            "metadata": {
                "candidate_id": candidate.id,
                "evaluation_passed": evaluation.passed,
                "metric_scores": metric_scores,
                "routing_mode": routing_mode,
            },
        }

    problem = agent_optimize.EvalSuiteOptimizationProblem.from_suite(
        suite,
        run_suite=run_suite,
    )

    result = problem.optimize()

    assert suite == original
    assert "optimization" not in problem.base_suite
    assert routed_modes == ["seed", "tool_grounded"]
    assert result.best_candidate.get_path("providers.0.config.routing.mode") == (
        "tool_grounded"
    )
    assert result.metadata["search_paths"] == ["providers.0.config.routing.mode"]

    best_history = max(result.history, key=lambda item: item.average_score)
    assert best_history.metadata["candidate_suite"]["providers"][0]["config"] == {
        "routing": {"mode": "tool_grounded"},
        "headers": {"x-local-test": "1"},
    }
    assert best_history.metadata["candidate_patch"] == {
        "providers.0.config.routing.mode": "tool_grounded"
    }
    assert best_history.metadata["evaluation_passed"] is True
    assert best_history.metadata["report"]["metadata"]["routing_mode"] == (
        "tool_grounded"
    )


def test_eval_facade_runs_autoeval_template_and_local_metric_without_services():
    template_names = agent_evals.get_template_names()
    config = agent_evals.get_template("agent_workflow")
    yaml_text = agent_evals.to_yaml_string(config)
    roundtrip = agent_evals.from_yaml_string(yaml_text)
    route = agent_evals.select_routing_mode(
        "contains",
        agent_evals.RoutingMode.HYBRID,
    )

    evaluator = agent_evals.LocalEvaluator()
    result = evaluator.evaluate(
        "contains",
        inputs=[{"response": "refund approved by autonomous support agent"}],
        config={"keyword": "approved"},
    )

    assert "agent_workflow" in template_names
    assert roundtrip.name == "agent_workflow"
    assert agent_evals.can_run_locally("contains") is True
    assert agent_evals.can_run_locally("groundedness") is False
    assert route is agent_evals.RoutingMode.LOCAL
    assert result.executed_locally == {"contains"}
    assert result.results.eval_results[0].output == pytest.approx(1.0)


def test_manifest_optimization_diagnosis_routes_search_space_paths():
    diagnoses = diagnose_agent_report_evaluation(
        {
            "cases": [
                {
                    "metrics": [
                        {
                            "name": "manifest_optimization_quality",
                            "score": 0.25,
                            "reason": "Missing candidates, patches, and search paths.",
                        }
                    ]
                }
            ]
        }
    )

    components = {diagnosis.component for diagnosis in diagnoses}
    paths = {path for diagnosis in diagnoses for path in diagnosis.suggested_paths}
    search_paths = agent_optimize.relevant_search_paths(
        {
            "optimization.target.search_space.prompt.system": ["seed", "policy"],
            "optimization.optimizer.max_candidates": [2, 4],
            "evaluation.manifest_optimization_quality.min_candidate_count": [1, 2],
            "prompt.system": ["unrelated"],
        },
        diagnoses,
    )

    assert {"harness", "evaluator", "multi_agent", "planner"}.issubset(components)
    assert "optimization.target.search_space" in paths
    assert search_paths == {
        "optimization.target.search_space.prompt.system",
        "optimization.optimizer.max_candidates",
        "evaluation.manifest_optimization_quality.min_candidate_count",
    }
