"""Which binding mode an agent gets: canonical only when its tools are the environment's, each
called as-is."""

from __future__ import annotations

import copy
import random

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import _WrappedReferencingError

from fi.alk.harness.adaptation.profile import (
    AgentProfile,
    argument_mismatches,
    identity_mapping,
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
        _account(
            id={"type": "string", "description": "The account's id"},
            tier={"type": "string", "enum": ["basic", "plus"]},
        ),
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


# The environment forbids properties it does not name, at the top level and one level down.
CLOSED = ToolDefinition(
    name="close_case",
    description="Close a case.",
    parameters={
        "type": "object",
        "properties": {
            "case": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
                "additionalProperties": False,
            }
        },
        "required": ["case"],
        "additionalProperties": False,
    },
    mutates_state=True,
)


def _close(*, top: bool, nested: bool) -> AgentProfile:
    case = {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
        **({"additionalProperties": False} if nested else {}),
    }
    tool = _tool("close_case", {"case": case}, ["case"])
    if top:
        tool["parameters"]["additionalProperties"] = False
    return _agent(tool)


def test_an_agent_that_forbids_what_the_environment_forbids_is_canonical():
    assert select_mode(_close(top=True, nested=True), [CLOSED]) is BindingMode.CANONICAL


@pytest.mark.parametrize(
    "top, nested, where",
    [(False, True, "arguments"), (True, False, "case")],
    ids=["top-level", "nested"],
)
def test_an_agent_schema_that_allows_properties_the_environment_forbids_is_routed(
    top, nested, where
):
    """The reviewer's case: the agent's schema says nothing of other properties, so it allows
    them, and a call carrying one is refused by the environment at run time."""
    agent = _close(top=top, nested=nested)
    problems = argument_mismatches(
        agent.agent_tools[0]["parameters"], CLOSED.parameters
    )
    assert problems == [
        f"{where} may carry properties the environment forbids "
        "(additionalProperties is false there, not here)"
    ]
    assert select_mode(agent, [CLOSED]) is BindingMode.TOOL_ROUTED


def test_the_compatibility_answer_and_the_run_agree_on_an_extra_property():
    """What the wider schema would let through is what the environment refuses."""
    refused = run_bound_call(
        None,
        "close_case",
        {"case": {"id": "a"}, "note": "extra"},
        run_operation=lambda tool, arguments: {"closed": True},
        environment_tools=[CLOSED],
    )
    assert refused.status == "invalid" and "note" in refused.error


# --- an agent's schema that accepts more than the environment's -----------------------------------

TEXT = {"type": "string"}
COUNT = {"type": "integer"}


def _object(**keywords: object) -> dict:
    return {"type": "object", **keywords}


def _list(**keywords: object) -> dict:
    return _object(properties={"list": {"type": "array", **keywords}})


# Each with a call the agent's schema accepts and the environment's refuses.
WIDER = {
    "an environment argument left open": (
        _object(properties={"id": TEXT}),
        _object(properties={"id": TEXT, "note": COUNT}),
        {"note": "x"},
    ),
    "no properties at all": (_object(), _object(properties={"id": TEXT}), {"id": 5}),
    "additional properties of a type": (
        _object(properties={"id": TEXT}, additionalProperties=TEXT),
        _object(properties={"id": TEXT, "note": COUNT}),
        {"note": "x"},
    ),
    "an environment argument left open, nested": (
        _object(properties={"case": _object()}),
        _object(properties={"case": _object(properties={"id": TEXT})}),
        {"case": {"id": 1}},
    ),
    "the same additional properties, one side naming one": (
        _object(additionalProperties=COUNT),
        _object(properties={"a": TEXT}, additionalProperties=COUNT),
        {"a": 1},
    ),
    "the same additional properties, the agent naming one": (
        _object(properties={"a": COUNT}, additionalProperties=TEXT),
        _object(additionalProperties=TEXT),
        {"a": 1},
    ),
    "pattern properties": (
        _object(additionalProperties=False, patternProperties={"^x": {}}),
        _object(additionalProperties=False),
        {"x1": 1},
    ),
    "a property the environment never takes": (
        _object(properties={"x": TEXT}),
        _object(properties={"x": False}),
        {"x": "a"},
    ),
    "items the environment never takes": (_list(), _list(items=False), {"list": [1]}),
    "nothing the environment takes": (_object(), False, {"a": 1}),
    "prefix items": (
        _list(items=TEXT, prefixItems=[COUNT]),
        _list(items=TEXT),
        {"list": [1]},
    ),
    "contains none": (
        _list(contains=TEXT, minContains=0),
        _list(contains=TEXT),
        {"list": [1]},
    ),
    "unevaluated properties": (
        _object(properties={"a": COUNT}, unevaluatedProperties=False),
        _object(unevaluatedProperties=False),
        {"a": 1},
    ),
    "unevaluated items": (
        _list(items=TEXT, unevaluatedItems=False),
        _list(unevaluatedItems=False),
        {"list": ["a"]},
    ),
    "true for one": (
        _object(properties={"a": {"enum": [True]}}),
        _object(properties={"a": {"enum": [1]}}),
        {"a": True},
    ),
    "zero for false": (
        _object(properties={"a": {"const": 0}}),
        _object(properties={"a": {"const": False}}),
        {"a": 0},
    ),
    "the same reference to other definitions": (
        _object(properties={"a": {"$ref": "#/$defs/A"}}, **{"$defs": {"A": {}}}),
        _object(properties={"a": {"$ref": "#/$defs/A"}}, **{"$defs": {"A": TEXT}}),
        {"a": 1},
    ),
    "the same reference to another root": (
        _object(
            properties={
                "a": {
                    "$id": "http://agent/a",
                    "$ref": "#/$defs/A",
                    "$defs": {"A": {}},
                }
            },
            **{"$defs": {"A": TEXT}},
        ),
        _object(properties={"a": {"$ref": "#/$defs/A"}}, **{"$defs": {"A": TEXT}}),
        {"a": 1},
    ),
}


@pytest.mark.parametrize("name", WIDER)
def test_a_wider_agent_schema_is_a_mismatch(name):
    agent, environment, call = WIDER[name]
    assert Draft202012Validator(agent).is_valid(call)
    assert not Draft202012Validator(environment).is_valid(call)
    assert argument_mismatches(agent, environment)


@pytest.mark.parametrize(
    "agent, environment",
    [
        # Shared definitions, described in other words.
        (
            _object(
                properties={"a": {"$ref": "#/$defs/A", "description": "ours"}},
                **{"$defs": {"A": TEXT}},
            ),
            _object(properties={"a": {"$ref": "#/$defs/A"}}, **{"$defs": {"A": TEXT}}),
        ),
        # A provider's schema: the environment's properties, other names left open on both sides.
        (_object(properties={"id": TEXT}), _object(properties={"id": TEXT})),
        # A generated one: closed, its draft named, an optional environment argument left out.
        (
            _object(
                properties={"id": TEXT},
                additionalProperties=False,
                **{"$schema": "http://json-schema.org/draft-07/schema#"},
            ),
            _object(properties={"id": TEXT, "note": COUNT}),
        ),
        # An environment argument that takes anything may be left undeclared.
        (
            _object(properties={"id": TEXT}),
            _object(properties={"id": TEXT, "note": {"description": "Anything."}}),
        ),
        # A property called like an annotation is a property.
        (_object(properties={"title": TEXT}), _object(properties={"title": TEXT})),
        (
            _object(properties={"a": {"anyOf": [{**TEXT, "title": "A"}, COUNT]}}),
            _object(properties={"a": {"anyOf": [TEXT, COUNT]}}),
        ),
    ],
)
def test_a_schema_shown_compatible_is_no_mismatch(agent, environment):
    assert argument_mismatches(agent, environment) == []


def test_a_schema_that_cannot_be_used_is_a_mismatch():
    for agent in (
        _object(properties=[]),
        _object(required="id"),
        _object(properties={"id": {"type": "strng"}}),
        _object(properties={"id": {"$ref": "#/nope"}}),
        _object(properties={"id": {"$ref": "http://127.0.0.1:9/id"}}),
    ):
        (problem, *_rest) = argument_mismatches(agent, {"type": "object"})
        assert "cannot be used" in problem


def test_an_agent_with_some_tools_that_leaves_an_optional_argument_out_maps_onto_itself():
    """Its schema is open to other names, so a call of it is not always an environment call; the
    mapping passes on only the arguments it declares, and that always is one."""
    tool = _tool("open_case", {"account": OPEN.parameters["properties"]["account"]}, [])
    tool["parameters"]["required"] = ["account"]
    agent = _agent(tool)
    assert argument_mismatches(tool["parameters"], OPEN.parameters)
    assert select_mode(agent, [OPEN, BOOK]) is BindingMode.TOOL_ROUTED
    mapping = identity_mapping(agent, [OPEN, BOOK])
    assert mapping["tools"][0]["steps"][0]["arguments"] == {
        "account": {"arg": "account"}
    }
    # Nested, nothing is dropped on the way, so there the open schema still needs a mapping.
    nested = _agent(_tool("open_case", {"account": _account(id=TEXT)}, ["account"]))
    assert identity_mapping(nested, [OPEN, BOOK]) is None


# --- the same, over schemas nobody thought of -----------------------------------------------------

_NAMES = ("a", "b", "x1")
_SCALARS = (0, 1, 1.0, 2, 1.5, True, False, None, "", "a", "bb", "ccc")


def _schema(rng: random.Random, depth: int = 0) -> object:
    if rng.random() < 0.12:
        return rng.choice([True, False, {}])
    kinds = ["string", "integer", "number", "boolean", "null", "object", "array"]
    schema: dict = {}
    if rng.random() < 0.8:
        kind = rng.choice(kinds[-2:] if depth == 0 else kinds)
        schema["type"] = [kind, "null"] if rng.random() < 0.15 else kind

    def inner() -> object:
        return _schema(rng, depth + 1) if depth < 2 else {"type": rng.choice(kinds[:5])}

    def some() -> dict:
        return {name: inner() for name in _NAMES if rng.random() < 0.5}

    rules = {
        "enum": lambda: rng.sample(_SCALARS, 3),
        "const": lambda: rng.choice(_SCALARS),
        "minimum": lambda: rng.choice([0, 1, 2]),
        "maxLength": lambda: rng.choice([0, 1, 2]),
        "pattern": lambda: rng.choice(["^a", "b"]),
        "maxItems": lambda: rng.choice([0, 1, 2]),
        "not": inner,
        "anyOf": lambda: [inner(), inner()],
        "if": inner,
        "then": inner,
        "contains": inner,
        "minContains": lambda: rng.choice([0, 1]),
        "prefixItems": lambda: [inner()],
        "propertyNames": lambda: {"maxLength": 1},
        "patternProperties": lambda: {"^x": inner()},
        "unevaluatedProperties": inner,
        "unevaluatedItems": inner,
        "minProperties": lambda: 1,
        "dependentRequired": lambda: {"a": ["b"]},
        "$ref": lambda: rng.choice(
            ["#", "#/$defs/A", "#/properties/a", "#/definitions/A"]
        ),
        "$id": lambda: rng.choice(["http://one/", "http://two/"]),
        "$defs": lambda: {"A": inner()},
        "nullable": lambda: True,
        "title": lambda: "words",
    }
    common = {
        "properties": some,
        "required": lambda: rng.sample(_NAMES, rng.randint(0, 2)),
        "additionalProperties": lambda: rng.choice([False, False, True, inner()]),
        "items": inner,
    }
    for pool, chance in ((common, 0.45), (rules, 0.04)):
        for keyword, value in pool.items():
            if rng.random() < chance:
                schema[keyword] = value()
    return schema


def _near(rng: random.Random, schema: object) -> object:
    """`schema` with a few rules taken away, added or changed, anywhere in it."""
    changed = copy.deepcopy(schema)
    for _ in range(rng.randint(0, 2)):
        places = [changed]
        for place in places:
            if isinstance(place, dict):
                places.extend(one for one in place.values() if isinstance(one, dict))
        place = rng.choice([one for one in places if isinstance(one, dict)] or [{}])
        if place and rng.random() < 0.5:
            del place[rng.choice(list(place))]
        else:
            other = _schema(rng, 1)
            if isinstance(other, dict) and other:
                keyword = rng.choice(list(other))
                place[keyword] = other[keyword]
    return changed


def _value(rng: random.Random, depth: int = 0) -> object:
    roll = rng.random()
    if depth < 3 and roll < (0.8 if depth == 0 else 0.3):
        return {name: _value(rng, depth + 1) for name in _NAMES if rng.random() < 0.5}
    if depth < 3 and roll < (0.95 if depth == 0 else 0.45):
        return [_value(rng, depth + 1) for _ in range(rng.randint(0, 2))]
    return rng.choice(_SCALARS)


def test_no_mismatch_means_no_agent_call_the_environment_refuses():
    """Pairs of schemas, mostly near each other, against random values: whenever the answer is
    "compatible", a value the agent's schema accepts is one the environment's accepts."""
    rng = random.Random(7951)
    values = [_value(rng) for _ in range(120)]
    compatible = agreed = 0
    for _ in range(1500):
        environment = _schema(rng)
        agent = _near(rng, environment) if rng.random() < 0.85 else _schema(rng)
        if not isinstance(agent, dict) or argument_mismatches(agent, environment):
            continue
        compatible += 1
        theirs = Draft202012Validator(environment)
        ours = Draft202012Validator(agent)
        for value in values:
            try:
                sent = ours.is_valid(value)
            except (RecursionError, _WrappedReferencingError):
                break
            if not sent:
                continue
            agreed += 1
            assert theirs.is_valid(value), (agent, environment, value)
    # Enough of them to mean something.
    assert compatible > 100 and agreed > 2000, (compatible, agreed)
