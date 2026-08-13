"""CLI: ``python -m fi.alk.generation --repo /path/to/agent --n 20 --out artifacts/scenarios``."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .llm import DEFAULT_MODEL, LiteLLMClient
from .pipeline import GenerationConfig, generate
from .sources import resolve_source


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m fi.alk.generation",
        description="Generate grounded, checkable test scenarios for an agent.",
    )
    parser.add_argument(
        "--source", default="repo", help="agent connection kind (default: repo)"
    )
    parser.add_argument(
        "--repo", help="path to the agent's repository folder (repo source)"
    )
    parser.add_argument("--n", type=int, default=20, help="target number of scenarios")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="litellm model string")
    parser.add_argument(
        "--budget-usd", type=float, default=2.0, help="hard spend ceiling for this run"
    )
    parser.add_argument(
        "--out", default="artifacts/generated-scenarios", help="output directory"
    )
    parser.add_argument(
        "--no-critic", action="store_true", help="skip the QA review pass"
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
    )
    source_kwargs = {}
    if args.source == "repo":
        if not args.repo:
            print("--repo is required for the repo source", file=sys.stderr)
            return 2
        source_kwargs["path"] = args.repo
    guidance = args.guidance
    if guidance.startswith("@"):
        with open(guidance[1:], encoding="utf-8") as fh:
            guidance = fh.read()
    source = resolve_source(args.source, **source_kwargs)
    llm = LiteLLMClient(model=args.model, budget_usd=args.budget_usd)
    config = GenerationConfig(
        n=args.n, critic_enabled=not args.no_critic, out_dir=args.out
    )

    result = generate(source, llm, config)
    print(
        json.dumps(
            {
                "agent": result.contract.agent,
                "scenarios": len(result.records),
                "rejected": len(result.rejected),
                "out": args.out,
                "usage": result.usage,
            },
            indent=2,
        )
    )
    return 0 if result.records else 1


if __name__ == "__main__":
    raise SystemExit(main())
