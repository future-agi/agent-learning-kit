"""Which request envelope a submitted HTTP agent actually accepts.

The simulator can send two shapes. The contract could record only those two, so an agent
implementing neither had no way to say so: the submit_contract enum was `["fi.alk",
"openai_chat"]`, the understand stage picked one, and nothing objected until the agent rejected
the first turn of a real conversation with its own 422, wrapped as `chat_target_failed`. By then a
world had been built, scenarios written, and validation passed.

The stage was not guessing carelessly. It was handed a form with no correct box to tick.
"""

from __future__ import annotations

import pytest

from fi.alk.harness.contract import (
    AgentContract,
    Runtime,
    RuntimeInterface,
    ToolSpec,
    validate_contract,
)


def _contract(interface: RuntimeInterface | None) -> AgentContract:
    return AgentContract(
        agent="notes",
        tools=[ToolSpec(name="list_notes", args=["user"])],
        real_use_cases=["read a note"],
        runtime=Runtime(language="python", interface=interface),
    )


def _http(protocol: str) -> RuntimeInterface:
    return RuntimeInterface(kind="http", protocol=protocol, port=8080, path="/chat")


def test_an_agent_that_speaks_neither_envelope_can_say_so():
    """The truth has to be recordable before anything can act on it."""
    assert _http("custom").protocol == "custom"


def test_being_http_is_not_a_claim_about_the_body():
    """`http` says how the endpoint is reached and nothing about the shape of what is posted to
    it. It used to alias to fi.alk, which turned a transport fact into an unverified envelope
    claim by a shorter route."""
    assert _http("http").protocol == "custom"


def test_a_genuinely_unknown_protocol_is_still_refused():
    with pytest.raises(ValueError, match="runtime_http_protocol_unsupported"):
        _http("graphql")


def test_an_unsupported_envelope_is_refused_at_contract_time():
    """Not at the first turn. The run cannot succeed either way, and everything between the
    contract and the call is work that gets thrown away."""
    problems = validate_contract(_contract(_http("custom")))
    envelope = [one for one in problems if one.startswith("runtime.interface:")]
    assert envelope, problems
    said = envelope[0]
    # It names both shapes, because they are written down nowhere else a repo author would look.
    assert "new_message" in said and "choices[0].message" in said
    assert "model, messages" in said
    # And what to do about it.
    assert "add one to the repository" in said


@pytest.mark.parametrize("protocol", ["fi.alk", "openai_chat"])
def test_a_supported_envelope_passes(protocol):
    assert not [
        one
        for one in validate_contract(_contract(_http(protocol)))
        if one.startswith("runtime.interface:")
    ]


def test_an_agent_with_no_http_interface_is_unaffected():
    """Voice and browser agents have no envelope to declare, and must not acquire a problem."""
    assert not [
        one
        for one in validate_contract(_contract(None))
        if one.startswith("runtime.interface:")
    ]


def test_the_documented_envelopes_match_what_is_actually_sent():
    """The description and the skill both spell these out. They are copied from a builder in a
    different package, so nothing structural keeps them true; this is what notices."""
    import inspect

    from fi.simulate.agent.wrappers.http import HTTPAgentWrapper

    source = inspect.getsource(HTTPAgentWrapper._request_payload)
    for field in (
        "thread_id",
        "execution_id",
        "turn_index",
        "scenario_name",
        "persona",
        "situation",
        "expected_outcome",
        "messages",
        "new_message",
        "tools",
        "metadata",
    ):
        assert f'"{field}"' in source, f"fi.alk no longer sends {field}"

    said = next(
        one
        for one in validate_contract(_contract(_http("custom")))
        if one.startswith("runtime.interface:")
    )
    for field in ("thread_id", "new_message", "metadata"):
        assert field in said, f"the refusal no longer names {field}"
