from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Any


AGENT_LEARNING_CLI_SCHEMA_VERSION = "agent-learning.cli.v1"
AGENT_LEARNING_EVAL_SCHEMA_VERSION = "agent-learning.eval.v1"

_PUBLIC_VALUE_REPLACEMENTS = {
    "agent-simulate.cli.v1": AGENT_LEARNING_CLI_SCHEMA_VERSION,
    "agent-simulate.eval.v1": AGENT_LEARNING_EVAL_SCHEMA_VERSION,
    "agent-simulate.eval-optimization.v1": "agent-learning.eval-optimization.v1",
    "agent-simulate.actions.v1": "agent-learning.actions.v1",
    "agent-simulate.action-run.v1": "agent-learning.action-run.v1",
    "agent-simulate.baseline.v1": "agent-learning.baseline.v1",
    "agent-simulate.compare.v1": "agent-learning.compare.v1",
    "agent-simulate.init.v1": "agent-learning.init.v1",
    "agent-simulate.optimization.v1": "agent-learning.optimization.v1",
    "agent-simulate.redteam.v1": "agent-learning.redteam.v1",
    "agent-simulate.regression_promotion.v1": (
        "agent-learning.regression-promotion.v1"
    ),
    "agent-simulate.replay.v1": "agent-learning.replay.v1",
    "agent-simulate.report.v1": "agent-learning.report.v1",
    "agent_simulate": "agent_learning_kit",
    "agent-simulate": "agent-learning-kit",
}


def public_schema_value(value: str) -> str:
    """Return the public Agent Learning value for a vendored exact value."""

    return _PUBLIC_VALUE_REPLACEMENTS.get(value, value)


def normalize_public_payload(value: Any) -> Any:
    """Normalize vendored exact strings in public SDK artifacts."""

    if isinstance(value, str):
        return public_schema_value(value)
    if isinstance(value, Mapping):
        return {
            key: normalize_public_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [normalize_public_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(normalize_public_payload(item) for item in value)
    return copy.deepcopy(value)


def public_payload(payload: Mapping[str, Any], *, kind: str | None = None) -> dict[str, Any]:
    """Return a normalized public mapping, optionally forcing its top-level kind."""

    result = normalize_public_payload(payload)
    if not isinstance(result, dict):
        result = dict(payload)
    if kind is not None:
        result["kind"] = kind
    return result


def with_optimization_candidate_lineage(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Attach a content-addressed candidate lineage contract when possible."""

    result = copy.deepcopy(dict(payload))
    optimization = _as_mapping(result.get("optimization"))
    history = [_as_mapping(item) for item in _as_list(optimization.get("history"))]
    history = [item for item in history if item]
    if not optimization or not history:
        return result

    lineage = _optimization_candidate_lineage(result, optimization, history)
    if not lineage["rows"]:
        return result
    result["optimization_candidate_lineage"] = lineage
    optimization["candidate_lineage"] = copy.deepcopy(lineage)
    result["optimization"] = optimization

    summary = _as_mapping(result.get("summary"))
    summary["candidate_lineage_count"] = lineage["candidate_count"]
    summary["candidate_lineage_content_addressed_count"] = lineage[
        "content_addressed_count"
    ]
    summary["candidate_lineage_selected_score_delta"] = lineage[
        "selected_score_delta_from_seed"
    ]
    result["summary"] = summary
    return result


def _optimization_candidate_lineage(
    payload: Mapping[str, Any],
    optimization: Mapping[str, Any],
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    best_candidate_id = str(
        optimization.get("best_candidate_id")
        or _as_mapping(payload.get("summary")).get("best_candidate_id")
        or ""
    )
    rows = [
        _optimization_candidate_lineage_row(
            item,
            index=index,
            best_candidate_id=best_candidate_id,
        )
        for index, item in enumerate(history)
    ]
    rows = [item for item in rows if item.get("candidate_id")]
    ranked = sorted(
        rows,
        key=lambda item: (
            _numeric_or_min(item.get("score")),
            -int(item.get("iteration_index") or 0),
        ),
        reverse=True,
    )
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank
    rows_by_id = {str(row.get("candidate_id")): row for row in ranked}
    selected = rows_by_id.get(best_candidate_id) if best_candidate_id else None
    seed = min(rows, key=lambda item: int(item.get("iteration_index") or 0))
    score_values = [
        float(row["score"])
        for row in rows
        if isinstance(row.get("score"), (int, float))
    ]
    selected_delta = None
    if selected and isinstance(selected.get("score"), (int, float)) and isinstance(
        seed.get("score"),
        (int, float),
    ):
        selected_delta = round(float(selected["score"]) - float(seed["score"]), 6)
    return {
        "kind": "agent-learning.optimization.candidate-lineage.v1",
        "selected_candidate_id": best_candidate_id or None,
        "candidate_count": len({row["candidate_id"] for row in rows}),
        "history_count": len(rows),
        "content_addressed_count": sum(
            1 for row in rows if row.get("content_addressed")
        ),
        "selected_score_delta_from_seed": selected_delta,
        "score_range": {
            "min": min(score_values) if score_values else None,
            "max": max(score_values) if score_values else None,
        },
        "search_paths": sorted(
            {
                str(path)
                for row in rows
                for path in _as_list(row.get("search_paths"))
                if str(path)
            }
        ),
        "patch_paths": sorted(
            {
                str(path)
                for row in rows
                for path in _as_list(row.get("patch_paths"))
                if str(path)
            }
        ),
        "metric_names": sorted(
            {
                str(metric)
                for row in rows
                for metric in _as_mapping(row.get("metrics"))
                if str(metric)
            }
        ),
        "rows": sorted(
            rows,
            key=lambda item: int(item.get("iteration_index") or 0),
        ),
    }


def _optimization_candidate_lineage_row(
    item: Mapping[str, Any],
    *,
    index: int,
    best_candidate_id: str,
) -> dict[str, Any]:
    candidate_id = str(item.get("candidate_id") or f"candidate_{index}")
    patch = _as_mapping(item.get("patch") or item.get("candidate_patch"))
    metrics = _as_mapping(item.get("metrics"))
    report_summary = _as_mapping(item.get("report_summary"))
    report = _as_mapping(item.get("report"))
    if not report_summary and report:
        report_summary = _as_mapping(report.get("summary"))
    candidate_config = _as_mapping(item.get("candidate_config"))
    freeze = {
        "kind": "agent-learning.optimization.candidate-freeze.v1",
        "hash_algorithm": "sha256",
        "patch_sha256": _json_sha256(patch),
        "candidate_config_sha256": _json_sha256(candidate_config)
        if candidate_config
        else None,
        "metrics_sha256": _json_sha256(metrics),
        "report_summary_sha256": _json_sha256(report_summary)
        if report_summary
        else None,
    }
    freeze["content_addressed"] = bool(
        freeze["patch_sha256"] and freeze["metrics_sha256"]
    )
    return {
        "kind": "agent-learning.optimization.candidate-lineage-row.v1",
        "candidate_id": candidate_id,
        "iteration_index": index,
        "selected": bool(best_candidate_id and candidate_id == best_candidate_id),
        "score": item.get("score"),
        "evaluation_score": item.get("evaluation_score"),
        "evaluation_passed": item.get("evaluation_passed"),
        "patch_paths": _patch_leaf_paths(patch),
        "search_paths": sorted(
            str(path) for path in _as_list(item.get("search_paths")) if str(path)
        ),
        "metrics": metrics,
        "finding_count": len(_as_list(item.get("findings"))),
        "report_status": report.get("status") or report_summary.get("status"),
        "report_score": report_summary.get("score"),
        "proposal_role": item.get("proposal_role"),
        "proposal_round": item.get("proposal_round"),
        "proposal_reason": item.get("proposal_reason"),
        "freeze": freeze,
        "content_addressed": freeze["content_addressed"],
    }


def _patch_leaf_paths(value: Any, prefix: str = "") -> list[str]:
    if isinstance(value, Mapping):
        paths: list[str] = []
        for key, item in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(_patch_leaf_paths(item, child_prefix))
        return paths
    if isinstance(value, list):
        paths = []
        for index, item in enumerate(value):
            child_prefix = f"{prefix}.{index}" if prefix else str(index)
            paths.extend(_patch_leaf_paths(item, child_prefix))
        return paths
    return [prefix] if prefix else []


def _json_sha256(value: Any) -> str:
    data = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _numeric_or_min(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return float("-inf")


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
