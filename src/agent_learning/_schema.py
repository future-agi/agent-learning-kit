from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


AGENT_LEARNING_CLI_SCHEMA_VERSION = "agent-learning.cli.v1"
AGENT_LEARNING_EVAL_SCHEMA_VERSION = "agent-learning.eval.v1"

_PUBLIC_VALUE_REPLACEMENTS = {
    "agent-simulate.cli.v1": AGENT_LEARNING_CLI_SCHEMA_VERSION,
    "agent-simulate.eval.v1": AGENT_LEARNING_EVAL_SCHEMA_VERSION,
    "agent-simulate.eval-optimization.v1": "agent-learning.eval-optimization.v1",
    "agent-simulate.actions.v1": "agent-learning.actions.v1",
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
