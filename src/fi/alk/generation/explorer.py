"""Contract extraction as a bounded tool loop over the agent's repository.

The model is given read-only tools (list a directory, read a file, search text) plus one submit tool,
and a turn budget. It decides what to open, exactly like a coding agent reading an unfamiliar repo.
The harness owns the loop: it executes tool calls inside a path-sandboxed root, feeds results back,
validates the submitted contract, and returns validator problems to the model for another attempt
instead of accepting a bad contract. If the turn budget runs out, the harness forces a submission.

Every string the model sees is self-contained: the system prompt defines the task, the contract
schema, and the verification rules without assuming any outside context.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from .contract import AgentContract, validate_contract
from .llm import LLMClient

logger = logging.getLogger(__name__)

_MAX_TURNS = 20
_MAX_READ_CHARS = 12_000
_MAX_RESULT_CHARS = 14_000

_SYSTEM = """You are a senior software engineer. Your job: read the source code of an AI agent and
produce its testing CONTRACT, a JSON document that later test generation will treat as the complete
and only truth about this agent. Anything you put in the contract that is not verifiably in the code
will corrupt every test built on it, so you verify identifiers by reading the files where they are
defined, and you copy names character for character.

You have tools to explore the repository: list directories, read files, and search for text. Explore
until you have verified, then call submit_contract exactly once with the finished contract. Work
efficiently: start from the README and the files that define the agent's tools, instructions, and
data; do not read files that cannot change the contract (build config, lockfiles, tests of the
framework itself).

The contract fields, all required (use an empty list or empty string only when genuinely nothing
applies):
- agent: short name for the agent
- one_liner: what the agent does, one sentence
- modality: how a user reaches it: "voice" | "chat" | "browser" | "code" | "data_sql" | "research" |
  "computer_use" | "other"
- conversational: true when a user talks with it across multiple turns (voice and chat agents), else
  false
- system_prompt_excerpt: the 10-20 most behavior-defining lines of the agent's own instructions,
  quoted verbatim from the code
- hard_constraints: rules the agent's instructions or code actually enforce (required elicitation,
  refusals, limits, ordering rules), one line each, each traceable to a specific place in the code
- tools: the agent's real callable tools: [{"name": "<exact function/tool name>", "args": ["<exact
  parameter names>"], "arg_values": {"<arg>": [<valid values or ids, from code or data>]},
  "description": "<one line>"}]. Only tools that exist. Exact spelling. For enum-like parameters,
  list the real valid values you found.
- data_schema: the real data the agent operates over (menu items, tables, records) with REAL ids,
  names, prices or values, as compact JSON. Later tests take every concrete value from here, so
  include the actual entries you found, not examples of their shape.
- base_environment: {"summary": "<one line: the world this agent needs>", "seed": {<the starting
  state a test environment should contain, drawn from the real data>}}
- real_use_cases: 8-15 one-line things a user genuinely does with this agent, each naming the tool
  and arguments it exercises where relevant
- signature_cases: 6-12 one-line test-worthy situations grounded in a specific constraint or data
  fact you found (a constraint that forces a clarifying question, an id that does not exist, a
  refusal the instructions demand, a correction mid-task)
- grading_notes: 3-6 lines on how to verify THIS agent behaved correctly: what state it changes,
  which tool arguments carry the user's request, what "correct" means
- anti_hallucination: names someone might plausibly invent for this agent that do NOT exist (wrong
  tool names, wrong argument names, ids that look valid but are not), so tests can be checked
  against them

If submit_contract returns validation problems, fix them and submit again."""

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List entries of a directory inside the agent repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path; '' for the root.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file inside the agent repository (truncated past 12000 chars; "
            "pass offset to continue).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {
                        "type": "integer",
                        "description": "Character offset to start from.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_text",
            "description": "Search all repository files for a plain substring; returns file:line hits.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_contract",
            "description": "Submit the finished contract. Call exactly once, after verifying.",
            "parameters": {
                "type": "object",
                "properties": {
                    "contract": {
                        "type": "object",
                        "description": "The full contract JSON.",
                    }
                },
                "required": ["contract"],
            },
        },
    },
]

_SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".omega",
}


class _RepoTools:
    """Path-sandboxed read tools over one repository root."""

    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)

    def _resolve(self, path: str) -> str:
        resolved = os.path.abspath(os.path.join(self.root, str(path or "").lstrip("/")))
        if resolved != self.root and not resolved.startswith(self.root + os.sep):
            raise ValueError(f"path escapes the repository root: {path}")
        return resolved

    def list_dir(self, path: str = "") -> str:
        target = self._resolve(path)
        if not os.path.isdir(target):
            return f"not a directory: {path}"
        entries = []
        for entry in sorted(os.listdir(target)):
            if entry in _SKIP_DIRS:
                continue
            full = os.path.join(target, entry)
            suffix = (
                "/" if os.path.isdir(full) else f"  ({os.path.getsize(full)} bytes)"
            )
            entries.append(f"{entry}{suffix}")
        return "\n".join(entries) or "(empty)"

    def read_file(self, path: str, offset: int = 0) -> str:
        target = self._resolve(path)
        if not os.path.isfile(target):
            return f"not a file: {path}"
        try:
            with open(target, encoding="utf-8", errors="ignore") as fh:
                fh.seek(max(0, int(offset or 0)))
                body = fh.read(_MAX_READ_CHARS)
        except OSError as exc:
            return f"read failed: {exc}"
        marker = (
            ""
            if len(body) < _MAX_READ_CHARS
            else f"\n... truncated; continue with offset={offset + _MAX_READ_CHARS}"
        )
        return body + marker

    def search_text(self, query: str) -> str:
        query = str(query or "")
        if not query.strip():
            return "empty query"
        hits: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for filename in filenames:
                full = os.path.join(dirpath, filename)
                try:
                    with open(full, encoding="utf-8", errors="ignore") as fh:
                        for line_number, line in enumerate(fh, 1):
                            if query in line:
                                rel = os.path.relpath(full, self.root)
                                hits.append(
                                    f"{rel}:{line_number}: {line.strip()[:160]}"
                                )
                                if len(hits) >= 60:
                                    return "\n".join(hits) + "\n... (capped at 60 hits)"
                except OSError:
                    continue
        return "\n".join(hits) or "no hits"


def explore_contract(
    root: str, llm: LLMClient, *, max_turns: int = _MAX_TURNS
) -> AgentContract:
    """Run the exploration loop until a valid contract is submitted."""
    tools = _RepoTools(root)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                "The agent repository root is mounted for your tools. Explore it and submit the "
                "contract.\n\nRoot listing:\n" + tools.list_dir("")
            ),
        },
    ]
    submitted: AgentContract | None = None
    for turn in range(max_turns):
        forced = turn == max_turns - 1
        if forced:
            messages.append(
                {
                    "role": "user",
                    "content": "Turn budget exhausted. Call submit_contract NOW with your best "
                    "verified contract.",
                }
            )
        reply = llm.complete_turn(messages, tools=_TOOLS, temperature=0.15)
        calls = reply.get("tool_calls") or []
        if not calls:
            messages.append(
                {"role": "assistant", "content": reply.get("content") or ""}
            )
            messages.append(
                {
                    "role": "user",
                    "content": "Use the tools. Explore the repository, then call submit_contract.",
                }
            )
            continue
        messages.append(_assistant_message(reply))
        for call in calls:
            name = call.get("name")
            arguments = call.get("arguments") or {}
            if name == "submit_contract":
                result, submitted = _try_submit(arguments)
            else:
                result = _run_tool(tools, name, arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or name,
                    "content": str(result)[:_MAX_RESULT_CHARS],
                }
            )
            if submitted is not None:
                logger.info("contract submitted", extra={"turns": turn + 1})
                return submitted
    raise RuntimeError(
        f"exploration ended after {max_turns} turns without a valid contract"
    )


def _assistant_message(reply: dict[str, Any]) -> dict[str, Any]:
    raw = reply.get("raw")
    if raw is not None:
        try:
            return raw.model_dump()
        except AttributeError:
            pass
    return {
        "role": "assistant",
        "content": reply.get("content") or "",
        "tool_calls": [
            {
                "id": call.get("id") or call.get("name"),
                "type": "function",
                "function": {
                    "name": call.get("name"),
                    "arguments": json.dumps(call.get("arguments") or {}),
                },
            }
            for call in reply.get("tool_calls") or []
        ],
    }


def _run_tool(tools: _RepoTools, name: str, arguments: dict[str, Any]) -> str:
    try:
        if name == "list_dir":
            return tools.list_dir(arguments.get("path", ""))
        if name == "read_file":
            return tools.read_file(
                arguments.get("path", ""), int(arguments.get("offset") or 0)
            )
        if name == "search_text":
            return tools.search_text(arguments.get("query", ""))
    except ValueError as exc:
        return str(exc)
    return f"unknown tool: {name}"


def _try_submit(arguments: dict[str, Any]) -> tuple[str, AgentContract | None]:
    payload = arguments.get("contract")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = None
    if not isinstance(payload, dict):
        return (
            "submit_contract requires a JSON object under the 'contract' key; fix and resubmit",
            None,
        )
    try:
        contract = AgentContract.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - fed back to the model
        return (
            f"contract failed schema validation; fix and resubmit: {_short(exc)}",
            None,
        )
    problems = validate_contract(contract)
    if problems:
        return f"contract failed checks; fix and resubmit: {problems}", None
    return "accepted", contract


def _short(exc: Exception) -> str:
    return re.sub(r"\s+", " ", str(exc))[:600]
