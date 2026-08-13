"""The agent CONTRACT: the code-verified ground truth every later prompt is confined to.

Extraction hands the evidence blob to the model once and validates the result structurally. The
contract is the anti-hallucination device: rows, scenarios, and checks may only reference the tools,
arguments, entities, and constraints listed here, and the validators enforce that.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from .llm import LLMClient


class ToolSpec(BaseModel):
    name: str
    args: list[str] = Field(default_factory=list)
    arg_values: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class AgentContract(BaseModel):
    """What the agent verifiably is. Nothing downstream may contradict this."""

    agent: str
    one_liner: str = ""
    modality: str = "chat"
    conversational: bool = True
    system_prompt_excerpt: str = ""
    hard_constraints: list[str] = Field(default_factory=list)
    tools: list[ToolSpec] = Field(default_factory=list)
    data_schema: dict[str, Any] = Field(default_factory=dict)
    base_environment: dict[str, Any] = Field(default_factory=dict)
    real_use_cases: list[str] = Field(default_factory=list)
    signature_cases: list[str] = Field(default_factory=list)
    grading_notes: str = ""
    anti_hallucination: list[str] = Field(default_factory=list)

    def tool_names(self) -> set[str]:
        return {tool.name for tool in self.tools}

    def brief(self, *, full_schema: bool = True) -> str:
        """The grounding block handed to the model on every downstream call."""
        lines: list[str] = []
        for tool in self.tools:
            values = f"  [values: {json.dumps(tool.arg_values)[:300]}]" if tool.arg_values else ""
            lines.append(f"  - {tool.name}({', '.join(tool.args)}){values} : {tool.description[:140]}")
        parts = [
            f"AGENT: {self.agent} - {self.one_liner}",
            f"MODALITY: {self.modality}",
            "REAL TOOLS (use ONLY these, with these exact arg names):\n" + ("\n".join(lines) or "  (none)"),
        ]
        if self.hard_constraints:
            parts.append(
                "HARD CONSTRAINTS the agent MUST follow (checks must never contradict these):\n  - "
                + "\n  - ".join(self.hard_constraints[:14])
            )
        if self.data_schema and full_schema:
            parts.append(
                "REAL DATA / SCHEMA (ground every value and id in this; never invent):\n"
                + json.dumps(self.data_schema)[:2400]
            )
        if self.grading_notes:
            parts.append(f"GRADING NOTES (how checks must be written for THIS agent):\n{self.grading_notes[:900]}")
        if self.anti_hallucination:
            parts.append(
                "NEVER USE THESE (they do not exist / are wrong): "
                + json.dumps(self.anti_hallucination)[:700]
            )
        return "\n\n".join(parts)


_EXTRACT_SYSTEM = """You are a senior engineer reading an AI agent's actual source to write its \
testing CONTRACT. Everything you output must be verifiably present in the provided material; when \
unsure, leave a field empty rather than guess. Exact identifiers matter: tool names, argument names, \
enum values, and entity ids must be copied character for character."""

_EXTRACT_USER = """Read this agent's repository material and return its CONTRACT as JSON.

{evidence}

Return JSON with exactly these keys:
- agent: short name
- one_liner: what the agent does, one sentence
- modality: one of voice | chat | browser | code | data_sql | research | computer_use | other
- conversational: true if a user talks to it across turns (voice/chat), else false
- system_prompt_excerpt: the most behavior-defining 10-20 lines of its instructions, verbatim
- hard_constraints: rules its instructions or code enforce (refusals, required elicitation, limits), \
each one line, verbatim-grounded
- tools: [{{name, args (exact parameter names), arg_values (enums / valid ids per arg, from code or \
data), description}}] - only tools that exist in the code
- data_schema: the real data model it operates over (menus, tables, entities with their REAL ids and \
prices/values), compact JSON. This is what checks will be grounded in, so include real item ids.
- base_environment: {{summary, seed}} - what world must exist for it to run (mocked), with seed data \
drawn from the real data
- real_use_cases: 8-15 one-line user-facing things people genuinely do with it, each naming the tool \
and args it exercises where relevant
- signature_cases: 6-12 one-line cases its own engineer would insist on testing (constraint \
enforcement, disambiguation, not-found, refusal, correction) - each grounded in a specific \
constraint or data fact above
- grading_notes: 3-6 lines on how to check THIS agent (what state it changes, which tool arguments \
carry the user's request, what "correct" means)
- anti_hallucination: interface-shaped names someone might plausibly invent for this agent that do \
NOT exist (wrong tool names, wrong arg names, nonexistent menu/table entries)"""


def extract_contract(evidence_text: str, llm: LLMClient) -> AgentContract:
    raw = llm.complete_json(
        _EXTRACT_SYSTEM,
        _EXTRACT_USER.format(evidence=evidence_text),
        temperature=0.15,
        max_tokens=10_000,
    )
    if isinstance(raw, list):
        raw = next((item for item in raw if isinstance(item, dict)), {})
    contract = AgentContract.model_validate(raw)
    problems = validate_contract(contract)
    if problems:
        raise ValueError(f"extracted contract failed validation: {problems}")
    return contract


def validate_contract(contract: AgentContract) -> list[str]:
    problems: list[str] = []
    if not contract.agent.strip():
        problems.append("empty:agent")
    if not contract.tools:
        problems.append("no-tools")
    for index, tool in enumerate(contract.tools):
        if not tool.name.strip():
            problems.append(f"tool[{index}]:no-name")
    if not contract.real_use_cases:
        problems.append("no-use-cases")
    seen = [name for name in contract.tool_names() if name]
    if len(seen) != len(set(seen)):
        problems.append("duplicate-tool-names")
    return problems
