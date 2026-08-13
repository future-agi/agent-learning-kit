"""Production-trace grounding: turn real interactions into test scenarios.

The strongest grounding a test can have is that it already happened. Given transcripts of real
calls (or chat logs) alongside the agent's contract, mining distills each interaction into a
scenario plan in the standard schema, with provenance pinned to the source trace. Mined plans then
pass the same gates as invented ones: reality supplies the situation, the contract still supplies
every id and value, and the validators still refuse anything ungrounded.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .contract import AgentContract
from .llm import LLMClient
from .prompts import SCENARIO_MODEL, guidance_block

_TRACE_EXTENSIONS = (".json", ".jsonl", ".txt", ".md", ".csv")
_MAX_TRACE_CHARS = 7000
_MAX_TRACES_PER_CALL = 4


def load_traces(path: str) -> list[dict[str, str]]:
    """Load raw traces from a file or folder: [{"ref": <name>, "text": <bounded content>}]."""
    paths: list[str] = []
    if os.path.isfile(path):
        paths = [path]
    elif os.path.isdir(path):
        for name in sorted(os.listdir(path)):
            if name.endswith(_TRACE_EXTENSIONS) and not name.startswith("."):
                paths.append(os.path.join(path, name))
    traces: list[dict[str, str]] = []
    for file_path in paths:
        try:
            with open(file_path, encoding="utf-8", errors="ignore") as fh:
                text = fh.read(_MAX_TRACE_CHARS)
        except OSError:
            continue
        if text.strip():
            traces.append({"ref": os.path.basename(file_path), "text": text})
    return traces


def _mining_prompt(brief: str, batch: list[dict[str, str]], guidance: str) -> str:
    blob = "\n\n".join(f"--- TRACE {t['ref']} ---\n{t['text']}" for t in batch)
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
                row["provenance"] = {
                    "kind": "production_trace",
                    "trace_ref": str(row.get("trace_ref", "")),
                }
                plans.append(row)
    return plans


def _self_test() -> str:  # pragma: no cover - imported for existence checks only
    return json.dumps({"module": "traces"})
