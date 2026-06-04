from __future__ import annotations

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
    "build_manifest_agent_callback",
    "build_manifest_environments",
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


def __getattr__(name: str) -> Any:
    module_name = _SIMULATE_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module `agent_learning.simulate` has no attribute `{name}`")
    return getattr(optional_module(module_name, _SIMULATE_EXTRA), name)


def __dir__() -> list[str]:
    return sorted(set(__all__))


__all__ = [
    *_SIMULATE_EXPORTS,
    "apply_manifest_env",
    "build_manifest_agent_callback",
    "build_manifest_environments",
    "compare_result_files",
    "create_baseline_file",
    "detect_manifest_command",
    "evaluate_manifest_report",
    "load_eval_suite_file",
    "load_manifest",
    "load_manifest_file",
    "missing_manifest_env",
    "optimize_manifest_file",
    "public_result",
    "render_junit",
    "render_markdown",
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
]
