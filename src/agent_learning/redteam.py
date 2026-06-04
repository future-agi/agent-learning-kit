from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping, Optional

from ._facade import optional_module

AGENT_LEARNING_REDTEAM_KIND = "agent-learning.redteam.v1"
_SIMULATE_EXTRA = "simulate"
_REDTEAM_EXTRA = "trinity"

_SIMULATE_REDTEAM_EXPORT_NAMES = (
    "AdversarialEnvironmentPack",
    "AgentControlPlaneEnvironment",
    "AgentTrustBoundaryEnvironment",
    "AutonomyLoopEnvironment",
    "BrowserEnvironment",
    "RedTeamCampaignEnvironment",
    "RedTeamReadinessEnvironment",
    "WorkspaceRunEnvironment",
    "WorldAttackReplayEnvironment",
    "load_adversarial_attack_pack",
    "load_red_team_campaign_manifest",
    "load_red_team_readiness_manifest",
    "load_world_attack_replay",
    "normalize_adversarial_attack_pack",
    "normalize_red_team_campaign_manifest",
    "normalize_red_team_readiness_manifest",
    "normalize_world_attack_replay",
)

_GUARDRAILS_EXPORT_NAMES = (
    "Guardrails",
    "GuardrailsConfig",
    "GuardrailModel",
    "RailType",
    "AggregationStrategy",
    "SafetyCategory",
    "ScannerConfig",
    "TopicConfig",
    "LanguageConfig",
    "RegexPatternConfig",
    "GuardrailResult",
    "GuardrailsResponse",
    "GuardrailsGateway",
    "ScreeningSession",
    "AsyncScreeningSession",
)

_SCANNER_EXPORT_NAMES = (
    "ScanResult",
    "ScannerAction",
    "PipelineResult",
    "ScannerPipeline",
    "create_default_pipeline",
    "JailbreakScanner",
    "CodeInjectionScanner",
    "SecretsScanner",
    "MaliciousURLScanner",
    "InvisibleCharScanner",
    "LanguageScanner",
    "TopicRestrictionScanner",
    "RegexScanner",
    "RegexPattern",
    "COMMON_PATTERNS",
    "EvalDelegateScanner",
    "PIIScanner",
    "ToxicityScanner",
    "BiasScanner",
    "SafetyScanner",
    "ContentModerationScanner",
    "PromptInjectionScanner",
)

_CODE_SECURITY_EXPORT_NAMES = (
    "__version__",
    "Severity",
    "EvaluationMode",
    "VulnerabilityCategory",
    "CodeLocation",
    "SecurityFinding",
    "FunctionalTestCase",
    "TestCase",
    "CodeSecurityInput",
    "CodeSecurityOutput",
    "CWE_CATEGORIES",
    "CWE_METADATA",
    "SEVERITY_WEIGHTS",
    "get_cwe_metadata",
    "get_cwe_severity",
    "get_cwe_category",
    "Finding",
    "Location",
    "Input",
    "Output",
    "CodeAnalyzer",
    "AnalysisResult",
    "FunctionInfo",
    "ImportInfo",
    "StringLiteral",
    "PythonAnalyzer",
    "JavaScriptAnalyzer",
    "JavaAnalyzer",
    "GoAnalyzer",
    "BaseDetector",
    "PatternBasedDetector",
    "CompositeDetector",
    "register_detector",
    "get_detector",
    "list_detectors",
    "get_all_detectors",
    "get_detectors_by_category",
    "get_detectors_by_cwe",
    "CodeSecurityScore",
    "QuickSecurityCheck",
    "InjectionSecurityScore",
    "CryptographySecurityScore",
    "SecretsSecurityScore",
    "SerializationSecurityScore",
    "JointSecurityMetrics",
    "JointMetricsResult",
    "FunctionalTestResult",
    "compute_func_at_k",
    "compute_sec_at_k",
    "compute_func_sec_at_k",
    "InstructModeEvaluator",
    "AutocompleteModeEvaluator",
    "RepairModeEvaluator",
    "AdversarialModeEvaluator",
    "InstructModeResult",
    "AutocompleteModeResult",
    "RepairModeResult",
    "AdversarialModeResult",
    "BaseJudge",
    "JudgeResult",
    "JudgeFinding",
    "ConsensusMode",
    "PatternJudge",
    "PatternRule",
    "LLMJudge",
    "MockLLMJudge",
    "DualJudge",
    "SecurityBenchmark",
    "InstructTest",
    "AutocompleteTest",
    "RepairTest",
    "BenchmarkResult",
    "CWEBreakdown",
    "load_benchmark",
    "list_available_benchmarks",
    "PYTHON_INSTRUCT_TESTS",
    "PYTHON_AUTOCOMPLETE_TESTS",
    "PYTHON_REPAIR_TESTS",
    "SecurityLeaderboard",
    "ModelEntry",
    "LeaderboardReport",
    "CWEComparison",
    "LanguageComparison",
    "ReportGenerator",
    "generate_security_report",
)

_AGENT_SECURITY_EXPORT_NAMES = (
    "ActionSafety",
    "AgentReportEvaluator",
    "ToolSelectionAccuracy",
    "evaluate_agent_report",
)

_REDTEAM_EXPORTS = {
    **{name: "fi.simulate" for name in _SIMULATE_REDTEAM_EXPORT_NAMES},
    **{name: "fi.evals.guardrails" for name in _GUARDRAILS_EXPORT_NAMES},
    **{name: "fi.evals.guardrails.scanners" for name in _SCANNER_EXPORT_NAMES},
    **{name: "fi.evals.metrics.code_security" for name in _CODE_SECURITY_EXPORT_NAMES},
    **{name: "fi.evals.metrics.agents" for name in _AGENT_SECURITY_EXPORT_NAMES},
}


def _manifest() -> Any:
    return optional_module("fi.simulate.manifest", _SIMULATE_EXTRA)


def load_manifest_file(path: str | Path) -> dict[str, Any]:
    return _manifest().load_manifest_file(path)


load_manifest = load_manifest_file


def prepare_redteam_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return _manifest().prepare_redteam_manifest(manifest)


async def redteam_manifest_file(
    path: str | Path,
    *,
    options: Optional[Any] = None,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    dry_run: Optional[bool] = None,
) -> dict[str, Any]:
    payload = await _manifest().redteam_manifest_file(
        path,
        options=options,
        name=name,
        threshold=threshold,
        dry_run=dry_run,
    )
    return _public_redteam_payload(payload)


run_redteam_manifest_file = redteam_manifest_file


async def redteam_manifest(
    manifest: Mapping[str, Any],
    *,
    manifest_path: str | Path = ".",
    options: Optional[Any] = None,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    dry_run: Optional[bool] = None,
) -> dict[str, Any]:
    payload = await _manifest().redteam_manifest(
        manifest,
        manifest_path=manifest_path,
        options=options,
        name=name,
        threshold=threshold,
        dry_run=dry_run,
    )
    return _public_redteam_payload(payload)


run_redteam_manifest = redteam_manifest


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


def required_manifest_env(manifest: Mapping[str, Any]) -> list[str]:
    return _manifest().required_manifest_env(manifest)


def missing_manifest_env(manifest: Mapping[str, Any]) -> list[str]:
    return _manifest().missing_manifest_env(manifest)


def validate_manifest_env(manifest: Mapping[str, Any]) -> None:
    _manifest().validate_manifest_env(manifest)


def __getattr__(name: str) -> Any:
    module_name = _REDTEAM_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module `agent_learning.redteam` has no attribute `{name}`")
    return getattr(optional_module(module_name, _REDTEAM_EXTRA), name)


def __dir__() -> list[str]:
    return sorted(set(__all__))


def _public_redteam_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(payload))
    result["kind"] = AGENT_LEARNING_REDTEAM_KIND
    return result


__all__ = [
    *_REDTEAM_EXPORTS,
    "AGENT_LEARNING_REDTEAM_KIND",
    "load_manifest",
    "load_manifest_file",
    "missing_manifest_env",
    "prepare_redteam_manifest",
    "redteam_manifest",
    "redteam_manifest_file",
    "render_junit",
    "render_markdown",
    "render_sarif",
    "required_manifest_env",
    "run_redteam_manifest",
    "run_redteam_manifest_file",
    "validate_manifest_env",
]
