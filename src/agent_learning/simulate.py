from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ._facade import optional_module

_SIMULATE_EXTRA = "simulate"

_FI_SIMULATE_EXPORT_NAMES = (
    "AgentDefinition",
    "SimulatorAgentDefinition",
    "LLMConfig",
    "TTSConfig",
    "STTConfig",
    "VADConfig",
    "AgentInput",
    "AgentResponse",
    "AgentWrapper",
    "SimulationArtifact",
    "SimulationEvent",
    "GenericAgentWrapper",
    "FrameworkAdapterSpec",
    "supported_frameworks",
    "wrap_agent",
    "wrap_framework",
    "EchoAgentWrapper",
    "RuleBasedAgentWrapper",
    "ScriptedAgentWrapper",
    "make_tool_response",
    "OpenAIAgentWrapper",
    "LangChainAgentWrapper",
    "GeminiAgentWrapper",
    "AnthropicAgentWrapper",
    "AdversarialEnvironmentPack",
    "AgentControlPlaneEnvironment",
    "AgentIntegrationEnvironment",
    "AgentMemoryLineageEnvironment",
    "AgentTrustBoundaryEnvironment",
    "AutonomyLoopEnvironment",
    "BrowserEnvironment",
    "DomainPackageEnvironment",
    "EnvironmentAdapter",
    "EnvironmentSnapshot",
    "FileEnvironment",
    "FrameworkCapabilityEnvironment",
    "FrameworkImportManifestEnvironment",
    "FrameworkLifecycleEnvironment",
    "FrameworkPortabilityEnvironment",
    "FrameworkProbeEnvironment",
    "FrameworkTraceEnvironment",
    "ImageEnvironment",
    "MultiAgentRoomEnvironment",
    "ObservabilityReplayEnvironment",
    "OptimizerPortfolioEnvironment",
    "OptimizerTraceEnvironment",
    "OrchestrationTraceEnvironment",
    "RetrievalMemoryEnvironment",
    "RedTeamCampaignEnvironment",
    "RedTeamReadinessEnvironment",
    "StreamingTraceEnvironment",
    "StructuredArtifactEnvironment",
    "ToolExecutionResult",
    "ToolFaultInjectionEnvironment",
    "ToolMockEnvironment",
    "VoiceEnvironment",
    "WorldAttackReplayEnvironment",
    "WorldContractEnvironment",
    "WorldOrchestrationReplayEnvironment",
    "WorkspaceRunEnvironment",
    "load_adversarial_attack_pack",
    "load_agent_integration_manifest",
    "load_agent_memory_lineage_manifest",
    "load_browser_mutation_pack",
    "load_browser_trace_export",
    "load_voice_export",
    "load_world_attack_replay",
    "load_world_orchestration_replay",
    "load_workspace_run_manifest",
    "load_pipecat_frame_log",
    "load_world_contract",
    "load_playwright_trace_export",
    "load_red_team_campaign_manifest",
    "load_red_team_readiness_manifest",
    "load_framework_trace_export",
    "load_framework_import_manifest",
    "load_mcp_tool_session_export",
    "load_observability_replay_pack",
    "load_optimizer_backend_portfolio",
    "load_framework_multi_agent_transcript",
    "load_orchestration_trace_export",
    "load_streaming_trace_export",
    "load_autogen_groupchat_transcript",
    "load_crewai_event_log",
    "load_openai_agents_trace",
    "load_openai_responses_trace",
    "load_langchain_event_stream",
    "load_langgraph_event_stream",
    "normalize_voice_timing_distribution",
    "normalize_pipecat_frame_log",
    "normalize_orchestration_trace_events",
    "normalize_orchestration_trace_export",
    "normalize_streaming_trace_events",
    "normalize_streaming_trace_export",
    "normalize_framework_lifecycle_trace",
    "normalize_framework_import_manifest",
    "normalize_framework_capability_matrix",
    "normalize_agent_control_plane",
    "normalize_agent_memory_lineage_manifest",
    "normalize_agent_trust_boundary_model",
    "normalize_framework_portability_matrix",
    "normalize_framework_trace_events",
    "normalize_framework_probe_suite",
    "normalize_framework_adapter_conformance",
    "normalize_observability_replay_pack",
    "normalize_optimizer_backend_portfolio",
    "normalize_optimizer_society_trace",
    "normalize_framework_trace_export",
    "normalize_mcp_tool_session_export",
    "normalize_openai_responses_trace",
    "normalize_browser_trace_export",
    "normalize_browser_mutation_pack",
    "normalize_voice_export",
    "normalize_adversarial_attack_pack",
    "normalize_agent_integration_manifest",
    "normalize_workspace_run_manifest",
    "normalize_world_attack_replay",
    "normalize_world_orchestration_replay",
    "normalize_world_contract",
    "normalize_playwright_trace_export",
    "normalize_red_team_campaign_manifest",
    "normalize_red_team_readiness_manifest",
    "AttackDefinition",
    "AttackVector",
    "Persona",
    "Scenario",
    "TestReport",
    "TestCaseResult",
    "TestRunner",
    "ScenarioGenerator",
    "SyntheticDataGenerator",
    "SyntheticScenarioConfig",
    "SyntheticTrajectoryTemplateBundle",
    "SyntheticTrajectoryTemplateConfig",
    "SyntheticToolTaskBundle",
    "SyntheticToolTaskConfig",
    "evaluate_report",
    "evaluate_agent_report",
    "MANIFEST_SCHEMA_VERSION",
    "ManifestError",
    "ManifestOptimizationOptions",
    "ManifestRunOptions",
    "EVAL_SUITE_SCHEMA_VERSION",
    "EvalSuiteOptions",
    "apply_manifest_env",
    "build_framework_run_manifest",
    "build_manifest_agent_callback",
    "build_manifest_environments",
    "build_multi_framework_suite_manifest",
    "build_manifest_optimization_problem",
    "compare_result_files",
    "compare_results",
    "create_baseline",
    "create_baseline_file",
    "detect_manifest_command",
    "evaluate_manifest_report",
    "load_manifest",
    "load_manifest_file",
    "load_eval_suite_file",
    "missing_manifest_env",
    "optimize_manifest",
    "optimize_manifest_file",
    "prepare_redteam_manifest",
    "promote_to_regression",
    "promote_to_regression_file",
    "public_result",
    "redteam_manifest",
    "redteam_manifest_file",
    "required_manifest_env",
    "render_junit",
    "render_markdown",
    "render_report",
    "render_report_file",
    "render_sarif",
    "replay_manifests",
    "run_eval_suite",
    "run_eval_suite_file",
    "run_local_text_manifest",
    "run_manifest",
    "run_manifest_file",
    "run_redteam_manifest",
    "run_redteam_manifest_file",
    "supported_manifest_environment_types",
    "validate_manifest_env",
)

_SIMULATE_EXPORTS = {name: "fi.simulate" for name in _FI_SIMULATE_EXPORT_NAMES}
_SIMULATE_EXPORTS.update(
    {
        "BaseEngine": "fi.simulate.simulation.engines",
        "CloudEngine": "fi.simulate.simulation.engines",
        "LiveKitEngine": "fi.simulate.simulation.engines",
        "LocalTextEngine": "fi.simulate.simulation.engines",
    }
)

AGENT_LEARNING_RUN_KIND = "agent-learning.run.v1"
AGENT_LEARNING_SUITE_KIND = "agent-learning.suite.v1"


def _manifest() -> Any:
    return optional_module("fi.simulate.manifest", _SIMULATE_EXTRA)


def _suite() -> Any:
    return optional_module("fi.simulate.suite", _SIMULATE_EXTRA)


def _simulate() -> Any:
    return optional_module("fi.simulate", _SIMULATE_EXTRA)


def load_manifest_file(path: str | Path) -> dict[str, Any]:
    return _manifest().load_manifest_file(path)


load_manifest = load_manifest_file


def detect_manifest_command(manifest: Mapping[str, Any]) -> str:
    return _manifest().detect_manifest_command(manifest)


def required_manifest_env(manifest: Mapping[str, Any]) -> list[str]:
    return _manifest().required_manifest_env(manifest)


def missing_manifest_env(manifest: Mapping[str, Any]) -> list[str]:
    return _manifest().missing_manifest_env(manifest)


def validate_manifest_env(manifest: Mapping[str, Any]) -> None:
    _manifest().validate_manifest_env(manifest)


def apply_manifest_env(manifest: Mapping[str, Any]) -> None:
    _manifest().apply_manifest_env(manifest)


def build_manifest_agent_callback(
    agent: Mapping[str, Any],
    *,
    base_dir: str | Path = ".",
) -> Any:
    return _manifest().build_manifest_agent_callback(agent, base_dir=base_dir)


def build_manifest_environments(
    environments: Any,
    *,
    base_dir: str | Path = ".",
) -> list[Any]:
    return _manifest().build_manifest_environments(environments, base_dir=base_dir)


def supported_manifest_environment_types() -> list[str]:
    return _manifest().supported_manifest_environment_types()


def build_task_run_manifest(
    *,
    name: str,
    agent: Mapping[str, Any],
    task_description: Optional[str] = None,
    expected_result: Optional[str] = None,
    scenario: Optional[Mapping[str, Any]] = None,
    environments: Sequence[Mapping[str, Any]] = (),
    required_env: Sequence[str] = (),
    available_tools: Sequence[str] = (),
    required_tools: Sequence[str] = (),
    success_criteria: Sequence[str] = (),
    evaluation_config: Optional[Mapping[str, Any]] = None,
    threshold: float = 0.7,
    simulation_engine: str = "local_text",
    min_turns: int = 1,
    max_turns: int = 1,
    auto_execute_tools: bool = True,
    modality: Optional[str] = None,
    persona: Optional[Mapping[str, Any]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Build a runnable simulation manifest for any task/world agent.

    This is the SDK counterpart to hand-writing ``agent-learning.run.v1`` JSON:
    callers provide an existing manifest agent spec (scripted, callable,
    framework, or any future adapter), optional environments, and optional
    agent-report evaluation settings. Runtime semantics stay in simulate-sdk.
    """

    if not name:
        raise ValueError("name is required")
    if not agent:
        raise ValueError("agent is required")
    if scenario is None and not task_description:
        raise ValueError("task_description is required when scenario is not provided")
    if min_turns < 1:
        raise ValueError("min_turns must be >= 1")
    if max_turns < min_turns:
        raise ValueError("max_turns must be >= min_turns")

    simulation: dict[str, Any] = {
        "engine": str(simulation_engine),
        "max_turns": int(max_turns),
        "min_turns": int(min_turns),
        "auto_execute_tools": bool(auto_execute_tools),
        "environments": [copy.deepcopy(dict(item)) for item in environments],
    }
    if modality:
        simulation["modality"] = str(modality)

    manifest: dict[str, Any] = {
        "version": AGENT_LEARNING_RUN_KIND,
        "name": str(name),
        "required_env": _unique_strings(required_env),
        "scenario": copy.deepcopy(
            dict(scenario)
            if scenario is not None
            else _default_task_scenario(
                str(name),
                task_description=str(task_description),
                expected_result=expected_result,
                persona=persona,
            )
        ),
        "agent": copy.deepcopy(dict(agent)),
        "simulation": simulation,
        "evaluation": _task_run_evaluation(
            task_description=task_description,
            expected_result=expected_result,
            available_tools=available_tools,
            required_tools=required_tools,
            success_criteria=success_criteria,
            evaluation_config=evaluation_config,
            threshold=threshold,
        ),
    }
    if metadata:
        manifest["metadata"] = {
            "source": "agent_learning.simulate.build_task_run_manifest",
            **copy.deepcopy(dict(metadata)),
        }
    return manifest


def build_realtime_run_manifest(
    *,
    name: str,
    framework: str = "livekit",
    voice: Optional[Mapping[str, Any]] = None,
    streaming_trace: Optional[Mapping[str, Any]] = None,
    agent: Optional[Mapping[str, Any]] = None,
    scenario: Optional[Mapping[str, Any]] = None,
    required_env: Sequence[str] = (),
    metadata: Optional[Mapping[str, Any]] = None,
    simulation_engine: str = "local_text",
    min_turns: int = 2,
    max_turns: int = 2,
    evaluation_enabled: bool = False,
) -> dict[str, Any]:
    """Build a local realtime voice + streaming simulation manifest.

    This is the SDK counterpart to
    ``examples/voice_streaming_realtime_manifest.json``: callers can simulate a
    realtime provider stack with a ``voice`` environment, a ``streaming_trace``
    environment, and a scripted agent that exercises transcript, routing,
    streaming event, and TTS tools.
    """

    if not name:
        raise ValueError("name is required")
    if min_turns < 1:
        raise ValueError("min_turns must be >= 1")
    if max_turns < min_turns:
        raise ValueError("max_turns must be >= min_turns")

    framework_key = _framework_key(framework)
    voice_data = copy.deepcopy(
        dict(voice) if voice is not None else _default_realtime_voice(framework_key)
    )
    voice_data.setdefault("framework", framework_key)
    streaming_data = copy.deepcopy(
        dict(streaming_trace)
        if streaming_trace is not None
        else _default_realtime_streaming_trace(framework_key)
    )
    streaming_data.setdefault("framework", framework_key)
    manifest: dict[str, Any] = {
        "version": AGENT_LEARNING_RUN_KIND,
        "name": str(name),
        "required_env": _unique_strings(required_env),
        "scenario": copy.deepcopy(
            dict(scenario)
            if scenario is not None
            else _default_realtime_scenario(str(name), framework_key)
        ),
        "agent": copy.deepcopy(dict(agent or _default_realtime_agent())),
        "simulation": {
            "engine": str(simulation_engine),
            "modality": "voice",
            "max_turns": int(max_turns),
            "min_turns": int(min_turns),
            "environments": [
                {"type": "voice", "data": voice_data},
                {"type": "streaming_trace", "data": streaming_data},
            ],
        },
        "evaluation": {"enabled": bool(evaluation_enabled)},
    }
    if metadata:
        manifest["metadata"] = {
            "source": "agent_learning.simulate.build_realtime_run_manifest",
            **copy.deepcopy(dict(metadata)),
        }
    return manifest


def build_browser_cua_run_manifest(
    *,
    name: str,
    browser: Optional[Mapping[str, Any]] = None,
    agent: Optional[Mapping[str, Any]] = None,
    scenario: Optional[Mapping[str, Any]] = None,
    evaluation_config: Optional[Mapping[str, Any]] = None,
    required_env: Sequence[str] = (),
    allowed_domains: Sequence[str] = ("shop.example.test",),
    url: str = "https://shop.example.test/checkout",
    confirmation_url: str = "https://shop.example.test/confirmation",
    order_id: str = "ord_123",
    threshold: float = 0.9,
    simulation_engine: str = "local_text",
    min_turns: int = 4,
    max_turns: Optional[int] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Build a direct browser/CUA simulation manifest.

    This is the SDK run counterpart to the browser/CUA optimization cookbook:
    it exercises browser snapshots, selector drift, mutation packs, storage,
    runtime, network, visual grounding, and prompt-injection surfaces as one
    local simulation without requiring an optimizer run.
    """

    if not name:
        raise ValueError("name is required")
    if min_turns < 1:
        raise ValueError("min_turns must be >= 1")
    if max_turns is not None and max_turns < min_turns:
        raise ValueError("max_turns must be >= min_turns")

    from . import optimize as _agent_optimize

    optimization_manifest = _agent_optimize.build_browser_cua_optimization_manifest(
        name=name,
        agent=agent,
        scenario=scenario,
        evaluation_config=evaluation_config,
        required_env=required_env,
        allowed_domains=allowed_domains,
        url=url,
        confirmation_url=confirmation_url,
        order_id=order_id,
        threshold=threshold,
        simulation_engine=simulation_engine,
        min_turns=min_turns,
        max_turns=max_turns,
        target_metadata=metadata,
    )
    search_space = (
        optimization_manifest.get("optimization", {})
        .get("target", {})
        .get("search_space", {})
    )
    default_environments = list(
        search_space.get("simulation.environments")
        or [optimization_manifest["simulation"]["environments"]]
    )[-1]
    environments = (
        [_browser_cua_environment(browser)]
        if browser is not None
        else copy.deepcopy(default_environments)
    )
    manifest: dict[str, Any] = {
        "version": AGENT_LEARNING_RUN_KIND,
        "name": str(name),
        "required_env": _unique_strings(required_env),
        "scenario": copy.deepcopy(optimization_manifest["scenario"]),
        "agent": copy.deepcopy(optimization_manifest["agent"]),
        "simulation": {
            "engine": str(simulation_engine),
            "modality": "cua",
            "max_turns": int(optimization_manifest["simulation"]["max_turns"]),
            "min_turns": int(min_turns),
            "auto_execute_tools": True,
            "environments": copy.deepcopy(environments),
        },
        "evaluation": copy.deepcopy(optimization_manifest["evaluation"]),
    }
    if metadata:
        manifest["metadata"] = {
            "source": "agent_learning.simulate.build_browser_cua_run_manifest",
            **copy.deepcopy(dict(metadata)),
        }
    return manifest


def build_agent_integration_run_manifest(
    *,
    name: str,
    integration: Optional[Mapping[str, Any]] = None,
    agent: Optional[Mapping[str, Any]] = None,
    scenario: Optional[Mapping[str, Any]] = None,
    evaluation_config: Optional[Mapping[str, Any]] = None,
    required_env: Sequence[str] = (),
    providers: Sequence[str] = (
        "livekit",
        "vapi",
        "retell",
        "bland",
        "elevenlabs",
        "deepgram",
        "agora",
        "pipecat",
        "twilio",
    ),
    channels: Sequence[str] = (
        "chat",
        "voice",
        "webrtc",
        "phone",
        "sip",
        "websocket",
        "media_stream",
    ),
    trace_frameworks: Sequence[str] = (
        "langchain",
        "langgraph",
        "openai_agents",
        "autogen",
        "crewai",
        "llamaindex",
        "pydantic_ai",
        "pipecat",
        "livekit",
    ),
    provider_channels: Optional[Mapping[str, Sequence[str]]] = None,
    threshold: float = 0.9,
    simulation_engine: str = "local_text",
    min_turns: int = 4,
    max_turns: Optional[int] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Build a direct provider/framework integration simulation manifest."""

    if not name:
        raise ValueError("name is required")
    if min_turns < 1:
        raise ValueError("min_turns must be >= 1")
    if max_turns is not None and max_turns < min_turns:
        raise ValueError("max_turns must be >= min_turns")

    from . import optimize as _agent_optimize

    optimization_manifest = (
        _agent_optimize.build_agent_integration_optimization_manifest(
            name=name,
            agent=agent,
            scenario=scenario,
            evaluation_config=evaluation_config,
            required_env=required_env,
            providers=providers,
            channels=channels,
            trace_frameworks=trace_frameworks,
            provider_channels=provider_channels,
            threshold=threshold,
            simulation_engine=simulation_engine,
            min_turns=min_turns,
            max_turns=max_turns,
            target_metadata=metadata,
        )
    )
    search_space = (
        optimization_manifest.get("optimization", {})
        .get("target", {})
        .get("search_space", {})
    )
    default_environments = list(
        search_space.get("simulation.environments")
        or [optimization_manifest["simulation"]["environments"]]
    )[-1]
    environments = (
        [_agent_integration_environment(integration)]
        if integration is not None
        else copy.deepcopy(default_environments)
    )
    manifest: dict[str, Any] = {
        "version": AGENT_LEARNING_RUN_KIND,
        "name": str(name),
        "required_env": _unique_strings(required_env),
        "scenario": copy.deepcopy(optimization_manifest["scenario"]),
        "agent": copy.deepcopy(optimization_manifest["agent"]),
        "simulation": {
            "engine": str(simulation_engine),
            "max_turns": int(optimization_manifest["simulation"]["max_turns"]),
            "min_turns": int(min_turns),
            "auto_execute_tools": True,
            "environments": copy.deepcopy(environments),
        },
        "evaluation": copy.deepcopy(optimization_manifest["evaluation"]),
    }
    if metadata:
        manifest["metadata"] = {
            "source": "agent_learning.simulate.build_agent_integration_run_manifest",
            **copy.deepcopy(dict(metadata)),
        }
    return manifest


def build_workspace_observability_run_manifest(
    *,
    name: str,
    workspace: Optional[Sequence[Mapping[str, Any]]] = None,
    agent: Optional[Mapping[str, Any]] = None,
    scenario: Optional[Mapping[str, Any]] = None,
    evaluation_config: Optional[Mapping[str, Any]] = None,
    required_env: Sequence[str] = (),
    repository_url: str = "https://github.com/futureagi/support-agent",
    commit_sha: str = "abc123def4567890",
    threshold: float = 0.9,
    simulation_engine: str = "local_text",
    min_turns: int = 4,
    max_turns: Optional[int] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Build a direct Future AGI workspace/observability simulation manifest."""

    if not name:
        raise ValueError("name is required")
    if not repository_url:
        raise ValueError("repository_url is required")
    if not commit_sha:
        raise ValueError("commit_sha is required")
    if min_turns < 1:
        raise ValueError("min_turns must be >= 1")
    if max_turns is not None and max_turns < min_turns:
        raise ValueError("max_turns must be >= min_turns")

    from . import optimize as _agent_optimize

    optimization_manifest = (
        _agent_optimize.build_workspace_observability_optimization_manifest(
            name=name,
            agent=agent,
            scenario=scenario,
            evaluation_config=evaluation_config,
            required_env=required_env,
            repository_url=repository_url,
            commit_sha=commit_sha,
            threshold=threshold,
            simulation_engine=simulation_engine,
            min_turns=min_turns,
            max_turns=max_turns,
            target_metadata=metadata,
        )
    )
    search_space = (
        optimization_manifest.get("optimization", {})
        .get("target", {})
        .get("search_space", {})
    )
    default_environments = list(
        search_space.get("simulation.environments")
        or [optimization_manifest["simulation"]["environments"]]
    )[-1]
    environments = (
        [_workspace_observability_environment(item) for item in workspace]
        if workspace is not None
        else copy.deepcopy(default_environments)
    )
    if not environments:
        raise ValueError("workspace must contain at least one environment")
    manifest: dict[str, Any] = {
        "version": AGENT_LEARNING_RUN_KIND,
        "name": str(name),
        "required_env": _unique_strings(required_env),
        "scenario": copy.deepcopy(optimization_manifest["scenario"]),
        "agent": copy.deepcopy(optimization_manifest["agent"]),
        "simulation": {
            "engine": str(simulation_engine),
            "max_turns": int(optimization_manifest["simulation"]["max_turns"]),
            "min_turns": int(min_turns),
            "auto_execute_tools": True,
            "environments": copy.deepcopy(environments),
        },
        "evaluation": copy.deepcopy(optimization_manifest["evaluation"]),
    }
    if metadata:
        manifest["metadata"] = {
            "source": "agent_learning.simulate.build_workspace_observability_run_manifest",
            **copy.deepcopy(dict(metadata)),
        }
    return manifest


def build_framework_run_manifest(
    *,
    name: str,
    framework: str,
    target: str,
    required_env: Sequence[str] = (),
    method: Optional[str] = None,
    input_mode: Optional[str] = None,
    modality: Optional[str] = None,
    factory: bool = True,
    trace_runtime: bool = True,
    metadata: Optional[Mapping[str, Any]] = None,
    scenario: Optional[Mapping[str, Any]] = None,
    framework_trace: Optional[Mapping[str, Any]] = None,
    max_turns: int = 1,
    min_turns: int = 1,
    evaluation_enabled: bool = False,
    output_key: Optional[str] = None,
    system_prompt: Optional[str] = None,
) -> dict[str, Any]:
    """Build a local simulation manifest for any framework adapter.

    The manifest uses the same ``agent.type=framework`` path as the CLI
    cookbooks, so known presets such as LangChain, LangGraph, LiveKit, and
    Pipecat use built-in adapter defaults while unknown frameworks can supply
    method/input-mode overrides.
    """

    if not name:
        raise ValueError("name is required")
    if not framework:
        raise ValueError("framework is required")
    if not target:
        raise ValueError("target is required")
    if min_turns < 1:
        raise ValueError("min_turns must be >= 1")
    if max_turns < min_turns:
        raise ValueError("max_turns must be >= min_turns")

    framework_key = _framework_key(framework)
    resolved_modality = str(modality or _framework_default_modality(framework_key))
    agent: dict[str, Any] = {
        "type": "framework",
        "framework": framework_key,
        "target": str(target),
        "factory": bool(factory),
        "trace_runtime": bool(trace_runtime),
        "metadata": {
            "sdk": "agent_learning.simulate.build_framework_run_manifest",
            **copy.deepcopy(dict(metadata or {})),
        },
    }
    if method:
        agent["method"] = str(method)
    if input_mode:
        agent["input_mode"] = str(input_mode)
    if output_key:
        agent["output_key"] = str(output_key)
    if system_prompt:
        agent["system_prompt"] = str(system_prompt)

    simulation: dict[str, Any] = {
        "engine": "local_text",
        "max_turns": int(max_turns),
        "min_turns": int(min_turns),
        "environments": [
            {
                "type": "framework_trace",
                "data": copy.deepcopy(
                    dict(framework_trace)
                    if framework_trace is not None
                    else _default_framework_trace(
                        framework_key,
                        method=method,
                        modality=resolved_modality,
                    )
                ),
            }
        ],
    }
    if resolved_modality != "text":
        simulation["modality"] = resolved_modality

    return {
        "version": AGENT_LEARNING_RUN_KIND,
        "name": str(name),
        "required_env": _unique_strings(required_env),
        "scenario": copy.deepcopy(
            dict(scenario)
            if scenario is not None
            else _default_framework_scenario(str(name), framework_key, resolved_modality)
        ),
        "agent": agent,
        "simulation": simulation,
        "evaluation": {"enabled": bool(evaluation_enabled)},
    }


def build_multi_framework_suite_manifest(
    *,
    name: str,
    framework_manifests: Sequence[Mapping[str, Any]],
    required_env: Sequence[str] = (),
    no_eval: bool = True,
    required_frameworks: Optional[Sequence[str]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Build a suite manifest over multiple framework run manifests.

    Each item needs ``framework`` and ``path``. Optional ``id``/``name`` values
    control the child job IDs and display names.
    """

    if not name:
        raise ValueError("name is required")
    if not framework_manifests:
        raise ValueError("framework_manifests must contain at least one item")
    jobs: list[dict[str, Any]] = []
    frameworks: list[str] = []
    for index, raw in enumerate(framework_manifests, start=1):
        item = copy.deepcopy(dict(raw))
        framework = _framework_key(str(item.get("framework") or "custom"))
        path = item.get("path")
        if path in (None, ""):
            raise ValueError(f"framework manifest {index} requires a path")
        frameworks.append(framework)
        job_id = str(item.get("id") or f"{framework}-framework")
        jobs.append(
            {
                "id": job_id,
                "command": "run",
                "path": str(path),
                "no_eval": bool(item.get("no_eval", no_eval)),
                "name": str(item.get("name") or f"{name}-{job_id}"),
            }
        )
    return {
        "version": AGENT_LEARNING_SUITE_KIND,
        "name": str(name),
        "required_env": _unique_strings(required_env),
        "required_capabilities": {
            "commands": ["run"],
            "result_kinds": [AGENT_LEARNING_RUN_KIND],
            "environment_types": ["framework_trace"],
            "environment_state_keys": ["framework_runtime"],
            "frameworks": _unique_strings(required_frameworks or frameworks),
            "metrics": [],
        },
        "jobs": jobs,
        "metadata": {
            "source": "agent_learning.simulate.build_multi_framework_suite_manifest",
            **copy.deepcopy(dict(metadata or {})),
        },
    }


def _default_task_scenario(
    name: str,
    *,
    task_description: str,
    expected_result: Optional[Any],
    persona: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "name": str(name),
        "dataset": [
            {
                "persona": copy.deepcopy(
                    dict(persona or {"name": "Task Owner", "role": "task-owner"})
                ),
                "situation": str(task_description),
                "outcome": (
                    str(expected_result)
                    if expected_result is not None
                    else "The task completes successfully."
                ),
            }
        ],
    }


def _task_run_evaluation(
    *,
    task_description: Optional[str],
    expected_result: Optional[Any],
    available_tools: Sequence[str],
    required_tools: Sequence[str],
    success_criteria: Sequence[str],
    evaluation_config: Optional[Mapping[str, Any]],
    threshold: float,
) -> dict[str, Any]:
    config = copy.deepcopy(dict(evaluation_config or {}))
    if task_description is not None:
        config.setdefault("task_description", str(task_description))
    if expected_result is not None:
        config.setdefault("expected_result", str(expected_result))
    if available_tools:
        config.setdefault("available_tools", _unique_strings(available_tools))
    if required_tools:
        config.setdefault("required_tools", _unique_strings(required_tools))
    if success_criteria:
        config.setdefault("success_criteria", _unique_strings(success_criteria))
    if not config:
        return {"enabled": False}
    return {
        "enabled": True,
        "agent_report": {
            "threshold": float(threshold),
            "config": config,
        },
    }


def _default_realtime_scenario(name: str, framework: str) -> dict[str, Any]:
    return {
        "name": str(name),
        "dataset": [
            {
                "persona": {"name": "Asha", "role": "realtime-agent-owner"},
                "situation": (
                    f"Asha needs a {framework} realtime voice session replayed "
                    "with streaming tool evidence before routing a support call."
                ),
                "outcome": (
                    "The call is routed to refund support with transcript, "
                    "timing, streaming, and TTS evidence."
                ),
            }
        ],
    }


def _default_realtime_agent() -> dict[str, Any]:
    return {
        "type": "scripted",
        "responses": [
            {
                "content": (
                    "Checking the realtime voice session before routing the call."
                ),
                "tool_calls": [
                    {"id": "voice_status", "name": "voice_status", "arguments": {}},
                    {"id": "voice_timing", "name": "voice_timing", "arguments": {}},
                    {
                        "id": "transcribe_user",
                        "name": "transcribe_audio",
                        "arguments": {"id": "utt_1"},
                    },
                    {
                        "id": "route_support",
                        "name": "route_call",
                        "arguments": {
                            "route": "support",
                            "reason": "refund support request",
                        },
                    },
                ],
            },
            {
                "content": "Checking the streaming trace before speaking the answer.",
                "tool_calls": [
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
                    {
                        "id": "speak_answer",
                        "name": "speak",
                        "arguments": {
                            "text": (
                                "Your refund request has been routed to support "
                                "with realtime evidence."
                            ),
                            "latency_ms": 260,
                            "duration_ms": 1800,
                        },
                    },
                ],
            },
        ],
    }


def _default_realtime_voice(framework: str) -> dict[str, Any]:
    return {
        "framework": framework,
        "sample_rate_hz": 16000,
        "stt_latency_ms": 140,
        "tts_latency_ms": 280,
        "utterances": [
            {
                "id": "utt_1",
                "speaker": "user",
                "transcript": "I need help with a refund on my order.",
                "start_ms": 0,
                "end_ms": 1720,
                "latency_ms": 132,
                "confidence": 0.97,
                "language": "en",
            }
        ],
        "frame_replay": [
            {
                "id": "frame_1",
                "type": "audio_frame",
                "speaker": "user",
                "timestamp_ms": 80,
                "duration_ms": 20,
                "energy": 0.74,
            },
            {
                "id": "frame_overlap",
                "type": "audio_frame",
                "speaker": "agent",
                "timestamp_ms": 900,
                "duration_ms": 20,
                "overlap": True,
                "energy": 0.42,
            },
        ],
        "timing_distribution": {
            "stage_order": ["vad", "stt", "llm", "tts"],
            "stages": {
                "vad": [24, 29, 31],
                "stt": [120, 132, 148],
                "llm": [210, 224, 241],
                "tts": [250, 260, 280],
            }
        },
        "routes": {
            "support": {"queue": "refund_support", "priority": "high"},
            "billing": {"queue": "billing"},
        },
        "initial_route": "support",
        "allow_interruptions": True,
        "noise_profile": {"snr_db": 24, "background": "office"},
    }


def _default_realtime_streaming_trace(framework: str) -> dict[str, Any]:
    return {
        "framework": framework,
        "events": [
            {
                "id": "stream_start",
                "type": "session_start",
                "role": "system",
                "content": "session opened",
                "timestamp_ms": 0,
            },
            {
                "id": "stream_token_1",
                "type": "token_delta",
                "role": "assistant",
                "content": "Your refund",
                "timestamp_ms": 120,
            },
            {
                "id": "stream_tool_delta",
                "type": "tool_delta",
                "name": "route_call",
                "role": "assistant",
                "tool_name": "route_call",
                "arguments": {"route": "support"},
                "timestamp_ms": 240,
            },
            {
                "id": "stream_end",
                "type": "message_done",
                "role": "assistant",
                "content": "Your refund request has been routed to support.",
                "timestamp_ms": 520,
            },
        ],
        "metadata": {"cookbook": "sdk-realtime-voice-simulation"},
    }


def write_manifest_file(manifest: Mapping[str, Any], path: str | Path) -> Path:
    """Write a simulation manifest as formatted JSON and return the path."""

    manifest_path = Path(path).expanduser().resolve()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(dict(manifest), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return manifest_path


async def run_local_text_manifest(
    manifest: Mapping[str, Any],
    manifest_path: str | Path,
) -> Any:
    return await _manifest().run_local_text_manifest(manifest, manifest_path)


def evaluate_manifest_report(manifest: Mapping[str, Any], report: Any) -> Any:
    return _manifest().evaluate_manifest_report(manifest, report)


async def run_manifest_file(
    path: str | Path,
    *,
    options: Optional[Any] = None,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    no_eval: Optional[bool] = None,
    dry_run: Optional[bool] = None,
) -> dict[str, Any]:
    return await _manifest().run_manifest_file(
        path,
        options=options,
        name=name,
        threshold=threshold,
        no_eval=no_eval,
        dry_run=dry_run,
    )


async def run_manifest(
    manifest: Mapping[str, Any],
    *,
    manifest_path: str | Path = ".",
    options: Optional[Any] = None,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    no_eval: Optional[bool] = None,
    dry_run: Optional[bool] = None,
) -> dict[str, Any]:
    return await _manifest().run_manifest(
        manifest,
        manifest_path=manifest_path,
        options=options,
        name=name,
        threshold=threshold,
        no_eval=no_eval,
        dry_run=dry_run,
    )


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


def render_junit(result: Mapping[str, Any]) -> str:
    return _manifest().render_junit(result)


def render_sarif(
    result: Mapping[str, Any],
    *,
    manifest_path: str | Path = ".",
) -> str:
    return _manifest().render_sarif(result, manifest_path=manifest_path)


def render_markdown(
    result: Mapping[str, Any],
    *,
    source_path: str | Path = ".",
) -> str:
    return _manifest().render_markdown(result, source_path=source_path)


def create_baseline_file(path: str | Path, *, name: Optional[str] = None) -> dict[str, Any]:
    return _manifest().create_baseline_file(path, name=name)


def create_baseline(
    source: Mapping[str, Any],
    *,
    source_path: str | Path = ".",
    name: Optional[str] = None,
) -> dict[str, Any]:
    return _manifest().create_baseline(
        source,
        source_path=source_path,
        name=name,
    )


def compare_result_files(
    baseline_path: str | Path,
    current_path: str | Path,
    *,
    min_score_delta: float = 0.0,
    max_new_findings: int = 0,
    max_new_error_findings: int = 0,
    min_metric_delta: Optional[float] = None,
    name: Optional[str] = None,
) -> dict[str, Any]:
    return _manifest().compare_result_files(
        baseline_path,
        current_path,
        min_score_delta=min_score_delta,
        max_new_findings=max_new_findings,
        max_new_error_findings=max_new_error_findings,
        min_metric_delta=min_metric_delta,
        name=name,
    )


def compare_results(
    baseline: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    baseline_path: str | Path = "baseline.json",
    current_path: str | Path = "current.json",
    min_score_delta: float = 0.0,
    max_new_findings: int = 0,
    max_new_error_findings: int = 0,
    min_metric_delta: Optional[float] = None,
    name: Optional[str] = None,
) -> dict[str, Any]:
    return _manifest().compare_results(
        baseline,
        current,
        baseline_path=baseline_path,
        current_path=current_path,
        min_score_delta=min_score_delta,
        max_new_findings=max_new_findings,
        max_new_error_findings=max_new_error_findings,
        min_metric_delta=min_metric_delta,
        name=name,
    )


def render_report_file(path: str | Path, *, name: Optional[str] = None) -> dict[str, Any]:
    return _manifest().render_report_file(path, name=name)


def render_report(
    source: Mapping[str, Any],
    *,
    source_path: str | Path = ".",
    name: Optional[str] = None,
) -> dict[str, Any]:
    return _manifest().render_report(source, source_path=source_path, name=name)


def promote_to_regression_file(
    path: str | Path,
    *,
    name: Optional[str] = None,
    min_level: str = "warning",
    max_findings: int = 25,
    required_env: Sequence[str] = (),
) -> dict[str, Any]:
    return _manifest().promote_to_regression_file(
        path,
        name=name,
        min_level=min_level,
        max_findings=max_findings,
        required_env=required_env,
    )


def promote_to_regression(
    source: Mapping[str, Any],
    *,
    source_path: str | Path = ".",
    name: Optional[str] = None,
    min_level: str = "warning",
    max_findings: int = 25,
    required_env: Sequence[str] = (),
) -> dict[str, Any]:
    return _manifest().promote_to_regression(
        source,
        source_path=source_path,
        name=name,
        min_level=min_level,
        max_findings=max_findings,
        required_env=required_env,
    )


def replay_manifests(
    manifests: Sequence[str | Path],
    *,
    name: Optional[str] = None,
    dry_run: bool = False,
    fail_fast: bool = False,
) -> dict[str, Any]:
    return _manifest().replay_manifests(
        manifests,
        name=name,
        dry_run=dry_run,
        fail_fast=fail_fast,
    )


def load_eval_suite_file(path: str | Path) -> dict[str, Any]:
    return _suite().load_eval_suite_file(path)


def build_eval_suite_manifest(
    *,
    name: str,
    providers: Optional[Sequence[Mapping[str, Any]]] = None,
    prompts: Optional[Sequence[Mapping[str, Any]]] = None,
    tests: Optional[Sequence[Mapping[str, Any]]] = None,
    threshold: float = 1.0,
    outputs: Optional[Mapping[str, Any]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    version: str = "agent-learning.eval.v1",
) -> dict[str, Any]:
    return _suite().build_eval_suite_manifest(
        name=name,
        providers=providers,
        prompts=prompts,
        tests=tests,
        threshold=threshold,
        outputs=outputs,
        metadata=metadata,
        version=version,
    )


def write_eval_suite_file(suite: Mapping[str, Any], path: str | Path) -> Path:
    return _suite().write_eval_suite_file(suite, path)


def run_eval_suite_file(
    path: str | Path,
    *,
    options: Optional[Any] = None,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    dry_run: Optional[bool] = None,
) -> dict[str, Any]:
    return _suite().run_eval_suite_file(
        path,
        options=options,
        name=name,
        threshold=threshold,
        dry_run=dry_run,
    )


def run_eval_suite(
    suite: Mapping[str, Any],
    *,
    suite_path: str | Path = ".",
    options: Optional[Any] = None,
) -> dict[str, Any]:
    return _suite().run_eval_suite(suite, suite_path=suite_path, options=options)


def public_result(result: Mapping[str, Any]) -> dict[str, Any]:
    return _manifest().public_result(result)


def wrap_agent(*args: Any, **kwargs: Any) -> Any:
    return _simulate().wrap_agent(*args, **kwargs)


def wrap_framework(*args: Any, **kwargs: Any) -> Any:
    return _simulate().wrap_framework(*args, **kwargs)


def _default_framework_scenario(
    name: str,
    framework: str,
    modality: str,
) -> dict[str, Any]:
    role = "voice-agent-owner" if modality == "voice" else "framework-owner"
    return {
        "name": name,
        "dataset": [
            {
                "persona": {
                    "name": "Maya",
                    "role": role,
                },
                "situation": (
                    f"Maya needs a {framework} agent simulated through the "
                    "generic Agent Learning framework adapter."
                ),
                "outcome": (
                    f"The {framework} adapter completes with framework runtime "
                    "trace evidence."
                ),
            }
        ],
    }


def _default_framework_trace(
    framework: str,
    *,
    method: Optional[str],
    modality: str,
) -> dict[str, Any]:
    resolved_method = method or _framework_default_method(framework)
    signals = ["voice", "tool"] if modality == "voice" else ["model", "tool"]
    if framework == "langgraph":
        signals = ["graph", "tool", "state"]
    elif framework == "pipecat":
        signals = ["voice", "frame", "tool"]
    elif framework == "livekit":
        signals = ["voice", "room", "tool"]
    elif framework not in _known_frameworks():
        signals = ["planner", "tool", "policy"]
    return {
        "framework": framework,
        "spans": [
            {
                "id": f"{framework}_adapter",
                "name": f"{framework}.{resolved_method}",
                "input": "agent learning framework simulation",
                "output": "completed",
                "tool_calls": [{"name": "framework_trace_status"}],
                "signals": signals,
            }
        ],
        "adapter_required_signals": signals,
        "adapter_required_mappings": {"tool": ["tool_name"]},
    }


def _framework_default_method(framework: str) -> str:
    defaults = {
        "langchain": "ainvoke",
        "langgraph": "ainvoke",
        "llamaindex": "achat",
        "crewai": "kickoff",
        "autogen": "run",
        "openai_agents": "run",
        "livekit": "respond",
        "pipecat": "process",
    }
    return defaults.get(framework, "run")


def _browser_cua_environment(item: Mapping[str, Any]) -> dict[str, Any]:
    copied = copy.deepcopy(dict(item))
    if copied.get("type") in {"browser", "browser_cua", "cua", "computer_use"}:
        copied.setdefault("data", {})
        return copied
    if copied.get("browser_cua") is not None:
        return {"type": "browser_cua", "data": copied["browser_cua"]}
    if copied.get("browser") is not None:
        return {"type": "browser", "data": copied["browser"]}
    if copied.get("mutation_pack") is not None or copied.get("prompt_injections") is not None:
        return {"type": "browser_cua", "data": copied}
    return {"type": "browser", "data": copied}


def _agent_integration_environment(item: Mapping[str, Any]) -> dict[str, Any]:
    copied = copy.deepcopy(dict(item))
    if copied.get("type") == "agent_integration":
        copied.setdefault("data", {})
        return copied
    if copied.get("agent_integration") is not None:
        return {"type": "agent_integration", "data": copied["agent_integration"]}
    return {"type": "agent_integration", "data": copied}


def _workspace_observability_environment(item: Mapping[str, Any]) -> dict[str, Any]:
    copied = copy.deepcopy(dict(item))
    if copied.get("type") in {"workspace_run_manifest", "observability_replay"}:
        copied.setdefault("data", {})
        return copied
    if copied.get("workspace_run") is not None:
        return {"type": "workspace_run_manifest", "data": copied["workspace_run"]}
    if copied.get("observability_replay") is not None:
        return {"type": "observability_replay", "data": copied["observability_replay"]}
    if copied.get("cases") is not None:
        return {"type": "observability_replay", "data": copied}
    return {"type": "workspace_run_manifest", "data": copied}


def _framework_default_modality(framework: str) -> str:
    if framework in {
        "livekit",
        "pipecat",
        "vapi",
        "retell",
        "elevenlabs",
        "deepgram",
        "agora",
        "twilio",
    }:
        return "voice"
    if framework in {"computer_use", "browser_use", "playwright"}:
        return "cua"
    if framework == "vision_agent":
        return "image"
    return "text"


def _known_frameworks() -> set[str]:
    try:
        return set(_simulate().supported_frameworks())
    except Exception:
        return set()


def _framework_key(framework: str) -> str:
    return str(framework or "custom").strip().lower().replace("-", "_").replace(" ", "_")


def _unique_strings(values: Sequence[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def __getattr__(name: str) -> Any:
    module_name = _SIMULATE_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module `agent_learning.simulate` has no attribute `{name}`")
    return getattr(optional_module(module_name, _SIMULATE_EXTRA), name)


def __dir__() -> list[str]:
    return sorted(set(__all__))


__all__ = [
    *_SIMULATE_EXPORTS,
    "AGENT_LEARNING_RUN_KIND",
    "AGENT_LEARNING_SUITE_KIND",
    "apply_manifest_env",
    "build_agent_integration_run_manifest",
    "build_eval_suite_manifest",
    "build_browser_cua_run_manifest",
    "build_framework_run_manifest",
    "build_manifest_agent_callback",
    "build_manifest_environments",
    "build_multi_framework_suite_manifest",
    "build_realtime_run_manifest",
    "build_task_run_manifest",
    "build_workspace_observability_run_manifest",
    "compare_result_files",
    "compare_results",
    "create_baseline",
    "create_baseline_file",
    "detect_manifest_command",
    "evaluate_manifest_report",
    "load_eval_suite_file",
    "load_manifest",
    "load_manifest_file",
    "missing_manifest_env",
    "optimize_manifest_file",
    "promote_to_regression",
    "promote_to_regression_file",
    "public_result",
    "render_junit",
    "render_markdown",
    "render_report",
    "render_report_file",
    "render_sarif",
    "replay_manifests",
    "required_manifest_env",
    "run_eval_suite",
    "run_eval_suite_file",
    "run_local_text_manifest",
    "run_manifest",
    "run_manifest_file",
    "supported_manifest_environment_types",
    "validate_manifest_env",
    "write_eval_suite_file",
    "write_manifest_file",
]
