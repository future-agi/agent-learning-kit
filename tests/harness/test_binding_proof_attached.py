"""A binding proof skips and perturbs a call routed onto an attached server's write tool.

An attached tool changes state unless its server marks it read-only (`world_tools`), so a mapping
onto one is held to the same adversarial variants as a mapping onto the environment's own tools:
the customer's argument, not a constant in the binding, has to decide what is written.

The world here is in memory: `prove_binding` is what is under test, and it reaches the world only
through `PostgresProof.attempt`, which is given the real `run_bound_call`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fi.alk.harness import binding_proof, folder
from fi.alk.harness.binding_proof import prove_binding
from fi.alk.harness.bindings import run_bound_call
from fi.alk.harness.releases import AgentBindingV1, Attempt, ToolDefinition

SHA = "sha256:" + "ab" * 32
SUITE_SHA = "sha256:" + "cd" * 32
INSERT = ToolDefinition(
    name="sql__insert",
    description="Insert one ticket.",
    parameters={
        "type": "object",
        "properties": {
            "ticket_id": {"type": "string"},
            "kind": {"type": "string", "enum": ["bug", "task"]},
            "on": {"type": "string"},
            "note": {"type": "string"},
        },
        "required": ["ticket_id"],
        "additionalProperties": False,
    },
    # What `world_tools` gives a tool whose server does not mark it read-only.
    mutates_state=True,
)
SAVE = {
    "name": "save_ticket",
    "description": "Save the ticket the customer named.",
    "parameters": {
        "type": "object",
        "properties": {"ticket": {"type": "string"}},
        "required": ["ticket"],
    },
    "admission": "routed",
}
SCENARIO = SimpleNamespace(
    solution=[SimpleNamespace(tool="sql__insert", arguments={"ticket_id": "T-1"})],
    alternatives=[],
)


def _binding(
    target: dict,
    fixtures: dict | None = None,
    *,
    properties: dict | None = None,
    arguments: dict | None = None,
    aliases: dict | None = None,
) -> AgentBindingV1:
    """`save_ticket` onto `sql__insert`, `ticket_id` from `target`; `properties` and `arguments`
    are further ones of the agent's tool and of the environment's call."""
    parameters = SAVE["parameters"]
    return AgentBindingV1.model_validate(
        {
            "schema_version": "futureagi.agent-binding.v1",
            "digest": SHA,
            "agent": {
                "agent_definition_id": "d",
                "agent_version_id": "v",
                "configuration_digest": SHA,
                "connector": "mcp_client",
                "agent_config_digest": SHA,
                "provider_target_id": "t",
                "provider_digest": SHA,
            },
            "environment": {"digest": SHA},
            "mode": "tool_routed",
            "agent_tools": [
                {
                    **SAVE,
                    "parameters": {
                        **parameters,
                        "properties": {
                            **parameters["properties"],
                            **(properties or {}),
                        },
                    },
                }
            ],
            "tools": [
                {
                    "agent_tool": "save_ticket",
                    "steps": [
                        {
                            "id": "insert",
                            "tool": "sql__insert",
                            "arguments": {"ticket_id": target, **(arguments or {})},
                        }
                    ],
                }
            ],
            "fixtures": fixtures or {},
            "aliases": aliases or {},
        }
    )


class _World:
    """The release has no tool of its own; its attached server offers the write."""

    # What the scenario's check reads of the one insert it wants.
    checked: dict = {"ticket_id": "T-1"}  # noqa: RUF012 - a test sets it

    def __init__(self, *_arguments: object) -> None:
        self.environment_tools: list[ToolDefinition] = []

    def __enter__(self) -> _World:  # noqa: PYI034 - 3.10 has no typing.Self
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def attached_definitions(self) -> list[ToolDefinition]:
        return [INSERT]

    def attempt(self, _scenario, calls, binding) -> Attempt:
        inserted: list[dict] = []

        def insert(_tool: str, arguments: dict) -> dict:
            inserted.append({name: arguments.get(name) for name in self.checked})
            return {"inserted": arguments["ticket_id"]}

        outcomes = [
            run_bound_call(
                binding,
                tool,
                arguments,
                run_operation=insert,
                environment_tools=[INSERT],
            )
            for tool, arguments in calls
        ]
        saved = SimpleNamespace(
            held=inserted == [self.checked], said=f"inserted {inserted}"
        )
        return Attempt(
            ready=SimpleNamespace(ok=True, said=""),
            faults=[],
            checks=[("ticket_saved", saved)],
            outcomes=outcomes,
            played=[],
        )


@pytest.fixture
def world(monkeypatch):
    release = SimpleNamespace(digest=SHA)
    suite = SimpleNamespace(
        digest=SUITE_SHA,
        environment=SimpleNamespace(digest=SHA),
        scenarios=[SimpleNamespace(key="save", path="scenarios/save")],
        policy_profile=None,
    )
    monkeypatch.setattr(
        binding_proof, "load_environment_release", lambda _root: release
    )
    monkeypatch.setattr(binding_proof, "load_suite_release", lambda _root: suite)
    monkeypatch.setattr(binding_proof, "_check_digest", lambda *_: None)
    monkeypatch.setattr(binding_proof, "PostgresProof", _World)
    monkeypatch.setattr(folder, "read_folder", lambda *_: SCENARIO)


def _prove(binding: AgentBindingV1, **arguments: object):
    call = {"tool": "save_ticket", "arguments": {"ticket": "T-1", **arguments}}
    return prove_binding(
        "environment", "suite", binding, {"save": [call]}, postgres_dsn=""
    )


CONSTANT = ({"fixture": "ticket"}, {"ticket": "T-1"})


def test_the_customers_argument_decides_what_an_attached_write_tool_writes(world):
    proof = _prove(_binding({"arg": "ticket"}))
    verdict = proof.scenarios["save"]
    assert verdict.status == "proved", verdict
    # Left out, nothing is saved; garbled, another ticket is: neither reaches the expected world.
    assert verdict.adversarial == {
        "nothing": "failed",
        "skip:1": "failed",
        "perturb:1": "failed",
    }
    assert proof.passed


def test_a_mapping_that_fixes_the_attached_tools_target_proves_nothing(world):
    """The binding writes ticket T-1 whatever the customer's agent asked for, so the fixture is
    solved by the binding and the scenario measures nothing about the agent."""
    proof = _prove(_binding({"fixture": "ticket"}, {"ticket": "T-1"}))
    verdict = proof.scenarios["save"]
    assert verdict.adversarial["perturb:1"] == "held"
    assert verdict.status == "failed" and "perturb:1" in verdict.reason
    assert not proof.passed


# Arguments most other values are invalid for. The call with nonsense in one of them is refused
# before anything runs, whatever the binding does with the ticket, so it shows nothing.
CONSTRAINED = {
    "enum": ({"type": "string", "enum": ["low", "high"]}, "low"),
    "one-value": ({"type": "string", "enum": ["low"]}, "low"),
    "const": ({"const": 1}, 1),
    "maximum": ({"type": "integer", "maximum": 5}, 1),
    "range": ({"type": "number", "minimum": 0, "exclusiveMaximum": 0.5}, 0.25),
    "multiple": ({"type": "integer", "multipleOf": 30, "maximum": 120}, 120),
    "pattern": ({"type": "string", "pattern": "^\\d{4}-"}, "2026-01-01"),
    "length": ({"type": "string", "minLength": 3, "maxLength": 3}, "abc"),
    "format": ({"type": "string", "format": "date"}, "2026-12-31"),
    "union": ({"anyOf": [{"enum": ["a"]}, {"enum": ["b"]}]}, "a"),
    "nested": (
        {
            "type": "object",
            "properties": {"tags": {"type": "array", "items": {"enum": ["x", "y"]}}},
        },
        {"tags": ["x", "x"]},
    ),
}


@pytest.mark.parametrize("name", CONSTRAINED)
def test_a_constant_target_is_not_hidden_by_an_argument_few_values_fit(world, name):
    schema, value = CONSTRAINED[name]
    proof = _prove(_binding(*CONSTANT, properties={"extra": schema}), extra=value)
    verdict = proof.scenarios["save"]
    assert verdict.adversarial["perturb:1"] == "held", verdict
    assert verdict.status == "failed" and not proof.passed


@pytest.mark.parametrize("name", CONSTRAINED)
def test_an_argument_the_checks_do_not_read_fails_no_binding_that_passes_the_target(
    world, name
):
    schema, value = CONSTRAINED[name]
    proof = _prove(
        _binding({"arg": "ticket"}, properties={"extra": schema}), extra=value
    )
    assert proof.scenarios["save"].adversarial["perturb:1"] == "failed"
    assert proof.passed


@pytest.mark.parametrize(
    "schema, sent, argument",
    [
        # A conversion and an alias table take few values too, and refuse the rest the same way.
        ({"type": "string"}, "12/31/2026", {"arg": "extra", "convert": "date"}),
        ({"type": "string"}, "first", {"arg": "extra", "alias": "days"}),
        (
            {"type": "string"},
            "first",
            {"arg": "extra", "map": {"first": "1", "x": "2"}},
        ),
        ({"type": "string"}, "bug", None),
    ],
    ids=["convert", "alias", "map", "the environment's enum"],
)
def test_a_constant_target_is_not_hidden_by_a_value_the_mapping_or_the_environment_refuses(
    world, schema, sent, argument
):
    arguments = {"on": argument} if argument else {"kind": {"arg": "extra"}}
    aliases = {"days": {"first": "2026-01-01", "second": "2026-01-02"}}
    shape = {
        "properties": {"extra": schema},
        "arguments": arguments,
        "aliases": aliases,
    }
    proof = _prove(_binding(*CONSTANT, **shape), extra=sent)
    assert proof.scenarios["save"].adversarial["perturb:1"] == "held"
    assert not proof.passed
    proof = _prove(_binding({"arg": "ticket"}, **shape), extra=sent)
    assert proof.passed, proof


def test_a_constant_target_is_not_hidden_by_another_argument_that_matters(
    world, monkeypatch
):
    """The kind is the customer's and the checks read it, so a call with everything changed fails;
    the ticket the agent names is ignored all the same."""
    monkeypatch.setattr(_World, "checked", {"ticket_id": "T-1", "kind": "bug"})
    shape = {
        "properties": {"extra": {"type": "string", "enum": ["bug", "task"]}},
        "arguments": {"kind": {"arg": "extra"}},
    }
    verdict = _prove(_binding(*CONSTANT, **shape), extra="bug").scenarios["save"]
    assert verdict.adversarial["perturb:1"] == "failed"
    assert verdict.adversarial["perturb:1:ticket"] == "held"
    assert verdict.status == "failed"
    verdict = _prove(_binding({"arg": "ticket"}, **shape), extra="bug").scenarios[
        "save"
    ]
    assert verdict.status == "proved" and "perturb:1:ticket" not in verdict.adversarial


def test_an_argument_with_one_value_is_not_one_the_binding_settled(world):
    """A provider's transfer to its one destination: the agent could send nothing else, so that
    the target is the same whatever it writes in the note is not the binding's doing."""
    proof = _prove(
        _binding(
            {"arg": "ticket"},
            properties={
                "ticket": {"type": "string", "enum": ["T-1"]},
                "note": {"type": "string"},
            },
            arguments={"note": {"arg": "note"}},
        ),
        note="Wants a person.",
    )
    verdict = proof.scenarios["save"]
    assert verdict.adversarial["perturb:1"] == "exempt", verdict
    assert verdict.status == "proved" and proof.passed


def test_a_constant_target_read_off_an_argument_with_one_value_is_still_the_bindings(
    world,
):
    proof = _prove(
        _binding(
            {"arg": "extra", "map": {"low": "T-1"}},
            properties={"extra": {"type": "string", "enum": ["low"]}},
        ),
        extra="low",
    )
    assert proof.scenarios["save"].adversarial["perturb:1:ticket"] == "held"
    assert not proof.passed


def test_two_names_for_one_target_are_a_constant(world):
    """Every name the agent may use comes to the same ticket: the binding chose it."""
    shape = {"aliases": {"tickets": {"T-1": "T-1", "T-2": "T-1"}}}
    target = {"arg": "ticket", "alias": "tickets"}
    proof = _prove(_binding(target, **shape))
    assert proof.scenarios["save"].adversarial["perturb:1"] == "held"
    shape["aliases"]["tickets"]["T-3"] = "T-3"
    assert _prove(_binding(target, **shape)).passed
