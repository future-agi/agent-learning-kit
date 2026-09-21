"""A conversation about the whole job, rather than about the stage that happens to be open.

The pipeline runs stage by stage, and each stage holds only its own tools and its own context. So
folding a person's message into whichever stage is running answers "write two more scenarios" and
cannot answer "what is the environment stage doing", "why is scenario fourteen weak", or anything
at all once authoring has finished.

This opens a session of its own against what the job produced. It reads the same directory the
writers wrote, holds the same tools they held, and is bound by the same gates: a scenario changed
here is proved before it is kept, exactly as one written here would be. A chat that could save an
unproved scenario would be a way around the gates, and the gates are why a result from this harness
means anything.

It also has one tool the writers do not: a way to ask the person a question and wait for the
answer. The CLI ships its own version of that, but it prompts an operator who is not there.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .backends import SessionSpec, ToolServer, tool, tool_server
from .config import chosen_model, load_skill
from .contract import AgentContract
from .live import Channel
from .scenario_tools import SCENARIO_SERVER, scenario_tools
from .session import Stage

SKILL = "converse"
JOB_SERVER = "job"

# Read whole rather than tailed: a person asking what a stage cost or how long it took is asking
# about the run, and the files that answer that are small.
_READABLE = {
    "contract": "contract.json",
    "sub_goals": "sub_goals.json",
    "coverage": "coverage.json",
    "cost": "cost.json",
    "manifest": "manifest.json",
    "simulator_prompt": "simulator_prompt.md",
}


def _read(destination: Path, name: str) -> str:
    path = destination / _READABLE[name]
    if not path.is_file():
        return f"{name} has not been written yet."
    text = path.read_text(encoding="utf-8", errors="replace")
    return text if len(text) <= 60_000 else text[:60_000] + "\n... (truncated)"


def _progress(destination: Path) -> str:
    """Which stage the job reached, when, and what it cost.

    Built from the event log the harness already writes, because that is the record the platform
    reads too: answering from anywhere else would let the chat and the dashboard disagree.
    """
    events = destination / "harness-events.jsonl"
    if not events.is_file():
        return "no stage events yet."
    lines: list[str] = []
    for line in events.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        kind = str(record.get("type") or "")
        if not kind.startswith("harness.stage."):
            continue
        payload = record.get("payload") or {}
        lines.append(
            f"{record.get('wall_time', '')} {kind.rsplit('.', 1)[-1]:9} "
            f"{payload.get('stage', '')} {('status=' + str(payload['status'])) if 'status' in payload else ''}"
        )
    return "\n".join(lines[-60:]) or "no stage events yet."


def _coverage(destination: Path) -> str:
    """Every axis and level with its count, and which sub-goals nothing names.

    Counted, not sampled. Asked "which overlays does the suite cover", a model with only
    `inspect_scenario` reads a handful and generalises, and on a real suite that reported six of
    the nine levels actually present.
    """
    from collections import Counter

    from .scenario_tools import load_scenarios

    scenarios = load_scenarios(destination)
    if not scenarios:
        return "no scenarios have been written yet."
    axes: dict[str, Counter] = {}
    for scenario in scenarios:
        for axis, level in (scenario.coverage or {}).items():
            axes.setdefault(axis, Counter())[str(level)] += 1
    lines = [f"{len(scenarios)} scenarios."]
    unplaced = sum(1 for one in scenarios if not one.coverage)
    if unplaced:
        lines.append(f"{unplaced} sit on no cell at all.")
    for axis in sorted(axes):
        counted = axes[axis]
        lines.append(f"\n{axis}: {len(counted)} levels")
        for level, count in counted.most_common():
            lines.append(f"  {level:30} {count}")
    named = Counter(name for one in scenarios for name in (one.sub_goals or []))
    catalogue = destination / "sub_goals.json"
    if catalogue.is_file():
        try:
            body = json.loads(catalogue.read_text(encoding="utf-8"))
            defined = [
                str(one.get("name"))
                for one in (body if isinstance(body, list) else body.get("sub_goals") or [])
            ]
        except ValueError:
            defined = []
        unused = [one for one in defined if one and one not in named]
        lines.append(f"\nsub-goals: {len(defined)} defined, {len(named)} named by a scenario")
        if unused:
            lines.append("  never named: " + ", ".join(sorted(unused)))
    return "\n".join(lines)


def job_tools(destination: Path, channel: Channel) -> ToolServer:
    """What the conversation can read about the run, and how it asks the person a question."""

    @tool(
        "read_job",
        "Read one of the job's own documents whole: "
        + ", ".join(sorted(_READABLE))
        + ". Use this for questions about the agent, the axes, the catalogue or the bill.",
        {"name": str},
    )
    async def read_job(args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name") or "").strip()
        if name not in _READABLE:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"{name!r} is not one of: {', '.join(sorted(_READABLE))}",
                    }
                ],
                "is_error": True,
            }
        return {"content": [{"type": "text", "text": _read(destination, name)}]}

    @tool(
        "read_progress",
        "Which stage the job reached, in order, with when each started and finished. Answers "
        "what it is doing now and what it did earlier.",
        {},
    )
    async def read_progress(_args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": _progress(destination)}]}

    @tool(
        "ask_user",
        "Ask the person watching a question and wait for their answer. For things only they "
        "know: which modality is really being tested, whether an edit that moves the gates "
        "should go ahead, which of two readings they meant. Do not use it to narrate.",
        {"prompt": str, "options": list, "multi_select": bool},
    )
    async def ask_user(args: dict[str, Any]) -> dict[str, Any]:
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return {
                "content": [{"type": "text", "text": "a question needs a prompt"}],
                "is_error": True,
            }
        options = [str(one) for one in (args.get("options") or []) if str(one).strip()]
        answer = channel.ask(
            {
                "prompt": prompt,
                "options": options,
                "multi_select": bool(args.get("multi_select")),
            }
        )
        if not answer.get("answered"):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"No answer: {answer.get('reason', 'unknown')}. Say what you "
                        "assumed and carry on, or stop and say what you need.",
                    }
                ]
            }
        said = answer.get("text") or answer.get("pick") or answer.get("picks") or answer
        return {"content": [{"type": "text", "text": f"They answered: {said}"}]}

    @tool(
        "read_coverage",
        "The exact tally of the suite: every axis, every level, how many scenarios sit on each, "
        "and which sub-goals are never named. Counted from what is on disk. Use this rather than "
        "reading scenarios one by one, which samples and then generalises wrongly.",
        {},
    )
    async def read_coverage(_args: dict[str, Any]) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": _coverage(destination)}]}

    return tool_server(
        JOB_SERVER, tools=[read_job, read_progress, read_coverage, ask_user]
    )


def open_conversation(
    contract: AgentContract,
    *,
    out: Path,
    channel: Channel | None = None,
    max_turns: int = 60,
) -> Stage:
    """A session that can be asked anything about this job, and can change it through the gates."""
    destination = Path(out)
    talking = channel or Channel()
    # The writers' own server, so every edit made here passes ready, solvable and not-vacuous the
    # way an authored one does. `wanted=0` because a conversation is not filling a quota.
    writing, kept = scenario_tools(contract, destination, destination, wanted=0)
    spec = SessionSpec(
        system_prompt=(
            f"## This agent\n\n{contract.brief(with_data=True, sample_rows=3)}"
            f"\n\n{load_skill(SKILL)}"
            + (
                f"\n\nThe suite currently holds {len(kept)} scenarios: "
                + ", ".join(one.name for one in kept)
                if kept
                else "\n\nNo scenarios have been written yet."
            )
        ),
        servers={SCENARIO_SERVER: writing, JOB_SERVER: job_tools(destination, talking)},
        builtins=(),
        cwd=str(destination),
        max_turns=max_turns,
        model=chosen_model(),
        thinking=True,
    )
    stage = Stage(spec, name=SKILL)
    stage.channel = talking
    return stage
