from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

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
    "optimize_eval_suite",
    "optimize_eval_suite_file",
    "optimize_manifest",
    "optimize_manifest_file",
    "problem_from_eval_suite_file",
    "problem_from_simulate_manifest_file",
    "relevant_search_paths",
]
