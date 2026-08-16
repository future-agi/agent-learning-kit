"""The agent contract: what the agent verifiably is, read from its own source.

Everything downstream is confined to this. A world may only implement tools listed here, a
scenario may only reference values grounded in here, and a checkpoint may only assert against
what is here. It is the anti-hallucination device for every later stage.

The harness produces it by reading the agent's code and calling ``submit_contract``. Validation
runs inside that tool, so problems are returned into the conversation and the model tries again
rather than a bad contract reaching disk.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, model_validator

_STRING_FIELDS = (
    "agent",
    "one_liner",
    "modality",
    "system_prompt_excerpt",
    "grading_notes",
)
_LIST_FIELDS = (
    "hard_constraints",
    "real_use_cases",
    "signature_cases",
    "anti_hallucination",
)
_DICT_FIELDS = ("data_schema", "base_environment")


class ToolSpec(BaseModel):
    name: str
    args: list[str] = Field(default_factory=list)
    arg_types: dict[str, str] = Field(default_factory=dict)
    arg_values: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class AgentContract(BaseModel):
    """What the agent verifiably is. Nothing downstream may contradict this."""

    @model_validator(mode="before")
    @classmethod
    def _normalize_shapes(cls, payload: Any) -> Any:
        """Model JSON varies in benign ways: a list where prose was asked, a bare string where a
        list was. Normalize instead of rejecting, because shape variance is not a grounding
        error and rejecting it burns turns on something that does not matter."""
        if not isinstance(payload, dict):
            return payload
        for key in _STRING_FIELDS:
            value = payload.get(key)
            if isinstance(value, list):
                payload[key] = "\n".join(str(item) for item in value)
            elif value is not None and not isinstance(value, str):
                payload[key] = str(value)
        for key in _LIST_FIELDS:
            value = payload.get(key)
            if isinstance(value, str):
                payload[key] = [value]
            elif isinstance(value, list):
                payload[key] = [
                    str(item) if not isinstance(item, str) else item for item in value
                ]
        for key in _DICT_FIELDS:
            value = payload.get(key)
            if value is not None and not isinstance(value, dict):
                payload[key] = {"value": value}
        return payload

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
    open_questions: list[str] = Field(default_factory=list)

    def tool_names(self) -> set[str]:
        return {tool.name for tool in self.tools}

    def brief(self, *, full_schema: bool = True, with_data: bool = False) -> str:
        """The grounding block handed to the model on every downstream call.

        ``with_data`` includes the agent's real starting records rather than only their shape.
        A stage that writes scenarios needs to know a menu exists; a stage that builds the world
        has to reproduce it row for row, and a shape without records is not enough to do that.
        """
        lines: list[str] = []
        for tool in self.tools:
            signature = ", ".join(
                f"{arg}: {tool.arg_types[arg]}" if arg in tool.arg_types else arg
                for arg in tool.args
            )
            values = (
                f"  [values: {json.dumps(tool.arg_values)[:300]}]"
                if tool.arg_values
                else ""
            )
            lines.append(
                f"  - {tool.name}({signature}){values} : {tool.description[:140]}"
            )
        parts = [
            f"AGENT: {self.agent} - {self.one_liner}",
            f"MODALITY: {self.modality}",
            "REAL TOOLS (use ONLY these, with these exact arg names and types):\n"
            + ("\n".join(lines) or "  (none)"),
        ]
        if self.hard_constraints:
            parts.append(
                "HARD CONSTRAINTS the agent MUST follow (nothing may contradict these):\n  - "
                + "\n  - ".join(self.hard_constraints[:14])
            )
        if self.data_schema and full_schema:
            parts.append(
                "DATA SHAPE (the fields each record has):\n"
                + json.dumps(self.data_schema)[: 24000 if with_data else 2400]
            )
        if self.base_environment and with_data:
            parts.append(
                "THE AGENT'S REAL STARTING DATA. Reproduce this exactly, including anything\n"
                "that looks like a mistake: a misspelled id, an item marked unavailable, an odd\n"
                "price. The world is a replica of what the agent has, not a corrected version,\n"
                "and a test written against a corrected world will not catch the real bug.\n"
                + json.dumps(self.base_environment, ensure_ascii=False)
            )
        if self.grading_notes:
            parts.append(f"GRADING NOTES for this agent:\n{self.grading_notes[:900]}")
        if self.anti_hallucination:
            parts.append(
                "NEVER USE THESE (they do not exist / are wrong): "
                + json.dumps(self.anti_hallucination)[:700]
            )
        return "\n\n".join(parts)


def validate_contract(contract: AgentContract) -> list[str]:
    """Structural problems that make a contract unusable downstream.

    Deliberately narrow. This cannot tell whether the model read the agent correctly, only
    whether the result is shaped well enough to build a world from. Semantic grounding is the
    operator's job, which is why the harness surfaces the contract for review.
    """
    problems: list[str] = []
    if not contract.agent.strip():
        problems.append("empty:agent")
    if not contract.tools:
        problems.append("no-tools")
    for index, tool in enumerate(contract.tools):
        if not tool.name.strip():
            problems.append(f"tool[{index}]:no-name")
            continue
        unknown = sorted(set(tool.arg_types) - set(tool.args))
        if unknown:
            problems.append(
                f"tool[{tool.name}]:types-for-unknown-args:{','.join(unknown)}"
            )
    # A tool genuinely taking no arguments is ordinary; every tool taking none is not. It means
    # the arguments were read and then not recorded, and since the world, the probes and the
    # checkpoints are all built from these names, nothing downstream can detect their absence.
    if contract.tools and not any(tool.args for tool in contract.tools):
        problems.append(
            "no-arguments-on-any-tool: list each tool's exact parameter names in args"
        )
    if not contract.real_use_cases:
        problems.append("no-use-cases")
    # Iterate the tools, not tool_names(): that returns a set, so duplicates collapse before
    # they can be counted and the check silently never fires.
    names = [tool.name for tool in contract.tools if tool.name.strip()]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        problems.append(f"duplicate-tool-names:{','.join(duplicates)}")
    return problems
