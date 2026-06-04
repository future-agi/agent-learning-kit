from __future__ import annotations

import argparse
import importlib
import json
import sys
from typing import Optional, Sequence

from .config import current_config


SIMULATE_COMMANDS = {
    "baseline",
    "compare",
    "eval",
    "init",
    "optimize",
    "promote-to-regression",
    "redteam",
    "replay",
    "report",
    "run",
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        return _help()
    command = args[0]
    if command == "doctor":
        return _doctor()
    if command == "simulate":
        return _simulate(args[1:])
    if command in SIMULATE_COMMANDS:
        return _simulate(args)
    return _help(f"unknown command: {command}")


def _simulate(args: Sequence[str]) -> int:
    try:
        cli = importlib.import_module("fi.simulate.cli")
    except Exception as exc:
        print(
            "agent-learn: simulation commands require "
            "`agent-learning-kit[simulate]` or `agent-learning-kit[trinity]`.",
            file=sys.stderr,
        )
        print(f"agent-learn: import failed: {exc}", file=sys.stderr)
        return 2
    return int(cli.main(list(args)))


def _doctor() -> int:
    modules = {
        "simulate": "fi.simulate",
        "evaluation": "fi.evals",
        "optimize": "fi.opt",
    }
    payload = {
        "config": {
            "api_key_configured": bool(current_config().api_key),
            "api_url": current_config().api_url,
            "project_id_configured": bool(current_config().project_id),
            "workspace_id_configured": bool(current_config().workspace_id),
        },
        "modules": {},
    }
    for name, module_name in modules.items():
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            payload["modules"][name] = {
                "available": False,
                "module": module_name,
                "error": str(exc),
            }
        else:
            payload["modules"][name] = {
                "available": True,
                "module": module_name,
            }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _help(error: Optional[str] = None) -> int:
    if error:
        print(f"agent-learn: {error}", file=sys.stderr)
    parser = argparse.ArgumentParser(
        prog="agent-learn",
        description="Unified CLI for Future AGI agent simulation, evaluation, and optimization.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        help=(
            "doctor, simulate, run, eval, redteam, optimize, replay, report, "
            "compare, baseline, promote-to-regression, init"
        ),
    )
    parser.print_help(sys.stderr if error else sys.stdout)
    return 2 if error else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
