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
        "properties": {"ticket_id": {"type": "string"}},
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


def _binding(target: dict, fixtures: dict | None = None) -> AgentBindingV1:
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
            "agent_tools": [SAVE],
            "tools": [
                {
                    "agent_tool": "save_ticket",
                    "steps": [
                        {
                            "id": "insert",
                            "tool": "sql__insert",
                            "arguments": {"ticket_id": target},
                        }
                    ],
                }
            ],
            "fixtures": fixtures or {},
        }
    )


class _World:
    """The release has no tool of its own; its attached server offers the write."""

    def __init__(self, *_arguments: object) -> None:
        self.environment_tools: list[ToolDefinition] = []

    def __enter__(self) -> _World:  # noqa: PYI034 - 3.10 has no typing.Self
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def attached_definitions(self) -> list[ToolDefinition]:
        return [INSERT]

    def attempt(self, _scenario, calls, binding) -> Attempt:
        tickets: list[str] = []

        def insert(_tool: str, arguments: dict) -> dict:
            tickets.append(arguments["ticket_id"])
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
        saved = SimpleNamespace(held=tickets == ["T-1"], said=f"tickets are {tickets}")
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


def _prove(binding: AgentBindingV1):
    trajectory = {"save": [{"tool": "save_ticket", "arguments": {"ticket": "T-1"}}]}
    return prove_binding("environment", "suite", binding, trajectory, postgres_dsn="")


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
