from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ._facade import optional_module

_OPTIMIZE_EXTRA = "optimize"

_FI_OPT_EXPORT_NAMES = (
    "AgentComponent",
    "AgentComponentSpec",
    "AgentCandidate",
    "AgentDatasetSinkResult",
    "AgentDeploymentExport",
    "AgentMutationBundle",
    "AgentMutationLibrary",
    "AgentMultiInteractionAblationReport",
    "AgentMultiInteractionBackendLineage",
    "AgentMultiInteractionBackendPlan",
    "AgentMultiInteractionBackendRun",
    "AgentMultiInteractionOptimizationResult",
    "AgentMultiInteractionOptimizer",
    "AgentObservabilityRecord",
    "AgentObservabilityWindow",
    "AgentRegistryReplayPackLineageEntry",
    "AgentRegistryReplayPackLineageReport",
    "AgentRegistryReplayPackLineageTransition",
    "AgentRegistryReplayPackManifest",
    "AgentRegistryReplayPackPromotionCheck",
    "AgentRegistryReplayPackTriageReport",
    "AgentRegressionCase",
    "AgentRegressionDataset",
    "AgentRegressionDatasetCoverageReport",
    "AgentPromotionCheck",
    "AgentRollbackDecision",
    "COMPONENT_SPECS",
    "CandidateEvaluation",
    "ComponentDiagnosis",
    "DEFAULT_AGENT_MUTATION_LIBRARY",
    "FailureMode",
    "FAILURE_ROUTES",
    "EvalSuiteOptimizationProblem",
    "FrameworkMutationRule",
    "FutureAGIExperimentHistoryOptimizer",
    "FutureAGIReplayOptimizerSchedule",
    "FutureAGIRegressionReplayOptimizer",
    "PromotionMetricCheck",
    "ResearchCorpusSummary",
    "ResearchPaper",
    "RollbackObservation",
    "check_agent_deployment_rollback",
    "check_agent_deployment_promotion",
    "check_futureagi_registry_replay_pack_promotion",
    "compare_futureagi_registry_replay_pack_lineage",
    "build_agent_regression_dataset",
    "build_agent_regression_dataset_coverage_report",
    "build_agent_research_corpus",
    "build_deep_read_queue",
    "build_futureagi_registry_replay_pack_manifest",
    "build_optimizer_society_trace",
    "load_agent_report_replay_cases",
    "ManifestOptimizationProblem",
    "diagnose_agent_report_evaluation",
    "export_agent_deployment",
    "load_agent_observability_feedback",
    "load_futureagi_experiment_history",
    "load_research_papers",
    "load_futureagi_regression_dataset",
    "map_research_to_red_team_campaign",
    "normalize_research_paper",
    "publish_futureagi_regression_dataset",
    "research_note_for",
    "research_summary_markdown",
    "triage_futureagi_registry_replay_pack_regression",
    "OptimizationLayer",
    "OptimizationTarget",
    "optimize_eval_suite",
    "optimize_eval_suite_file",
    "optimize_simulate_manifest",
    "optimize_simulate_manifest_file",
    "problem_from_eval_suite",
    "problem_from_eval_suite_file",
    "problem_from_simulate_manifest",
    "problem_from_simulate_manifest_file",
    "diagnose_report",
    "diagnose_text",
    "infer_red_team_signals",
    "infer_research_themes",
    "relevant_search_paths",
    "set_path",
    "SimulationEvaluator",
    "SimulateEvalSuiteOptimizationProblem",
    "SimulateManifestOptimizationProblem",
    "schedule_futureagi_registry_replay_optimization",
    "deep_merge",
    "EvaluationResult",
    "IterationHistory",
    "LLMMessage",
    "OptimizationResult",
)

_OPTIMIZER_EXPORT_NAMES = (
    "RandomSearchOptimizer",
    "BayesianSearchOptimizer",
    "MetaPromptOptimizer",
    "ProTeGi",
    "GEPAOptimizer",
    "PromptWizardOptimizer",
    "AgentOptimizer",
    "AgentBanditOptimizer",
    "AgentCurriculumOptimizer",
    "AgentCurriculumStage",
    "AgentEvolutionOptimizer",
    "AgentFeedbackCase",
    "AgentFeedbackOptimizationResult",
    "AgentFeedbackOptimizer",
    "AgentMultiInteractionAblationReport",
    "AgentMultiInteractionBackendLineage",
    "AgentMultiInteractionBackendPlan",
    "AgentMultiInteractionBackendRun",
    "AgentMultiInteractionOptimizationResult",
    "AgentMultiInteractionOptimizer",
    "AgentSocialMemoryOptimizer",
    "FutureAGIRegressionReplayOptimizer",
    "FutureAGIExperimentHistoryOptimizer",
    "FutureAGIReplayOptimizerSchedule",
    "schedule_futureagi_registry_replay_optimization",
    "AgentParetoOptimizer",
    "AgentTPEOptimizer",
    "AgentSearchProposal",
    "AgentSearchState",
    "AgentSearchStrategy",
    "AgentSocietyRole",
    "CouncilAgentOptimizer",
    "DeterministicCouncilStrategy",
    "SocietyAgentOptimizer",
    "SocietyRoleGraphSearchStrategy",
    "SocietySearchStrategy",
)

_OPTIMIZE_EXPORTS = {name: "fi.opt" for name in _FI_OPT_EXPORT_NAMES}
_OPTIMIZE_EXPORTS.update({name: "fi.opt.optimizers" for name in _OPTIMIZER_EXPORT_NAMES})


def _opt() -> Any:
    return optional_module("fi.opt", _OPTIMIZE_EXTRA)


def _manifest() -> Any:
    return optional_module("fi.simulate.manifest", "simulate")


def _suite() -> Any:
    return optional_module("fi.simulate.suite", "simulate")


def diagnose_text(*args: Any, **kwargs: Any) -> Any:
    return _opt().diagnose_text(*args, **kwargs)


def diagnose_report(*args: Any, **kwargs: Any) -> Any:
    return _opt().diagnose_report(*args, **kwargs)


def relevant_search_paths(*args: Any, **kwargs: Any) -> Any:
    return _opt().relevant_search_paths(*args, **kwargs)


def optimize_manifest_file(
    path: str | Path,
    *,
    options: Optional[Any] = None,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    max_candidates: Optional[int] = None,
    dry_run: Optional[bool] = None,
) -> dict[str, Any]:
    return _manifest().optimize_manifest_file(
        path,
        options=options,
        name=name,
        threshold=threshold,
        max_candidates=max_candidates,
        dry_run=dry_run,
    )


def optimize_manifest(
    manifest: Mapping[str, Any],
    *,
    manifest_path: str | Path = ".",
    options: Optional[Any] = None,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    max_candidates: Optional[int] = None,
    dry_run: Optional[bool] = None,
) -> dict[str, Any]:
    return _manifest().optimize_manifest(
        manifest,
        manifest_path=manifest_path,
        options=options,
        name=name,
        threshold=threshold,
        max_candidates=max_candidates,
        dry_run=dry_run,
    )


def build_framework_optimization_manifest(
    *,
    name: str,
    framework: str,
    target: str,
    adapter_candidates: Sequence[Mapping[str, Any]],
    evaluation_config: Mapping[str, Any],
    scenario: Optional[Mapping[str, Any]] = None,
    environments: Optional[Sequence[Mapping[str, Any]]] = None,
    environment_candidates: Optional[Sequence[Sequence[Mapping[str, Any]]]] = None,
    required_env: Sequence[str] = (),
    optimizer: Optional[Mapping[str, Any]] = None,
    threshold: float = 0.9,
    factory: bool = True,
    trace_runtime: bool = True,
    metadata: Optional[Mapping[str, Any]] = None,
    base_agent: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Build a runnable manifest for optimizing any framework adapter.

    The helper keeps the public SDK path concise while preserving the same
    manifest contract used by ``agent-learn optimize``. Candidates are explicit
    adapter specs, so callers can avoid invalid method/input-mode pairings.
    """

    if not name:
        raise ValueError("name is required")
    if not framework:
        raise ValueError("framework is required")
    if not target:
        raise ValueError("target is required")
    if not adapter_candidates:
        raise ValueError("adapter_candidates must contain at least one candidate")
    if not evaluation_config:
        raise ValueError("evaluation_config is required")

    agent_candidates = [
        _framework_agent_candidate(
            framework=framework,
            target=target,
            candidate=candidate,
            factory=factory,
            trace_runtime=trace_runtime,
            metadata=metadata,
        )
        for candidate in adapter_candidates
    ]
    base_agent_config = (
        copy.deepcopy(dict(base_agent))
        if base_agent is not None
        else copy.deepcopy(agent_candidates[0])
    )
    base_environments = _base_environments(
        environments=environments,
        environment_candidates=environment_candidates,
    )

    search_space: dict[str, list[Any]] = {
        "agent": copy.deepcopy(agent_candidates),
    }
    if environment_candidates is not None:
        if not environment_candidates:
            raise ValueError("environment_candidates must not be empty when provided")
        search_space["simulation.environments"] = [
            copy.deepcopy(list(candidate))
            for candidate in environment_candidates
        ]

    return {
        "version": "agent-learning.optimization.v1",
        "name": name,
        "required_env": [str(key) for key in required_env],
        "scenario": copy.deepcopy(dict(scenario or _default_framework_scenario(name))),
        "agent": copy.deepcopy(base_agent_config),
        "simulation": {
            "engine": "local_text",
            "max_turns": 1,
            "min_turns": 1,
            "environments": copy.deepcopy(base_environments),
        },
        "evaluation": {
            "agent_report": {
                "threshold": float(threshold),
                "config": copy.deepcopy(dict(evaluation_config)),
            }
        },
        "optimization": {
            "threshold": float(threshold),
            "target": {
                "name": name,
                "layers": ["framework", "harness", "evaluator"],
                "base_config": {
                    "agent": copy.deepcopy(base_agent_config),
                    "simulation": {
                        "environments": copy.deepcopy(base_environments),
                    },
                },
                "search_space": search_space,
                "metadata": {
                    "source": "agent_learning.optimize.build_framework_optimization_manifest",
                    "framework": framework,
                },
            },
            "optimizer": copy.deepcopy(dict(optimizer or _default_framework_optimizer(agent_candidates))),
        },
    }


def optimize_framework_adapter(
    *,
    manifest_path: str | Path = ".",
    options: Optional[Any] = None,
    result_name: Optional[str] = None,
    dry_run: Optional[bool] = None,
    **manifest_kwargs: Any,
) -> dict[str, Any]:
    """Build and execute a framework adapter optimization manifest."""

    manifest = build_framework_optimization_manifest(**manifest_kwargs)
    return optimize_manifest(
        manifest,
        manifest_path=manifest_path,
        options=options,
        name=result_name,
        dry_run=dry_run,
    )


def _framework_agent_candidate(
    *,
    framework: str,
    target: str,
    candidate: Mapping[str, Any],
    factory: bool,
    trace_runtime: bool,
    metadata: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    candidate_dict = copy.deepcopy(dict(candidate))
    merged_metadata = {
        **copy.deepcopy(dict(metadata or {})),
        **copy.deepcopy(dict(candidate_dict.pop("metadata", {}) or {})),
    }
    return {
        "type": "framework",
        "framework": framework,
        "target": target,
        "factory": bool(candidate_dict.pop("factory", factory)),
        "trace_runtime": bool(candidate_dict.pop("trace_runtime", trace_runtime)),
        "metadata": merged_metadata,
        **candidate_dict,
    }


def _base_environments(
    *,
    environments: Optional[Sequence[Mapping[str, Any]]],
    environment_candidates: Optional[Sequence[Sequence[Mapping[str, Any]]]],
) -> list[dict[str, Any]]:
    if environments is not None:
        return [copy.deepcopy(dict(item)) for item in environments]
    if environment_candidates:
        return [copy.deepcopy(dict(item)) for item in environment_candidates[0]]
    return []


def _default_framework_scenario(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "dataset": [
            {
                "persona": {"name": "SDK user", "role": "framework-owner"},
                "situation": "Optimize a framework adapter through Agent Learning Kit.",
                "outcome": "The optimized adapter satisfies the configured evaluation.",
            }
        ],
    }


def _default_framework_optimizer(
    agent_candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "algorithm": "agent",
        "max_candidates": max(2, len(agent_candidates) + 1),
        "include_seed": True,
        "auto_diagnose": False,
    }


def optimize_eval_suite_file(
    path: str | Path,
    *,
    options: Optional[Any] = None,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    max_candidates: Optional[int] = None,
    dry_run: Optional[bool] = None,
) -> dict[str, Any]:
    return _suite().optimize_eval_suite_file(
        path,
        options=options,
        name=name,
        threshold=threshold,
        max_candidates=max_candidates,
        dry_run=dry_run,
    )


def optimize_eval_suite(
    suite: Mapping[str, Any],
    *,
    suite_path: str | Path = ".",
    options: Optional[Any] = None,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    max_candidates: Optional[int] = None,
    dry_run: Optional[bool] = None,
) -> dict[str, Any]:
    return _suite().optimize_eval_suite(
        suite,
        suite_path=suite_path,
        options=options,
        name=name,
        threshold=threshold,
        max_candidates=max_candidates,
        dry_run=dry_run,
    )


def problem_from_eval_suite_file(*args: Any, **kwargs: Any) -> Any:
    return _opt().problem_from_eval_suite_file(*args, **kwargs)


def problem_from_simulate_manifest_file(*args: Any, **kwargs: Any) -> Any:
    return _opt().problem_from_simulate_manifest_file(*args, **kwargs)


def __getattr__(name: str) -> Any:
    module_name = _OPTIMIZE_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(
            f"module `agent_learning.optimize` has no attribute `{name}`"
        )
    return getattr(optional_module(module_name, _OPTIMIZE_EXTRA), name)


def __dir__() -> list[str]:
    return sorted(set(__all__))


__all__ = [
    *_OPTIMIZE_EXPORTS,
    "diagnose_report",
    "diagnose_text",
    "build_framework_optimization_manifest",
    "optimize_eval_suite",
    "optimize_eval_suite_file",
    "optimize_framework_adapter",
    "optimize_manifest",
    "optimize_manifest_file",
    "problem_from_eval_suite_file",
    "problem_from_simulate_manifest_file",
    "relevant_search_paths",
]
