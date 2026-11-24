"""A customer agent declared directly: its name, instructions and tool schemas (connector
`mcp_client`).

Vapi and Retell agents are inspected at their provider. An agent that is an MCP client (a coding
agent, a LangGraph application, a customer's own service) has no provider to inspect, so the
customer declares it: the tools it calls, each with its JSON schema, and its instructions.
Adaptation then treats the declaration exactly as an inspected profile: every declared tool is
admitted as routed, the mode is chosen on the schemas (`profile.select_mode`), the builder maps and
proves the binding, and a run serves the agent's own tool names over the world's MCP endpoint
(`world_mcp`), each call routed through the binding. The declaration's digest is the agent's
identity in the binding, as an inspected provider profile's is: a changed tool or instruction is
another agent, adapted again rather than run under this binding.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..json_schemas import schema_problems
from ..provider_import import ToolAdmission, provider_profile_digest

DECLARED = "declared"  # the profile's `provider`
MCP_CLIENT = "mcp_client"  # the connector
_TOOL_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
_EMPTY_PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}}


class DeclaredTool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=_TOOL_NAME_PATTERN)
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: dict(_EMPTY_PARAMETERS))

    @field_validator("parameters")
    @classmethod
    def _object_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("type") != "object":
            raise ValueError("parameters must be a JSON Schema object")
        if problems := schema_problems(value):
            raise ValueError(
                f"parameters must be a JSON Schema of its own: {'; '.join(problems)}"
            )
        return value


class DeclaredAgent(BaseModel):
    """What the customer tells us about an agent we cannot inspect. Nothing here is secret: the
    agent's keys stay with the customer, who runs it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    instructions: str = ""
    tools: list[DeclaredTool] = Field(default_factory=list)
    # The customer's own identifier for this agent, when it has one.
    agent_id: str = ""

    @model_validator(mode="after")
    def _unique_tools(self) -> DeclaredAgent:
        names = [tool.name for tool in self.tools]
        if repeated := sorted({name for name in names if names.count(name) > 1}):
            raise ValueError(f"tools declared more than once: {', '.join(repeated)}")
        return self


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "agent"


_DERIVED = ("provider", "source_target_id", "modality")


def declared_profile(config: Mapping[str, Any]) -> dict[str, Any]:
    """The inspected-profile shape of a declared agent, from a job's `agent.config`, a saved
    profile file or a profile this made. `ValueError` when the declaration is not one.

    The one normalization of a declaration: adaptation's profile, the binding's identity and
    compose's check of the run's agent all come through here, and a profile normalizes to itself,
    so an explicit `agent_id` is kept rather than replaced by the name's slug on the way."""
    body = dict(config)
    if body.get("provider") == DECLARED:
        body = {key: value for key, value in body.items() if key not in _DERIVED}
    agent = DeclaredAgent.model_validate(body)
    profile = {
        "provider": DECLARED,
        "source_target_id": agent.agent_id or _slug(agent.name),
        "name": agent.name,
        "modality": "chat",
        "instructions": agent.instructions,
        "tools": [tool.model_dump() for tool in agent.tools],
    }
    if agent.agent_id:
        profile["agent_id"] = agent.agent_id
    return profile


def declared_digest(config: Mapping[str, Any]) -> str:
    """The binding identity of a declared agent: what `build_profile` records as its
    `provider_digest`, so a run can check the declaration it was given is the one bound."""
    return provider_profile_digest(declared_profile(config))


def declared_admission(target: Mapping[str, Any]) -> list[ToolAdmission]:
    """Every declared tool is routed: the agent has nothing that acts outside the world."""
    return [
        ToolAdmission(
            name=str(tool["name"]),
            provider_type=DECLARED,
            action="routed",
            reason="declared by the agent; every call is routed to the world",
        )
        for tool in target.get("tools") or []
    ]


def declared_tool_definitions(target: Mapping[str, Any]) -> list[dict[str, Any]]:
    """`{name, description, parameters, admission}` of every declared tool, as the binding
    records them and the world's MCP endpoint lists them."""
    return [
        {
            "name": str(tool["name"]),
            "description": str(tool.get("description") or ""),
            "parameters": dict(tool.get("parameters") or _EMPTY_PARAMETERS),
            "admission": "routed",
        }
        for tool in target.get("tools") or []
    ]
