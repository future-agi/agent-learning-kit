"""Production-trace grounding: turn real interactions into test scenarios.

The strongest grounding a test can have is that it already happened. Given transcripts of real
calls (or chat logs) alongside the agent's contract, mining distills each interaction into a
scenario plan in the standard schema, with provenance pinned to the source trace. Mined plans then
pass the same gates as invented ones: reality supplies the situation, the contract still supplies
every id and value, and the validators still refuse anything ungrounded.

Two loops sit in front of that. An exploration loop reads a folder whose layout is unknown, works
out how a trace is stored there, and decides which traces are worth mining: where there are more
than a suite can hold, the ones where the interaction went wrong earn their place first. An
amplification loop then takes each failure and asks for the neighbouring situations that share it,
so a suite pins the exact interaction that broke and fences the class it belongs to.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from .contract import AgentContract
from .explorer import READ_TOOLS, ReadOnlyTree, assistant_message, run_read_tool
from .llm import LLMClient
from .prompts import SCENARIO_MODEL, guidance_block

logger = logging.getLogger(__name__)

_TRACE_EXTENSIONS = (".json", ".jsonl", ".txt", ".md", ".csv", ".log", ".yaml", ".yml")
_MAX_TRACE_CHARS = 7000
_MAX_TRACES_PER_CALL = 4
_MAX_TRACES_MINED = 40
_MAX_EXPLORE_TURNS = 14
_MAX_RESULT_CHARS = 14_000
_MAX_TRACE_FILES = 400  # the flat fallback reads a bounded slice of a large tree


def _window(text: str) -> str:
    """Keep the head (intent) AND the tail (resolution) of a long interaction."""
    if len(text) <= _MAX_TRACE_CHARS:
        return text
    half = _MAX_TRACE_CHARS // 2
    return text[:half] + "\n... [middle omitted] ...\n" + text[-half:]


def load_traces(path: str) -> list[dict[str, str]]:
    """Load raw traces from a file or a folder tree, without asking a model anything.

    Walks recursively. A flat listing silently returned almost nothing for the common case of
    recordings filed under dated subdirectories, and because this is the fallback when
    exploration does not submit, the grounding disappeared without any error.
    """
    root = os.path.abspath(path)
    paths: list[str] = []
    if os.path.isfile(root):
        paths = [root]
    elif os.path.isdir(root):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                if name.endswith(_TRACE_EXTENSIONS) and not name.startswith("."):
                    paths.append(os.path.join(dirpath, name))
                    if len(paths) >= _MAX_TRACE_FILES:
                        break
            if len(paths) >= _MAX_TRACE_FILES:
                break
    traces: list[dict[str, str]] = []
    for file_path in paths:
        try:
            with open(file_path, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        if not text.strip():
            continue
        ref = (
            os.path.relpath(file_path, root)
            if os.path.isdir(root)
            else os.path.basename(file_path)
        )
        traces.append({"ref": ref, "text": _window(text)})
    return traces


# --------------------------------------------------------------------------------------
# Exploration: an unknown folder of recorded interactions
# --------------------------------------------------------------------------------------

_EXPLORER_SYSTEM = """You are a test engineer who has been handed a folder of recorded interactions
between real users and a deployed AI agent. Nobody has told you how the folder is organised or what
format the recordings are in. Your job is to work that out for yourself and then choose which
recordings deserve to become regression tests.

You have tools to list directories, read files, and search for text. Start by opening enough of the
folder to understand its layout and how a single recording is stored: one file per interaction, many
interactions inside one file, or a folder per interaction. Read whole examples, not fragments, until
you can say what a recording looks like and roughly how many there are.

Then judge the recordings. A recording is worth turning into a test when the interaction went wrong:
the user did not get what they came for, the agent did something the user had to correct, an error
or failure appears in the exchange, the user repeated themselves or gave up, or the outcome
contradicts what the agent was asked to do. Interactions that simply went well are worth far less,
because a test built from them only confirms what already works.

How many to select depends on what you find. When the folder holds only a handful of recordings,
take all of them. When it holds more than a suite could reasonably contain, select the ones that
went wrong first, and add successful ones only to cover a common path that no failing recording
touches. Never select more than 25.

You judge each recording from its own content. There may be a status or outcome field you can trust;
there may not be, in which case you read the exchange and decide. Say which you did.

When you have chosen, call submit_selection exactly once. If it reports a problem, fix it and submit
again."""

_SELECT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_selection",
        "description": "Submit the recordings chosen for regression testing. Call once, after exploring.",
        "parameters": {
            "type": "object",
            "properties": {
                "format_notes": {
                    "type": "string",
                    "description": "How a recording is stored here and how you judged its outcome.",
                },
                "total_seen": {
                    "type": "integer",
                    "description": "How many recordings the folder appears to hold in total.",
                },
                "selected": {
                    "type": "array",
                    "description": "The chosen recordings, most valuable first.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Path to the file holding this recording, relative to the folder root.",
                            },
                            "outcome": {
                                "type": "string",
                                "enum": ["failed", "succeeded"],
                                "description": "Whether the interaction went wrong for the user.",
                            },
                            "why": {
                                "type": "string",
                                "description": "One line: what went wrong, or why this path is worth keeping.",
                            },
                        },
                        "required": ["path", "outcome", "why"],
                    },
                },
            },
            "required": ["selected", "total_seen", "format_notes"],
        },
    },
}


def _folder_overview(root: str) -> str:
    """Deterministic census of the folder, so the model starts from facts instead of guesses."""
    by_extension: dict[str, int] = {}
    samples: list[str] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            total += 1
            extension = os.path.splitext(filename)[1] or "(none)"
            by_extension[extension] = by_extension.get(extension, 0) + 1
            if len(samples) < 12:
                samples.append(os.path.relpath(os.path.join(dirpath, filename), root))
    census = ", ".join(
        f"{count} x {extension}" for extension, count in sorted(by_extension.items())
    )
    return (
        f"The folder holds {total} files in total ({census}).\n"
        f"A sample of paths:\n" + "\n".join(f"- {s}" for s in samples)
    )


def explore_traces(
    root: str, llm: LLMClient, *, max_turns: int = _MAX_EXPLORE_TURNS
) -> list[dict[str, str]]:
    """Let the model navigate an unknown trace folder and choose what to mine.

    Returns the selected recordings with their text loaded, failing ones first. An exploration that
    never submits returns nothing rather than guessing: the caller falls back to a flat load.
    """
    tools = ReadOnlyTree(root)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _EXPLORER_SYSTEM},
        {
            "role": "user",
            "content": (
                "The recordings folder is mounted for your tools.\n\n"
                + _folder_overview(root)
                + "\n\nRoot listing:\n"
                + tools.list_dir("")
            ),
        },
    ]
    for turn in range(max_turns):
        if turn == max_turns - 1:
            messages.append(
                {
                    "role": "user",
                    "content": "Turn budget exhausted. Call submit_selection NOW with your best "
                    "current selection.",
                }
            )
        reply = llm.complete_turn(
            messages,
            tools=READ_TOOLS + [_SELECT_TOOL],
            temperature=0.15,
            max_tokens=12_000,
        )
        calls = reply.get("tool_calls") or []
        if not calls:
            messages.append(
                {"role": "assistant", "content": reply.get("content") or ""}
            )
            messages.append(
                {
                    "role": "user",
                    "content": "Use the tools. Explore the folder, then call submit_selection.",
                }
            )
            continue
        messages.append(assistant_message(reply))
        for call in calls:
            name = call.get("name")
            arguments = call.get("arguments") or {}
            if name == "submit_selection":
                selected, problem = _load_selection(tools, root, arguments)
                if selected:
                    logger.info(
                        "trace selection",
                        extra={
                            "selected": len(selected),
                            "total_seen": arguments.get("total_seen"),
                        },
                    )
                    return selected
                result = problem
            else:
                result = run_read_tool(tools, name, arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or name,
                    "content": str(result)[:_MAX_RESULT_CHARS],
                }
            )
    logger.warning("trace exploration ended without a selection")
    return []


def _load_selection(
    tools: ReadOnlyTree, root: str, arguments: dict[str, Any]
) -> tuple[list[dict[str, str]], str]:
    """Read the chosen files inside the sandbox; report unreadable choices back to the model."""
    raw = arguments.get("selected")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    if not isinstance(raw, list) or not raw:
        return (
            [],
            "submit_selection needs a non-empty 'selected' list; fix and submit again",
        )
    loaded: list[dict[str, str]] = []
    missing: list[str] = []
    for entry in raw[:25]:
        if not isinstance(entry, dict) or not entry.get("path"):
            continue
        path = str(entry["path"])
        try:
            target = tools.resolve(path)
        except ValueError:
            missing.append(path)
            continue
        if not os.path.isfile(target):
            missing.append(path)
            continue
        try:
            with open(target, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            missing.append(path)
            continue
        if not text.strip():
            missing.append(path)
            continue
        loaded.append(
            {
                "ref": os.path.relpath(target, os.path.abspath(root)),
                "text": _window(text),
                "outcome": str(entry.get("outcome", "")).strip().lower(),
                "why": str(entry.get("why", "")),
            }
        )
    if not loaded:
        return [], (
            f"none of the selected paths could be read: {missing[:10]}. "
            "Use paths relative to the folder root, then submit again"
        )
    if missing:
        logger.warning("trace selection skipped unreadable paths: %s", missing[:10])
    # Failing interactions carry more test value, so they head the queue for the share of N.
    loaded.sort(key=lambda t: 0 if t.get("outcome") == "failed" else 1)
    return loaded, ""


# --------------------------------------------------------------------------------------
# Mining: recordings into scenario plans
# --------------------------------------------------------------------------------------


def _mining_prompt(brief: str, batch: list[dict[str, str]], guidance: str) -> str:
    parts = []
    for trace in batch:
        header = f"--- TRACE {trace['ref']} ---"
        if trace.get("outcome"):
            header += (
                f"\n[this interaction was judged to have {trace['outcome']}"
                + (f": {trace['why']}" if trace.get("why") else "")
                + "]"
            )
        parts.append(f"{header}\n{trace['text']}")
    blob = "\n\n".join(parts)
    return f"""{brief}

Below are transcripts of REAL interactions this agent (or its production predecessor) had with real
users. Turn each into a test scenario plan that RECREATES the interaction, so the current agent can
be tested against situations that verifiably occur in production.

{blob}

For each trace, extract:
- what the user actually wanted, and which facts they stated up front versus only when asked;
- the condition of the world the interaction reveals (what existed, what was unavailable);
- how it ended, and whether the agent handled it correctly.

Then write one plan per trace, in this exact schema:
- id: a short slug
- trace_ref: the trace name it recreates, exactly as given above
- use_case: the user-facing job, in the user's words
- situation: ONE line naming the condition this interaction fixes, from the user or world side
- target_failure: if the traced agent failed, the failure it committed; if it succeeded, the
  regression that would break this real interaction
- why_it_matters: this happened with a real user; say what was or would be lost
- unique_end_state: the single correct final state for this interaction
- goal: one line, the end-objective from the user's side

Rules:
- Ground every reference in the contract above: where the trace mentions an item or value, map it to
  the contract's real id; where it mentions something outside the contract, the plan tests how the
  agent handles exactly that request, never an invented interface.
- One plan per trace. Traces showing the same situation with the same correct outcome produce ONE
  plan citing both refs.
{guidance_block(guidance)}Return JSON: {{"rows": [{{"id": "...", "trace_ref": "...", "use_case": "...",
"situation": "...", "target_failure": "...", "why_it_matters": "...", "unique_end_state": "...",
"goal": "..."}}]}}"""


def mine_traces(
    contract: AgentContract,
    traces: list[dict[str, str]],
    llm: LLMClient,
    *,
    guidance: str = "",
) -> list[dict[str, Any]]:
    """Distill raw traces into scenario plans carrying trace provenance."""
    brief = contract.brief()
    # Large trace sets: drop near-duplicate transcripts deterministically (same token-set
    # similarity used for scenario dedup), then cap what is mined. Ten thousand calls are
    # mostly repeats of the same few dozen situations; representatives carry the signal.
    from .dedup import similarity

    unique: list[dict[str, str]] = []
    for trace in traces:
        row = {"situation": trace["text"][:1500]}
        if not any(
            similarity(row, {"situation": kept["text"][:1500]}) >= 0.75
            for kept in unique
        ):
            unique.append(trace)
    if len(unique) > _MAX_TRACES_MINED:
        unique = unique[:_MAX_TRACES_MINED]
    traces = unique
    outcome_by_ref = {t["ref"]: t.get("outcome", "") for t in traces}
    known_refs = set(outcome_by_ref)
    plans: list[dict[str, Any]] = []
    for start in range(0, len(traces), _MAX_TRACES_PER_CALL):
        batch = traces[start : start + _MAX_TRACES_PER_CALL]
        raw = llm.complete_json(
            SCENARIO_MODEL,
            _mining_prompt(brief, batch, guidance),
            temperature=0.2,
            max_tokens=16_000,
        )
        rows = raw.get("rows", raw) if isinstance(raw, dict) else raw
        for row in rows if isinstance(rows, list) else []:
            if (
                isinstance(row, dict)
                and row.get("situation")
                and row.get("target_failure")
            ):
                ref = str(row.get("trace_ref", ""))
                # Provenance has to be verifiable. A plan claiming to recreate a recording
                # that was never supplied was invented, and it is worse than a missing plan
                # because the report presents it as grounded in production.
                if ref not in known_refs:
                    logger.warning(
                        "dropping mined plan citing an unknown trace_ref: %r", ref[:80]
                    )
                    continue
                row["provenance"] = {"kind": "production_trace", "trace_ref": ref}
                # A recreation of an interaction that went wrong earns a neighbourhood around it.
                row["amplify"] = outcome_by_ref.get(ref, "") == "failed"
                plans.append(row)
    return plans


# --------------------------------------------------------------------------------------
# Amplification: fencing the class a real failure belongs to
# --------------------------------------------------------------------------------------


def _amplify_prompt(brief: str, plan: dict[str, Any], want: int) -> str:
    return f"""{brief}

The scenario plan below recreates an interaction this agent had with a real user, and that
interaction went wrong. Recreating it protects against that exact interaction happening again, which
is worth doing, but it protects against nothing else: the same underlying weakness will still show
up the moment a user arrives with a slightly different version of the same situation.

THE REAL INTERACTION:
{json.dumps(plan)[:4000]}

Write {want} further scenario plans that surround this one. Each must be able to fail for the SAME
underlying reason as the real interaction, while differing in the circumstances that reach it: the
same rule tested against a different item in the agent's data, the same mistake made at a different
point in the interaction, the same demand arriving with the user's request phrased around a
different need, the same condition met when something else about the world has changed.

What every plan must satisfy:
- It stands on its own as a test: a competent version of this agent could genuinely fail it.
- It reaches the weakness by a route the real interaction did not already take, so a suite holding
  all of them tells you how wide the problem is rather than repeating one data point.
- Every item, value and identifier comes from the contract above.
- The correct end state is a single unambiguous outcome, not a range of acceptable ones.

Return JSON: {{"rows": [{{"id": "<short slug>", "use_case": "...", "situation": "<one line naming
the condition, from the user or world side>", "target_failure": "<the wrong behavior this catches>",
"why_it_matters": "<the consequence if it happens>", "unique_end_state": "<the one correct final
state>", "goal": "<one line, the end-objective from the user's side>"}}]}}"""


def amplify_plans(
    contract: AgentContract,
    plans: list[dict[str, Any]],
    llm: LLMClient,
    *,
    per_plan: int = 3,
    limit: int = 0,
) -> list[dict[str, Any]]:
    """Grow a neighbourhood around each failing recreation, capped by what the suite still needs."""
    brief = contract.brief()
    neighbours: list[dict[str, Any]] = []
    for plan in plans:
        if not plan.get("amplify"):
            continue
        if limit and len(neighbours) >= limit:
            break
        want = per_plan if not limit else max(1, min(per_plan, limit - len(neighbours)))
        raw = llm.complete_json(
            SCENARIO_MODEL,
            _amplify_prompt(brief, plan, want),
            temperature=0.45,
            max_tokens=16_000,
        )
        rows = raw.get("rows", raw) if isinstance(raw, dict) else raw
        trace_ref = str((plan.get("provenance") or {}).get("trace_ref", ""))
        for row in (rows if isinstance(rows, list) else [])[:want]:
            if (
                isinstance(row, dict)
                and row.get("situation")
                and row.get("target_failure")
            ):
                row["provenance"] = {
                    "kind": "trace_amplified",
                    "trace_ref": trace_ref,
                    "amplifies": str(plan.get("id", "")),
                }
                neighbours.append(row)
    return neighbours
