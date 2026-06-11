from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional

from .types import OptimizationResult


def build_optimizer_society_trace(
    result: OptimizationResult,
    *,
    name: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Export a council/society optimization run as portable trace evidence."""

    result_metadata = dict(result.metadata or {})
    roles = _role_records(result_metadata)
    proposals = [_proposal_record(item) for item in result.history]
    proposals = [item for item in proposals if item]
    search_paths = sorted(str(path) for path in result_metadata.get("search_paths", []) if str(path))
    diagnostics = [
        dict(item)
        for item in _as_list(result_metadata.get("diagnostics"))
        if isinstance(item, Mapping)
    ]
    role_credit = _role_credit(proposals)
    best_candidate_id = str(result_metadata.get("best_candidate_id") or getattr(result.best_candidate, "id", "") or "")
    final_score = float(result.final_score)
    governance = _governance_records(
        result_metadata=result_metadata,
        roles=roles,
        proposals=proposals,
        diagnostics=diagnostics,
        search_paths=search_paths,
        role_credit=role_credit,
        best_candidate_id=best_candidate_id,
        final_score=final_score,
    )
    signals = _signals(
        roles=roles,
        proposals=proposals,
        diagnostics=diagnostics,
        search_paths=search_paths,
        role_credit=role_credit,
        best_candidate_id=best_candidate_id,
        governance=governance,
    )
    summary = _summary(
        roles=roles,
        proposals=proposals,
        diagnostics=diagnostics,
        search_paths=search_paths,
        role_credit=role_credit,
        best_candidate_id=best_candidate_id,
        final_score=final_score,
        rounds=_as_list(result_metadata.get("rounds")),
        governance=governance,
    )
    return {
        "kind": "optimizer_society_trace",
        "name": name or str(result_metadata.get("target_name") or "optimizer-society-trace"),
        "optimizer": str(result_metadata.get("optimizer") or "agent-opt"),
        "strategy": result_metadata.get("strategy"),
        "roles": roles,
        "proposals": proposals,
        "rounds": [dict(item) for item in _as_list(result_metadata.get("rounds")) if isinstance(item, Mapping)],
        "diagnostics": diagnostics,
        "search_paths": search_paths,
        "role_credit": role_credit,
        "governance": governance,
        "best_candidate_id": best_candidate_id or None,
        "final_score": final_score,
        "signals": sorted(signals),
        "summary": summary,
        "metadata": {
            "source": "agent-opt",
            **{k: v for k, v in result_metadata.items() if k not in {"rounds", "diagnostics"}},
            **dict(metadata or {}),
        },
    }


def _role_records(metadata: Mapping[str, Any]) -> list[Dict[str, Any]]:
    role_graph = [
        dict(item)
        for item in _as_list(metadata.get("role_graph"))
        if isinstance(item, Mapping)
    ]
    if role_graph:
        return role_graph
    return [{"name": str(role)} for role in _as_list(metadata.get("roles")) if str(role)]


def _proposal_record(history: Any) -> Dict[str, Any]:
    item_metadata = dict(getattr(history, "metadata", {}) or {})
    proposal_metadata = dict(item_metadata.get("proposal_metadata") or {})
    role = str(item_metadata.get("proposal_role") or "unknown")
    patch = dict(item_metadata.get("patch") or {})
    return {
        "id": str(getattr(history, "candidate_id", None) or item_metadata.get("candidate_id") or ""),
        "candidate_id": str(getattr(history, "candidate_id", None) or item_metadata.get("candidate_id") or ""),
        "role": role,
        "round": item_metadata.get("proposal_round"),
        "score": float(getattr(history, "average_score", 0.0)),
        "reason": str(item_metadata.get("proposal_reason") or item_metadata.get("reason") or ""),
        "parent_ids": [
            str(parent)
            for parent in _as_list(item_metadata.get("proposal_parent_ids"))
            if str(parent)
        ],
        "patch": patch,
        "search_paths": sorted(str(path) for path in patch.keys()),
        "role_kind": str(item_metadata.get("role_kind") or proposal_metadata.get("role_kind") or ""),
        "role_archetype": str(item_metadata.get("role_archetype") or proposal_metadata.get("role_archetype") or ""),
        "metadata": proposal_metadata,
    }


def _role_credit(proposals: Iterable[Mapping[str, Any]]) -> list[Dict[str, Any]]:
    credit: Dict[str, Dict[str, Any]] = {}
    for proposal in proposals:
        role = str(proposal.get("role") or "unknown")
        key = _normalize(role) or "unknown"
        score = _optional_float(proposal.get("score"))
        entry = credit.setdefault(
            key,
            {
                "role": role,
                "proposal_count": 0,
                "evaluated_count": 0,
                "best_score": None,
                "best_candidate_id": None,
                "search_paths": set(),
            },
        )
        entry["proposal_count"] += 1
        entry["search_paths"].update(str(path) for path in _as_list(proposal.get("search_paths")) if str(path))
        if score is None:
            continue
        entry["evaluated_count"] += 1
        if entry["best_score"] is None or score > float(entry["best_score"]):
            entry["best_score"] = score
            entry["best_candidate_id"] = proposal.get("candidate_id")
    return [
        {
            **entry,
            "search_paths": sorted(entry["search_paths"]),
        }
        for entry in sorted(credit.values(), key=lambda item: str(item["role"]))
    ]


def _signals(
    *,
    roles: list[Mapping[str, Any]],
    proposals: list[Mapping[str, Any]],
    diagnostics: list[Mapping[str, Any]],
    search_paths: list[str],
    role_credit: list[Mapping[str, Any]],
    best_candidate_id: str,
    governance: Mapping[str, Any],
) -> set[str]:
    signals = {"optimizer", "society_trace"}
    if roles:
        signals.add("role")
    if any(role.get("proposal_kind") for role in roles) or any(proposal.get("role_kind") for proposal in proposals):
        signals.add("role_graph")
    if any(role.get("archetype") for role in roles) or any(proposal.get("role_archetype") for proposal in proposals):
        signals.add("archetype")
    if proposals:
        signals.update({"proposal", "candidate", "evaluation", "score", "stop"})
    if diagnostics:
        signals.add("diagnostic")
    if search_paths or any(proposal.get("search_paths") for proposal in proposals):
        signals.add("search_path")
    if role_credit:
        signals.add("credit")
    governance_signals = {
        _normalize(signal)
        for signal in _as_list(governance.get("signals"))
        if _normalize(signal)
    }
    if governance_signals or _as_list(governance.get("checks")):
        signals.update({"governance", *governance_signals})
    if best_candidate_id:
        signals.add("best_candidate")
    role_tokens = {
        _normalize(proposal.get("role"))
        for proposal in proposals
    } | {
        _normalize(proposal.get("role_kind"))
        for proposal in proposals
    }
    if role_tokens & {"critic", "adversary", "vidura", "krishna"}:
        signals.add("critique")
    if role_tokens & {"synthesizer", "coverage_synthesis", "sangha"}:
        signals.add("synthesis")
    if role_tokens & {"steward", "dharma_steward"}:
        signals.add("steward")
    return signals


def _summary(
    *,
    roles: list[Mapping[str, Any]],
    proposals: list[Mapping[str, Any]],
    diagnostics: list[Mapping[str, Any]],
    search_paths: list[str],
    role_credit: list[Mapping[str, Any]],
    best_candidate_id: str,
    final_score: float,
    rounds: list[Any],
    governance: Mapping[str, Any],
) -> Dict[str, Any]:
    candidate_ids = [str(proposal.get("candidate_id") or "") for proposal in proposals if proposal.get("candidate_id")]
    role_tokens = {
        _normalize(proposal.get("role"))
        for proposal in proposals
    } | {
        _normalize(proposal.get("role_kind"))
        for proposal in proposals
    }
    governance_summary = dict(governance.get("summary") or {})
    summary = {
        "role_count": len(roles),
        "proposal_count": len(proposals),
        "evaluation_count": len(proposals),
        "round_count": len(rounds) or len({proposal.get("round") for proposal in proposals if proposal.get("round") is not None}),
        "diagnostic_count": len(diagnostics),
        "search_path_count": len(search_paths),
        "role_credit_count": len(role_credit),
        "duplicate_candidate_count": max(0, len(candidate_ids) - len(set(candidate_ids))),
        "best_candidate_id": best_candidate_id or None,
        "final_score": final_score,
        "has_role_graph": any(role.get("proposal_kind") for role in roles),
        "has_critique": bool(role_tokens & {"critic", "adversary", "vidura", "krishna"}),
        "has_synthesis": bool(role_tokens & {"synthesizer", "coverage_synthesis", "sangha"}),
        "has_steward": bool(role_tokens & {"steward", "dharma_steward"}),
        "terminal_status": "completed",
    }
    for key in (
        "governance_check_count",
        "governance_passed_count",
        "governance_pass_rate",
        "has_governance",
        "has_role_diversity",
        "has_mediator",
        "has_contract_gate",
        "has_rollback",
        "has_locality",
        "has_dependency_audit",
    ):
        if key in governance_summary:
            summary[key] = governance_summary[key]
    return summary


def _governance_records(
    *,
    result_metadata: Mapping[str, Any],
    roles: list[Mapping[str, Any]],
    proposals: list[Mapping[str, Any]],
    diagnostics: list[Mapping[str, Any]],
    search_paths: list[str],
    role_credit: list[Mapping[str, Any]],
    best_candidate_id: str,
    final_score: float,
) -> Dict[str, Any]:
    explicit = result_metadata.get("governance") or result_metadata.get("optimizer_governance")
    explicit_checks = []
    explicit_signals = []
    if isinstance(explicit, Mapping):
        explicit_checks = _as_list(explicit.get("checks"))
        explicit_signals = _as_list(explicit.get("signals"))
    elif explicit:
        explicit_checks = _as_list(explicit)
    explicit_checks.extend(_as_list(result_metadata.get("governance_checks")))

    role_names = {
        _normalize(role.get("name") or role.get("role"))
        for role in roles
    }
    role_kinds = {
        _normalize(role.get("proposal_kind"))
        for role in roles
    } | {
        _normalize(proposal.get("role_kind"))
        for proposal in proposals
    }
    proposal_roles = {
        _normalize(proposal.get("role"))
        for proposal in proposals
    }
    path_set = {str(path) for path in search_paths if str(path)}
    patched_paths = {
        str(path)
        for proposal in proposals
        for path in _as_list(proposal.get("search_paths"))
        if str(path)
    }
    patched_paths.update(
        str(path)
        for proposal in proposals
        for path in dict(proposal.get("patch") or {}).keys()
        if str(path)
    )
    non_seed_roles = {role for role in proposal_roles if role and role not in {"seed", "unknown"}}
    has_critique = bool((proposal_roles | role_kinds | role_names) & {"critic", "adversary", "vidura", "krishna"})
    has_synthesis = bool((proposal_roles | role_kinds | role_names) & {"synthesizer", "coverage_synthesis", "sangha"})
    has_steward = bool((proposal_roles | role_kinds | role_names) & {"steward", "dharma_steward"})
    has_contract_path = any(
        any(token in path for token in ("contract", "policy", "security", "safety", "guardrail"))
        for path in path_set | patched_paths
    )
    has_dependency_audit = bool(
        result_metadata.get("leave_one_backend_dependency")
        or result_metadata.get("leave_one_backend_out")
        or result_metadata.get("backend_lineage")
    )
    checks = [
        _governance_check(
            "role_diversity",
            len(non_seed_roles) >= 3 or len(role_names) >= 3,
            evidence={"roles": sorted(role_names | non_seed_roles)},
            reason="multiple independent proposal roles reduce single-strategy collapse",
        ),
        _governance_check(
            "topology_adaptation",
            bool(role_kinds) or bool(result_metadata.get("role_graph")),
            evidence={"role_kinds": sorted(kind for kind in role_kinds if kind)},
            reason="role graph or role-kind metadata records the optimizer topology",
        ),
        _governance_check(
            "adversarial_review",
            has_critique,
            evidence={"roles": sorted(role_names | proposal_roles | role_kinds)},
            reason="critic or adversary role challenges candidate changes",
        ),
        _governance_check(
            "mediator_review",
            has_synthesis,
            evidence={"roles": sorted(role_names | proposal_roles | role_kinds)},
            reason="synthesis role combines compatible local repairs",
        ),
        _governance_check(
            "steward_review",
            has_steward,
            evidence={"roles": sorted(role_names | proposal_roles | role_kinds)},
            reason="steward role tests minimality and process safety",
        ),
        _governance_check(
            "credit_assignment",
            bool(role_credit),
            evidence={"credit_roles": [str(item.get("role")) for item in role_credit]},
            reason="role credit ledger connects outcomes to proposal sources",
        ),
        _governance_check(
            "search_locality",
            bool(path_set) and patched_paths.issubset(path_set),
            evidence={"search_paths": sorted(path_set), "patched_paths": sorted(patched_paths)},
            reason="candidate patches stay inside diagnosed search paths",
        ),
        _governance_check(
            "contract_gate",
            has_contract_path,
            evidence={"search_paths": sorted(path_set), "diagnostics": diagnostics},
            reason="policy/security/contract paths are tied to diagnosed failures",
        ),
        _governance_check(
            "rollback_check",
            has_steward or any(_as_list(proposal.get("parent_ids")) for proposal in proposals),
            evidence={"has_steward": has_steward},
            reason="steward or parent lineage supports rollback/minimality audit",
        ),
        _governance_check(
            "terminal_selection",
            bool(best_candidate_id) and final_score is not None,
            evidence={"best_candidate_id": best_candidate_id, "final_score": final_score},
            reason="trace names the selected candidate and final score",
        ),
        _governance_check(
            "dependency_audit",
            has_dependency_audit,
            evidence={
                "leave_one_backend_dependency": result_metadata.get("leave_one_backend_dependency"),
                "backend_lineage": result_metadata.get("backend_lineage"),
            },
            reason="multi-backend runs should expose dependency or backend-lineage evidence",
        ),
    ]
    checks.extend(_normalize_governance_check(item) for item in explicit_checks)
    checks = [check for check in checks if check]
    seen: Dict[str, int] = {}
    deduped_checks: list[Dict[str, Any]] = []
    for check in checks:
        name = _normalize(check.get("name"))
        if not name:
            continue
        if name in seen:
            existing_index = seen[name]
            if check.get("passed") and not deduped_checks[existing_index].get("passed"):
                deduped_checks[existing_index] = check
            continue
        seen[name] = len(deduped_checks)
        deduped_checks.append(check)
    signals = {
        "governance",
        *(_normalize(check.get("name")) for check in deduped_checks if check.get("passed")),
        *(_normalize(signal) for signal in explicit_signals if _normalize(signal)),
    }
    passed_count = sum(1 for check in deduped_checks if check.get("passed"))
    check_count = len(deduped_checks)
    summary = {
        "governance_check_count": check_count,
        "governance_passed_count": passed_count,
        "governance_pass_rate": round(passed_count / check_count, 4) if check_count else 0.0,
        "has_governance": check_count > 0,
        "has_role_diversity": _governance_passed(deduped_checks, "role_diversity"),
        "has_mediator": _governance_passed(deduped_checks, "mediator_review"),
        "has_contract_gate": _governance_passed(deduped_checks, "contract_gate"),
        "has_rollback": _governance_passed(deduped_checks, "rollback_check"),
        "has_locality": _governance_passed(deduped_checks, "search_locality"),
        "has_dependency_audit": _governance_passed(deduped_checks, "dependency_audit"),
    }
    return {
        "checks": deduped_checks,
        "signals": sorted(signal for signal in signals if signal),
        "summary": summary,
    }


def _governance_check(
    name: str,
    passed: bool,
    *,
    evidence: Optional[Mapping[str, Any]] = None,
    reason: str = "",
) -> Dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "reason": reason,
        "evidence": dict(evidence or {}),
    }


def _normalize_governance_check(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        check = dict(value)
        name = _normalize(check.get("name") or check.get("check") or check.get("signal"))
        if not name:
            return {}
        return {
            "name": name,
            "passed": bool(check.get("passed", check.get("match", True))),
            "reason": str(check.get("reason") or ""),
            "evidence": dict(check.get("evidence") or {}),
        }
    name = _normalize(value)
    if not name:
        return {}
    return {"name": name, "passed": True, "reason": "", "evidence": {}}


def _governance_passed(checks: Iterable[Mapping[str, Any]], name: str) -> bool:
    normalized = _normalize(name)
    return any(_normalize(check.get("name")) == normalized and bool(check.get("passed")) for check in checks)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_").replace(".", "_")
