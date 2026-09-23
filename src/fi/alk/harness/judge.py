"""Deciding a judged sub-goal against the world the session left behind.

A judged sub-goal is one nothing observable settles, so a model reaches the verdict. Until now
nothing did: judged sub-goals carried a placeholder check returning None, which the scheduler read
as "held", so every one passed before anything looked. This runs in the sandbox at the end of the
session, while the world is still alive, so the judge reads real state rather than a snapshot.

Nothing here is modality-specific. It reasons over the world's tables, the actions the agent
took and what was said, which a voice call, a typed conversation and a browser the agent drives all
leave behind in the same shape, so the wording stays neutral rather than naming a call.

The explanation is read by the agent's owner, so it speaks only about what the agent did. Anything
about the judging itself (a failed query, a missing transcript, a crash) goes to the log, never into
the verdict. A judge that still cannot decide after a retry with the evidence inlined returns held
None, which the scheduler never reports as a pass.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Sequence

from .backends import SessionSpec, tool, tool_server
from .config import chosen_model
from .session import Stage
from .tools import schema

logger = logging.getLogger(__name__)

JUDGE_MODEL_ALIAS = "ALK_JUDGE_MODEL"
_LIMIT = 600
_TRANSCRIPT_LIMIT = 24000

_INSTRUCTIONS = """
You decide one claim about a conversation an agent already had, using what it said, the actions it
took and the state of its system afterwards.

Look before you answer: read the actions you were given, read the conversation when the claim is
about what was said, and inspect the system's records when the claim is about what changed. The
records are PostgreSQL; use inspect_world to discover them rather than guessing names. Judge the
claim against the situation the conversation actually set up: a step the caller declined, or one the
situation never called for, is not a failure of the agent.

Then call decide, once, with `passed` true or false. You must decide: the conversation, the actions
and the records together are enough, so weigh them and commit. A claim about how the agent handles
a situation is not passed when that situation never came up: decide false and say plainly that it
did not come up.

`explanation` is shown to the agent's owner. Write one or two plain sentences about the agent's
behaviour and its result, in the terms of their business: what the agent said or did, and what that
changed. Never mention tables, rows, queries, tool names, records you could or could not read, this
review, a test, a scenario, a simulation or any error. Put anything about how you checked into
`evidence`, which only the reviewers of this process see.
""".strip()


def judge_model() -> str:
    """The judge's model. Set ALK_JUDGE_MODEL to run it on something other than the harness model."""
    return os.environ.get(JUDGE_MODEL_ALIAS, "").strip() or chosen_model()


def _short(value: object) -> object:
    if isinstance(value, str) and len(value) > _LIMIT:
        return value[:_LIMIT] + f"... [{len(value)} chars]"
    if isinstance(value, dict):
        return {key: _short(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_short(item) for item in value[:20]]
    return value


def _dump(value: object) -> str:
    return json.dumps(_short(value), default=str, indent=1)


def _dump_calls(calls: Sequence[Any]) -> str:
    """Every action, however long the conversation: only each field is trimmed, never the list."""
    return json.dumps([_short(_call(c)) for c in calls], default=str, indent=1)


async def judge(
    goal: Any,
    world: Any,
    calls: Sequence[Any],
    *,
    messages: Sequence[Any] = (),
    scenario: Any = None,
) -> tuple[bool | None, str]:
    """Decide one judged sub-goal: (passed, explanation). Never raises."""
    verdict: dict[str, tuple[bool | None, str]] = {}

    @tool(
        "inspect_world",
        "The world's tables: without a name, every table and its row count; with one, its rows.",
        schema({"table": str}, []),
    )
    async def inspect_world(args: dict[str, Any]) -> dict[str, Any]:
        table = str(args.get("table") or "").strip()
        state = world.state(table or None)
        if table:
            return _say(_dump(state))
        return _say(_dump({name: len(rows or []) for name, rows in state.items()}))

    @tool(
        "query_world",
        "Run one read-only PostgreSQL query for a specific row. Use inspect_world for schema discovery.",
        schema({"sql": str}, ["sql"]),
    )
    async def query_world(args: dict[str, Any]) -> dict[str, Any]:
        sql = str(args.get("sql") or "")
        try:
            return _say(_dump(world.query(sql)))
        except Exception as exc:  # noqa: BLE001 - bad model SQL is feedback, not a stage crash
            return _say(
                _dump(
                    {
                        "error": f"{type(exc).__name__}: {exc}",
                        "dialect": "postgresql",
                        "recovery": "Use inspect_world to discover real tables, then retry once.",
                    }
                ),
                error=True,
            )

    @tool(
        "read_transcript",
        "What was said, in order. `user` is the caller, `assistant` is the agent being judged.",
        schema({}, []),
    )
    async def read_transcript(args: dict[str, Any]) -> dict[str, Any]:
        if not messages:
            return _say(
                "Nothing was captured of what was said. Decide from the actions and the records."
            )
        return _say(_transcript(messages))

    @tool(
        "decide",
        "Commit to the verdict, once, after looking.",
        schema(
            {"passed": bool, "explanation": str, "evidence": str},
            ["passed", "explanation"],
        ),
    )
    async def decide(args: dict[str, Any]) -> dict[str, Any]:
        passed = args.get("passed")
        explanation = str(args.get("explanation") or "").strip()
        if not isinstance(passed, bool):
            return _say("passed must be true or false", error=True)
        if not explanation:
            return _say("a verdict needs an explanation of what the agent did", error=True)
        evidence = str(args.get("evidence") or "").strip()
        if evidence:
            logger.info("judge evidence for %s: %s", getattr(goal, "name", ""), evidence)
        verdict["it"] = (passed, explanation)
        return _say("recorded")

    spec = SessionSpec(
        system_prompt=_INSTRUCTIONS,
        servers={
            "world": tool_server(
                name="world",
                tools=[inspect_world, query_world, read_transcript, decide],
            )
        },
        max_turns=12,
        model=judge_model(),
    )
    situation = ""
    if scenario is not None:
        situation = (
            f"The situation the conversation set up: {getattr(scenario, 'instruction', '') or '(not given)'}\n"
            f"What it was meant to show: {getattr(scenario, 'tests', '') or '(not given)'}\n\n"
        )
    prompt = (
        f"{situation}"
        f"Claim {getattr(goal, 'name', '')!r}.\n"
        f"What it means: {getattr(goal, 'what', '') or '(none written)'}\n"
        f"Why it needs judgement: {getattr(goal, 'judged', '')}\n\n"
        f"Actions the agent took:\n{_dump_calls(calls)}\n\n"
        + (
            ""
            if calls
            else "No actions were recorded, so only the conversation is observable: decide a "
            "claim about what the agent did from what it said, confirmed and did not contradict.\n\n"
        )
        + "Look as needed, then call decide."
    )
    retry = (
        f"{prompt}\n\nWhat was said:\n{_transcript(messages) if messages else '(nothing captured)'}\n\n"
        "Everything needed is above. Call decide now with passed true or false."
    )
    for attempt, text in enumerate((prompt, retry), start=1):
        try:
            async with Stage(spec, name="judge-sub-goals", overheard=False) as stage:
                await stage.say(text)
        except Exception as exc:  # noqa: BLE001 - a judge that could not run is not a failed agent
            logger.warning(
                "judge attempt %d for %s could not run: %s: %s",
                attempt, getattr(goal, "name", ""), type(exc).__name__, exc,
            )
        if "it" in verdict:
            return verdict["it"]
    logger.warning("judge gave no verdict for %s", getattr(goal, "name", ""))
    return None, ""


def _transcript(messages: Sequence[Any]) -> str:
    """The turns as spoken, oldest first. A long call keeps its tail, where a readback would be."""
    body = "\n".join(
        f"{m.get('role') or 'unknown'}: {str(m.get('content') or '').strip()}"
        for m in messages
        if isinstance(m, dict)
    )
    return body if len(body) <= _TRANSCRIPT_LIMIT else "...\n" + body[-_TRANSCRIPT_LIMIT:]


def _call(call: Any) -> dict[str, Any]:
    return {
        "name": getattr(call, "name", ""),
        "arguments": getattr(call, "arguments", None) or {},
        "result": getattr(call, "result", None),
        "ok": bool(getattr(call, "ok", False)),
        "refused": bool(getattr(call, "refused", False)),
        "error": str(getattr(call, "error", "") or ""),
    }


def _say(text: str, *, error: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if error:
        body["is_error"] = True
    return body
