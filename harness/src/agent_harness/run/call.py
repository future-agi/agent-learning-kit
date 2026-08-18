"""One scenario, against the real hosted agent, end to end.

Everything the harness built is wired together here and then ALK's own voice case places the
call. The harness does not reimplement any of that: it supplies the world the agent's tools act
on, the caller's instruction, and the grading afterwards.

    world + setup ──► webhook ──► public url ──► assistant's own tools repointed
                                                          │
                        ALK's voice case places the call ──┘
                                                          │
                          the world afterwards + the calls ──► sub-goal checks

Run it:

    set -a; . ./.env.acceptance; set +a
    uv run python -m harness.run.call --name drive_thru --scenario orders_a_big_mac
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from ..config import artifact_dir
from ..scenario_tools import load_scenarios
from .live import grade, wire

CASE = os.environ.get("HARNESS_VOICE_CASE", "2.1.2")


def place_the_call(case: str, dry_run: bool = False) -> int:
    """Hand over to ALK's voice case, which owns everything about placing a call."""
    runner = Path("oss/simulation-acceptance/run_voice_case.py")
    if not runner.exists():
        raise RuntimeError(f"no voice runner at {runner}; run from the repo root")
    command = [sys.executable, str(runner), case] + (["--dry-run"] if dry_run else [])
    return subprocess.call(command)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-harness-call", description=__doc__)
    parser.add_argument("--name", required=True, help="which agent")
    parser.add_argument("--scenario", required=True, help="which scenario, by name")
    parser.add_argument("--case", default=CASE, help="ALK voice case id")
    parser.add_argument(
        "--dry-run", action="store_true", help="wire everything up but do not place the call"
    )
    args = parser.parse_args(argv)

    root = artifact_dir(args.name)
    written = load_scenarios(root)
    scenario = next((one for one in written if one.name == args.scenario), None)
    if scenario is None:
        print(
            f"no scenario called {args.scenario!r}. There is: "
            + ", ".join(one.name for one in written),
            file=sys.stderr,
        )
        return 1

    world, instruction, webhook, tunnel, url, moved = wire(scenario, root)
    try:
        print(f"agent:     {args.name}")
        print(f"scenario:  {scenario.name}")
        print(f"webhook:   {url}/tool")
        print(f"repointed: {', '.join(moved)}")
        print(f"sub-goals: {', '.join(scenario.sub_goals)}\n")

        # The caller's instruction reaches the voice case through the environment, so nothing
        # about how a simulated caller behaves is decided twice.
        os.environ["HARNESS_INSTRUCTION"] = instruction
        os.environ["HARNESS_SCENARIO"] = scenario.name
        os.environ["HARNESS_OUTCOME"] = scenario.tests

        code = place_the_call(args.case, dry_run=args.dry_run)
        if args.dry_run:
            print("\ndry run: nothing was called, and the world is untouched.")
            return code

        result = grade(scenario, world, root)
        print()
        print(result.line())
        for one in result.settled:
            print(one.line())
        for name in result.judged:
            print(f"  [?] {name} — judged, not graded here")
        print("\nwhat the agent actually did:")
        for call in result.calls or ["(no tool calls reached the world)"]:
            print(f"  {call}")
        return 0 if result.settled and result.met == len(result.settled) else 2
    finally:
        webhook.stop()
        if tunnel is not None:
            tunnel.terminate()
        world.close()


if __name__ == "__main__":
    raise SystemExit(main())
