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


def build_task_optimization_manifest(
    *,
    name: str,
    agent_candidates: Sequence[Mapping[str, Any]],
    evaluation_config: Mapping[str, Any],
    scenario: Optional[Mapping[str, Any]] = None,
    environments: Optional[Sequence[Mapping[str, Any]]] = None,
    environment_candidates: Optional[Sequence[Sequence[Mapping[str, Any]]]] = None,
    required_env: Sequence[str] = (),
    optimizer: Optional[Mapping[str, Any]] = None,
    threshold: float = 0.9,
    layers: Sequence[str] = ("planner", "tools", "world", "environment", "evaluator"),
    simulation_engine: str = "local_text",
    min_turns: int = 1,
    max_turns: Optional[int] = None,
    auto_execute_tools: bool = True,
    base_agent: Optional[Mapping[str, Any]] = None,
    search_space: Optional[Mapping[str, Sequence[Any]]] = None,
    target_base_config: Optional[Mapping[str, Any]] = None,
    target_metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Build a runnable optimization manifest for any task/world agent.

    Unlike ``build_framework_optimization_manifest``, candidates are complete
    manifest agent configs. The helper can also search environment bundles and
    arbitrary manifest paths, which makes it usable for worlds, memory, policy,
    red-team harnesses, provider settings, or custom framework knobs without
    hand-writing the optimization JSON.
    """

    if not name:
        raise ValueError("name is required")
    if not agent_candidates:
        raise ValueError("agent_candidates must contain at least one candidate")
    if not evaluation_config:
        raise ValueError("evaluation_config is required")
    if min_turns < 1:
        raise ValueError("min_turns must be >= 1")

    max_turns_value = int(max_turns if max_turns is not None else min_turns)
    if max_turns_value < min_turns:
        raise ValueError("max_turns must be >= min_turns")

    copied_agents = [copy.deepcopy(dict(candidate)) for candidate in agent_candidates]
    base_agent_config = (
        copy.deepcopy(dict(base_agent))
        if base_agent is not None
        else copy.deepcopy(copied_agents[0])
    )
    base_environments = _base_environments(
        environments=environments,
        environment_candidates=environment_candidates,
    )

    target_base = copy.deepcopy(dict(target_base_config or {}))
    target_base.setdefault("agent", copy.deepcopy(base_agent_config))
    simulation_base = target_base.setdefault("simulation", {})
    if not isinstance(simulation_base, dict):
        raise ValueError("target_base_config.simulation must be a mapping")
    simulation_base.setdefault("environments", copy.deepcopy(base_environments))

    optimization_search_space = _task_search_space(
        agent_candidates=copied_agents,
        environment_candidates=environment_candidates,
        search_space=search_space,
    )
    metadata = {
        "source": "agent_learning.optimize.build_task_optimization_manifest",
        "task_kind": "task",
        **copy.deepcopy(dict(target_metadata or {})),
    }

    return {
        "version": "agent-learning.optimization.v1",
        "name": name,
        "required_env": [str(key) for key in required_env],
        "scenario": copy.deepcopy(dict(scenario or _default_task_scenario(name))),
        "agent": copy.deepcopy(base_agent_config),
        "simulation": {
            "engine": simulation_engine,
            "max_turns": max_turns_value,
            "min_turns": int(min_turns),
            "auto_execute_tools": bool(auto_execute_tools),
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
                "layers": [str(layer) for layer in layers],
                "base_config": target_base,
                "search_space": optimization_search_space,
                "metadata": metadata,
            },
            "optimizer": copy.deepcopy(
                dict(optimizer or _default_task_optimizer(optimization_search_space))
            ),
        },
    }


def optimize_task(
    *,
    manifest_path: str | Path = ".",
    options: Optional[Any] = None,
    result_name: Optional[str] = None,
    dry_run: Optional[bool] = None,
    **manifest_kwargs: Any,
) -> dict[str, Any]:
    """Build and execute a generic task/world optimization manifest."""

    manifest = build_task_optimization_manifest(**manifest_kwargs)
    return optimize_manifest(
        manifest,
        manifest_path=manifest_path,
        options=options,
        name=result_name,
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
    return build_task_optimization_manifest(
        name=name,
        agent_candidates=agent_candidates,
        evaluation_config=evaluation_config,
        scenario=scenario or _default_framework_scenario(name),
        environments=environments,
        environment_candidates=environment_candidates,
        required_env=required_env,
        optimizer=optimizer or _default_framework_optimizer(agent_candidates),
        threshold=threshold,
        layers=("framework", "harness", "evaluator"),
        min_turns=1,
        max_turns=1,
        base_agent=base_agent,
        target_metadata={
            "source": "agent_learning.optimize.build_framework_optimization_manifest",
            "task_kind": "framework_adapter",
            "framework": framework,
        },
    )


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


def _task_search_space(
    *,
    agent_candidates: Sequence[Mapping[str, Any]],
    environment_candidates: Optional[Sequence[Sequence[Mapping[str, Any]]]],
    search_space: Optional[Mapping[str, Sequence[Any]]],
) -> dict[str, list[Any]]:
    optimization_search_space: dict[str, list[Any]] = {
        "agent": [copy.deepcopy(dict(candidate)) for candidate in agent_candidates],
    }

    if environment_candidates is not None:
        if not environment_candidates:
            raise ValueError("environment_candidates must not be empty when provided")
        optimization_search_space["simulation.environments"] = [
            [copy.deepcopy(dict(item)) for item in candidate]
            for candidate in environment_candidates
        ]

    for path, choices in (search_space or {}).items():
        path_key = str(path)
        if not path_key:
            raise ValueError("search_space paths must be non-empty")
        if path_key in optimization_search_space:
            raise ValueError(f"search_space path {path_key!r} is already defined")
        if isinstance(choices, (str, bytes)) or isinstance(choices, Mapping):
            raise ValueError(
                f"search_space.{path_key} must be a sequence of candidate values"
            )
        values = [copy.deepcopy(value) for value in choices]
        if not values:
            raise ValueError(f"search_space.{path_key} must not be empty")
        optimization_search_space[path_key] = values

    return optimization_search_space


def _default_task_scenario(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "dataset": [
            {
                "persona": {"name": "SDK user", "role": "agent-owner"},
                "situation": "Optimize an agent task through Agent Learning Kit.",
                "outcome": "The optimized agent satisfies the configured evaluation.",
            }
        ],
    }


def _default_task_optimizer(
    search_space: Mapping[str, Sequence[Any]],
) -> dict[str, Any]:
    return {
        "algorithm": "agent",
        "max_candidates": max(2, _search_space_cardinality(search_space) + 1),
        "include_seed": True,
        "auto_diagnose": False,
    }


def _search_space_cardinality(search_space: Mapping[str, Sequence[Any]]) -> int:
    size = 1
    for choices in search_space.values():
        size *= max(1, len(choices))
    return size


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
    "build_task_optimization_manifest",
    "optimize_eval_suite",
    "optimize_eval_suite_file",
    "optimize_framework_adapter",
    "optimize_manifest",
    "optimize_manifest_file",
    "optimize_task",
    "problem_from_eval_suite_file",
    "problem_from_simulate_manifest_file",
    "relevant_search_paths",
]
