"""Run a stage from a terminal.

This is one renderer over the stage loop, not the product. It prints events as lines and reads
follow-ups from stdin; a browser front end subscribes to the same events and draws them as a
transcript beside the artifact. Keeping the terminal a renderer rather than the interface is what
makes the second one cheap.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from .build import open_stage as build_stage
from .build import opening as build_opening
from .config import DEFAULT_MODEL, artifact_dir
from .session import TEXT, Event
from .sources import resolve, supported
from .understand import load, open_stage, opening


def _render(event: Event) -> None:
    line = event.line()
    if event.kind == TEXT:
        print(line, end="", flush=True)
    else:
        print(f"\n{line}", flush=True)


async def _prompt(question: str) -> str:
    return (await asyncio.to_thread(input, question)).strip()


async def _answer_questions(
    tool_name: str, payload: dict[str, Any], _context: Any
) -> Any:
    """Render the model's clarifying questions and return the operator's answers.

    Anything that is not a question is allowed through: the session is already restricted to
    read-only built-ins plus our own tools, so there is nothing here to gate.
    """
    from claude_agent_sdk.types import PermissionResultAllow

    if tool_name != "AskUserQuestion":
        return PermissionResultAllow(updated_input=payload)

    answers: dict[str, Any] = {}
    for question in payload.get("questions", []):
        print(f"\n\n  {question.get('header', '?')}: {question.get('question', '')}")
        options = question.get("options", []) or []
        for index, option in enumerate(options, start=1):
            print(
                f"    {index}. {option.get('label')} - {option.get('description', '')}"
            )
        raw = await _prompt("  > ")
        chosen = raw
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            chosen = options[int(raw) - 1].get("label", raw)
        answers[question.get("question", "")] = chosen
    print()
    return PermissionResultAllow(
        updated_input={"questions": payload.get("questions", []), "answers": answers}
    )


async def _understand(args: argparse.Namespace) -> int:
    source = resolve(args.kind, name=args.name, root=args.path)
    stage, destination = open_stage(
        source,
        out=Path(args.out) if args.out else None,
        # Unattended, there is nobody to answer, so the model records what it could not
        # resolve in open_questions rather than blocking on a prompt nobody will see.
        ask=_answer_questions if args.interactive else None,
    )

    print(f"agent: {source.name}  ({source.kind})")
    print(f"out:   {destination}\n")

    await _converse(stage, opening(source), interactive=args.interactive)

    contract = load(destination)
    if contract is None:
        print("\nNo contract was submitted.", file=sys.stderr)
        return 1
    print(
        f"\ncontract: {len(contract.tools)} tools, "
        f"{len(contract.hard_constraints)} rules, "
        f"{len(contract.real_use_cases)} use cases, "
        f"{len(contract.open_questions)} open questions"
    )
    print(f"spent:    ${stage.spent_usd:.4f}")
    return 0


async def _converse(stage, opening_message: str, *, interactive: bool) -> None:
    """Say the opening, then keep the stage open for corrections.

    The same shape for every stage. A world is usually right on the second look, and the point
    of holding the session open is that correcting it is the next thing said rather than a
    rebuild from nothing.
    """
    async with stage:
        await stage.say(opening_message, on_event=_render)
        while interactive:
            try:
                said = await _prompt("\nkarthik  ")
            except (EOFError, KeyboardInterrupt):
                break
            if not said or said in {"q", "quit", "exit"}:
                break
            await stage.say(said, on_event=_render)


async def _build(args: argparse.Namespace) -> int:
    destination = Path(args.out) if args.out else artifact_dir(args.name)
    contract = load(destination)
    if contract is None:
        print(f"No contract at {destination}. Run `understand` first.", file=sys.stderr)
        return 1

    print(f"agent: {contract.agent}  ({len(contract.tools)} tools)")
    print(f"out:   {destination}\n")

    stage, _ = build_stage(
        contract,
        out=destination,
        ask=_answer_questions if args.interactive else None,
    )
    await _converse(stage, build_opening(contract), interactive=args.interactive)

    if not (destination / "world.sqlite").exists():
        print("\nNo world was saved.", file=sys.stderr)
        return 1
    print(f"\nworld: {destination}")
    print(f"spent: ${stage.spent_usd:.4f}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fi.alk.harness", description=__doc__)
    sub = parser.add_subparsers(dest="stage", required=True)

    understand = sub.add_parser(
        "understand", help="read an agent and produce its contract"
    )
    understand.add_argument("--name", required=True, help="what to call this agent")
    understand.add_argument("--path", required=True, help="where the agent is")
    understand.add_argument(
        "--kind", default="repo", choices=supported(), help="how the agent is supplied"
    )
    understand.add_argument("--out", default=None, help="artifact directory")
    understand.add_argument(
        "--once",
        dest="interactive",
        action="store_false",
        help="run unattended instead of staying open for corrections",
    )
    understand.add_argument("--model", default=DEFAULT_MODEL, help=argparse.SUPPRESS)
    understand.set_defaults(run=_understand, interactive=True)

    world = sub.add_parser("build", help="build the world from an agent's contract")
    world.add_argument("--name", required=True, help="which agent")
    world.add_argument("--out", default=None, help="artifact directory")
    world.add_argument(
        "--once",
        dest="interactive",
        action="store_false",
        help="run unattended instead of staying open for corrections",
    )
    world.set_defaults(run=_build, interactive=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(args.run(args))


if __name__ == "__main__":
    raise SystemExit(main())
