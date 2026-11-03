"""What an agent and its world were shown to support, as evidence the platform turns into a status.

The catalogue is versioned with ALK. Evidence is derived from the certification a build already writes: no new run,
no payloads or secrets, only check results, counts and reasons. A capability nothing in the build checked is
`not_run`, which the platform shows as Untested, never as Supported.
"""

from __future__ import annotations

from typing import Any

from .certification import CheckStatus, HarnessCertification

CATALOG_VERSION = "1"
EVIDENCE_SCHEMA_VERSION = "futureagi.capability-evidence.v1"

# id, label, what is checked, what it depends on, the kind of fix a failure points to.
CATALOGUE: tuple[dict[str, Any], ...] = (
    {"id": "start_health", "label": "Start and health", "checks": "the source or runtime starts and stays up",
     "depends_on": ["agent_runtime", "credentials", "source", "environment"], "fix_type": "startup"},
    {"id": "one_turn", "label": "One turn", "checks": "the agent answers a message",
     "depends_on": ["start_health"], "fix_type": "message_mapping"},
    {"id": "multi_turn", "label": "Multi-turn", "checks": "the agent keeps a conversation across turns",
     "depends_on": ["one_turn"], "fix_type": "message_mapping"},
    {"id": "streaming", "label": "Streaming", "checks": "partial replies arrive as they are produced",
     "depends_on": ["one_turn"], "fix_type": "message_mapping"},
    {"id": "files_artifacts", "label": "Files and artifacts", "checks": "files the agent produces are captured",
     "depends_on": ["start_health"], "fix_type": "evidence"},
    {"id": "tool_discovery", "label": "Tool discovery", "checks": "the agent's tools are found and described",
     "depends_on": ["source"], "fix_type": "tool_routing"},
    {"id": "tool_routing", "label": "Tool routing", "checks": "the agent's tool calls reach the simulated world",
     "depends_on": ["tool_discovery", "environment"], "fix_type": "tool_routing"},
    {"id": "state_evidence", "label": "State evidence", "checks": "the world's state can be read after a trial",
     "depends_on": ["environment"], "fix_type": "evidence"},
    {"id": "audio_evidence", "label": "Audio evidence", "checks": "the call's audio is recorded",
     "depends_on": ["start_health"], "fix_type": "evidence"},
    {"id": "browser", "label": "Browser", "checks": "a browser agent works inside the environment",
     "depends_on": ["start_health"], "fix_type": "none"},
    {"id": "child_agents", "label": "Child agents and skills", "checks": "workers and delegation are attributed",
     "depends_on": ["tool_routing"], "fix_type": "evidence"},
    {"id": "cancel", "label": "Cancel", "checks": "a trial can be stopped and keeps its partial evidence",
     "depends_on": ["start_health"], "fix_type": "none"},
    {"id": "world_reset", "label": "World reset", "checks": "the world returns to its baseline between trials",
     "depends_on": ["environment"], "fix_type": "evidence"},
    {"id": "trial_resume", "label": "Trial resume", "checks": "an interrupted trial resumes safely",
     "depends_on": ["world_reset"], "fix_type": "none"},
    {"id": "cost_measurement", "label": "Cost measurement", "checks": "the agent's own cost and tokens are read",
     "depends_on": ["one_turn"], "fix_type": "evidence"},
)


def _ratio(value: str) -> tuple[int, int]:
    try:
        accepted, total = (int(part) for part in value.split("/", 1))
    except (AttributeError, ValueError):
        return 0, 0
    return accepted, total


def _from_statuses(*checks: tuple[str, CheckStatus]) -> dict[str, Any]:
    """One finding from check statuses: any failure fails it, all passing passes it, otherwise not run."""
    results = [{"id": name, "result": status.value} for name, status in checks]
    statuses = {status for _, status in checks}
    if CheckStatus.FAILED in statuses:
        finding = "failed"
    elif statuses <= {CheckStatus.PASSED, CheckStatus.NOT_APPLICABLE} and CheckStatus.PASSED in statuses:
        finding = "passed"
    else:
        finding = "not_run"
    return {"finding": finding, "checks": results}


def _from_ratios(*checks: tuple[str, str]) -> dict[str, Any]:
    """One finding from accepted/total counts: none counted is not run, some short is passed with limits."""
    results = [{"id": name, "result": value} for name, value in checks]
    counted = [_ratio(value) for _, value in checks if _ratio(value)[1]]
    if not counted:
        return {"finding": "not_run", "checks": results}
    if all(accepted == total for accepted, total in counted):
        return {"finding": "passed", "checks": results}
    if all(accepted == 0 for accepted, _ in counted):
        return {"finding": "failed", "checks": results}
    return {"finding": "passed_with_limits", "checks": results}


def capability_evidence(
    certificate: HarnessCertification, failed_targets: dict[str, list[str]] | None = None
) -> dict[str, Any]:
    """Evidence for every catalogue capability, from what the build's certification checked. `failed_targets` names,
    per check, the tools or actions that failed it, so a partly supported capability says exactly what it lacks."""
    checks = certificate.checks
    derived = {
        "start_health": _from_statuses(("static", checks.static), ("processes", checks.processes)),
        "one_turn": _from_statuses(("chat_ingress", checks.chat_ingress)),
        # Tools found and their contracts certified: what discovery means here.
        "tool_discovery": _from_ratios(("tool_contract", checks.tool_contract)),
        "tool_routing": _from_ratios(
            ("tool_contract", checks.tool_contract), ("action_behavior", checks.action_behavior)
        ),
        "state_evidence": _from_statuses(
            ("schema_and_seed", checks.schema_and_seed), ("world_isolation", checks.world_isolation)
        ),
        "world_reset": _from_statuses(("reset_equivalence", checks.reset_equivalence)),
    }
    for found in derived.values():
        for check in found["checks"]:
            if failed_targets is not None and check["id"] in failed_targets:
                check["failed"] = sorted(failed_targets[check["id"]])
    capabilities = {
        entry["id"]: {
            **derived.get(entry["id"], {"finding": "not_run", "checks": []}),
            "depends_on": entry["depends_on"],
            "fix_type": entry["fix_type"],
        }
        for entry in CATALOGUE
    }
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "catalog_version": CATALOG_VERSION,
        "inputs": {
            "source_digest": certificate.source.digest,
            "bundle_digest": certificate.compiler.bundle_digest,
        },
        "limitations": list(certificate.limitations),
        # Catalogue order, so a reader decides each gate before what depends on it; JSON objects carry no order.
        "order": [entry["id"] for entry in CATALOGUE],
        "labels": {entry["id"]: entry["label"] for entry in CATALOGUE},
        "capabilities": capabilities,
    }
