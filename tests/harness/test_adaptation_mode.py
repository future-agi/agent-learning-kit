"""Which binding mode an agent gets: canonical only when its tools are the environment's, each
called as-is."""

from __future__ import annotations

import pytest

from fi.alk.harness.adaptation.profile import (
    AgentProfile,
    argument_mismatches,
    select_mode,
)
from fi.alk.harness.bindings import run_bound_call
from fi.alk.harness.provider_import import ToolAdmission
from fi.alk.harness.provider_lifecycle import ProviderType
from fi.alk.harness.releases import BindingMode, ToolDefinition

BOOK = ToolDefinition(
    name="book_appointment",
    description="Book a slot.",
    parameters={
        "type": "object",
        "properties": {
            "patient_id": {"type": "string"},
            "slot_id": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["patient_id", "slot_id", "reason"],
    },
    mutates_state=True,
)
SEND = ToolDefinition(
    name="send_intake_forms",
    description="Send the forms.",
    parameters={
        "type": "object",
        "properties": {
            "patient_id": {"type": "string"},
            "channel": {"type": "string", "enum": ["sms", "email"]},
        },
        "required": ["patient_id", "channel"],
    },
    mutates_state=True,
)


def _agent(*tools: dict) -> AgentProfile:
    return AgentProfile(
        connector="vapi",
        provider=ProviderType.VAPI,
        target={},
        admission=[
            ToolAdmission(
                name=tool["name"], provider_type="function", action="routed", reason=""
            )
            for tool in tools
        ],
        agent_tools=[{**tool, "admission": "routed"} for tool in tools],
        agent=None,
    )


def _tool(name: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": "",
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def test_same_names_and_arguments_call_the_environment_directly():
    agent = _agent(
        _tool(
            "book_appointment",
            {
                "patient_id": {"type": "string"},
                "slot_id": {"type": "string"},
                "reason": {"type": "string", "description": "Why they are coming in"},
            },
            ["patient_id", "slot_id", "reason"],
        ),
        _tool(
            "send_intake_forms",
            {
                "patient_id": {"type": "string"},
                "channel": {"type": "string", "enum": ["email"]},
            },
            ["patient_id", "channel"],
        ),
    )
    assert select_mode(agent, [BOOK, SEND]) is BindingMode.CANONICAL


@pytest.mark.parametrize(
    "properties",
    [
        # The reviewer's case: same tool name, different argument names.
        {
            "patient": {"type": "string"},
            "slot": {"type": "string"},
            "reason": {"type": "string"},
        },
        # Same names, a required environment argument the agent never sends.
        {"patient_id": {"type": "string"}, "slot_id": {"type": "string"}},
        # Same names, an argument of another type.
        {
            "patient_id": {"type": "integer"},
            "slot_id": {"type": "string"},
            "reason": {"type": "string"},
        },
    ],
    ids=["renamed-arguments", "missing-required", "different-type"],
)
def test_same_name_with_incompatible_arguments_needs_a_routed_binding(properties):
    agent = _agent(_tool("book_appointment", properties, list(properties)))
    assert select_mode(agent, [BOOK, SEND]) is BindingMode.TOOL_ROUTED


def test_a_value_the_environment_does_not_accept_needs_a_routed_binding():
    agent = _agent(
        _tool(
            "send_intake_forms",
            {
                "patient_id": {"type": "string"},
                "channel": {"type": "string", "enum": ["sms", "email", "fax"]},
            },
            ["patient_id", "channel"],
        )
    )
    assert select_mode(agent, [BOOK, SEND]) is BindingMode.TOOL_ROUTED


def test_an_agent_with_fewer_tools_than_the_environment_is_routed():
    """Every tool it has is the environment's, called as-is, but a canonical binding would serve
    it the environment's other tools too."""
    agent = _agent(
        _tool(
            "book_appointment",
            dict(BOOK.parameters["properties"]),
            list(BOOK.parameters["required"]),
        )
    )
    assert select_mode(agent, [BOOK, SEND]) is BindingMode.TOOL_ROUTED
    assert select_mode(agent, [BOOK]) is BindingMode.CANONICAL


# An environment tool whose argument is an object: the agent's schema has to match inside it too.
OPEN = ToolDefinition(
    name="open_case",
    description="Open a case on an account.",
    parameters={
        "type": "object",
        "properties": {
            "account": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "tier": {"type": "string", "enum": ["basic", "plus"]},
                },
                "required": ["id"],
            },
            "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        },
        "required": ["account"],
    },
    mutates_state=True,
)


def _open(account: dict, tags: dict | None = None) -> AgentProfile:
    properties = {"account": account, **({"tags": tags} if tags else {})}
    return _agent(_tool("open_case", properties, ["account"]))


def _account(**properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": ["id"]}


def test_the_same_nested_schema_calls_the_environment_directly():
    agent = _open(
        _account(id={"type": "string", "description": "The account's id"}),
        {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    )
    assert select_mode(agent, [OPEN]) is BindingMode.CANONICAL


@pytest.mark.parametrize(
    "account, tags, said",
    [
        # The reviewer's case: the same argument names, another type one level down.
        (_account(id={"type": "integer"}), None, "account.id is integer"),
        (
            {"type": "object", "properties": {"id": {"type": "string"}}},
            None,
            "account.id is required but the agent may leave it out",
        ),
        (
            _account(id={"type": "string"}, tier={"type": "string"}),
            None,
            "account.tier may carry values",
        ),
        (
            _account(id={"type": "string"}, region={"type": "string"}),
            None,
            "account.region is not an environment argument",
        ),
        (
            _account(id={"type": "string"}),
            {"type": "array", "items": {"type": "integer"}, "maxItems": 3},
            "tags[] is integer",
        ),
        # A rule this does not read (a bound) must be the agent's too, or it is a mismatch.
        (
            _account(id={"type": "string"}),
            {"type": "array", "items": {"type": "string"}},
            "the environment sets maxItems",
        ),
    ],
    ids=[
        "nested-type",
        "nested-required",
        "nested-enum",
        "nested-unknown",
        "item-type",
        "unmatched-rule",
    ],
)
def test_a_nested_schema_the_environment_would_refuse_needs_a_routed_binding(
    account, tags, said
):
    agent = _open(account, tags)
    problems = argument_mismatches(agent.agent_tools[0]["parameters"], OPEN.parameters)
    assert any(said in one for one in problems), problems
    assert select_mode(agent, [OPEN]) is BindingMode.TOOL_ROUTED


def test_an_operation_the_environments_schema_refuses_runs_nothing():
    """Whatever chose the mode, the environment's whole schema is held on every call before its
    handler runs: here a canonical call with the nested id as a number."""
    ran = []

    def run(tool, arguments):
        ran.append((tool, arguments))
        return {"case": "C-1"}

    refused = run_bound_call(
        None,
        "open_case",
        {"account": {"id": 42}},
        run_operation=run,
        environment_tools=[OPEN],
    )
    assert refused.status == "invalid" and "account.id" in refused.error
    assert ran == []
    taken = run_bound_call(
        None,
        "open_case",
        {"account": {"id": "A-42"}, "tags": None},
        run_operation=run,
        environment_tools=[OPEN],
    )
    assert taken.status == "ok"
    assert ran == [("open_case", {"account": {"id": "A-42"}})]
