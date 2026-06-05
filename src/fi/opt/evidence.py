from __future__ import annotations

import copy
import json
from typing import Any, Mapping, Optional, Sequence

from .targets import AgentCandidate, CandidateEvaluation


DEFAULT_SIMULATION_EVIDENCE_WEIGHTS: dict[str, float] = {
    "tool_coverage": 1.0,
    "framework_trace": 2.0,
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
    turns framework trace, runtime semantic, memory-lineage, orchestration, tool,
    and world-contract evidence into a single optimizer-grade score.
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
        "world": {"world", "environment"},
        "orchestration": {"orchestration", "multi_agent"},
        "memory": {"memory", "retrieval"},
    }
    if layers & aliases.get(layer, {layer}):
        return True
    keys = _environment_keys(env_states)
    if layer == "framework":
        return "framework_trace" in keys
    if layer == "world":
        return "world_contract" in keys
    if layer == "orchestration":
        return "world_orchestration_replay" in keys
    if layer == "memory":
        return "agent_memory_lineage" in keys
    return False


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
