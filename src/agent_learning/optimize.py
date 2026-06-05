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


def build_orchestration_optimization_manifest(
    *,
    name: str,
    stack_candidates: Sequence[Mapping[str, Any]],
    evaluation_config: Mapping[str, Any],
    agent_candidates: Optional[Sequence[Mapping[str, Any]]] = None,
    scenario: Optional[Mapping[str, Any]] = None,
    required_env: Sequence[str] = (),
    optimizer: Optional[Mapping[str, Any]] = None,
    threshold: float = 0.9,
    simulation_engine: str = "local_text",
    min_turns: int = 1,
    max_turns: Optional[int] = None,
    auto_execute_tools: bool = True,
    search_space: Optional[Mapping[str, Sequence[Any]]] = None,
    target_base_config: Optional[Mapping[str, Any]] = None,
    target_metadata: Optional[Mapping[str, Any]] = None,
    layers: Sequence[str] = (
        "orchestration",
        "framework",
        "world",
        "memory",
        "multi_agent",
        "tools",
        "evaluator",
    ),
) -> dict[str, Any]:
    """Build a runnable optimization manifest for a full orchestration stack.

    A stack candidate is a coherent environment bundle. It can provide an
    explicit ``environments`` list, or shorthand blocks such as
    ``world_orchestration_replay``, ``world_contract``, ``framework_trace``,
    ``retrieval_memory``, ``agent_memory_lineage``, and ``multi_agent_room``.
    The optimizer searches those bundles as one unit so world, framework,
    memory, and collaboration evidence cannot drift apart across candidates.
    """

    if not name:
        raise ValueError("name is required")
    if not stack_candidates:
        raise ValueError("stack_candidates must contain at least one candidate")
    if not evaluation_config:
        raise ValueError("evaluation_config is required")

    environment_candidates = [
        _orchestration_environment_bundle(candidate)
        for candidate in stack_candidates
    ]
    agents = (
        [copy.deepcopy(dict(candidate)) for candidate in agent_candidates]
        if agent_candidates is not None
        else [_default_orchestration_agent()]
    )
    inferred_turns = _max_agent_response_count(agents, min_turns)

    return build_task_optimization_manifest(
        name=name,
        agent_candidates=agents,
        evaluation_config=evaluation_config,
        scenario=scenario or _default_orchestration_scenario(name),
        environment_candidates=environment_candidates,
        required_env=required_env,
        optimizer=optimizer,
        threshold=threshold,
        layers=layers,
        simulation_engine=simulation_engine,
        min_turns=min_turns,
        max_turns=max_turns if max_turns is not None else inferred_turns,
        auto_execute_tools=auto_execute_tools,
        search_space=search_space,
        target_base_config=target_base_config,
        target_metadata={
            "source": "agent_learning.optimize.build_orchestration_optimization_manifest",
            "task_kind": "orchestration_stack",
            **copy.deepcopy(dict(target_metadata or {})),
        },
    )


def optimize_orchestration_stack(
    *,
    manifest_path: str | Path = ".",
    options: Optional[Any] = None,
    result_name: Optional[str] = None,
    dry_run: Optional[bool] = None,
    **manifest_kwargs: Any,
) -> dict[str, Any]:
    """Build and execute an orchestration-stack optimization manifest."""

    manifest = build_orchestration_optimization_manifest(**manifest_kwargs)
    return optimize_manifest(
        manifest,
        manifest_path=manifest_path,
        options=options,
        name=result_name,
        dry_run=dry_run,
    )


def build_multi_agent_optimization_manifest(
    *,
    name: str,
    participants: Mapping[str, Any] | Sequence[Any],
    agent_candidates: Sequence[Mapping[str, Any]],
    evaluation_config: Mapping[str, Any],
    room: Optional[Mapping[str, Any]] = None,
    room_candidates: Optional[Sequence[Mapping[str, Any]]] = None,
    scenario: Optional[Mapping[str, Any]] = None,
    required_env: Sequence[str] = (),
    optimizer: Optional[Mapping[str, Any]] = None,
    threshold: float = 0.9,
    simulation_engine: str = "local_text",
    min_turns: int = 1,
    max_turns: Optional[int] = None,
    auto_execute_tools: bool = True,
    search_space: Optional[Mapping[str, Sequence[Any]]] = None,
    target_metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Build a runnable optimization manifest for multi-agent coordination.

    The helper optimizes both the scripted agent trace and the simulated
    ``multi_agent_room`` contract. That is the useful SDK primitive for
    handoffs, review, reconciliation, and shared room-state checks.
    """

    if not name:
        raise ValueError("name is required")
    if not agent_candidates:
        raise ValueError("agent_candidates must contain at least one candidate")
    if not evaluation_config:
        raise ValueError("evaluation_config is required")

    base_room_data = _multi_agent_room_data(participants=participants, room=room)
    room_env = _multi_agent_environment(base_room_data)
    environment_candidates = None
    environments: Optional[list[dict[str, Any]]] = [room_env]
    if room_candidates is not None:
        if not room_candidates:
            raise ValueError("room_candidates must not be empty when provided")
        environments = None
        environment_candidates = [
            [
                _multi_agent_environment(
                    _multi_agent_room_candidate(base_room_data, candidate)
                )
            ]
            for candidate in room_candidates
        ]

    inferred_turns = max(
        [
            len(candidate.get("responses", []))
            for candidate in agent_candidates
            if isinstance(candidate.get("responses", []), Sequence)
        ]
        or [min_turns]
    )
    max_turns_value = max_turns if max_turns is not None else max(min_turns, inferred_turns)

    return build_task_optimization_manifest(
        name=name,
        agent_candidates=agent_candidates,
        evaluation_config=evaluation_config,
        scenario=scenario or _default_multi_agent_scenario(name),
        environments=environments,
        environment_candidates=environment_candidates,
        required_env=required_env,
        optimizer=optimizer,
        threshold=threshold,
        layers=("multi_agent", "orchestration", "tools", "memory", "evaluator"),
        simulation_engine=simulation_engine,
        min_turns=min_turns,
        max_turns=max_turns_value,
        auto_execute_tools=auto_execute_tools,
        search_space=search_space,
        target_metadata={
            "source": "agent_learning.optimize.build_multi_agent_optimization_manifest",
            "task_kind": "multi_agent_coordination",
            **copy.deepcopy(dict(target_metadata or {})),
        },
    )


def optimize_multi_agent_coordination(
    *,
    manifest_path: str | Path = ".",
    options: Optional[Any] = None,
    result_name: Optional[str] = None,
    dry_run: Optional[bool] = None,
    **manifest_kwargs: Any,
) -> dict[str, Any]:
    """Build and execute a multi-agent coordination optimization manifest."""

    manifest = build_multi_agent_optimization_manifest(**manifest_kwargs)
    return optimize_manifest(
        manifest,
        manifest_path=manifest_path,
        options=options,
        name=result_name,
        dry_run=dry_run,
    )


def build_realtime_optimization_manifest(
    *,
    name: str,
    realtime_candidates: Sequence[Mapping[str, Any]],
    evaluation_config: Mapping[str, Any],
    agent_candidates: Optional[Sequence[Mapping[str, Any]]] = None,
    scenario: Optional[Mapping[str, Any]] = None,
    required_env: Sequence[str] = (),
    optimizer: Optional[Mapping[str, Any]] = None,
    threshold: float = 0.9,
    framework: str = "livekit",
    modality: str = "voice",
    simulation_engine: str = "local_text",
    min_turns: int = 1,
    max_turns: Optional[int] = None,
    auto_execute_tools: bool = True,
    search_space: Optional[Mapping[str, Sequence[Any]]] = None,
    target_metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Build a runnable realtime voice/streaming optimization manifest.

    Each realtime candidate can declare ``voice`` and/or ``streaming_trace``
    data. The helper turns those into manifest environments and searches the
    environment bundle as one candidate, which keeps call routing, audio
    quality, and streaming-token evidence coherent.
    """

    if not name:
        raise ValueError("name is required")
    if not realtime_candidates:
        raise ValueError("realtime_candidates must contain at least one candidate")
    if not evaluation_config:
        raise ValueError("evaluation_config is required")

    environment_candidates = [
        _realtime_environment_bundle(candidate, framework=framework)
        for candidate in realtime_candidates
    ]
    includes_voice = any(
        any(environment["type"] == "voice" for environment in bundle)
        for bundle in environment_candidates
    )
    includes_streaming = any(
        any(environment["type"] == "streaming_trace" for environment in bundle)
        for bundle in environment_candidates
    )
    agents = (
        [copy.deepcopy(dict(candidate)) for candidate in agent_candidates]
        if agent_candidates is not None
        else [
            _default_realtime_agent(
                include_voice=includes_voice,
                include_streaming=includes_streaming,
            )
        ]
    )

    manifest = build_task_optimization_manifest(
        name=name,
        agent_candidates=agents,
        evaluation_config=evaluation_config,
        scenario=scenario or _default_realtime_scenario(name),
        environment_candidates=environment_candidates,
        required_env=required_env,
        optimizer=optimizer,
        threshold=threshold,
        layers=("harness", "voice", "streaming", "integration", "evaluator"),
        simulation_engine=simulation_engine,
        min_turns=min_turns,
        max_turns=max_turns,
        auto_execute_tools=auto_execute_tools,
        search_space=search_space,
        target_base_config={"simulation": {"modality": modality}},
        target_metadata={
            "source": "agent_learning.optimize.build_realtime_optimization_manifest",
            "task_kind": "realtime_voice_streaming",
            "framework": framework,
            **copy.deepcopy(dict(target_metadata or {})),
        },
    )
    manifest["simulation"]["modality"] = modality
    return manifest


def optimize_realtime_stack(
    *,
    manifest_path: str | Path = ".",
    options: Optional[Any] = None,
    result_name: Optional[str] = None,
    dry_run: Optional[bool] = None,
    **manifest_kwargs: Any,
) -> dict[str, Any]:
    """Build and execute a realtime voice/streaming optimization manifest."""

    manifest = build_realtime_optimization_manifest(**manifest_kwargs)
    return optimize_manifest(
        manifest,
        manifest_path=manifest_path,
        options=options,
        name=result_name,
        dry_run=dry_run,
    )


def build_memory_optimization_manifest(
    *,
    name: str,
    memory_candidates: Sequence[Mapping[str, Any]],
    evaluation_config: Mapping[str, Any],
    agent_candidates: Optional[Sequence[Mapping[str, Any]]] = None,
    scenario: Optional[Mapping[str, Any]] = None,
    required_env: Sequence[str] = (),
    optimizer: Optional[Mapping[str, Any]] = None,
    threshold: float = 0.9,
    simulation_engine: str = "local_text",
    min_turns: int = 1,
    max_turns: Optional[int] = None,
    auto_execute_tools: bool = True,
    search_space: Optional[Mapping[str, Sequence[Any]]] = None,
    target_metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Build a runnable memory/retrieval optimization manifest.

    Candidates can provide ``retrieval_memory`` and/or ``agent_memory_lineage``
    data. They are searched as one environment bundle so retrieval freshness,
    source attribution, memory writes, policy checks, and observability lineage
    stay coherent.
    """

    if not name:
        raise ValueError("name is required")
    if not memory_candidates:
        raise ValueError("memory_candidates must contain at least one candidate")
    if not evaluation_config:
        raise ValueError("evaluation_config is required")

    environment_candidates = [
        _memory_environment_bundle(candidate)
        for candidate in memory_candidates
    ]
    agents = (
        [copy.deepcopy(dict(candidate)) for candidate in agent_candidates]
        if agent_candidates is not None
        else [_default_memory_agent()]
    )
    inferred_turns = _max_agent_response_count(agents, min_turns)

    return build_task_optimization_manifest(
        name=name,
        agent_candidates=agents,
        evaluation_config=evaluation_config,
        scenario=scenario or _default_memory_scenario(name),
        environment_candidates=environment_candidates,
        required_env=required_env,
        optimizer=optimizer,
        threshold=threshold,
        layers=("retrieval", "memory", "tools", "policy", "evaluator"),
        simulation_engine=simulation_engine,
        min_turns=min_turns,
        max_turns=max_turns if max_turns is not None else inferred_turns,
        auto_execute_tools=auto_execute_tools,
        search_space=search_space,
        target_metadata={
            "source": "agent_learning.optimize.build_memory_optimization_manifest",
            "task_kind": "memory_retrieval",
            **copy.deepcopy(dict(target_metadata or {})),
        },
    )


def optimize_memory_layer(
    *,
    manifest_path: str | Path = ".",
    options: Optional[Any] = None,
    result_name: Optional[str] = None,
    dry_run: Optional[bool] = None,
    **manifest_kwargs: Any,
) -> dict[str, Any]:
    """Build and execute a memory/retrieval optimization manifest."""

    manifest = build_memory_optimization_manifest(**manifest_kwargs)
    return optimize_manifest(
        manifest,
        manifest_path=manifest_path,
        options=options,
        name=result_name,
        dry_run=dry_run,
    )


def build_artifact_optimization_suite(
    *,
    name: str,
    artifact_path: str | Path,
    field_candidates: Sequence[Sequence[Mapping[str, Any]]],
    assertions: Sequence[Mapping[str, Any]],
    prompt_template: Optional[str] = None,
    provider_id: str = "artifact",
    test_id: Optional[str] = None,
    threshold: float = 1.0,
    optimizer: Optional[Mapping[str, Any]] = None,
    target_metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Build a promptfoo-style optimization suite for saved artifacts.

    This is the SDK bridge for artifact-first CI: keep assertions fixed, then
    optimize the artifact provider's extracted evidence fields. It evaluates
    existing run/red-team/optimization artifacts without rerunning the agent.
    """

    if not name:
        raise ValueError("name is required")
    if not field_candidates:
        raise ValueError("field_candidates must contain at least one candidate")
    if not assertions:
        raise ValueError("assertions must contain at least one assertion")

    fields = [_artifact_field_candidate(candidate) for candidate in field_candidates]
    checks = [copy.deepcopy(dict(assertion)) for assertion in assertions]
    artifact_path_value = str(artifact_path)
    search_space = {"providers.0.fields": copy.deepcopy(fields)}

    return {
        "version": "agent-learning.eval.v1",
        "name": name,
        "providers": [
            {
                "id": str(provider_id),
                "type": "artifact",
                "path": "{{artifact_path}}",
                "fields": copy.deepcopy(fields[0]),
            }
        ],
        "prompts": [
            {
                "id": "artifact-evidence",
                "template": prompt_template
                or "Evaluate saved artifact evidence from {{artifact_path}}.",
            }
        ],
        "tests": [
            {
                "id": test_id or f"{name}-gate",
                "vars": {"artifact_path": artifact_path_value},
                "assertions": checks,
            }
        ],
        "optimization": {
            "threshold": float(threshold),
            "target": {
                "name": name,
                "layers": ["harness", "environment", "evaluator"],
                "base_config": {
                    "providers": [{"fields": copy.deepcopy(fields[0])}]
                },
                "search_space": search_space,
                "metadata": {
                    "source": "agent_learning.optimize.build_artifact_optimization_suite",
                    "task_kind": "artifact_evidence",
                    **copy.deepcopy(dict(target_metadata or {})),
                },
            },
            "optimizer": copy.deepcopy(
                dict(optimizer or _default_artifact_optimizer(fields))
            ),
        },
    }


def optimize_artifact_evidence(
    *,
    suite_path: str | Path = ".",
    options: Optional[Any] = None,
    result_name: Optional[str] = None,
    dry_run: Optional[bool] = None,
    **suite_kwargs: Any,
) -> dict[str, Any]:
    """Build and execute an artifact-evidence optimization suite."""

    suite = build_artifact_optimization_suite(**suite_kwargs)
    return optimize_eval_suite(
        suite,
        suite_path=suite_path,
        options=options,
        name=result_name,
        dry_run=dry_run,
    )


def build_redteam_optimization_manifest(
    *,
    name: str,
    attack_candidates: Sequence[Sequence[str]],
    surface_candidates: Sequence[Sequence[str]],
    evaluation_config: Mapping[str, Any],
    scenario: Optional[Mapping[str, Any]] = None,
    agent: Optional[Mapping[str, Any]] = None,
    redteam: Optional[Mapping[str, Any]] = None,
    required_env: Sequence[str] = (),
    optimizer: Optional[Mapping[str, Any]] = None,
    threshold: float = 0.9,
    taxonomies: Sequence[str] = ("owasp_llm_top_10", "owasp_agentic_ai"),
    channels: Sequence[str] = ("chat",),
    providers: Sequence[str] = ("local_cli",),
    frameworks: Sequence[str] = ("agent_learning_kit",),
    target: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Build a runnable red-team campaign optimization manifest.

    This is the SDK path for the promptfoo-style red-team use case: optimize the
    attack/surface matrix while the simulator auto-generates the adversarial
    attack pack and campaign evidence that ai-evaluation scores.
    """

    if not name:
        raise ValueError("name is required")
    if not attack_candidates:
        raise ValueError("attack_candidates must contain at least one candidate")
    if not surface_candidates:
        raise ValueError("surface_candidates must contain at least one candidate")
    if not evaluation_config:
        raise ValueError("evaluation_config is required")

    attacks = _string_matrix("attack_candidates", attack_candidates)
    surfaces = _string_matrix("surface_candidates", surface_candidates)
    base_redteam = {
        "auto_generate": True,
        "taxonomies": [str(item) for item in taxonomies],
        "attacks": copy.deepcopy(attacks[0]),
        "surfaces": copy.deepcopy(surfaces[0]),
        "channels": [str(item) for item in channels],
        "providers": [str(item) for item in providers],
        "frameworks": [str(item) for item in frameworks],
        "target": copy.deepcopy(dict(target or {"agent": name, "environment": "local"})),
    }
    base_redteam.update(copy.deepcopy(dict(redteam or {})))
    search_space = {
        "redteam.attacks": copy.deepcopy(attacks),
        "redteam.surfaces": copy.deepcopy(surfaces),
    }

    return {
        "version": "agent-learning.optimization.v1",
        "name": name,
        "required_env": [str(key) for key in required_env],
        "redteam": copy.deepcopy(base_redteam),
        "scenario": copy.deepcopy(dict(scenario or _default_redteam_scenario(name))),
        "agent": copy.deepcopy(dict(agent or _default_redteam_agent())),
        "simulation": {
            "engine": "local_text",
            "max_turns": 3,
            "min_turns": 3,
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
                "layers": ["harness", "security", "evaluator"],
                "base_config": {
                    "redteam": {
                        "attacks": copy.deepcopy(attacks[0]),
                        "surfaces": copy.deepcopy(surfaces[0]),
                    }
                },
                "search_space": search_space,
                "metadata": {
                    "source": "agent_learning.optimize.build_redteam_optimization_manifest",
                    "task_kind": "redteam_campaign",
                },
            },
            "optimizer": copy.deepcopy(
                dict(optimizer or _default_task_optimizer(search_space))
            ),
        },
    }


def optimize_redteam_campaign(
    *,
    manifest_path: str | Path = ".",
    options: Optional[Any] = None,
    result_name: Optional[str] = None,
    dry_run: Optional[bool] = None,
    **manifest_kwargs: Any,
) -> dict[str, Any]:
    """Build and execute a red-team campaign optimization manifest."""

    manifest = build_redteam_optimization_manifest(**manifest_kwargs)
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


def _multi_agent_room_data(
    *,
    participants: Mapping[str, Any] | Sequence[Any],
    room: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    room_data = copy.deepcopy(dict(room or {}))
    configured_participants = (
        room_data.pop("participants", None)
        or room_data.pop("agents", None)
        or room_data.pop("roles", None)
        or participants
    )
    room_data["participants"] = _copy_multi_agent_participants(
        configured_participants
    )
    return room_data


def _multi_agent_room_candidate(
    base_room_data: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    room_data = copy.deepcopy(dict(base_room_data))
    room_data.update(copy.deepcopy(dict(candidate)))
    return _multi_agent_room_data(
        participants=room_data.get("participants", {}),
        room=room_data,
    )


def _copy_multi_agent_participants(
    participants: Mapping[str, Any] | Sequence[Any],
) -> Mapping[str, Any] | list[Any]:
    if isinstance(participants, Mapping):
        copied = copy.deepcopy(dict(participants))
        if not copied:
            raise ValueError("participants must not be empty")
        return copied
    if isinstance(participants, (str, bytes)):
        raise ValueError("participants must be a mapping or sequence of roles")
    copied_list = [
        copy.deepcopy(dict(item)) if isinstance(item, Mapping) else str(item)
        for item in participants
        if item not in (None, "")
    ]
    if not copied_list:
        raise ValueError("participants must not be empty")
    return copied_list


def _multi_agent_environment(room_data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "multi_agent_room",
        "data": copy.deepcopy(dict(room_data)),
    }


def _realtime_environment_bundle(
    candidate: Mapping[str, Any],
    *,
    framework: str,
) -> list[dict[str, Any]]:
    candidate_dict = copy.deepcopy(dict(candidate))
    explicit_environments = candidate_dict.pop("environments", None)
    if explicit_environments is not None:
        bundle = [copy.deepcopy(dict(item)) for item in explicit_environments]
        if not bundle:
            raise ValueError("realtime candidate environments must not be empty")
        return bundle

    bundle: list[dict[str, Any]] = []
    if "voice" in candidate_dict:
        bundle.append(
            _typed_realtime_environment(
                "voice",
                candidate_dict.pop("voice"),
                framework=framework,
            )
        )
    streaming_data = candidate_dict.pop(
        "streaming_trace",
        candidate_dict.pop("streaming", None),
    )
    if streaming_data is not None:
        bundle.append(
            _typed_realtime_environment(
                "streaming_trace",
                streaming_data,
                framework=framework,
            )
        )
    if candidate_dict:
        raise ValueError(
            "realtime candidate keys must be environments, voice, streaming_trace, or streaming"
        )
    if not bundle:
        raise ValueError("realtime candidate must define voice or streaming_trace")
    return bundle


def _typed_realtime_environment(
    environment_type: str,
    data: Any,
    *,
    framework: str,
) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise ValueError(f"{environment_type} candidate data must be a mapping")
    environment_data = copy.deepcopy(dict(data))
    environment_data.setdefault("framework", framework)
    return {"type": environment_type, "data": environment_data}


def _memory_environment_bundle(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidate_dict = copy.deepcopy(dict(candidate))
    explicit_environments = candidate_dict.pop("environments", None)
    if explicit_environments is not None:
        bundle = [copy.deepcopy(dict(item)) for item in explicit_environments]
        if not bundle:
            raise ValueError("memory candidate environments must not be empty")
        return bundle

    bundle: list[dict[str, Any]] = []
    retrieval_data = candidate_dict.pop(
        "retrieval_memory",
        candidate_dict.pop("retrieval", None),
    )
    if retrieval_data is not None:
        bundle.append(_typed_memory_environment("retrieval_memory", retrieval_data))
    lineage_data = candidate_dict.pop(
        "agent_memory_lineage",
        candidate_dict.pop("lineage", None),
    )
    if lineage_data is not None:
        bundle.append(_typed_memory_environment("agent_memory_lineage", lineage_data))
    if candidate_dict:
        raise ValueError(
            "memory candidate keys must be environments, retrieval_memory, retrieval, agent_memory_lineage, or lineage"
        )
    if not bundle:
        raise ValueError(
            "memory candidate must define retrieval_memory or agent_memory_lineage"
        )
    return bundle


def _typed_memory_environment(environment_type: str, data: Any) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise ValueError(f"{environment_type} candidate data must be a mapping")
    return {"type": environment_type, "data": copy.deepcopy(dict(data))}


_ORCHESTRATION_ENVIRONMENT_ALIASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("world_orchestration_replay", "world_replay", "world_orchestration"),
        "world_orchestration_replay",
    ),
    (("world_contract", "world"), "world_contract"),
    (("orchestration_trace", "orchestration"), "orchestration_trace"),
    (("framework_trace", "framework"), "framework_trace"),
    (("retrieval_memory", "retrieval"), "retrieval_memory"),
    (
        ("agent_memory_lineage", "memory_lineage", "lineage"),
        "agent_memory_lineage",
    ),
    (("multi_agent_room", "room", "multi_agent"), "multi_agent_room"),
    (("structured_artifact", "artifact"), "structured_artifact"),
    (("domain_package", "domain"), "domain_package"),
    (("adversarial_attack_pack", "attack_pack", "attacks"), "adversarial_attack_pack"),
    (("red_team_campaign", "redteam_campaign"), "red_team_campaign"),
    (("red_team_readiness", "redteam_readiness"), "red_team_readiness"),
    (("voice", "voice_trace"), "voice"),
    (("streaming_trace", "streaming"), "streaming_trace"),
    (("workspace_run_manifest", "workspace_run"), "workspace_run_manifest"),
)


def _orchestration_environment_bundle(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidate_dict = copy.deepcopy(dict(candidate))
    explicit_environments = candidate_dict.pop("environments", None)
    if explicit_environments is not None:
        bundle = _environment_list(explicit_environments)
        if not bundle:
            raise ValueError("orchestration candidate environments must not be empty")
        return bundle

    for annotation_key in ("id", "name", "description", "metadata"):
        candidate_dict.pop(annotation_key, None)

    bundle: list[dict[str, Any]] = []
    for aliases, environment_type in _ORCHESTRATION_ENVIRONMENT_ALIASES:
        data = _pop_first(candidate_dict, aliases)
        if data is not None:
            bundle.append(_typed_orchestration_environment(environment_type, data))

    if candidate_dict:
        allowed = sorted(
            {
                "environments",
                "id",
                "name",
                "description",
                "metadata",
                *[
                    alias
                    for aliases, _environment_type in _ORCHESTRATION_ENVIRONMENT_ALIASES
                    for alias in aliases
                ],
            }
        )
        raise ValueError(
            "orchestration candidate has unsupported key(s): "
            f"{', '.join(sorted(candidate_dict))}; expected one of {', '.join(allowed)}"
        )
    if not bundle:
        raise ValueError("orchestration candidate must define at least one environment")
    return bundle


def _environment_list(environments: Any) -> list[dict[str, Any]]:
    if isinstance(environments, Mapping):
        environments = [environments]
    if isinstance(environments, (str, bytes)) or environments is None:
        raise ValueError("environments must be a mapping or sequence of mappings")
    bundle: list[dict[str, Any]] = []
    for index, raw in enumerate(environments, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"environment {index} must be a mapping")
        item = copy.deepcopy(dict(raw))
        if not item.get("type"):
            raise ValueError(f"environment {index} requires type")
        bundle.append(item)
    return bundle


def _typed_orchestration_environment(environment_type: str, data: Any) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise ValueError(f"{environment_type} candidate data must be a mapping")
    item = copy.deepcopy(dict(data))
    if item.get("type") and "data" in item:
        return item
    return {"type": environment_type, "data": item}


def _pop_first(source: dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in source:
            return source.pop(key)
    return None


def _artifact_field_candidate(
    fields: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(fields, (str, bytes)) or isinstance(fields, Mapping):
        raise ValueError("each field candidate must be a sequence of field mappings")
    copied = [copy.deepcopy(dict(field)) for field in fields]
    if not copied:
        raise ValueError("field candidate must not be empty")
    for index, field in enumerate(copied, start=1):
        if not field.get("path"):
            raise ValueError(f"field candidate item {index} requires path")
        field.setdefault("name", str(field.get("id") or field.get("path")))
    return copied


def _max_agent_response_count(
    agent_candidates: Sequence[Mapping[str, Any]],
    minimum: int,
) -> int:
    counts = [
        len(candidate.get("responses", []))
        for candidate in agent_candidates
        if isinstance(candidate.get("responses", []), Sequence)
    ]
    return max([int(minimum), *counts])


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


def _string_matrix(name: str, values: Sequence[Sequence[str]]) -> list[list[str]]:
    matrix: list[list[str]] = []
    for index, candidate in enumerate(values):
        if isinstance(candidate, (str, bytes)):
            raise ValueError(f"{name}[{index}] must be a sequence of strings")
        items = [str(item) for item in candidate if str(item or "").strip()]
        if not items:
            raise ValueError(f"{name}[{index}] must not be empty")
        matrix.append(items)
    return matrix


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


def _default_redteam_scenario(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "dataset": [
            {
                "persona": {"name": "Asha", "role": "security-engineer"},
                "situation": "Optimize a red-team attack and surface matrix through Agent Learning Kit.",
                "outcome": "The optimized campaign covers the required attacks and surfaces.",
            }
        ],
    }


def _default_multi_agent_scenario(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "dataset": [
            {
                "persona": {"name": "SDK user", "role": "multi-agent-owner"},
                "situation": "Optimize handoff, review, and reconciliation through Agent Learning Kit.",
                "outcome": "The optimized multi-agent trace satisfies the configured coordination gates.",
            }
        ],
    }


def _default_orchestration_scenario(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "dataset": [
            {
                "persona": {"name": "SDK user", "role": "orchestration-owner"},
                "situation": (
                    "Optimize a full agent orchestration stack across world, "
                    "framework, memory, collaboration, and evaluator evidence."
                ),
                "outcome": (
                    "The optimized orchestration stack satisfies the configured "
                    "task and environment gates."
                ),
            }
        ],
    }


def _default_orchestration_agent() -> dict[str, Any]:
    return {
        "type": "scripted",
        "responses": [
            {
                "content": "Inspecting world orchestration and applying the required transition.",
                "tool_calls": [
                    {
                        "id": "world_status",
                        "name": "world_orchestration_replay_status",
                        "arguments": {},
                    },
                    {
                        "id": "approve_refund",
                        "name": "apply_world_transition",
                        "arguments": {"id": "approve_refund"},
                    },
                ],
            },
            {
                "content": "Inspecting framework and retrieval evidence for the orchestration.",
                "tool_calls": [
                    {
                        "id": "framework_status",
                        "name": "framework_trace_status",
                        "arguments": {},
                    },
                    {
                        "id": "retrieve_policy",
                        "name": "retrieve_documents",
                        "arguments": {"query": "current refund policy"},
                    },
                    {
                        "id": "read_policy",
                        "name": "read_document",
                        "arguments": {"id": "doc_refund_2026"},
                    },
                    {
                        "id": "cite_policy",
                        "name": "cite_sources",
                        "arguments": {
                            "doc_ids": ["doc_refund_2026"],
                            "claim": "Refund approval is grounded in current policy.",
                            "freshness_checked": True,
                        },
                    },
                ],
            },
            {
                "content": "Inspecting memory lineage and multi-agent review evidence.",
                "tool_calls": [
                    {
                        "id": "memory_lineage",
                        "name": "agent_memory_lineage_status",
                        "arguments": {},
                    },
                    {
                        "id": "retrieval_memory",
                        "name": "retrieval_memory_status",
                        "arguments": {},
                    },
                    {
                        "id": "room_status",
                        "name": "room_status",
                        "arguments": {},
                    },
                    {
                        "id": "critic_review",
                        "name": "request_review",
                        "arguments": {
                            "reviewer": "critic",
                            "target": "world orchestration refund decision",
                            "criteria": ["policy", "memory", "world"],
                        },
                    },
                ],
            },
            {
                "content": (
                    "The orchestration stack proves the world transition, "
                    "framework trace, policy grounding, memory provenance, "
                    "and critic-reviewed decision."
                ),
                "tool_calls": [
                    {
                        "id": "reconcile",
                        "name": "reconcile",
                        "arguments": {
                            "summary": "approved refund orchestration accepted",
                            "accepted_source": "critic",
                            "conflicts": [],
                            "participants": ["planner", "retriever", "critic"],
                        },
                    }
                ],
            },
        ],
    }


def _default_realtime_scenario(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "dataset": [
            {
                "persona": {"name": "SDK user", "role": "realtime-agent-owner"},
                "situation": "Optimize realtime voice and streaming evidence through Agent Learning Kit.",
                "outcome": "The optimized realtime harness satisfies the configured latency, voice, and streaming gates.",
            }
        ],
    }


def _default_realtime_agent(
    *,
    include_voice: bool,
    include_streaming: bool,
) -> dict[str, Any]:
    first_turn_tools: list[dict[str, Any]] = []
    second_turn_tools: list[dict[str, Any]] = []
    if include_voice:
        first_turn_tools.extend([
            {"id": "voice_status", "name": "voice_status", "arguments": {}},
            {"id": "voice_timing", "name": "voice_timing", "arguments": {}},
            {
                "id": "transcribe_user",
                "name": "transcribe_audio",
                "arguments": {"id": "utt_refund"},
            },
            {
                "id": "route_support",
                "name": "route_call",
                "arguments": {
                    "route": "support",
                    "reason": "refund support request",
                },
            },
        ])
        second_turn_tools.append(
            {
                "id": "speak_answer",
                "name": "speak",
                "arguments": {
                    "text": "Your refund request has been routed to support.",
                    "latency_ms": 240,
                },
            }
        )
    if include_streaming:
        second_turn_tools.extend([
            {
                "id": "stream_status",
                "name": "streaming_trace_status",
                "arguments": {},
            },
            {
                "id": "stream_tool_events",
                "name": "list_stream_events",
                "arguments": {"signal": "tool_delta"},
            },
            {
                "id": "inspect_stream_tool",
                "name": "inspect_stream_event",
                "arguments": {"id": "stream_tool_delta"},
            },
        ])
    return {
        "type": "scripted",
        "responses": [
            {
                "content": "Inspecting realtime voice routing and transcription evidence.",
                "tool_calls": first_turn_tools,
            },
            {
                "content": "Realtime voice and streaming evidence proves the support route.",
                "tool_calls": second_turn_tools,
            },
        ],
    }


def _default_memory_scenario(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "dataset": [
            {
                "persona": {"name": "SDK user", "role": "memory-owner"},
                "situation": "Optimize retrieval freshness and memory lineage through Agent Learning Kit.",
                "outcome": "The optimized memory harness satisfies retrieval, attribution, policy, and lineage gates.",
            }
        ],
    }


def _default_memory_agent() -> dict[str, Any]:
    return {
        "type": "scripted",
        "responses": [
            {
                "content": "Retrieve the current refund policy and read the source document.",
                "tool_calls": [
                    {
                        "id": "retrieve_policy",
                        "name": "retrieve_documents",
                        "arguments": {
                            "query": "current refund policy source grounding",
                            "top_k": 1,
                        },
                    },
                    {
                        "id": "read_policy",
                        "name": "read_document",
                        "arguments": {"id": "doc_refund_2026"},
                    },
                    {
                        "id": "cite_policy",
                        "name": "cite_sources",
                        "arguments": {
                            "doc_ids": ["doc_refund_2026"],
                            "claim": "Refund approval is allowed by the current policy.",
                            "freshness_checked": True,
                        },
                    },
                ],
            },
            {
                "content": (
                    "The current refund policy grounding, source attribution, "
                    "memory provenance, and retention/deletion/redaction "
                    "policies are recorded and enforced."
                ),
                "tool_calls": [
                    {
                        "id": "memory_lineage_status",
                        "name": "agent_memory_lineage_status",
                        "arguments": {},
                    },
                    {
                        "id": "memory_lineage_ops",
                        "name": "list_memory_lineage_operations",
                        "arguments": {},
                    },
                    {
                        "id": "retrieval_memory_status",
                        "name": "retrieval_memory_status",
                        "arguments": {},
                    },
                    {
                        "id": "write_decision_memory",
                        "name": "write_memory",
                        "arguments": {
                            "key": "refund_decision",
                            "value": "approved_with_policy_grounding",
                        },
                    },
                ],
            },
        ],
    }


def _default_redteam_agent() -> dict[str, Any]:
    return {
        "type": "scripted",
        "responses": [
            {
                "content": "Inspecting the generated adversarial attack pack.",
                "tool_calls": [
                    {"id": "adv", "name": "adversarial_pack_status", "arguments": {}}
                ],
            },
            {
                "content": "Inspecting red-team campaign coverage and gaps.",
                "tool_calls": [
                    {
                        "id": "campaign",
                        "name": "red_team_campaign_status",
                        "arguments": {},
                    },
                    {
                        "id": "gaps",
                        "name": "list_red_team_campaign_gaps",
                        "arguments": {},
                    },
                ],
            },
            {
                "content": "The optimized red-team campaign covers the required attacks and surfaces.",
                "tool_calls": [],
            },
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


def _default_artifact_optimizer(
    field_candidates: Sequence[Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    return {
        "algorithm": "agent",
        "max_candidates": max(2, len(field_candidates) + 1),
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
    "build_artifact_optimization_suite",
    "build_framework_optimization_manifest",
    "build_memory_optimization_manifest",
    "build_multi_agent_optimization_manifest",
    "build_orchestration_optimization_manifest",
    "build_realtime_optimization_manifest",
    "build_redteam_optimization_manifest",
    "build_task_optimization_manifest",
    "optimize_eval_suite",
    "optimize_eval_suite_file",
    "optimize_artifact_evidence",
    "optimize_framework_adapter",
    "optimize_manifest",
    "optimize_manifest_file",
    "optimize_memory_layer",
    "optimize_multi_agent_coordination",
    "optimize_orchestration_stack",
    "optimize_realtime_stack",
    "optimize_redteam_campaign",
    "optimize_task",
    "problem_from_eval_suite_file",
    "problem_from_simulate_manifest_file",
    "relevant_search_paths",
]
