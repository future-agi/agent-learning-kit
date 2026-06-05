from __future__ import annotations

import copy
import json
from typing import Any, Mapping, Optional, Sequence

from .targets import AgentCandidate, CandidateEvaluation


DEFAULT_SIMULATION_EVIDENCE_WEIGHTS: dict[str, float] = {
    "tool_coverage": 1.0,
    "framework_trace": 2.0,
    "framework_import": 2.0,
    "red_team_readiness": 3.0,
    "runtime_semantics": 1.0,
    "world_contract": 3.0,
    "world_orchestration_replay": 3.0,
    "agent_memory_lineage": 2.0,
}


def score_simulation_evidence(
    report: Any,
    *,
    manifest: Optional[Mapping[str, Any]] = None,
    candidate: Optional[AgentCandidate] = None,
    config: Optional[Mapping[str, Any]] = None,
) -> CandidateEvaluation:
    """Score normalized simulation evidence for optimizer candidate feedback.

    The scorer intentionally stays deterministic. It consumes the environment
    evidence emitted by simulate engines (``metadata.environment_state``) and
    turns framework trace, framework-import readiness, red-team readiness,
    runtime semantic, memory-lineage, orchestration, tool, and world-contract
    evidence into a single optimizer-grade score.
    """

    cfg = copy.deepcopy(dict(config or {}))
    manifest_config = _manifest_agent_report_config(manifest)
    layers = _target_layers(manifest=manifest, candidate=candidate, config=cfg)
    env_states = _environment_states(report)
    tools_called = _tool_names(report)
    weights = {
        **DEFAULT_SIMULATION_EVIDENCE_WEIGHTS,
        **_float_mapping(cfg.get("weights") or cfg.get("metric_weights")),
    }

    components: list[dict[str, Any]] = []
    tool_component = _score_tool_coverage(
        tools_called,
        required_tools=_configured_list(
            "required_tools",
            cfg,
            manifest_config,
        ),
    )
    if tool_component is not None:
        components.append(tool_component)

    if _should_score("framework", layers, env_states, cfg):
        components.append(
            _score_framework_trace(
                env_states,
                cfg=cfg,
                manifest_config=manifest_config,
            )
        )
        runtime_component = _score_runtime_semantics(
            env_states,
            candidate=candidate,
            cfg=cfg,
            manifest_config=manifest_config,
        )
        if runtime_component is not None:
            components.append(runtime_component)

    if _should_score("framework_import", layers, env_states, cfg):
        components.append(
            _score_framework_import_manifest(
                env_states,
                cfg=cfg,
                manifest_config=manifest_config,
            )
        )

    if _should_score("red_team_readiness", layers, env_states, cfg):
        components.append(
            _score_red_team_readiness(
                env_states,
                cfg=cfg,
                manifest_config=manifest_config,
            )
        )

    if _should_score("world", layers, env_states, cfg):
        components.append(
            _score_world_contract(
                env_states,
                cfg=cfg,
                manifest_config=manifest_config,
            )
        )

    if _should_score("orchestration", layers, env_states, cfg):
        components.append(
            _score_world_orchestration_replay(
                env_states,
                cfg=cfg,
                manifest_config=manifest_config,
            )
        )

    if _should_score("memory", layers, env_states, cfg):
        components.append(
            _score_agent_memory_lineage(
                env_states,
                cfg=cfg,
                manifest_config=manifest_config,
            )
        )

    if not components:
        components.append(
            {
                "name": "simulation_evidence",
                "score": 0.0,
                "weight": 1.0,
                "reason": "No supported simulation evidence found.",
                "details": {},
            }
        )

    weighted_sum = 0.0
    total_weight = 0.0
    for component in components:
        weight = float(weights.get(component["name"], component.get("weight", 1.0)))
        component["weight"] = weight
        weighted_sum += float(component["score"]) * weight
        total_weight += weight
    score = round(weighted_sum / total_weight, 4) if total_weight else 0.0

    candidate = candidate or AgentCandidate.from_config(
        {},
        target_name="simulation-evidence",
        metadata={"kind": "ad_hoc_evidence_score"},
    )
    return CandidateEvaluation(
        candidate=candidate,
        score=score,
        reason=_evidence_reason(components),
        report=report,
        metadata={
            "simulation_evidence_score": {
                "score": score,
                "components": copy.deepcopy(components),
                "tools_called": sorted(tools_called),
                "environment_keys": sorted(_environment_keys(env_states)),
                "research_basis": [
                    "CausalFlow 2026: failed traces should produce minimal, validated repairs.",
                    "AgentTrace/provenance 2026: process evidence beats final-answer-only scoring.",
                    "Runtime-persistence 2026: framework runtime semantics are part of trace validity.",
                    "VeRO 2026: harness optimization needs versioned rewards and structured observations.",
                    "Agent red-team 2026: readiness evidence must cover target, campaign, runtime, controls, and observability.",
                ],
            }
        },
    )


def _score_tool_coverage(
    tools_called: set[str],
    *,
    required_tools: Sequence[str],
) -> Optional[dict[str, Any]]:
    if not required_tools:
        return None
    required = {_norm(tool) for tool in required_tools if _norm(tool)}
    observed = {_norm(tool) for tool in tools_called if _norm(tool)}
    matched = sorted(required & observed)
    missing = sorted(required - observed)
    score = len(matched) / len(required) if required else 1.0
    return {
        "name": "tool_coverage",
        "score": round(score, 4),
        "reason": "required tools covered" if not missing else "missing required tools",
        "details": {
            "matched": matched,
            "missing": missing,
            "observed": sorted(observed),
        },
    }


def _score_framework_trace(
    env_states: Sequence[Mapping[str, Any]],
    *,
    cfg: Mapping[str, Any],
    manifest_config: Mapping[str, Any],
) -> dict[str, Any]:
    payload = _first_payload(env_states, "framework_trace")
    if not payload:
        return _missing_component("framework_trace", "No framework_trace environment evidence.")

    spans = _as_list(payload.get("spans"))
    events = _as_list(payload.get("events"))
    observed = _token_set(payload)
    required = _configured_list(
        "required_framework_trace",
        cfg,
        manifest_config,
        nested_keys=("framework_trace", "required_signals"),
    )
    required_tokens = {_norm(item) for item in required if _norm(item)}
    matched = sorted(required_tokens & observed)
    signal_score = (
        len(matched) / len(required_tokens)
        if required_tokens
        else (1.0 if observed else 0.0)
    )

    conformance = _as_mapping(payload.get("adapter_conformance"))
    conformance_score = 1.0
    if conformance:
        conformance_score = 1.0 if conformance.get("passed") is not False else 0.0
        missing = _as_list(conformance.get("missing_signals")) + _as_list(
            conformance.get("missing_mappings")
        )
        if missing:
            conformance_score = min(conformance_score, 0.5)

    density_score = 1.0 if spans or events else 0.0
    score = round(
        0.2
        + 0.35 * density_score
        + 0.35 * signal_score
        + 0.10 * conformance_score,
        4,
    )
    return {
        "name": "framework_trace",
        "score": min(1.0, score),
        "reason": "framework trace evidence present",
        "details": {
            "framework": payload.get("framework"),
            "span_count": len(spans),
            "event_count": len(events),
            "matched_required": matched,
            "missing_required": sorted(required_tokens - set(matched)),
            "adapter_conformance": copy.deepcopy(conformance),
        },
    }


def _score_runtime_semantics(
    env_states: Sequence[Mapping[str, Any]],
    *,
    candidate: Optional[AgentCandidate],
    cfg: Mapping[str, Any],
    manifest_config: Mapping[str, Any],
) -> Optional[dict[str, Any]]:
    contract = _first_mapping(
        cfg.get("framework_runtime_contract"),
        manifest_config.get("framework_runtime_contract"),
    )
    if not contract:
        return None

    payload = _first_payload(env_states, "framework_trace")
    candidate_agent = _as_mapping(
        _path(_as_mapping(candidate.config if candidate is not None else {}), "agent")
    )
    method = (
        candidate_agent.get("method")
        or _path(candidate_agent, "adapter.method")
        or _path(candidate_agent, "runtime.method")
    )
    input_mode = (
        candidate_agent.get("input_mode")
        or _path(candidate_agent, "adapter.input_mode")
        or _path(candidate_agent, "runtime.input_mode")
    )
    observed = _token_set(payload)
    checks: list[tuple[str, bool]] = []
    if contract.get("method"):
        checks.append(
            (
                "method",
                _norm(method) == _norm(contract.get("method"))
                or _norm(contract.get("method")) in observed,
            )
        )
    if contract.get("input_mode"):
        checks.append(
            (
                "input_mode",
                _norm(input_mode) == _norm(contract.get("input_mode"))
                or _norm(contract.get("input_mode")) in observed,
            )
        )
    required_tools = {_norm(tool) for tool in _as_list(contract.get("required_tools"))}
    if required_tools:
        checks.append(
            (
                "required_tools",
                bool(required_tools & observed) or required_tools <= observed,
            )
        )
    if not checks:
        return None
    passed = [name for name, ok in checks if ok]
    failed = [name for name, ok in checks if not ok]
    score = len(passed) / len(checks)
    return {
        "name": "runtime_semantics",
        "score": round(score, 4),
        "reason": (
            "framework runtime contract matched"
            if not failed
            else "framework runtime contract mismatch"
        ),
        "details": {
            "passed": passed,
            "failed": failed,
            "expected_method": contract.get("method"),
            "candidate_method": method,
            "expected_input_mode": contract.get("input_mode"),
            "candidate_input_mode": input_mode,
        },
    }


def _score_framework_import_manifest(
    env_states: Sequence[Mapping[str, Any]],
    *,
    cfg: Mapping[str, Any],
    manifest_config: Mapping[str, Any],
) -> dict[str, Any]:
    payload = _first_payload(env_states, "framework_import_manifest")
    if not payload:
        return _missing_component(
            "framework_import",
            "No framework_import_manifest environment evidence.",
        )

    quality = _first_mapping(
        cfg.get("framework_import_quality"),
        manifest_config.get("framework_import_quality"),
    )
    summary = _as_mapping(payload.get("summary"))
    signals = {_norm(item) for item in _as_list(payload.get("signals")) if _norm(item)}
    observed = _framework_import_observed(summary, signals)

    required_import = {
        _norm(item)
        for item in _configured_list("required_framework_import", cfg, manifest_config)
        if _norm(item)
    }
    coverage_matched = sorted(required_import & observed)
    coverage_missing = sorted(required_import - observed)
    coverage_score = (
        len(coverage_matched) / len(required_import)
        if required_import
        else (1.0 if observed else 0.0)
    )

    checks: list[dict[str, Any]] = []
    _append_framework_import_count_checks(checks, summary, quality)
    _append_framework_import_boolean_checks(checks, summary, quality)
    _append_framework_import_required_checks(
        checks,
        summary,
        quality=quality,
        payload=payload,
    )
    quality_score = (
        sum(1 for check in checks if check["match"]) / len(checks)
        if checks
        else 1.0
    )

    blocking_gaps = {
        "missing_required_sources": _as_list(summary.get("missing_required_sources")),
        "missing_required_frameworks": _as_list(summary.get("missing_required_frameworks")),
        "missing_required_export_types": _as_list(summary.get("missing_required_export_types")),
        "missing_required_signals": _as_list(summary.get("missing_required_signals")),
        "failed_sources": _as_list(summary.get("failed_sources")),
    }
    gap_count = sum(len(values) for values in blocking_gaps.values()) + len(
        coverage_missing
    )
    gap_score = 1.0 if gap_count == 0 else 0.0
    score = round(0.35 * coverage_score + 0.45 * quality_score + 0.20 * gap_score, 4)
    return {
        "name": "framework_import",
        "score": score,
        "reason": (
            "framework import evidence is portable and gap-free"
            if score >= 0.99
            else "framework import evidence incomplete"
        ),
        "details": {
            "matched_required": coverage_matched,
            "missing_required": coverage_missing,
            "checks": checks,
            "blocking_gaps": blocking_gaps,
            "summary": copy.deepcopy(summary),
        },
    }


def _score_red_team_readiness(
    env_states: Sequence[Mapping[str, Any]],
    *,
    cfg: Mapping[str, Any],
    manifest_config: Mapping[str, Any],
) -> dict[str, Any]:
    payload = _first_payload(env_states, "red_team_readiness")
    if not payload:
        return _missing_component(
            "red_team_readiness",
            "No red_team_readiness environment evidence.",
        )

    quality = _first_mapping(
        cfg.get("red_team_readiness_quality"),
        manifest_config.get("red_team_readiness_quality"),
    )
    summary = _as_mapping(payload.get("summary"))
    signals = {_norm(item) for item in _as_list(payload.get("signals")) if _norm(item)}
    observed = _red_team_readiness_observed(summary, signals)
    required_readiness = {
        _norm(item)
        for item in _configured_list(
            "required_red_team_readiness",
            cfg,
            manifest_config,
        )
        if _norm(item)
    }
    coverage_matched = sorted(required_readiness & observed)
    coverage_missing = sorted(required_readiness - observed)
    coverage_score = (
        len(coverage_matched) / len(required_readiness)
        if required_readiness
        else (1.0 if observed else 0.0)
    )

    checks: list[dict[str, Any]] = []
    _append_red_team_readiness_count_checks(checks, summary, quality)
    _append_red_team_readiness_boolean_checks(checks, summary, quality)
    _append_red_team_readiness_required_checks(
        checks,
        summary,
        quality=quality,
        payload=payload,
    )
    quality_score = (
        sum(1 for check in checks if check["match"]) / len(checks)
        if checks
        else 1.0
    )
    blocking_gaps = {
        "blocking_gaps": _as_list(summary.get("blocking_gaps")),
        "missing_required_evidence": _as_list(summary.get("missing_required_evidence")),
        "missing_required_signals": _as_list(summary.get("missing_required_signals")),
        "failed_components": _as_list(summary.get("failed_components")),
    }
    gap_count = sum(len(values) for values in blocking_gaps.values()) + len(
        coverage_missing
    )
    gap_score = 1.0 if gap_count == 0 else 0.0
    score = round(0.35 * coverage_score + 0.45 * quality_score + 0.20 * gap_score, 4)
    return {
        "name": "red_team_readiness",
        "score": score,
        "reason": (
            "red-team readiness gate is complete and gap-free"
            if score >= 0.99
            else "red-team readiness evidence incomplete"
        ),
        "details": {
            "matched_required": coverage_matched,
            "missing_required": coverage_missing,
            "checks": checks,
            "blocking_gaps": blocking_gaps,
            "summary": copy.deepcopy(summary),
        },
    }


def _score_world_contract(
    env_states: Sequence[Mapping[str, Any]],
    *,
    cfg: Mapping[str, Any],
    manifest_config: Mapping[str, Any],
) -> dict[str, Any]:
    payload = _first_payload(env_states, "world_contract")
    if not payload:
        replay = _first_payload(env_states, "world_orchestration_replay")
        payload = _nested_world_contract(replay)
    if not payload:
        return _missing_component("world_contract", "No world_contract evidence.")

    quality = _first_mapping(
        cfg.get("world_contract_quality"),
        manifest_config.get("world_contract_quality"),
    )
    summary = _as_mapping(payload.get("summary"))
    transition_log = _as_list(payload.get("transition_log"))
    invariant_results = _as_list(payload.get("invariant_results"))
    success_results = _as_list(payload.get("success_results"))
    completed = {
        _norm(item.get("id") or item.get("name") or item.get("action"))
        for item in transition_log
        if isinstance(item, Mapping) and item.get("status") == "success"
    }
    required_transitions = [
        _norm(item.get("id") or item.get("name") or item.get("action"))
        if isinstance(item, Mapping)
        else _norm(item)
        for item in _as_list(quality.get("required_transitions"))
    ]
    required_transitions = [item for item in required_transitions if item]
    transition_score = (
        len(set(required_transitions) & completed) / len(set(required_transitions))
        if required_transitions
        else (1.0 if completed else 0.0)
    )
    invariant_score = (
        1.0
        if not invariant_results
        else float(all(_as_mapping(item).get("pass") is not False for item in invariant_results))
    )
    success_score = _world_success_score(summary, success_results, quality)
    violation_count = _world_violation_count(payload)
    violation_score = 1.0 if violation_count <= int(quality.get("max_violation_count", 0)) else 0.0
    expected_state = _as_mapping(quality.get("expected_state"))
    state_score = (
        1.0
        if not expected_state
        else float(_contains_subset(_as_mapping(payload.get("state")), expected_state))
    )

    score = round(
        0.25 * transition_score
        + 0.25 * success_score
        + 0.20 * invariant_score
        + 0.20 * violation_score
        + 0.10 * state_score,
        4,
    )
    return {
        "name": "world_contract",
        "score": score,
        "reason": (
            "world contract reached success without violations"
            if score >= 0.99
            else "world contract evidence incomplete"
        ),
        "details": {
            "completed_transitions": sorted(completed),
            "required_transitions": sorted(set(required_transitions)),
            "terminal_status": summary.get("terminal_status"),
            "violation_count": violation_count,
            "expected_state_matched": bool(state_score),
        },
    }


def _score_world_orchestration_replay(
    env_states: Sequence[Mapping[str, Any]],
    *,
    cfg: Mapping[str, Any],
    manifest_config: Mapping[str, Any],
) -> dict[str, Any]:
    payload = _first_payload(env_states, "world_orchestration_replay")
    if not payload:
        return _missing_component(
            "world_orchestration_replay",
            "No world_orchestration_replay evidence.",
        )
    orchestration = _as_mapping(
        payload.get("orchestration_trace")
        or _path(payload, "state.orchestration_trace")
    )
    nodes = _as_list(orchestration.get("nodes"))
    steps = _as_list(orchestration.get("steps"))
    events = _as_list(orchestration.get("events") or orchestration.get("records"))
    observed = _token_set(orchestration) | _token_set(payload)
    required = _configured_list(
        "required_orchestration_trace",
        cfg,
        manifest_config,
        nested_keys=("orchestration_trace", "required_signals"),
    )
    required_tokens = {_norm(item) for item in required if _norm(item)}
    coverage = (
        len(required_tokens & observed) / len(required_tokens)
        if required_tokens
        else (1.0 if nodes or steps or events else 0.0)
    )
    replay_summary = _as_mapping(payload.get("summary"))
    blocked_score = 1.0
    if "blocked_hostile_actions" in replay_summary:
        blocked_score = 1.0 if replay_summary.get("blocked_hostile_actions") else 0.5
    world_states: Sequence[Mapping[str, Any]] = (
        env_states
        if any(_as_mapping(state.get("world_contract")) for state in env_states)
        else [{"world_contract": _nested_world_contract(payload)}]
    )
    world_score = _score_world_contract(
        world_states,
        cfg=cfg,
        manifest_config=manifest_config,
    )["score"]
    score = round(0.35 * coverage + 0.25 * bool(nodes or steps or events) + 0.25 * world_score + 0.15 * blocked_score, 4)
    return {
        "name": "world_orchestration_replay",
        "score": min(1.0, score),
        "reason": "orchestration replay evidence present",
        "details": {
            "node_count": len(nodes),
            "step_count": len(steps),
            "event_count": len(events),
            "matched_required": sorted(required_tokens & observed),
            "world_contract_score": world_score,
        },
    }


def _score_agent_memory_lineage(
    env_states: Sequence[Mapping[str, Any]],
    *,
    cfg: Mapping[str, Any],
    manifest_config: Mapping[str, Any],
) -> dict[str, Any]:
    payload = _first_payload(env_states, "agent_memory_lineage")
    if not payload:
        return _missing_component(
            "agent_memory_lineage",
            "No agent_memory_lineage evidence.",
        )
    quality = _first_mapping(
        cfg.get("agent_memory_lineage_quality"),
        manifest_config.get("agent_memory_lineage_quality"),
    )
    summary = _as_mapping(payload.get("summary"))
    operations = _as_list(payload.get("operations"))
    operation_types = {
        _norm(item.get("operation") or item.get("type"))
        for item in operations
        if isinstance(item, Mapping)
    }
    required_operation_types = {
        _norm(item)
        for item in _as_list(quality.get("required_operation_types"))
        if _norm(item)
    }
    operation_score = (
        len(required_operation_types & operation_types) / len(required_operation_types)
        if required_operation_types
        else (1.0 if operations else 0.0)
    )
    required_evidence = {
        _norm(item)
        for item in _configured_list(
            "required_agent_memory_lineage",
            cfg,
            manifest_config,
        )
        if _norm(item)
    }
    observed = _token_set(payload)
    evidence_score = (
        len(required_evidence & observed) / len(required_evidence)
        if required_evidence
        else 1.0
    )
    gap_fields = (
        "blocking_gaps",
        "missing_required_evidence",
        "missing_required_signals",
        "policy_violations",
        "poisoning_failures",
        "isolation_violations",
    )
    gap_count = sum(len(_as_list(summary.get(field))) for field in gap_fields)
    policy_score = 1.0 if gap_count == 0 else 0.0
    count_checks = [
        ("min_store_count", "store_count"),
        ("min_memory_count", "memory_count"),
        ("min_operation_count", "operation_count"),
        ("min_observability_hooks", "observability_hook_count"),
        ("min_artifact_count", "artifact_count"),
    ]
    count_pass = 0
    count_total = 0
    for requirement, observed_key in count_checks:
        if requirement not in quality:
            continue
        count_total += 1
        if int(summary.get(observed_key, 0) or 0) >= int(quality[requirement]):
            count_pass += 1
    count_score = count_pass / count_total if count_total else 1.0
    score = round(
        0.35 * operation_score
        + 0.30 * evidence_score
        + 0.20 * policy_score
        + 0.15 * count_score,
        4,
    )
    return {
        "name": "agent_memory_lineage",
        "score": score,
        "reason": (
            "memory lineage is attributable and policy-clean"
            if score >= 0.99
            else "memory lineage evidence incomplete"
        ),
        "details": {
            "operation_types": sorted(operation_types),
            "required_operation_types": sorted(required_operation_types),
            "gap_count": gap_count,
            "summary": copy.deepcopy(summary),
        },
    }


def _should_score(
    layer: str,
    layers: set[str],
    env_states: Sequence[Mapping[str, Any]],
    cfg: Mapping[str, Any],
) -> bool:
    explicit = {_norm(item) for item in _as_list(cfg.get("include_components"))}
    if explicit:
        return _norm(layer) in explicit
    aliases = {
        "framework": {"framework", "runtime", "integration"},
        "framework_import": {
            "framework_import",
            "import",
            "import_manifest",
            "byo_framework",
            "byo_framework_import",
        },
        "red_team_readiness": {
            "red_team_readiness",
            "redteam_readiness",
            "readiness",
            "preflight",
            "security",
            "red_team",
            "redteam",
        },
        "world": {"world", "environment"},
        "orchestration": {"orchestration", "multi_agent"},
        "memory": {"memory", "retrieval"},
    }
    scoring_layers = {_norm(item) for item in _as_list(cfg.get("layers"))}
    if scoring_layers:
        return bool(scoring_layers & aliases.get(layer, {layer}))
    if layers & aliases.get(layer, {layer}):
        return True
    keys = _environment_keys(env_states)
    if layer == "framework":
        return "framework_trace" in keys
    if layer == "framework_import":
        return (
            "framework_import_manifest" in keys
            or bool(cfg.get("framework_import_quality"))
            or bool(cfg.get("required_framework_import"))
        )
    if layer == "red_team_readiness":
        return (
            "red_team_readiness" in keys
            or bool(cfg.get("red_team_readiness_quality"))
            or bool(cfg.get("required_red_team_readiness"))
        )
    if layer == "world":
        return "world_contract" in keys
    if layer == "orchestration":
        return "world_orchestration_replay" in keys
    if layer == "memory":
        return "agent_memory_lineage" in keys
    return False


def _framework_import_observed(
    summary: Mapping[str, Any],
    signals: set[str],
) -> set[str]:
    observed = set(signals)
    for key in (
        "observed_frameworks",
        "observed_export_types",
        "observed_signals",
        "source_keys",
    ):
        observed.update(_norm(item) for item in _as_list(summary.get(key)) if _norm(item))
    for boolean_key, signal in (
        ("source_count", "source"),
        ("passed_source_count", "passed_source"),
        ("has_target", "target"),
        ("has_adapter", "adapter"),
        ("has_trace_export", "trace_export"),
        ("has_event_stream", "event_stream"),
        ("has_lifecycle", "lifecycle"),
        ("has_capability_matrix", "capability_matrix"),
        ("has_probe_suite", "probe_suite"),
        ("has_portability_matrix", "portability_matrix"),
        ("has_observability", "observability"),
        ("has_artifacts", "artifact"),
    ):
        if summary.get(boolean_key):
            observed.add(signal)
    if summary:
        observed.add("framework_import")
        observed.add("framework_import_manifest")
    return {item for item in observed if item}


def _red_team_readiness_observed(
    summary: Mapping[str, Any],
    signals: set[str],
) -> set[str]:
    observed = set(signals)
    for key in ("observed_evidence", "observed_signals", "ready_components"):
        observed.update(_norm(item) for item in _as_list(summary.get(key)) if _norm(item))
    for boolean_key, signal in (
        ("has_target", "target"),
        ("has_framework_import", "framework_import"),
        ("framework_import_ready", "framework_import_ready"),
        ("has_red_team_campaign", "red_team_campaign"),
        ("red_team_campaign_ready", "red_team_campaign_ready"),
        ("has_workspace_run", "workspace_run"),
        ("workspace_run_ready", "workspace_run_ready"),
        ("has_trust_boundary", "trust_boundary"),
        ("trust_boundary_ready", "trust_boundary_ready"),
        ("has_control_plane", "control_plane"),
        ("control_plane_ready", "control_plane_ready"),
        ("has_observability", "observability"),
        ("has_artifacts", "artifact"),
    ):
        if summary.get(boolean_key):
            observed.add(signal)
    if summary:
        observed.update({"red_team_readiness", "readiness", "preflight", "gate"})
    return {item for item in observed if item}


def _append_red_team_readiness_count_checks(
    checks: list[dict[str, Any]],
    summary: Mapping[str, Any],
    quality: Mapping[str, Any],
) -> None:
    for requirement, observed_key in (
        ("min_ready_components", "ready_component_count"),
        ("min_artifact_count", "artifact_count"),
        ("min_observability_hooks", "observability_hook_count"),
    ):
        minimum = _int_or_none(quality.get(requirement))
        if minimum is None:
            continue
        actual = int(summary.get(observed_key, 0) or 0)
        checks.append(
            {
                "check": requirement,
                "expected": minimum,
                "actual": actual,
                "match": actual >= minimum,
            }
        )
    maximum = _int_or_none(quality.get("max_blocking_gaps"))
    if maximum is not None:
        actual = int(summary.get("blocking_gap_count", 0) or 0)
        checks.append(
            {
                "check": "max_blocking_gaps",
                "expected": maximum,
                "actual": actual,
                "match": actual <= maximum,
            }
        )


def _append_red_team_readiness_boolean_checks(
    checks: list[dict[str, Any]],
    summary: Mapping[str, Any],
    quality: Mapping[str, Any],
) -> None:
    for requirement, summary_key in (
        ("require_target", "has_target"),
        ("require_framework_import", "has_framework_import"),
        ("require_framework_import_ready", "framework_import_ready"),
        ("require_red_team_campaign", "has_red_team_campaign"),
        ("require_red_team_campaign_ready", "red_team_campaign_ready"),
        ("require_workspace_run", "has_workspace_run"),
        ("require_workspace_run_ready", "workspace_run_ready"),
        ("require_trust_boundary", "has_trust_boundary"),
        ("require_trust_boundary_ready", "trust_boundary_ready"),
        ("require_control_plane", "has_control_plane"),
        ("require_control_plane_ready", "control_plane_ready"),
        ("require_observability", "has_observability"),
        ("require_artifacts", "has_artifacts"),
    ):
        if requirement not in quality:
            continue
        expected = bool(quality.get(requirement))
        actual = bool(summary.get(summary_key))
        checks.append(
            {
                "check": requirement,
                "expected": expected,
                "actual": actual,
                "match": actual is expected,
            }
        )


def _append_red_team_readiness_required_checks(
    checks: list[dict[str, Any]],
    summary: Mapping[str, Any],
    *,
    quality: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> None:
    requirement_specs = (
        (
            "required_evidence",
            "evidence",
            "observed_evidence",
            "required_evidence",
        ),
        (
            "required_signals",
            "signals",
            "observed_signals",
            "required_signal",
        ),
        (
            "required_ready_components",
            "ready_components",
            "ready_components",
            "required_ready_component",
        ),
    )
    for primary, alias, observed_key, check_name in requirement_specs:
        required = {
            _norm(item)
            for item in (
                _as_list(quality.get(primary) or quality.get(alias))
                or _as_list(payload.get(primary))
            )
            if _norm(item)
        }
        if not required:
            continue
        observed = {
            _norm(item)
            for item in _as_list(summary.get(observed_key))
            if _norm(item)
        }
        for item in sorted(required):
            checks.append(
                {
                    "check": check_name,
                    "expected": item,
                    "actual": sorted(observed),
                    "match": item in observed,
                }
            )


def _append_framework_import_count_checks(
    checks: list[dict[str, Any]],
    summary: Mapping[str, Any],
    quality: Mapping[str, Any],
) -> None:
    for requirement, observed_key in (
        ("min_source_count", "source_count"),
        ("min_passed_sources", "passed_source_count"),
        ("min_artifact_count", "artifact_count"),
        ("min_observability_hooks", "observability_hook_count"),
    ):
        minimum = _int_or_none(quality.get(requirement))
        if minimum is None:
            continue
        actual = int(summary.get(observed_key, 0) or 0)
        checks.append(
            {
                "check": requirement,
                "expected": minimum,
                "actual": actual,
                "match": actual >= minimum,
            }
        )
    maximum = _int_or_none(quality.get("max_failed_sources"))
    if maximum is not None:
        actual = int(summary.get("failed_source_count", 0) or 0)
        checks.append(
            {
                "check": "max_failed_sources",
                "expected": maximum,
                "actual": actual,
                "match": actual <= maximum,
            }
        )


def _append_framework_import_boolean_checks(
    checks: list[dict[str, Any]],
    summary: Mapping[str, Any],
    quality: Mapping[str, Any],
) -> None:
    for requirement, summary_key in (
        ("require_target", "has_target"),
        ("require_adapter", "has_adapter"),
        ("require_trace_export", "has_trace_export"),
        ("require_event_stream", "has_event_stream"),
        ("require_lifecycle", "has_lifecycle"),
        ("require_capability_matrix", "has_capability_matrix"),
        ("require_probe_suite", "has_probe_suite"),
        ("require_portability_matrix", "has_portability_matrix"),
        ("require_observability", "has_observability"),
        ("require_artifacts", "has_artifacts"),
    ):
        if requirement not in quality:
            continue
        expected = bool(quality.get(requirement))
        actual = bool(summary.get(summary_key))
        checks.append(
            {
                "check": requirement,
                "expected": expected,
                "actual": actual,
                "match": actual is expected,
            }
        )


def _append_framework_import_required_checks(
    checks: list[dict[str, Any]],
    summary: Mapping[str, Any],
    *,
    quality: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> None:
    requirement_specs = (
        (
            "required_sources",
            "sources",
            "source_keys",
            "required_source",
        ),
        (
            "required_frameworks",
            "frameworks",
            "observed_frameworks",
            "required_framework",
        ),
        (
            "required_export_types",
            "export_types",
            "observed_export_types",
            "required_export_type",
        ),
        (
            "required_signals",
            "signals",
            "observed_signals",
            "required_signal",
        ),
    )
    for primary, alias, observed_key, check_name in requirement_specs:
        required = {
            _norm(item)
            for item in (
                _as_list(quality.get(primary) or quality.get(alias))
                or _as_list(payload.get(primary))
            )
            if _norm(item)
        }
        if not required:
            continue
        observed = {
            _norm(item)
            for item in _as_list(summary.get(observed_key))
            if _norm(item)
        }
        for item in sorted(required):
            checks.append(
                {
                    "check": check_name,
                    "expected": item,
                    "actual": sorted(observed),
                    "match": item in observed,
                }
            )


def _environment_states(report: Any) -> list[Mapping[str, Any]]:
    states: list[Mapping[str, Any]] = []
    for case in _report_cases(report):
        metadata = _as_mapping(_get(case, "metadata"))
        state = _as_mapping(metadata.get("environment_state"))
        if state:
            states.append(state)
    metadata = _as_mapping(_get(report, "metadata"))
    state = _as_mapping(metadata.get("environment_state"))
    if state:
        states.append(state)
    direct = _as_mapping(_get(report, "environment_state"))
    if direct:
        states.append(direct)
    return states


def _report_cases(report: Any) -> list[Any]:
    results = _get(report, "results")
    if isinstance(results, Sequence) and not isinstance(results, (str, bytes)):
        return list(results)
    if isinstance(report, Mapping):
        nested = report.get("report")
        if nested is not None and nested is not report:
            return _report_cases(nested)
    return [report]


def _tool_names(report: Any) -> set[str]:
    names: set[str] = set()
    for case in _report_cases(report):
        for raw in _as_list(_get(case, "tool_calls")):
            name = _tool_name(raw)
            if name:
                names.add(name)
        for message in _as_list(_get(case, "messages")):
            for raw in _as_list(_get(message, "tool_calls")):
                name = _tool_name(raw)
                if name:
                    names.add(name)
        for event in _as_list(_get(case, "events")):
            name = _tool_name(event)
            if name:
                names.add(name)
    return names


def _tool_name(raw: Any) -> str:
    item = _as_mapping(raw)
    return str(
        item.get("name")
        or item.get("tool_name")
        or item.get("function")
        or _path(item, "function.name")
        or ""
    )


def _first_payload(
    env_states: Sequence[Mapping[str, Any]],
    key: str,
) -> dict[str, Any]:
    for state in env_states:
        payload = _as_mapping(state.get(key))
        if payload:
            return copy.deepcopy(payload)
    return {}


def _nested_world_contract(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}
    candidates = [
        _path(payload, "world_contract"),
        _path(payload, "state.world_contract"),
        _path(payload, "world_attack_replay.world_contract"),
        _path(payload, "state.world_attack_replay.world_contract"),
        _path(payload, "world_attack_replay.state.world_contract"),
        _path(payload, "state.world_attack_replay.state.world_contract"),
    ]
    for candidate in candidates:
        mapped = _as_mapping(candidate)
        if mapped:
            return copy.deepcopy(mapped)
    return {}


def _manifest_agent_report_config(
    manifest: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    if not manifest:
        return {}
    return copy.deepcopy(
        _as_mapping(
            _path(_as_mapping(manifest), "evaluation.agent_report.config")
            or _path(_as_mapping(manifest), "agent_report.config")
            or {}
        )
    )


def _target_layers(
    *,
    manifest: Optional[Mapping[str, Any]],
    candidate: Optional[AgentCandidate],
    config: Mapping[str, Any],
) -> set[str]:
    layers = {_norm(item) for item in _as_list(config.get("layers"))}
    if candidate is not None:
        layers.update(_norm(item) for item in candidate.layers)
    if manifest:
        layers.update(
            _norm(item)
            for item in _as_list(_path(_as_mapping(manifest), "optimization.target.layers"))
        )
    return {item for item in layers if item}


def _environment_keys(env_states: Sequence[Mapping[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for state in env_states:
        keys.update(str(key) for key in state)
    return keys


def _configured_list(
    key: str,
    cfg: Mapping[str, Any],
    manifest_config: Mapping[str, Any],
    *,
    nested_keys: tuple[str, str] = (),
) -> list[str]:
    for source in (cfg, manifest_config):
        value = source.get(key)
        if value:
            return [str(item) for item in _as_list(value)]
        if nested_keys:
            value = _path(source, ".".join(nested_keys))
            if value:
                return [str(item) for item in _as_list(value)]
    return []


def _first_mapping(*values: Any) -> dict[str, Any]:
    for value in values:
        mapped = _as_mapping(value)
        if mapped:
            return copy.deepcopy(mapped)
    return {}


def _world_success_score(
    summary: Mapping[str, Any],
    success_results: Sequence[Any],
    quality: Mapping[str, Any],
) -> float:
    terminal = _norm(summary.get("terminal_status"))
    expected_terminal = _norm(
        quality.get("required_terminal_status")
        or quality.get("terminal_status")
        or "success"
    )
    if terminal:
        return 1.0 if terminal == expected_terminal else 0.0
    if success_results:
        return 1.0 if all(_as_mapping(item).get("pass") is True for item in success_results) else 0.0
    return 0.0


def _world_violation_count(payload: Mapping[str, Any]) -> int:
    count = 0
    for item in _as_list(payload.get("transition_log")):
        count += len(_as_list(_as_mapping(item).get("violations")))
    for item in _as_list(payload.get("invariant_results")):
        if _as_mapping(item).get("pass") is False:
            count += 1
    summary = _as_mapping(payload.get("summary"))
    for key in ("violation_count", "invariant_violation_count"):
        if key in summary:
            try:
                count += int(summary[key])
            except (TypeError, ValueError):
                pass
    return count


def _contains_subset(value: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    for key, expected_value in expected.items():
        if key not in value:
            return False
        actual_value = value[key]
        if isinstance(expected_value, Mapping):
            if not isinstance(actual_value, Mapping):
                return False
            if not _contains_subset(actual_value, expected_value):
                return False
        elif actual_value != expected_value:
            return False
    return True


def _token_set(value: Any) -> set[str]:
    tokens: set[str] = set()
    _collect_tokens(value, tokens)
    return {token for token in tokens if token}


def _collect_tokens(value: Any, tokens: set[str]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            tokens.add(_norm(key))
            _collect_tokens(item, tokens)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            _collect_tokens(item, tokens)
        return
    if isinstance(value, (str, int, float, bool)):
        raw = str(value)
        tokens.add(_norm(raw))
        for part in raw.replace(".", "_").replace("-", "_").split("_"):
            tokens.add(_norm(part))


def _missing_component(name: str, reason: str) -> dict[str, Any]:
    return {
        "name": name,
        "score": 0.0,
        "reason": reason,
        "details": {},
    }


def _evidence_reason(components: Sequence[Mapping[str, Any]]) -> str:
    weak = [str(item["name"]) for item in components if float(item["score"]) < 0.99]
    if not weak:
        return "Simulation evidence satisfies framework/world/orchestration contract."
    return "Simulation evidence gaps: " + ", ".join(weak)


def _float_mapping(value: Any) -> dict[str, float]:
    mapped = _as_mapping(value)
    result: dict[str, float] = {}
    for key, item in mapped.items():
        try:
            result[str(key)] = float(item)
        except (TypeError, ValueError):
            continue
    return result


def _int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _path(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            return None
    return current


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    if hasattr(value, "dict"):
        dumped = value.dict()
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    return {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _debug_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)
