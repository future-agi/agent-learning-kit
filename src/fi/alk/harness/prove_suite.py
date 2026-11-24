"""Prove every scenario of a suite again, writing each proof as soon as it is made.

Run at every certification so a case's proof always matches the environment it will run on:

    python -m fi.alk.harness.prove_suite --authoring DIR --output FILE [--budget-seconds N] [--deadline-at EPOCH]
        [--keys KEY,KEY]

The output is `{scenario_key: format-2 proof entry}`, rewritten atomically after every scenario, so a
killed process leaves the keys proved so far. When the time budget runs out, every remaining key is
recorded `validation_incomplete`: nothing is wrong with it, it was not reached. Exit 0 whenever it
ran; exit 3 when the authoring tree cannot be read. `--keys` proves only the named cases; the output
then holds only their keys, and the platform merges it over the proofs it already has.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .catalogue import load_catalogue
from .json_file import write_json_atomic
from .live_proof import apply_live, load_live, load_unplayed
from .outbound import redact_outbound_text
from .proof_records import ProofEntry
from .proofs import proof_entry, proof_key, unprovable_entry
from .prove import BlockedReason, distinctive_goals, load_proof_contract, load_world_actors, prove, shared_goals
from .scenario_tools import load_scenarios

logger = logging.getLogger(__name__)
EXIT_UNREADABLE = 3
DEADLINE_MARGIN_SECONDS = 300
INCOMPLETE_WHY = "not proved: the validation's time budget ran out before this case"
UNPLAYED_WHY = "not proved: the live check could not run its expected handling here ({why})"
# The offline pass's own budget when the caller gives none (`ALK_PROVE_SUITE_SECONDS`): generous, since at about 0.05 s
# a case a thousand cases take under a minute; the job's deadline still bounds it.
DEFAULT_SUITE_SECONDS = 3600.0
# How often the proofs file is written while cases are proved: often enough that a stopped job keeps its progress, not
# after every case, which rewrites the whole file a thousand times on a thousand cases.
WRITE_EVERY_SECONDS = 2.0


def suite_budget_seconds() -> float:
    """`ALK_PROVE_SUITE_SECONDS` when it is a positive number, else an hour."""
    import os

    try:
        value = float(str(os.environ.get("ALK_PROVE_SUITE_SECONDS") or "").strip())
    except ValueError:
        return DEFAULT_SUITE_SECONDS
    return value if value > 0 else DEFAULT_SUITE_SECONDS


def _write(output: Path, proofs: Mapping[str, ProofEntry]) -> None:
    write_json_atomic(output, proofs)


def _incomplete() -> dict[str, Any]:
    return unprovable_entry(INCOMPLETE_WHY, blocked_reason=BlockedReason.VALIDATION_INCOMPLETE)


def budget_seconds(budget: float, deadline_at: float | None, now: float) -> float:
    """The budget, cut so the job's own deadline keeps its margin."""
    if deadline_at is None:
        return budget
    return min(budget, deadline_at - now - DEADLINE_MARGIN_SECONDS)


def agent_tools(document: Path | None) -> set[str] | None:
    """The tool names of the agent version under test, from its agent document (`{"agent": {"tools": [{"name"}]}}`,
    as the agent reading writes it). None when no document is given or it cannot be read: the check is then skipped,
    never failed on a bad document."""
    if document is None:
        return None
    try:
        tools = json.loads(Path(document).read_text(encoding="utf-8"))["agent"]["tools"]
        return {str(one["name"]) for one in tools}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("agent document not read, so no case is checked against the agent's tools: %s", type(exc).__name__)
        return None


def prove_suite(
    authoring: Path,
    output: Path,
    *,
    budget: float | None = None,
    deadline_at: float | None = None,
    keys: set[str] | None = None,
    agent_document: Path | None = None,
    judge_case_goals: bool = False,
) -> dict[str, dict[str, Any]]:
    """`judge_case_goals` is set where the suite was just written (a build): a case whose controls break only goals its
    whole suite shares, or that has none, is held. Otherwise (a Validate) every case is proved as it was written, so a
    case written before those rules is kept as written with the reason as its note, never held."""
    scenarios = load_scenarios(authoring)
    catalogue = load_catalogue(authoring)
    contract = load_proof_contract(authoring)
    actors = load_world_actors(authoring)
    live = load_live(authoring)
    unplayed = load_unplayed(authoring)
    # The tools a live server plays (`mode` service); a tool in the agent's own process is assumed live and offline alike.
    served = (
        None
        if contract is None
        else {name for name in contract.tool_names() if getattr(contract.entry_for(name), "mode", "") == "service"}
    )
    tools = agent_tools(agent_document)
    started = time.monotonic()
    allowed = budget_seconds(suite_budget_seconds() if budget is None else budget, deadline_at, time.time())
    written = started
    shared = shared_goals(scenarios) if judge_case_goals else None
    proofs: dict[str, ProofEntry] = {}
    for scenario in scenarios:
        key = proof_key(scenario)
        if keys is not None and key not in keys:
            continue
        if time.monotonic() - started >= allowed:
            proofs[key] = _incomplete()
            continue
        try:
            goals = distinctive_goals(scenario, scenarios, shared) if judge_case_goals else ()
            proof = prove(
                scenario,
                catalogue,
                authoring,
                contract=contract,
                actors=actors,
                agent_tools=tools,
                case_goals=goals,
                as_written=not judge_case_goals,
            )
            if key in live:
                # The certification just played this case on the live runtime: its gates win.
                proof = apply_live(proof, live[key])
            elif key in unplayed and proof.assumed and (served is None or served & set(proof.assumed)):
                # The live pass was to run the steps this offline proof could only assume, and did not: nothing here
                # shows the case can be solved, so it is not proved, never kept as written on steps that never ran.
                proofs[key] = unprovable_entry(
                    UNPLAYED_WHY.format(why=unplayed[key]), blocked_reason=BlockedReason.VALIDATION_INCOMPLETE
                )
                continue
        except Exception as exc:  # noqa: BLE001 - a case that cannot be proved is recorded, never fatal
            first = (redact_outbound_text(str(exc)).splitlines() or [""])[0][:200]
            proofs[key] = unprovable_entry(f"{type(exc).__name__}: {first}".rstrip(": "))
        else:
            # `live`: its gates are what the live runtime showed, not what was recorded without running.
            proofs[key] = {**proof_entry(proof, len(scenario.solution)), "live": key in live}
        if time.monotonic() - written >= WRITE_EVERY_SECONDS:
            _write(output, proofs)
            written = time.monotonic()
    _write(output, proofs)
    return proofs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--authoring", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--budget-seconds", type=float, default=None)
    parser.add_argument("--deadline-at", type=float, default=None)
    parser.add_argument("--keys", default=None, help="comma-separated scenario keys to prove; default every one")
    parser.add_argument(
        "--agent-document", type=Path, default=None, help="the agent version's agent.json; its tools bound each case"
    )
    args = parser.parse_args(argv)
    keys = {one.strip() for one in args.keys.split(",") if one.strip()} if args.keys is not None else None
    if not args.authoring.is_dir():
        print(f"prove_suite: authoring tree not readable: {args.authoring}", file=sys.stderr)
        return EXIT_UNREADABLE
    try:
        prove_suite(
            args.authoring,
            args.output,
            budget=args.budget_seconds,
            deadline_at=args.deadline_at,
            keys=keys,
            agent_document=args.agent_document,
        )
    except OSError as exc:
        print(f"prove_suite: authoring tree not readable: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
