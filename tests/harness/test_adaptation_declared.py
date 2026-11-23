"""A customer agent that is an MCP client is declared, not inspected; the harness derives its
trajectories; the world's MCP endpoint serves its own tool names; a `both` goal is judged too."""

from __future__ import annotations

import pytest

from fi.alk.harness.adaptation.declared import (
    MCP_CLIENT,
    declared_digest,
    declared_profile,
)
from fi.alk.harness.adaptation.errors import AdaptationFailure
from fi.alk.harness.adaptation.profile import (
    profile_from_file,
    profile_from_job,
    select_mode,
)
from fi.alk.harness.adaptation.workbench import (
    parse_binding_draft,
    profile_from_document,
)
from fi.alk.harness.binding_trajectories import derive_trajectories
from fi.alk.harness.catalogue import Catalogue, SubGoal
from fi.alk.harness.releases import AgentBindingV1, BindingMode, ToolDefinition
from fi.alk.harness.run.grade import _claims
from fi.alk.harness.scenario import Scenario, Step
from fi.alk.harness.world_tools import WorldTools

SHA = "sha256:" + "ab" * 32
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
VERIFY = ToolDefinition(
    name="verify_patient",
    description="Find the patient.",
    parameters={
        "type": "object",
        "properties": {"full_name": {"type": "string"}, "dob": {"type": "string"}},
        "required": ["full_name", "dob"],
    },
    mutates_state=False,
)
ESCALATE = ToolDefinition(
    name="escalate_to_staff",
    description="Hand off.",
    parameters={"type": "object", "properties": {"reason": {"type": "string"}}},
    mutates_state=True,
)


def _declared(*tools: dict) -> dict:
    return {
        "provider": "declared",
        "name": "Northside front desk",
        "instructions": "Confirm before any change.",
        "tools": list(tools),
    }


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


# --- declared agents --------------------------------------------------------------------------


def test_a_declared_agent_is_adapted_like_an_inspected_one():
    body = _declared(
        _tool(
            "schedule_visit",
            {"patient_number": {"type": "string"}, "opening": {"type": "string"}},
            ["patient_number", "opening"],
        )
    )
    profile = profile_from_file(body)
    assert profile.connector == MCP_CLIENT
    assert profile.provider is None
    assert [one.action for one in profile.admission] == ["routed"]
    assert [one["name"] for one in profile.agent_tools] == ["schedule_visit"]
    # The declaration is the agent's identity: a run checks what it was given against this.
    assert profile.agent.provider_digest == declared_digest(
        {k: v for k, v in body.items() if k != "provider"}
    )
    assert select_mode(profile, [BOOK, SEND]) is BindingMode.TOOL_ROUTED


def test_a_declared_agent_with_the_environments_own_tools_is_canonical():
    profile = profile_from_file(
        _declared(
            _tool(
                "book_appointment",
                BOOK.parameters["properties"],
                BOOK.parameters["required"],
            )
        )
    )
    assert select_mode(profile, [BOOK, SEND]) is BindingMode.CANONICAL


def test_a_changed_declaration_is_another_agent():
    body = _declared(
        _tool("schedule_visit", {"opening": {"type": "string"}}, ["opening"])
    )
    changed = {**body, "instructions": body["instructions"] + " Never confirm."}
    assert (
        profile_from_file(body).agent.provider_digest
        != profile_from_file(changed).agent.provider_digest
    )


def _job(config: dict):
    from fi.alk.harness.job import HarnessJob

    return HarnessJob(
        job_id="job-adapt",
        run_id="run-adapt",
        execution="hosted",
        runtime={"isolation": "dedicated_vm"},
        source={"kind": "archive", "archive_artifact_id": "source-id"},
        agent={"connector": MCP_CLIENT, "config": config},
        metadata={
            "adaptation": {
                "agent": {
                    "agent_definition_id": "d",
                    "agent_version_id": "v",
                    "configuration_digest": SHA,
                    "agent_config_digest": SHA,
                    "connector": MCP_CLIENT,
                }
            }
        },
    )


def test_an_explicit_agent_id_survives_every_normalization():
    """The customer's own id is the agent's id in the profile, the binding and compose's check,
    however many times the declaration is normalized; the name's slug is only the fallback."""
    config = {k: v for k, v in _declared(SCHEDULE).items() if k != "provider"}
    config.update(name="CardGuard", agent_id="bank-support-42")
    profile = profile_from_job(_job(config), credentials={})
    assert profile.agent.provider_target_id == "bank-support-42"
    # What compose computes from the run's job is what adaptation sealed.
    assert profile.agent.provider_digest == declared_digest(config)
    again = profile_from_document(profile.document())
    assert again.agent == profile.agent
    assert declared_profile(profile.target) == profile.target
    anonymous = profile_from_job(
        _job({k: v for k, v in config.items() if k != "agent_id"}), credentials={}
    )
    assert anonymous.agent.provider_target_id == "cardguard"


@pytest.mark.parametrize(
    "body, code",
    [
        (
            _declared(_tool("x", {}, []), _tool("x", {}, [])),
            "adaptation_profile_invalid",
        ),
        ({**_declared(), "name": ""}, "adaptation_profile_invalid"),
        ({**_declared(), "provider": "livekit"}, "adaptation_profile_invalid"),
        (
            {**_declared(), "tools": [{"name": "x", "parameters": {"type": "string"}}]},
            "adaptation_profile_invalid",
        ),
    ],
    ids=["repeated-tool", "no-name", "unknown-provider", "not-an-object-schema"],
)
def test_a_declaration_that_is_not_one_is_refused(body, code):
    with pytest.raises(AdaptationFailure) as caught:
        profile_from_file(body)
    assert caught.value.code == code


# --- derived trajectories ---------------------------------------------------------------------


def _binding(
    *entries: dict,
    agent_tools: list[dict],
    aliases: dict | None = None,
    fixtures: dict | None = None,
) -> AgentBindingV1:
    return AgentBindingV1.model_validate(
        {
            "schema_version": "futureagi.agent-binding.v1",
            "digest": SHA,
            "agent": {
                "agent_definition_id": "d",
                "agent_version_id": "v",
                "configuration_digest": SHA,
                "connector": MCP_CLIENT,
                "agent_config_digest": SHA,
                "provider_target_id": "t",
                "provider_digest": SHA,
            },
            "environment": {"digest": SHA},
            "mode": "tool_routed",
            "agent_tools": [{**tool, "admission": "routed"} for tool in agent_tools],
            "tools": list(entries),
            "fixtures": fixtures or {},
            "aliases": aliases or {},
        }
    )


SCHEDULE = _tool(
    "schedule_visit",
    {
        "patient_number": {"type": "string"},
        "opening": {"type": "string"},
        "reason_for_visit": {"type": "string"},
    },
    ["patient_number", "opening"],
)
PAPERWORK = _tool(
    "send_paperwork",
    {
        "patient_number": {"type": "string"},
        "delivery": {"type": "string", "enum": ["text", "email"]},
    },
    ["patient_number", "delivery"],
)
LOOKUP = _tool(
    "lookup_patient",
    {
        "first_name": {"type": "string"},
        "last_name": {"type": "string"},
        "birth_date": {"type": "string"},
    },
    ["first_name", "last_name", "birth_date"],
)
CLINIC_BINDING = _binding(
    {
        "agent_tool": "schedule_visit",
        "steps": [
            {
                "id": "book",
                "tool": "book_appointment",
                "arguments": {
                    "patient_id": {"arg": "patient_number"},
                    "slot_id": {"arg": "opening"},
                    "reason": {"arg": "reason_for_visit"},
                },
            }
        ],
    },
    {
        "agent_tool": "send_paperwork",
        "steps": [
            {
                "id": "send",
                "tool": "send_intake_forms",
                "arguments": {
                    "patient_id": {"arg": "patient_number"},
                    "channel": {
                        "arg": "delivery",
                        "map": {"text": "sms", "email": "email"},
                    },
                },
            }
        ],
    },
    {
        "agent_tool": "lookup_patient",
        "steps": [
            {
                "id": "verify",
                "tool": "verify_patient",
                "arguments": {
                    "full_name": {
                        "concat": [{"arg": "first_name"}, {"arg": "last_name"}],
                        "separator": " ",
                    },
                    "dob": {"arg": "birth_date", "convert": "date"},
                },
            }
        ],
    },
    agent_tools=[SCHEDULE, PAPERWORK, LOOKUP],
)
TOOLS = [BOOK, SEND, VERIFY, ESCALATE]


def test_a_rename_is_inverted_into_the_agents_call():
    derived = derive_trajectories(
        CLINIC_BINDING,
        [
            (
                "book",
                [
                    Step(
                        tool="book_appointment",
                        arguments={
                            "patient_id": "P-1001",
                            "slot_id": "S-104",
                            "reason": "check-up",
                        },
                    )
                ],
            )
        ],
        TOOLS,
    )
    assert derived.trajectories == {
        "book": [
            {
                "tool": "schedule_visit",
                "arguments": {
                    "patient_number": "P-1001",
                    "opening": "S-104",
                    "reason_for_visit": "check-up",
                },
            }
        ]
    }
    assert not derived.undecided and not derived.unreached


def test_a_value_map_is_inverted_through_its_unique_key():
    derived = derive_trajectories(
        CLINIC_BINDING,
        [
            (
                "forms",
                [
                    Step(
                        tool="send_intake_forms",
                        arguments={"patient_id": "P-1003", "channel": "sms"},
                    )
                ],
            )
        ],
        TOOLS,
    )
    assert derived.trajectories["forms"][0]["arguments"] == {
        "patient_number": "P-1003",
        "delivery": "text",
    }


def test_a_joined_value_is_left_to_the_builder_with_the_reason():
    derived = derive_trajectories(
        CLINIC_BINDING,
        [
            (
                "verify",
                [
                    Step(
                        tool="verify_patient",
                        arguments={"full_name": "Priya Raman", "dob": "1990-03-11"},
                    )
                ],
            )
        ],
        TOOLS,
    )
    assert "verify" not in derived.trajectories
    assert (
        "concat" in derived.undecided["verify"]
        and "no inverse" in derived.undecided["verify"]
    )


def test_a_tool_no_entry_reaches_needs_no_trajectory():
    derived = derive_trajectories(
        CLINIC_BINDING,
        [
            (
                "late",
                [
                    Step(
                        tool="verify_patient", arguments={"full_name": "x", "dob": "y"}
                    ),
                    Step(tool="escalate_to_staff", arguments={"reason": "late"}),
                ],
            )
        ],
        TOOLS,
    )
    assert derived.unreached == {"late": "escalate_to_staff"}
    assert not derived.undecided


def test_an_argument_the_entry_never_sends_is_not_dropped_silently():
    binding = _binding(
        {
            "agent_tool": "schedule_visit",
            "steps": [
                {
                    "id": "book",
                    "tool": "book_appointment",
                    "arguments": {
                        "patient_id": {"arg": "patient_number"},
                        "slot_id": {"arg": "opening"},
                    },
                }
            ],
        },
        agent_tools=[SCHEDULE],
    )
    derived = derive_trajectories(
        binding,
        [
            (
                "book",
                [
                    Step(
                        tool="book_appointment",
                        arguments={
                            "patient_id": "P-1001",
                            "slot_id": "S-104",
                            "reason": "check-up",
                        },
                    )
                ],
            )
        ],
        TOOLS,
    )
    assert "never sends book_appointment.reason" in derived.undecided["book"]


def test_a_fixed_value_the_reference_also_passes_needs_no_agent_argument():
    binding = _binding(
        {
            "agent_tool": "schedule_visit",
            "steps": [
                {
                    "id": "book",
                    "tool": "book_appointment",
                    "arguments": {
                        "patient_id": {"arg": "patient_number"},
                        "slot_id": {"arg": "opening"},
                        "reason": {"fixture": "reason"},
                    },
                }
            ],
        },
        agent_tools=[SCHEDULE],
        fixtures={"reason": "check-up"},
    )
    steps = [
        Step(
            tool="book_appointment",
            arguments={
                "patient_id": "P-1001",
                "slot_id": "S-104",
                "reason": "check-up",
            },
        )
    ]
    assert derive_trajectories(binding, [("book", steps)], TOOLS).trajectories["book"][
        0
    ]["arguments"] == {"patient_number": "P-1001", "opening": "S-104"}
    other = [
        Step(
            tool="book_appointment",
            arguments={
                "patient_id": "P-1001",
                "slot_id": "S-104",
                "reason": "follow-up",
            },
        )
    ]
    assert (
        "fixes book_appointment.reason"
        in derive_trajectories(binding, [("book", other)], TOOLS).undecided["book"]
    )


def test_a_draft_may_leave_the_trajectories_to_the_harness():
    draft = parse_binding_draft({"binding": {"tools": []}})
    assert draft.trajectories == {}
    with pytest.raises(TypeError):
        parse_binding_draft({"binding": {"tools": []}, "trajectories": []})


def test_the_submit_binding_tool_may_leave_the_trajectories_out():
    """The tool's schema and the workbench agree: a call with only the binding is valid for the
    model to make, and reaches the workbench with no trajectories, for the harness to derive."""
    import asyncio

    from jsonschema import Draft202012Validator

    from fi.alk.harness.adaptation.workbench import binding_server

    received = []

    class Bench:
        def submit_binding(self, body):
            received.append(body)
            return True, "accepted"

    async def approve(_question, _options):
        return "yes"

    tool = next(
        one
        for one in binding_server(Bench(), approve).tools
        if one.name == "submit_binding"
    )
    args = {"binding": '{"tools": []}'}
    Draft202012Validator(tool.input_schema).validate(args)
    answer = asyncio.run(tool.handler(args))
    assert not answer.get("is_error"), answer
    assert received[0]["trajectories"] is None
    assert parse_binding_draft(received[0]).trajectories == {}


# --- the world's MCP endpoint -----------------------------------------------------------------


def test_the_world_lists_the_agents_own_tools_under_a_routed_binding():
    routed = WorldTools(
        {}, [BOOK, SEND], "postgresql://nowhere/world", binding=CLINIC_BINDING
    )
    assert [tool.name for tool in routed.tools()] == [
        "schedule_visit",
        "send_paperwork",
        "lookup_patient",
    ]
    canonical = WorldTools({}, [BOOK, SEND], "postgresql://nowhere/world")
    assert [tool.name for tool in canonical.tools()] == [
        "book_appointment",
        "send_intake_forms",
    ]


# --- grading both halves ----------------------------------------------------------------------


def test_a_both_goal_is_judged_as_well_as_checked():
    catalogue = Catalogue(
        sub_goals=[
            SubGoal(
                name="booked",
                what="the slot is booked",
                check="def check(world, calls):\n    return None",
                verifier="code",
            ),
            SubGoal(
                name="confirmed",
                what="confirmed first",
                check="def check(world, calls):\n    return None",
                judged="Pass: the agent read the slot back and got a yes.",
                verifier="both",
            ),
            SubGoal(
                name="polite",
                what="polite",
                judged="Pass: the agent was polite.",
                verifier="llm",
            ),
        ]
    )
    scenario = Scenario(
        name="book",
        instruction="Book me in.",
        sub_goals=["booked", "confirmed", "polite"],
    )
    assert [name for _criteria, name in _claims(scenario, catalogue)] == [
        "confirmed",
        "polite",
    ]
