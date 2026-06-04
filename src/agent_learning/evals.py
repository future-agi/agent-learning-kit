from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from ._facade import optional_module

_EVAL_EXTRA = "evaluation"

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


__all__ = [
    *_EVAL_EXPORTS,
    "evaluate",
    "evaluate_agent_report",
    "load_eval_suite_file",
    "optimize_eval_suite_file",
    "run_eval_suite",
    "run_eval_suite_file",
]
