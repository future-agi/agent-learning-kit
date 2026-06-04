from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ._facade import optional_module

_SIMULATE_EXTRA = "simulate"

_SIMULATE_EXPORTS = {
    "AgentInput": "fi.simulate",
    "AgentMemoryLineageEnvironment": "fi.simulate",
    "AgentResponse": "fi.simulate",
    "AgentWrapper": "fi.simulate",
    "EnvironmentAdapter": "fi.simulate",
    "FrameworkAdapterSpec": "fi.simulate",
    "FrameworkTraceEnvironment": "fi.simulate",
    "LocalTextEngine": "fi.simulate.simulation.engines",
    "MultiAgentRoomEnvironment": "fi.simulate",
    "OrchestrationTraceEnvironment": "fi.simulate",
    "Persona": "fi.simulate",
    "RetrievalMemoryEnvironment": "fi.simulate",
    "Scenario": "fi.simulate",
    "ScriptedAgentWrapper": "fi.simulate",
    "TestReport": "fi.simulate",
    "TestRunner": "fi.simulate",
    "ToolMockEnvironment": "fi.simulate",
    "VoiceEnvironment": "fi.simulate",
    "WorldContractEnvironment": "fi.simulate",
    "WorldOrchestrationReplayEnvironment": "fi.simulate",
    "normalize_framework_trace_events": "fi.simulate",
    "normalize_mcp_tool_session_export": "fi.simulate",
    "supported_frameworks": "fi.simulate",
    "wrap_agent": "fi.simulate",
    "wrap_framework": "fi.simulate",
}


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
