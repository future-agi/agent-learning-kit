from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any, Mapping, Optional, Sequence

from ._facade import optional_module
from ._module_alias import install_lazy_module_aliases
from ._schema import public_payload

_EVAL_EXTRA = "evaluation"
AGENT_LEARNING_EVAL_KIND = "agent-learning.eval.v1"
AGENT_LEARNING_EVAL_OPTIMIZATION_KIND = "agent-learning.eval-optimization.v1"
AGENT_LEARNING_ARTIFACT_EVALUATION_KIND = "agent-learning.artifact-evaluation.v1"
AGENT_LEARNING_TASK_EVIDENCE_KIND = "agent-learning.task-evidence.v1"

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

_STREAMING_EXPORT_NAMES = (
    "ChunkResult",
    "EarlyStopCondition",
    "EarlyStopReason",
    "StreamingConfig",
    "StreamingEvalResult",
    "StreamingState",
    "BufferState",
    "ChunkBuffer",
    "EarlyStopPolicy",
    "PolicyState",
    "EvalSpec",
    "StreamingEvaluator",
    "toxicity_scorer",
    "safety_scorer",
    "pii_scorer",
    "jailbreak_scorer",
    "coherence_scorer",
    "quality_scorer",
    "safety_composite_scorer",
    "quality_composite_scorer",
    "create_keyword_scorer",
    "create_pattern_scorer",
    "CompositeScorer",
)

_METRIC_EXPORT_NAMES = (
    "AggregatedMetric",
    "BLEUScore",
    "ROUGEScore",
    "LevenshteinSimilarity",
    "EmbeddingSimilarity",
    "NumericSimilarity",
    "SemanticListContains",
    "RecallScore",
    "Regex",
    "Contains",
    "ContainsAny",
    "ContainsAll",
    "ContainsNone",
    "Equals",
    "StartsWith",
    "EndsWith",
    "LengthLessThan",
    "LengthGreaterThan",
    "LengthBetween",
    "ContainsEmail",
    "ContainsLink",
    "JsonSchema",
    "ContainsJson",
    "CustomLLMJudge",
)

_AGENT_METRIC_EXPORT_NAMES = (
    "AgentReportEvalConfig",
    "AgentReportMetricResult",
    "AgentReportCaseResult",
    "AgentReportEvaluation",
    "AgentTrajectoryInput",
    "AgentStep",
    "ToolCall",
    "TaskDefinition",
    "TrajectoryAnalysis",
    "StepEfficiency",
    "ToolSelectionAccuracy",
    "TrajectoryScore",
    "GoalProgress",
    "ActionSafety",
    "ReasoningQuality",
    "analyze_domain_package_registry_coverage",
    "diff_domain_package_registries",
    "generate_domain_package_registry_fixtures",
    "generate_domain_package_registry_mutation_pack",
    "normalize_agent_report",
    "replay_domain_package_registry",
    "select_domain_package_registry_replay_pack",
    "validate_domain_package_registry",
)

_RAG_METRIC_EXPORT_NAMES = (
    "RAGInput",
    "RAGRetrievalInput",
    "RAGRankingInput",
    "ContextRecall",
    "ContextPrecision",
    "ContextEntityRecall",
    "NoiseSensitivity",
    "NDCG",
    "MRR",
    "AnswerRelevancy",
    "ContextUtilization",
    "RAGFaithfulness",
    "MultiHopReasoning",
    "SourceAttribution",
    "RAGScore",
    "RAGScoreDetailed",
)

_STRUCTURED_METRIC_EXPORT_NAMES = (
    "ValidationMode",
    "JSONInput",
    "PydanticInput",
    "YAMLInput",
    "StructuredInput",
    "ValidationError",
    "ValidationResult",
    "JSONValidator",
    "PydanticValidator",
    "YAMLValidator",
    "JSONValidation",
    "JSONSyntaxOnly",
    "SchemaCompliance",
    "TypeCompliance",
    "FieldCompleteness",
    "RequiredFieldsOnly",
    "FieldCoverage",
    "HierarchyScore",
    "TreeEditDistance",
    "StructuredOutputScore",
    "QuickStructuredCheck",
)

_HALLUCINATION_EXPORT_NAMES = (
    "HallucinationInput",
    "ClaimExtractionInput",
    "FactualConsistencyInput",
    "Claim",
    "NLIResult",
    "HallucinationResult",
    "Faithfulness",
    "ClaimSupport",
    "FactualConsistency",
    "ContradictionDetection",
    "HallucinationScore",
    "NLILabel",
    "check_entailment",
    "check_contradiction",
    "HallucinationSentinel",
    "HallucinationDetector",
)

_EVAL_EXPORTS = {name: "fi.evals" for name in _FI_EVAL_EXPORT_NAMES}
_EVAL_EXPORTS.update({name: "fi.evals.autoeval" for name in _AUTOEVAL_EXPORT_NAMES})
_EVAL_EXPORTS.update({name: "fi.evals.local" for name in _LOCAL_EVAL_EXPORT_NAMES})
_EVAL_EXPORTS.update({name: "fi.evals.streaming" for name in _STREAMING_EXPORT_NAMES})
_EVAL_EXPORTS["AgentReportEvaluator"] = "fi.evals.metrics.agents"
for _name in _METRIC_EXPORT_NAMES:
    _EVAL_EXPORTS.setdefault(_name, "fi.evals.metrics")
for _name in _AGENT_METRIC_EXPORT_NAMES:
    _EVAL_EXPORTS.setdefault(_name, "fi.evals.metrics.agents")
for _name in _RAG_METRIC_EXPORT_NAMES:
    _EVAL_EXPORTS.setdefault(_name, "fi.evals.metrics")
for _name in _STRUCTURED_METRIC_EXPORT_NAMES:
    _EVAL_EXPORTS.setdefault(_name, "fi.evals.metrics")
for _name in _HALLUCINATION_EXPORT_NAMES:
    _EVAL_EXPORTS.setdefault(_name, "fi.evals.metrics.hallucination")

_EVAL_SUBMODULE_ALIASES = {
    "autoeval": "fi.evals.autoeval",
    "core": "fi.evals.core",
    "core.prompt_generator": "fi.evals.core.prompt_generator",
    "feedback": "fi.evals.feedback",
    "framework": "fi.evals.framework",
    "framework.backends": "fi.evals.framework.backends",
    "framework.backends.base": "fi.evals.framework.backends.base",
    "framework.backends.thread_pool": "fi.evals.framework.backends.thread_pool",
    "framework.context": "fi.evals.framework.context",
    "framework.enrichment": "fi.evals.framework.enrichment",
    "framework.evaluator": "fi.evals.framework.evaluator",
    "framework.evaluators": "fi.evals.framework.evaluators",
    "framework.evaluators.blocking": "fi.evals.framework.evaluators.blocking",
    "framework.evaluators.non_blocking": "fi.evals.framework.evaluators.non_blocking",
    "framework.registry": "fi.evals.framework.registry",
    "framework.resilience": "fi.evals.framework.resilience",
    "framework.resilience.retry": "fi.evals.framework.resilience.retry",
    "guardrails": "fi.evals.guardrails",
    "guardrails.backends": "fi.evals.guardrails.backends",
    "guardrails.backends.base": "fi.evals.guardrails.backends.base",
    "guardrails.scanners": "fi.evals.guardrails.scanners",
    "guardrails.scanners.base": "fi.evals.guardrails.scanners.base",
    "guardrails.scanners.code_injection": "fi.evals.guardrails.scanners.code_injection",
    "guardrails.scanners.invisible_chars": "fi.evals.guardrails.scanners.invisible_chars",
    "guardrails.scanners.jailbreak": "fi.evals.guardrails.scanners.jailbreak",
    "guardrails.scanners.language": "fi.evals.guardrails.scanners.language",
    "guardrails.scanners.regex": "fi.evals.guardrails.scanners.regex",
    "guardrails.scanners.secrets": "fi.evals.guardrails.scanners.secrets",
    "guardrails.scanners.topics": "fi.evals.guardrails.scanners.topics",
    "llm": "fi.evals.llm",
    "local": "fi.evals.local",
    "metrics": "fi.evals.metrics",
    "metrics.agents": "fi.evals.metrics.agents",
    "metrics.agents.metrics": "fi.evals.metrics.agents.metrics",
    "metrics.agents.report": "fi.evals.metrics.agents.report",
    "metrics.agents.types": "fi.evals.metrics.agents.types",
    "metrics.base_metric": "fi.evals.metrics.base_metric",
    "metrics.code_security": "fi.evals.metrics.code_security",
    "metrics.function_calling": "fi.evals.metrics.function_calling",
    "metrics.hallucination": "fi.evals.metrics.hallucination",
    "metrics.llm_as_judges": "fi.evals.metrics.llm_as_judges",
    "metrics.rag": "fi.evals.metrics.rag",
    "metrics.structured": "fi.evals.metrics.structured",
    "metrics.structured.json_validation": "fi.evals.metrics.structured.json_validation",
    "otel": "fi.evals.otel",
    "streaming": "fi.evals.streaming",
}
_EVAL_PACKAGE_ALIASES = {
    alias
    for alias in _EVAL_SUBMODULE_ALIASES
    if "." not in alias or any(
        child.startswith(f"{alias}.") for child in _EVAL_SUBMODULE_ALIASES
    )
}

install_lazy_module_aliases(
    __name__,
    _EVAL_SUBMODULE_ALIASES,
    package_aliases=_EVAL_PACKAGE_ALIASES,
)


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


def build_task_evaluation_config(
    *,
    task_description: str,
    expected_result: Optional[str] = None,
    success_criteria: Sequence[str] = (),
    required_tools: Sequence[str] = (),
    available_tools: Sequence[str] = (),
    forbidden_patterns: Sequence[str] = (),
    sensitive_patterns: Sequence[str] = (),
    metric_weights: Optional[Mapping[str, float]] = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build an agent-report evaluation config for arbitrary task evidence."""

    if not task_description:
        raise ValueError("task_description is required")
    config: dict[str, Any] = {
        "task_description": str(task_description),
    }
    if expected_result is not None:
        config["expected_result"] = str(expected_result)
    if success_criteria:
        config["success_criteria"] = _unique_strings(success_criteria)
    if required_tools:
        config["required_tools"] = _unique_strings(required_tools)
    if available_tools:
        config["available_tools"] = _unique_strings(available_tools)
    if forbidden_patterns:
        config["forbidden_patterns"] = _unique_strings(forbidden_patterns)
    if sensitive_patterns:
        config["sensitive_patterns"] = _unique_strings(sensitive_patterns)
    if metric_weights:
        config["metric_weights"] = {
            str(key): float(value)
            for key, value in dict(metric_weights).items()
        }
    config.update({str(key): _plain(value) for key, value in extra.items()})
    return config


def build_evaluation_hook_config(
    *,
    task_description: str,
    endpoint: str,
    api_key_env: str = "AGENT_LEARNING_SDK_EVALUATION_HOOK_KEY",
    metric_name: str = "external_task_quality",
    expected_result: Optional[str] = None,
    success_criteria: Sequence[str] = (),
    required_tools: Sequence[str] = (),
    available_tools: Sequence[str] = (),
    threshold_metric_weight: float = 10.0,
    metadata: Optional[Mapping[str, Any]] = None,
    metric_weights: Optional[Mapping[str, float]] = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build task-evidence config that calls a redacted HTTP eval hook."""

    if not endpoint:
        raise ValueError("endpoint is required")
    weights = {
        str(metric_name): float(threshold_metric_weight),
        "task_completion": 1.0,
        "secret_leakage": 1.0,
        **{str(key): float(value) for key, value in dict(metric_weights or {}).items()},
    }
    return build_task_evaluation_config(
        task_description=task_description,
        expected_result=expected_result,
        success_criteria=success_criteria,
        required_tools=required_tools,
        available_tools=available_tools,
        metric_weights=weights,
        evaluation_hooks=[
            {
                "name": str(metric_name),
                "metric_name": str(metric_name),
                "endpoint": str(endpoint),
                "auth": {"type": "bearer", "token_env": str(api_key_env)}
                if api_key_env
                else {},
                "metadata": {
                    "source": "agent_learning.evals.build_evaluation_hook_config",
                    **dict(metadata or {}),
                },
            }
        ],
        **extra,
    )


def evaluate_task_evidence_with_hook(
    evidence: Mapping[str, Any],
    *,
    endpoint: str,
    task_description: str,
    api_key_env: str = "AGENT_LEARNING_SDK_EVALUATION_HOOK_KEY",
    metric_name: str = "external_task_quality",
    expected_result: Optional[str] = None,
    success_criteria: Sequence[str] = (),
    threshold: float = 0.7,
    name: Optional[str] = None,
    source_path: str | Path = ".",
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Evaluate arbitrary task evidence through a live HTTP eval hook."""

    config = build_evaluation_hook_config(
        task_description=task_description,
        endpoint=endpoint,
        api_key_env=api_key_env,
        metric_name=metric_name,
        expected_result=expected_result,
        success_criteria=success_criteria,
        metadata=metadata,
    )
    return evaluate_task_evidence(
        evidence,
        config=config,
        threshold=threshold,
        name=name,
        source_path=source_path,
    )


def build_task_evidence_artifact(
    evidence: Optional[Mapping[str, Any]] = None,
    *,
    name: Optional[str] = None,
    task_id: Optional[str] = None,
    input: Any = None,
    output: Any = None,
    expected_result: Any = None,
    messages: Optional[Sequence[Mapping[str, Any]]] = None,
    tool_calls: Sequence[Any] = (),
    tool_results: Optional[Mapping[str, Any] | Sequence[Mapping[str, Any]]] = None,
    metrics: Optional[Mapping[str, Any]] = None,
    environment_state: Optional[Mapping[str, Any]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    artifacts: Sequence[Any] = (),
    events: Sequence[Any] = (),
    status: Optional[str] = None,
) -> dict[str, Any]:
    """Normalize raw task evidence into an evaluable Agent Learning artifact."""

    source = _as_mapping(evidence)
    task_id_value = str(
        task_id
        or source.get("task_id")
        or source.get("id")
        or source.get("name")
        or "task-evidence"
    )
    name_value = str(name or source.get("name") or task_id_value)
    input_value = input if input is not None else _first_present(source, "input", "prompt", "question")
    output_value = output if output is not None else _first_present(source, "output", "result", "final_result", "answer", default="")
    expected_value = (
        expected_result
        if expected_result is not None
        else _first_present(source, "expected_result", "expected", "expected_output")
    )
    metrics_value = dict(metrics or _as_mapping(source.get("metrics")) or _as_mapping(source.get("metric_averages")))
    environment_state_value = dict(
        environment_state
        or _as_mapping(source.get("environment_state"))
        or _as_mapping(source.get("state"))
    )
    metadata_value = {
        **_as_mapping(source.get("metadata")),
        **dict(metadata or {}),
    }
    metadata_value.setdefault("task", source.get("task") or source.get("task_description") or task_id_value)
    if expected_value is not None:
        metadata_value.setdefault("expected_result", expected_value)
    if environment_state_value:
        metadata_value["environment_state"] = environment_state_value

    raw_tool_calls = list(tool_calls or _as_list(source.get("tool_calls")) or _as_list(source.get("tools_called")))
    normalized_tool_calls = _normalize_task_tool_calls(raw_tool_calls)
    source_messages = _as_list(source.get("messages"))
    messages_value = (
        [dict(item) for item in messages]
        if messages is not None
        else [dict(item) for item in source_messages if isinstance(item, Mapping)]
        or _task_messages(
            input_value=input_value,
            output_value=output_value,
            tool_calls=normalized_tool_calls,
            tool_results=tool_results,
        )
    )
    score = _task_evidence_score(metrics_value, source)
    status_value = str(status or source.get("status") or ("passed" if score >= 0.7 else "failed"))
    passed = bool(source.get("passed", status_value.lower() == "passed"))

    case = {
        "id": task_id_value,
        "name": task_id_value,
        "passed": passed,
        "score": round(score, 4),
        "messages": messages_value,
        "tool_calls": normalized_tool_calls,
        "artifacts": [item for item in _as_list(artifacts or source.get("artifacts"))],
        "events": [item for item in _as_list(events or source.get("events"))],
        "metadata": metadata_value,
        "evaluation": {
            "agent_report": {
                "passed": passed,
                "summary": {
                    "score": round(score, 4),
                    "metric_averages": metrics_value,
                },
            }
        },
    }
    return {
        "kind": AGENT_LEARNING_TASK_EVIDENCE_KIND,
        "name": name_value,
        "status": status_value,
        "exit_code": 0 if passed else 1,
        "summary": {
            "score": round(score, 4),
            "case_count": 1,
            "passed_count": 1 if passed else 0,
            "failed_count": 0 if passed else 1,
        },
        "report": {"results": [case]},
        "findings": list(_as_list(source.get("findings"))),
    }


def evaluate_task_evidence(
    evidence: Mapping[str, Any],
    config: Optional[Mapping[str, Any]] = None,
    *,
    threshold: float = 0.7,
    name: Optional[str] = None,
    source_path: str | Path = ".",
) -> dict[str, Any]:
    """Evaluate arbitrary task evidence through the agent-report evaluator."""

    artifact = build_task_evidence_artifact(evidence, name=name)
    return evaluate_artifact(
        artifact,
        config=config,
        threshold=threshold,
        name=name,
        source_path=source_path,
    )


def evaluate_task_evidence_file(
    path: str | Path,
    config: Optional[Mapping[str, Any]] = None,
    *,
    threshold: float = 0.7,
    name: Optional[str] = None,
) -> dict[str, Any]:
    """Load raw task evidence or an existing artifact and evaluate it."""

    source_path = Path(path).expanduser().resolve()
    payload = load_artifact_file(source_path)
    if _contains_agent_report(payload):
        return evaluate_artifact(
            payload,
            config=config,
            threshold=threshold,
            name=name,
            source_path=source_path,
        )
    return evaluate_task_evidence(
        payload,
        config=config,
        threshold=threshold,
        name=name,
        source_path=source_path,
    )


def write_task_evidence_file(
    evidence: Mapping[str, Any],
    path: str | Path,
    *,
    name: Optional[str] = None,
) -> Path:
    """Write normalized task evidence as an Agent Learning artifact."""

    artifact_path = Path(path).expanduser().resolve()
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(
            build_task_evidence_artifact(evidence, name=name),
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    return artifact_path


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
    environment_state_keys = _report_environment_state_keys(report)
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
            "environment_state_keys": environment_state_keys,
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


def _report_environment_state_keys(report: Mapping[str, Any]) -> list[str]:
    keys: set[str] = set()
    for result in _as_list(report.get("results")):
        case = _as_mapping(result)
        metadata = _as_mapping(case.get("metadata"))
        environment_state = _as_mapping(metadata.get("environment_state"))
        keys.update(str(key) for key in environment_state if key not in (None, ""))
    return sorted(keys)


def load_eval_suite_file(path: str | Path) -> dict[str, Any]:
    return public_payload(_suite().load_eval_suite_file(path))


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
    payload = _suite().run_eval_suite_file(
        path,
        options=options,
        name=name,
        threshold=threshold,
        dry_run=dry_run,
    )
    return public_payload(payload, kind=AGENT_LEARNING_EVAL_KIND)


def run_eval_suite(
    suite: Mapping[str, Any],
    *,
    suite_path: str | Path = ".",
    options: Optional[Any] = None,
) -> dict[str, Any]:
    payload = _suite().run_eval_suite(suite, suite_path=suite_path, options=options)
    return public_payload(payload, kind=AGENT_LEARNING_EVAL_KIND)


def optimize_eval_suite_file(
    path: str | Path,
    *,
    options: Optional[Any] = None,
    name: Optional[str] = None,
    threshold: Optional[float] = None,
    max_candidates: Optional[int] = None,
    dry_run: Optional[bool] = None,
) -> dict[str, Any]:
    payload = _suite().optimize_eval_suite_file(
        path,
        options=options,
        name=name,
        threshold=threshold,
        max_candidates=max_candidates,
        dry_run=dry_run,
    )
    return public_payload(payload, kind=AGENT_LEARNING_EVAL_OPTIMIZATION_KIND)


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


def _contains_agent_report(payload: Mapping[str, Any]) -> bool:
    try:
        _artifact_report(payload)
    except ValueError:
        return False
    return True


def _first_present(
    source: Mapping[str, Any],
    *keys: str,
    default: Any = None,
) -> Any:
    for key in keys:
        if key in source and source[key] not in (None, ""):
            return source[key]
    return default


def _normalize_task_tool_calls(tool_calls: Sequence[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(_as_list(tool_calls), start=1):
        if isinstance(raw, str):
            normalized.append(
                {
                    "id": f"tool_{index}",
                    "name": raw,
                    "arguments": {},
                }
            )
            continue
        item = _as_mapping(raw)
        if not item:
            continue
        function = _as_mapping(item.get("function"))
        name = item.get("name") or item.get("tool") or item.get("action") or function.get("name")
        if not name:
            continue
        arguments = (
            item.get("arguments")
            if "arguments" in item
            else item.get("args", item.get("input", function.get("arguments", {})))
        )
        normalized.append(
            {
                **item,
                "id": str(item.get("id") or item.get("tool_call_id") or f"tool_{index}"),
                "name": str(name),
                "arguments": _plain(arguments),
            }
        )
    return normalized


def _task_messages(
    *,
    input_value: Any,
    output_value: Any,
    tool_calls: Sequence[Mapping[str, Any]],
    tool_results: Optional[Mapping[str, Any] | Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if input_value not in (None, ""):
        messages.append({"role": "user", "content": str(input_value)})
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": str(output_value or ""),
    }
    if tool_calls:
        assistant["tool_calls"] = [dict(item) for item in tool_calls]
    messages.append(assistant)
    messages.extend(_task_tool_result_messages(tool_calls, tool_results))
    return messages


def _task_tool_result_messages(
    tool_calls: Sequence[Mapping[str, Any]],
    tool_results: Optional[Mapping[str, Any] | Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    if not tool_results:
        return [
            {
                "role": "tool",
                "tool_call_id": str(call.get("id")),
                "content": str(call.get("result")),
            }
            for call in tool_calls
            if call.get("id") and call.get("result") not in (None, "")
        ]
    if isinstance(tool_results, Mapping):
        return [
            {
                "role": "tool",
                "tool_call_id": str(call_id),
                "content": str(result),
            }
            for call_id, result in tool_results.items()
        ]
    return [dict(item) for item in tool_results]


def _task_evidence_score(
    metrics: Mapping[str, Any],
    source: Mapping[str, Any],
) -> float:
    for key in ("score", "task_completion", "world_contract_quality"):
        value = metrics.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    if source.get("score") is not None:
        try:
            return float(source["score"])
        except (TypeError, ValueError):
            pass
    return 1.0 if str(source.get("status") or "passed").lower() == "passed" else 0.0


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
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _unique_strings(values: Sequence[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in _as_list(values):
        text = str(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


__all__ = [
    *_EVAL_EXPORTS,
    "AGENT_LEARNING_ARTIFACT_EVALUATION_KIND",
    "AGENT_LEARNING_TASK_EVIDENCE_KIND",
    "build_evaluation_hook_config",
    "build_task_evaluation_config",
    "build_task_evidence_artifact",
    "build_eval_suite_manifest",
    "evaluate",
    "evaluate_agent_report",
    "evaluate_artifact",
    "evaluate_artifact_file",
    "evaluate_task_evidence",
    "evaluate_task_evidence_file",
    "evaluate_task_evidence_with_hook",
    "load_artifact_file",
    "load_eval_suite_file",
    "optimize_eval_suite_file",
    "run_eval_suite",
    "run_eval_suite_file",
    "write_eval_suite_file",
    "write_task_evidence_file",
]
