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

# How a person reaches an agent. This decides how it is later run — voice goes out as a live
# call, everything else runs locally — so it is defined once and referenced, never retyped.
MODALITIES = ("voice", "chat", "browser")

_STRING_FIELDS = (
    "agent",
    "one_liner",
    "modality",
    "system_prompt_excerpt",
    "notes",
)
_LIST_FIELDS = (
    "hard_constraints",
    "real_use_cases",
    "amendments",
)
_DICT_FIELDS = ("data_schema", "base_environment")


class ToolSpec(BaseModel):
    """One tool the agent really has.

    ``args`` is the load-bearing field: the world's handlers, the probes and every scenario are
    built from these exact names. It is also the one most often written under another name —
    ``parameters``, ``arguments``, ``params`` — or left out while ``arg_types`` names every
    argument anyway. All of those are the same information, so they are accepted and normalised
    rather than rejected, because a contract bounced for a synonym costs a full turn and teaches
    nothing about the agent.
    """

    @model_validator(mode="before")
    @classmethod
    def _normalize_args(cls, payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        if not payload.get("args"):
            for alias in ("parameters", "arguments", "params", "arg_names"):
                value = payload.get(alias)
                if isinstance(value, list) and value:
                    payload["args"] = value
                    break
                # Some writers give {name: type} where a list was asked for. The keys are the
                # argument names, which is exactly what was wanted.
                if isinstance(value, dict) and value:
                    payload["args"] = list(value)
                    payload.setdefault(
                        "arg_types", {k: str(v) for k, v in value.items()}
                    )
                    break
        if not payload.get("args"):
            # Nothing named the arguments directly, but a per-argument map still names them.
            for source in ("arg_types", "arg_values"):
                mapping = payload.get(source)
                if isinstance(mapping, dict) and mapping:
                    payload["args"] = list(mapping)
                    break
        if isinstance(payload.get("args"), str):
            payload["args"] = [payload["args"]]
        if isinstance(payload.get("args"), list):
            payload["args"] = [str(one) for one in payload["args"]]
        return payload

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

    # Defaulted rather than mandatory so a submission that forgets it reaches validate_contract,
    # which says what to do about it, instead of dying in the schema layer with a type error.
    agent: str = ""
    one_liner: str = ""
    modality: str = "chat"
    conversational: bool = True
    system_prompt_excerpt: str = ""
    hard_constraints: list[str] = Field(default_factory=list)
    tools: list[ToolSpec] = Field(default_factory=list)
    data_schema: dict[str, Any] = Field(default_factory=dict)
    base_environment: dict[str, Any] = Field(default_factory=dict)
    real_use_cases: list[str] = Field(default_factory=list)
    # Free-form. The fields above are the fixed core because code consumes them; this is where
    # the reader records whatever else about *this* agent is worth carrying forward — quirks,
    # traps, names that look real but are not — in whatever form fits. It is shown verbatim to
    # every later stage.
    notes: str = ""
    open_questions: list[str] = Field(default_factory=list)
    # Anything in here was not read from the agent's source. The contract is meant to be what
    # the agent verifiably is, so when the harness widens it the difference is recorded rather
    # than blended in, and whoever reads it later can tell the two apart.
    amendments: list[str] = Field(default_factory=list)

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
        if self.real_use_cases:
            parts.append(
                "REAL USE CASES (what this agent is actually for):\n  - "
                + "\n  - ".join(self.real_use_cases[:12])
            )
        if self.notes:
            parts.append(f"NOTES from reading the agent:\n{self.notes[:1500]}")
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
