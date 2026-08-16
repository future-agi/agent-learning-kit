"""The tools the harness offers a session, and the gates behind them.

The model does judgement; these do the parts that must be exact. Validation lives inside the
tool rather than after the session, so a problem is returned into the conversation and fixed on
the next turn instead of surfacing once the session is already over.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from .contract import AgentContract, validate_contract

CONTRACT_SERVER = "contract"


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _problems(problems: list[str]) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": "Not accepted. Fix these and call submit_contract again:\n  - "
                + "\n  - ".join(problems),
            }
        ],
        "is_error": True,
    }


def accept_contract(payload: dict[str, Any], destination: Path) -> dict[str, Any]:
    """The gate itself: validate, and write only if it passes.

    A plain function rather than only a tool body, so the rule that decides whether a contract
    is usable can be exercised and reasoned about without standing up a session.
    """
    try:
        contract = AgentContract.model_validate(payload)
    except Exception as invalid:
        return _problems([f"schema:{invalid}"[:600]])

    problems = validate_contract(contract)
    if problems:
        return _problems(problems)

    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "contract.json"
    path.write_text(
        json.dumps(contract.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return _ok(
        f"Accepted and saved to {path}.\n"
        f"{len(contract.tools)} tools: {', '.join(sorted(contract.tool_names()))}\n"
        f"{len(contract.hard_constraints)} rules, "
        f"{len(contract.real_use_cases)} use cases, "
        f"{len(contract.open_questions)} open questions."
    )


def contract_tools(destination: Path) -> Any:
    """A server exposing ``submit_contract``, writing to ``destination`` on acceptance."""

    @tool(
        "submit_contract",
        "Submit the agent's testing contract. Validated on submission; problems are returned "
        "to you so you can correct them and submit again.",
        {
            "agent": str,
            "one_liner": str,
            "modality": str,
            "conversational": bool,
            "system_prompt_excerpt": str,
            "hard_constraints": list,
            "tools": list,
            "data_schema": dict,
            "base_environment": dict,
            "real_use_cases": list,
            "signature_cases": list,
            "grading_notes": str,
            "anti_hallucination": list,
            "open_questions": list,
        },
    )
    async def submit_contract(args: dict[str, Any]) -> dict[str, Any]:
        return accept_contract(args, destination)

    return create_sdk_mcp_server(
        name=CONTRACT_SERVER, version="0.1.0", tools=[submit_contract]
    )


def qualified(server: str, tool_name: str) -> str:
    """The name an in-process MCP tool is granted under."""
    return f"mcp__{server}__{tool_name}"
