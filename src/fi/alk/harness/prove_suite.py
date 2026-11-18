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
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

EXIT_UNREADABLE = 3
DEADLINE_MARGIN_SECONDS = 300
INCOMPLETE_WHY = "not proved: the validation's time budget ran out before this case"


def _write(output: Path, proofs: dict[str, dict[str, Any]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=output.parent, prefix=".scenario-proofs-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(proofs, out, indent=2, sort_keys=True, ensure_ascii=False)
        os.chmod(temporary, 0o644)
        os.replace(temporary, output)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _incomplete() -> dict[str, Any]:
    from .proofs import unprovable_entry

    return unprovable_entry(INCOMPLETE_WHY, blocked_reason="validation_incomplete")


def budget_seconds(budget: float, deadline_at: float | None, now: float) -> float:
    """The budget, cut so the job's own deadline keeps its margin."""
    if deadline_at is None:
        return budget
    return min(budget, deadline_at - now - DEADLINE_MARGIN_SECONDS)


def prove_suite(
    authoring: Path,
    output: Path,
    *,
    budget: float = 1200,
    deadline_at: float | None = None,
    keys: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    from .catalogue import load_catalogue
    from .proofs import proof_entry, proof_key, unprovable_entry
    from .live_proof import apply_live, load_live
    from .prove import load_world_actors, load_proof_contract, prove
    from .scenario_tools import load_scenarios

    scenarios = load_scenarios(authoring)
    catalogue = load_catalogue(authoring)
    contract = load_proof_contract(authoring)
    actors = load_world_actors(authoring)
    live = load_live(authoring)
    started = time.monotonic()
    allowed = budget_seconds(budget, deadline_at, time.time())
    proofs: dict[str, dict[str, Any]] = {}
    for scenario in scenarios:
        key = proof_key(scenario)
        if keys is not None and key not in keys:
            continue
        if time.monotonic() - started >= allowed:
            proofs[key] = _incomplete()
            continue
        try:
            proof = prove(scenario, catalogue, authoring, contract=contract, actors=actors)
            if key in live:
                # The certification just played this case on the live runtime: its gates win.
                proof = apply_live(proof, live[key])
        except Exception as exc:  # noqa: BLE001 - a case that cannot be proved is recorded, never fatal
            first = (str(exc).splitlines() or [""])[0][:200]
            proofs[key] = unprovable_entry(f"{type(exc).__name__}: {first}".rstrip(": "))
        else:
            # `live`: its gates are what the live runtime showed, not what was recorded without running.
            proofs[key] = {**proof_entry(proof, len(scenario.solution)), "live": key in live}
        _write(output, proofs)
    _write(output, proofs)
    return proofs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--authoring", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--budget-seconds", type=float, default=1200)
    parser.add_argument("--deadline-at", type=float, default=None)
    parser.add_argument("--keys", default=None, help="comma-separated scenario keys to prove; default every one")
    args = parser.parse_args(argv)
    keys = {one.strip() for one in args.keys.split(",") if one.strip()} if args.keys is not None else None
    if not args.authoring.is_dir():
        print(f"prove_suite: authoring tree not readable: {args.authoring}", file=sys.stderr)
        return EXIT_UNREADABLE
    try:
        prove_suite(
            args.authoring, args.output, budget=args.budget_seconds, deadline_at=args.deadline_at, keys=keys
        )
    except OSError as exc:
        print(f"prove_suite: authoring tree not readable: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
