"""Each scenario's latest proof, saved beside the suite so the platform can record it."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .json_file import write_json_atomic
from .proof_records import ProofEntry
from .prove import RECORDED_NOT_RUN, BlockedReason, Proof, refuses_keeping
from .scenario import Scenario, scenario_slug

PROOFS = "proofs.json"


def load_proofs(destination: Path) -> dict[str, ProofEntry]:
    path = Path(destination) / PROOFS
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


# The suite's proofs as the validation wrote them (the live runtime's gates applied), which the platform records.
VALIDATED_PROOFS = "scenario-proofs.json"


def load_shown_proofs(destination: Path) -> dict[str, ProofEntry]:
    """Each case's proof as the platform shows it: the validation's record (`VALIDATED_PROOFS`), unless a later proof
    of the case was made here since (a case written or edited after it), compared by `proved_at`."""
    mine = load_proofs(destination)
    try:
        validated = json.loads((Path(destination) / VALIDATED_PROOFS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return mine
    if not isinstance(validated, dict):
        return mine
    shown = dict(mine)
    for key, entry in validated.items():
        here = mine.get(key)
        if not isinstance(entry, dict):
            continue
        if here is None or str(entry.get("proved_at") or "") >= str(here.get("proved_at") or ""):
            shown[key] = entry
    return shown


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
    why = " ".join(one for one in ("" if proof.proved else proof.why(), proof.advice()) if one)
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
        # Tools the reference calls that the agent version lacks (`tool_not_on_agent`); empty when none or unknown.
        "missing_tools": list(proof.missing_tools),
        **(
            {"recorded": list(proof.recorded), "recorded_note": f"{', '.join(proof.recorded)}: {RECORDED_NOT_RUN}"}
            if proof.recorded
            else {}
        ),
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


def proof_status(entry: Mapping[str, Any] | None) -> tuple[str, str]:
    """One scenario's proof as the builder reports it: `proved`, `kept as written` or `not proved`, and the reason
    its proof gives in its own words (the tools assumed, the reason code, the first line of why)."""
    entry = entry or {}
    why = str(entry.get("why") or "").strip()
    if entry.get("proved") is True:
        return "proved", ""
    if entry.get("kept_as_written") is True:
        assumed = ", ".join(str(one) for one in entry.get("assumed") or [])
        return "kept as written", "; ".join(part for part in (f"assumed {assumed}" if assumed else "", why) if part)
    if not entry:
        return "not proved", "no proof on record"
    code = str(entry.get("blocked_reason") or "not_validated")
    return "not proved", f"{code}: {why}" if why else code


def status_lines(
    proofs: Mapping[str, ProofEntry], kept: list[Scenario], *, names: list[str] | None = None, state: str = "",
    most: int = 200,
) -> list[str]:
    """The suite's counts, then one line per scenario (`name: state, reason`), narrowed by `names` and `state`, at
    most `most` lines: what the builder reads before it says anything about a scenario's state."""
    counts = proof_counts(proofs, [proof_key(one) for one in kept])
    lines = [
        f"{len(kept)} scenarios. Proofs on record: {counts['proved']} proved, {counts['kept_as_written']} kept as "
        f"written, {counts['not_proved']} not proved."
    ]
    wanted = {one.strip() for one in names or [] if one.strip()}
    shown = 0
    for scenario in kept:
        if wanted and scenario.name not in wanted and proof_key(scenario) not in wanted:
            continue
        said, why = proof_status(proofs.get(proof_key(scenario)))
        if state and said != state:
            continue
        if shown >= most:
            lines.append(f"... more not shown; narrow with names or state (at most {most} lines)")
            break
        lines.append(f"{scenario.name}: {said}" + (f", {why.splitlines()[0][:400]}" if why else ""))
        shown += 1
    missing = sorted(wanted - {one.name for one in kept} - {proof_key(one) for one in kept})
    if missing:
        lines.append("not in the suite: " + ", ".join(missing))
    return lines
