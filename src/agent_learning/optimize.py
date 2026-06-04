from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from ._facade import optional_module

_OPTIMIZE_EXTRA = "optimize"

_OPTIMIZE_EXPORTS = {
    "AgentBanditOptimizer": "fi.opt.optimizers",
    "AgentCandidate": "fi.opt",
    "AgentComponent": "fi.opt",
    "AgentCurriculumOptimizer": "fi.opt.optimizers",
    "AgentCurriculumStage": "fi.opt.optimizers",
    "AgentDatasetSinkResult": "fi.opt",
    "AgentDeploymentExport": "fi.opt",
    "AgentEvolutionOptimizer": "fi.opt.optimizers",
    "AgentFeedbackCase": "fi.opt.optimizers",
    "AgentFeedbackOptimizationResult": "fi.opt.optimizers",
    "AgentFeedbackOptimizer": "fi.opt.optimizers",
    "AgentMutationLibrary": "fi.opt",
    "AgentMultiInteractionAblationReport": "fi.opt",
    "AgentMultiInteractionBackendLineage": "fi.opt",
    "AgentMultiInteractionBackendPlan": "fi.opt",
    "AgentMultiInteractionBackendRun": "fi.opt",
    "AgentMultiInteractionOptimizationResult": "fi.opt",
    "AgentMultiInteractionOptimizer": "fi.opt",
    "AgentObservabilityRecord": "fi.opt",
    "AgentObservabilityWindow": "fi.opt",
    "AgentOptimizer": "fi.opt.optimizers",
    "AgentParetoOptimizer": "fi.opt.optimizers",
    "AgentPromotionCheck": "fi.opt",
    "AgentRegistryReplayPackLineageEntry": "fi.opt",
    "AgentRegistryReplayPackLineageReport": "fi.opt",
    "AgentRegistryReplayPackLineageTransition": "fi.opt",
    "AgentRegistryReplayPackManifest": "fi.opt",
    "AgentRegistryReplayPackPromotionCheck": "fi.opt",
    "AgentRegistryReplayPackTriageReport": "fi.opt",
    "AgentRegressionCase": "fi.opt",
    "AgentRegressionDataset": "fi.opt",
    "AgentRegressionDatasetCoverageReport": "fi.opt",
    "AgentRollbackDecision": "fi.opt",
    "AgentSearchProposal": "fi.opt.optimizers",
    "AgentSearchState": "fi.opt.optimizers",
    "AgentSearchStrategy": "fi.opt.optimizers",
    "AgentSocialMemoryOptimizer": "fi.opt.optimizers",
    "AgentSocietyRole": "fi.opt.optimizers",
    "AgentTPEOptimizer": "fi.opt.optimizers",
    "CandidateEvaluation": "fi.opt",
    "ComponentDiagnosis": "fi.opt",
    "CouncilAgentOptimizer": "fi.opt.optimizers",
    "DeterministicCouncilStrategy": "fi.opt.optimizers",
    "EvalSuiteOptimizationProblem": "fi.opt",
    "EvaluationResult": "fi.opt",
    "FutureAGIExperimentHistoryOptimizer": "fi.opt",
    "FutureAGIRegressionReplayOptimizer": "fi.opt",
    "FutureAGIReplayOptimizerSchedule": "fi.opt",
    "IterationHistory": "fi.opt",
    "LLMMessage": "fi.opt",
    "ManifestOptimizationProblem": "fi.opt",
    "OptimizationLayer": "fi.opt",
    "OptimizationResult": "fi.opt",
    "OptimizationTarget": "fi.opt",
    "PromotionMetricCheck": "fi.opt",
    "ResearchCorpusSummary": "fi.opt",
    "ResearchPaper": "fi.opt",
    "RollbackObservation": "fi.opt",
    "SimulationEvaluator": "fi.opt",
    "SocietyAgentOptimizer": "fi.opt.optimizers",
    "SocietyRoleGraphSearchStrategy": "fi.opt.optimizers",
    "SocietySearchStrategy": "fi.opt.optimizers",
    "build_agent_regression_dataset": "fi.opt",
    "build_agent_regression_dataset_coverage_report": "fi.opt",
    "build_agent_research_corpus": "fi.opt",
    "build_deep_read_queue": "fi.opt",
    "build_futureagi_registry_replay_pack_manifest": "fi.opt",
    "build_optimizer_society_trace": "fi.opt",
    "check_agent_deployment_promotion": "fi.opt",
    "check_agent_deployment_rollback": "fi.opt",
    "check_futureagi_registry_replay_pack_promotion": "fi.opt",
    "compare_futureagi_registry_replay_pack_lineage": "fi.opt",
    "export_agent_deployment": "fi.opt",
    "infer_red_team_signals": "fi.opt",
    "infer_research_themes": "fi.opt",
    "load_agent_observability_feedback": "fi.opt",
    "load_agent_report_replay_cases": "fi.opt",
    "load_futureagi_experiment_history": "fi.opt",
    "load_futureagi_regression_dataset": "fi.opt",
    "load_research_papers": "fi.opt",
    "map_research_to_red_team_campaign": "fi.opt",
    "normalize_research_paper": "fi.opt",
    "publish_futureagi_regression_dataset": "fi.opt",
    "research_note_for": "fi.opt",
    "research_summary_markdown": "fi.opt",
    "schedule_futureagi_registry_replay_optimization": "fi.opt",
    "triage_futureagi_registry_replay_pack_regression": "fi.opt",
}


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
