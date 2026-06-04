from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any, Mapping, Optional

from ._facade import optional_module

_EVAL_EXTRA = "evaluation"
AGENT_LEARNING_ARTIFACT_EVALUATION_KIND = "agent-learning.artifact-evaluation.v1"

_FI_EVAL_EXPORT_NAMES = (
    "ASRAccuracy",
    "AnswerRefusal",
    "AudioQualityEvaluator",
    "AudioTranscriptionEvaluator",
    "BaseEvaluation",
    "BatchResult",
    "BiasDetection",
    "BleuScore",
    "CaptionHallucination",
    "ChunkAttribution",
    "ChunkResult",
    "ChunkUtilization",
    "ClinicallyInappropriateTone",
    "Completeness",
    "ContainsCode",
    "ContainsValidLink",
    "ContentModeration",
    "ContentSafety",
    "ContextAdherence",
    "ContextRelevance",
    "ConversationCoherence",
    "ConversationResolution",
    "CulturalSensitivity",
    "CustomerAgentClarificationSeeking",
    "CustomerAgentContextRetention",
    "CustomerAgentConversationQuality",
    "CustomerAgentHumanEscalation",
    "CustomerAgentInterruptionHandling",
    "CustomerAgentLanguageHandling",
    "CustomerAgentLoopDetection",
    "CustomerAgentObjectionHandling",
    "CustomerAgentPromptConformance",
    "CustomerAgentQueryHandling",
    "CustomerAgentTerminationHandling",
    "DataPrivacyCompliance",
    "DetectHallucination",
    "DetectHallucinationMissingInfo",
    "EarlyStopPolicy",
    "EarlyStopReason",
    "EvalBuilder",
    "EvalResult",
    "EvalTemplate",
    "EvalTemplateManager",
    "EvaluateFunctionCalling",
    "Evaluator",
    "Execution",
    "ExecutionError",
    "ExecutionMode",
    "FactualAccuracy",
    "FrameworkEvaluator",
    "FuzzyMatch",
    "GroundTruthMatch",
    "Groundedness",
    "ImageInstructionAdherence",
    "IsCompliant",
    "IsConcise",
    "IsEmail",
    "IsFactuallyConsistent",
    "IsGoodSummary",
    "IsHarmfulAdvice",
    "IsHelpful",
    "IsInformalTone",
    "IsJson",
    "IsPolite",
    "LLMFunctionCalling",
    "NoAgeBias",
    "NoApologies",
    "NoGenderBias",
    "NoHarmfulTherapeuticGuidance",
    "NoLLMReference",
    "NoOpenAIReference",
    "NoRacialBias",
    "OCREvaluation",
    "OneLine",
    "PII",
    "PromptAdherence",
    "PromptInjection",
    "PromptInstructionAdherence",
    "Protect",
    "ProtectFlash",
    "Ranking",
    "Sexist",
    "StreamingConfig",
    "StreamingEvalResult",
    "StreamingEvaluator",
    "StreamingState",
    "SummaryQuality",
    "SyntheticImageEvaluator",
    "TTSAccuracy",
    "TaskCompletion",
    "TextToSQL",
    "Tone",
    "Toxicity",
    "TranslationAccuracy",
    "Turing",
    "async_evaluator",
    "blocking_evaluator",
    "custom_eval",
    "distributed_evaluator",
    "evaluate",
    "list_evaluations",
    "protect",
    "register_current_span",
    "register_evaluation",
    "resilient_evaluator",
    "simple_eval",
)

_AUTOEVAL_EXPORT_NAMES = (
    "AppCategory",
    "RiskLevel",
    "DomainSensitivity",
    "AppRequirement",
    "AppAnalysis",
    "AutoEvalResult",
    "EvalConfig",
    "ScannerConfig",
    "AutoEvalConfig",
    "AutoEvalPipeline",
    "register_eval_class",
    "register_scanner_class",
    "get_template",
    "list_templates",
    "get_template_names",
    "TEMPLATES",
    "AppAnalyzer",
    "EvalRecommender",
    "RuleBasedAnalyzer",
    "export_yaml",
    "export_json",
    "load_yaml",
    "load_json",
    "load_config",
    "to_yaml_string",
    "to_json_string",
    "from_yaml_string",
    "from_json_string",
    "InteractiveConfigurator",
    "InteractiveSession",
    "ClarificationQuestion",
)

_LOCAL_EVAL_EXPORT_NAMES = (
    "RoutingMode",
    "LOCAL_CAPABLE_METRICS",
    "can_run_locally",
    "select_routing_mode",
    "LocalMetricRegistry",
    "get_registry",
    "LocalEvaluator",
    "LocalEvaluatorConfig",
    "LocalEvaluationResult",
    "HybridEvaluator",
    "LocalLLMConfig",
    "OllamaLLM",
    "LocalLLMFactory",
)

_EVAL_EXPORTS = {name: "fi.evals" for name in _FI_EVAL_EXPORT_NAMES}
_EVAL_EXPORTS.update({name: "fi.evals.autoeval" for name in _AUTOEVAL_EXPORT_NAMES})
_EVAL_EXPORTS.update({name: "fi.evals.local" for name in _LOCAL_EVAL_EXPORT_NAMES})
_EVAL_EXPORTS["AgentReportEvaluator"] = "fi.evals.metrics.agents"


def _evals() -> Any:
    return optional_module("fi.evals", _EVAL_EXTRA)


def _agent_metrics() -> Any:
    return optional_module("fi.evals.metrics.agents", _EVAL_EXTRA)


def _suite() -> Any:
    return optional_module("fi.simulate.suite", "simulate")


def evaluate(*args: Any, **kwargs: Any) -> Any:
    return _evals().evaluate(*args, **kwargs)


def evaluate_agent_report(
    report: Any,
    config: Optional[Mapping[str, Any]] = None,
    *,
    threshold: float = 0.7,
) -> Any:
    return _agent_metrics().evaluate_agent_report(
        report,
        config=config,
        threshold=threshold,
    )


def load_artifact_file(path: str | Path) -> dict[str, Any]:
    artifact_path = Path(path).expanduser().resolve()
    artifact = _load_json_or_yaml(artifact_path)
    if not isinstance(artifact, Mapping):
        raise ValueError("artifact root must be an object")
    return dict(artifact)


def evaluate_artifact(
    artifact: Mapping[str, Any],
    config: Optional[Mapping[str, Any]] = None,
    *,
    threshold: float = 0.7,
    name: Optional[str] = None,
    source_path: str | Path = ".",
) -> dict[str, Any]:
    started = time.time()
    report, report_source = _artifact_report(artifact)
    evaluation = evaluate_agent_report(report, config=config, threshold=threshold)
    evaluation_payload = _plain(evaluation)
    cases = list(evaluation_payload.get("cases") or [])
    score = float(evaluation_payload.get("score") or 0.0)
    passed = bool(evaluation_payload.get("passed"))
    findings = list(evaluation_payload.get("findings") or [])
    source_path = Path(source_path).expanduser().resolve()
    return {
        "schema_version": AGENT_LEARNING_ARTIFACT_EVALUATION_KIND,
        "kind": AGENT_LEARNING_ARTIFACT_EVALUATION_KIND,
        "name": str(name or artifact.get("name") or source_path.stem),
        "status": "passed" if passed else "failed",
        "exit_code": 0 if passed else 1,
        "summary": {
            "score": round(score, 4),
            "threshold": threshold,
            "case_count": len(cases),
            "passed_case_count": sum(1 for case in cases if _as_mapping(case).get("passed")),
            "failed_case_count": sum(1 for case in cases if not _as_mapping(case).get("passed")),
            "finding_count": len(findings),
            "source_kind": artifact.get("kind"),
            "source_status": artifact.get("status"),
            "source_exit_code": artifact.get("exit_code"),
            "report_source": report_source,
            "metric_averages": dict(
                _as_mapping(evaluation_payload.get("summary")).get("metric_averages")
                or {}
            ),
        },
        "source": {
            "path": str(source_path),
            "kind": artifact.get("kind"),
            "name": artifact.get("name"),
            "status": artifact.get("status"),
            "exit_code": artifact.get("exit_code"),
            "report_source": report_source,
        },
        "evaluation": evaluation_payload,
        "findings": findings,
        "duration_seconds": round(time.time() - started, 4),
    }


def evaluate_artifact_file(
    path: str | Path,
    config: Optional[Mapping[str, Any]] = None,
    *,
    threshold: float = 0.7,
    name: Optional[str] = None,
) -> dict[str, Any]:
    artifact_path = Path(path).expanduser().resolve()
    artifact = load_artifact_file(artifact_path)
    return evaluate_artifact(
        artifact,
        config=config,
        threshold=threshold,
        name=name,
        source_path=artifact_path,
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


def __getattr__(name: str) -> Any:
    module_name = _EVAL_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module `agent_learning.evals` has no attribute `{name}`")
    return getattr(optional_module(module_name, _EVAL_EXTRA), name)


def __dir__() -> list[str]:
    return sorted(set(__all__))


def _artifact_report(artifact: Mapping[str, Any]) -> tuple[Any, str]:
    report = artifact.get("report")
    if isinstance(report, Mapping) and report.get("results") is not None:
        return dict(report), "report"
    if artifact.get("results") is not None:
        return dict(artifact), "root"

    optimization = _as_mapping(artifact.get("optimization"))
    history = [
        _as_mapping(item)
        for item in _as_list(optimization.get("history"))
        if isinstance(item, Mapping)
    ]
    history_with_report = [
        item
        for item in history
        if isinstance(item.get("report"), Mapping)
        and _as_mapping(item.get("report")).get("results") is not None
    ]
    if history_with_report:
        best = max(
            history_with_report,
            key=lambda item: float(item.get("score") or item.get("evaluation_score") or 0.0),
        )
        return dict(best["report"]), "optimization.history.best.report"
    raise ValueError(
        "artifact does not contain a report; expected `report.results`, "
        "`results`, or `optimization.history[*].report`"
    )


def _load_json_or_yaml(path: Path) -> Any:
    if not path.exists():
        raise ValueError(f"artifact file not found: {path}")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency clarity
            raise ValueError("YAML artifacts require PyYAML; use JSON or install PyYAML.") from exc
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


__all__ = [
    *_EVAL_EXPORTS,
    "AGENT_LEARNING_ARTIFACT_EVALUATION_KIND",
    "evaluate",
    "evaluate_agent_report",
    "evaluate_artifact",
    "evaluate_artifact_file",
    "load_artifact_file",
    "load_eval_suite_file",
    "optimize_eval_suite_file",
    "run_eval_suite",
    "run_eval_suite_file",
]
