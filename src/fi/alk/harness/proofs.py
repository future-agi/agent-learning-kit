"""Each scenario's latest proof, saved beside the suite so the platform can record it."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from .json_file import write_json_atomic
from .proof_records import ProofEntry
from .prove import BlockedReason, Proof, refuses_keeping
from .scenario import Scenario, scenario_slug

PROOFS = "proofs.json"


def load_proofs(destination: Path) -> dict[str, ProofEntry]:
    path = Path(destination) / PROOFS
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _save(destination: Path, proofs: Mapping[str, ProofEntry]) -> None:
    write_json_atomic(Path(destination) / PROOFS, proofs)


def utc_stamp() -> str:
    """Now, in UTC to the second and one fixed form, so two stamps compare as text the way the times do."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def proof_key(scenario: Scenario) -> str:
    """The key every proof of a scenario is saved under."""
    return getattr(scenario, "scenario_key", "") or scenario_slug(scenario.name)


def proof_entry(proof: Proof, solution_steps: int) -> ProofEntry:
    blocked = proof.blocked_reason()
    why = "" if proof.proved else proof.why()
    return {
        "format": 2,
        "holds": proof.holds,
        "proved": proof.proved,
        "kept_as_written": proof.kept_as_written,
        "ready": proof.ready,
        "solvable": proof.solvable,
        "not_vacuous": not proof.vacuous,
        "why": why,
        "proved_at": utc_stamp(),
        "assumed": list(proof.assumed),
        "solution_steps": solution_steps,
        "deferred": proof.deferred,
        "judged_only": proof.judged_only,
        "outcome": proof.outcome,
        "controls": [dict(one) for one in proof.controls],
        "leaks": [dict(one) for one in proof.leaks],
        "sources": [dict(one) for one in proof.sources],
        "policy_checks": [dict(one) for one in proof.policy_checks],
        "evidence": {name: list(kinds) for name, kinds in proof.evidence.items()},
        "excused": list(proof.excused),
        "blocked_reason": blocked,
        "refused": refuses_keeping(proof),
        **({"trial": proof.trial} if proof.trial else {}),
    }


def record_proof(destination: Path, scenario_key: str, proof: Proof, *, solution_steps: int = 0) -> None:
    """Save one scenario's proof, replacing its earlier one: what held, what ran and what was assumed."""
    proofs = load_proofs(destination)
    proofs[scenario_key] = proof_entry(proof, solution_steps)
    _save(destination, proofs)


def unprovable_entry(why: str, blocked_reason: BlockedReason = BlockedReason.BROKEN_CHECK) -> ProofEntry:
    return {
        "format": 2,
        "holds": False,
        "proved": False,
        "kept_as_written": False,
        "ready": False,
        "solvable": False,
        "not_vacuous": False,
        "why": why,
        "proved_at": utc_stamp(),
        "assumed": [],
        "solution_steps": 0,
        "deferred": False,
        "judged_only": False,
        "outcome": "change",
        "controls": [],
        "leaks": [],
        "sources": [],
        "policy_checks": [],
        "evidence": {},
        "excused": [],
        "blocked_reason": blocked_reason,
        "refused": True,
    }


def record_unprovable(destination: Path, scenario_key: str, why: str) -> None:
    """Save that a scenario could not be proved at all."""
    proofs = load_proofs(destination)
    proofs[scenario_key] = unprovable_entry(why)
    _save(destination, proofs)


def proof_counts(proofs: Mapping[str, ProofEntry], keys: list[str]) -> dict[str, int]:
    """Proved, kept as written and not proved, over `keys`. A key with no proof is not proved."""
    counts = {"proved": 0, "kept_as_written": 0, "not_proved": 0}
    for key in keys:
        entry = proofs.get(key) or {}
        if entry.get("proved") is True:
            counts["proved"] += 1
        elif entry.get("kept_as_written") is True:
            counts["kept_as_written"] += 1
        else:
            counts["not_proved"] += 1
    return counts
