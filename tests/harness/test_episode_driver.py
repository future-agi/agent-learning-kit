"""The episode seam's boundary rules, as properties rather than branches.

An external trainer is an untrusted caller acting inside our world, so most of these assert
something it must *not* be able to do. Each test names the rule it protects.
"""

from __future__ import annotations

import pytest

from fi.alk.harness.episode_driver import (
    EndReason,
    Episode,
    EpisodeClosed,
    PrivateEvidence,
    PublicResult,
    PublicTool,
    ToolNotPermitted,
)
from fi.alk.harness.world_door import AGENT, Outcome, ToolCall


class _Catalogue:
    """A release publishing two tools: one read, one write, plus a speech tool."""

    def __init__(self, tools: list[PublicTool] | None = None) -> None:
        self._tools = tools if tools is not None else [
            PublicTool("look_up_order", "Find an order", ("order_id",)),
            PublicTool("refund", "Refund an order", ("order_id", "amount")),
            PublicTool("say", "Speak to the customer", ("message",)),
        ]

    def published(self):
        return self._tools

    def world_action(self, tool: str) -> str:
        return f"orders.{tool}"

    def effect(self, tool: str) -> str:
        return "write" if tool == "refund" else "read"


class _Door:
    """A door that records the calls it was given and answers with a scripted outcome."""

    def __init__(self, outcome: Outcome | None = None) -> None:
        self.calls: list[ToolCall] = []
        self.outcome = outcome or Outcome(outcome="committed", status=200, result={"ok": True})

    def enter(self, call: ToolCall) -> Outcome:
        self.calls.append(call)
        return self.outcome


def _episode(door: _Door | None = None, **kwargs) -> Episode:
    return Episode(
        episode_id="ep-1",
        prompt="Refund the customer if policy allows.",
        door=door or _Door(),
        catalogue=_Catalogue(),
        **kwargs,
    )


def test_a_trainer_cannot_choose_who_it_acts_as() -> None:
    """Identity is server-derived. Impersonating a customer or worker must be impossible."""
    door = _Door()
    _episode(door).step(action_id="a1", tool="refund", arguments={"order_id": "o1"})
    call = door.calls[0]
    assert call.actor == AGENT
    assert call.proven is True
    assert call.worker is None


def test_the_action_id_becomes_the_idempotency_key() -> None:
    """A retried action must replay the first result, which is the door's keyed behaviour."""
    door = _Door()
    _episode(door).step(action_id="a1", tool="refund", arguments={"order_id": "o1"})
    assert door.calls[0].key == "a1"


def test_an_action_without_an_id_is_refused() -> None:
    """Without a key a lost response cannot be retried safely, so it is not allowed at all."""
    with pytest.raises(ToolNotPermitted, match="action_id"):
        _episode().step(action_id="", tool="refund")


def test_an_unpublished_tool_never_reaches_the_world() -> None:
    """SQL, workspace access, reset and the actor roster are not tools, so they are unnameable."""
    door = _Door()
    episode = _episode(door)
    for name in ["query", "reset", "write_file", "actors", "refund_v2"]:
        with pytest.raises(ToolNotPermitted):
            episode.step(action_id="a1", tool=name, arguments={})
    assert door.calls == [], "a refused tool must not be dispatched"


def test_an_undeclared_argument_is_refused_before_dispatch() -> None:
    door = _Door()
    with pytest.raises(ToolNotPermitted, match="currency"):
        _episode(door).step(
            action_id="a1", tool="refund", arguments={"order_id": "o1", "currency": "USD"}
        )
    assert door.calls == []


def test_a_refusals_reason_never_reaches_the_trainer() -> None:
    """A reason is written for an operator and can quote world state; only a code is public."""
    door = _Door(
        Outcome(
            outcome="refused",
            status=403,
            reason="order o1 belongs to tenant acme and its balance is 420.00",
            refusal_code="not_permitted",
        )
    )
    receipt = _episode(door).step(action_id="a1", tool="refund", arguments={"order_id": "o1"})
    assert receipt.public.ok is False
    assert receipt.public.error == "not_permitted"
    assert "acme" not in repr(receipt.public)
    assert "420.00" not in repr(receipt.public)
    # The detail is kept, but on the private half.
    assert "acme" in receipt.private.reason


def test_public_and_private_are_separate_types_that_cannot_carry_each_other() -> None:
    """Serializing the public half can never reach private evidence by walking a field."""
    public_fields = set(PublicResult.__dataclass_fields__)
    private_fields = set(PrivateEvidence.__dataclass_fields__)
    assert "reason" not in public_fields
    assert "fault" not in public_fields
    assert "status" not in public_fields
    # The two share only the identifiers needed to correlate them.
    assert public_fields & private_fields == {"action_id", "sequence"}


def test_a_failed_action_reports_no_output() -> None:
    door = _Door(Outcome(outcome="refused", status=403, refusal_code="not_permitted"))
    receipt = _episode(door).step(action_id="a1", tool="refund", arguments={"order_id": "o1"})
    assert receipt.public.output is None


def test_a_duplicate_write_is_still_a_settled_action_to_the_trainer() -> None:
    """The door reports `duplicate` for a replayed key; the trainer sees its original result."""
    door = _Door(Outcome(outcome="duplicate", status=200, result={"ok": True}))
    receipt = _episode(door).step(action_id="a1", tool="refund", arguments={"order_id": "o1"})
    assert receipt.public.ok is True
    assert receipt.private.outcome == "duplicate"


def test_sequence_numbers_order_the_episode() -> None:
    episode = _episode()
    first = episode.step(action_id="a1", tool="look_up_order", arguments={"order_id": "o1"})
    second = episode.step(action_id="a2", tool="refund", arguments={"order_id": "o1"})
    assert (first.public.sequence, second.public.sequence) == (1, 2)
    assert episode.observe().sequence == 2


def test_a_closed_episode_accepts_no_further_actions() -> None:
    episode = _episode()
    episode.freeze(EndReason.COMPLETED)
    with pytest.raises(EpisodeClosed):
        episode.step(action_id="a1", tool="refund", arguments={"order_id": "o1"})


def test_freezing_is_idempotent_and_keeps_the_first_reason() -> None:
    """A later cancel must not overwrite why the episode actually ended."""
    episode = _episode()
    episode.freeze(EndReason.COMPLETED)
    observation = episode.freeze(EndReason.CANCELLED)
    assert observation.end_reason is EndReason.COMPLETED
    assert observation.closed is True


def test_the_step_limit_ends_the_episode_rather_than_failing_a_tool() -> None:
    """A trainer must tell "I ran out of steps" from "the world refused me"."""
    door = _Door()
    episode = _episode(door, step_limit=1)
    episode.step(action_id="a1", tool="look_up_order", arguments={"order_id": "o1"})
    with pytest.raises(EpisodeClosed, match="step limit"):
        episode.step(action_id="a2", tool="refund", arguments={"order_id": "o1"})
    assert episode.observe().end_reason is EndReason.STEP_LIMIT
    assert len(door.calls) == 1, "the over-limit action must not reach the world"


def test_speech_is_an_action_so_who_spoke_survives_in_the_log() -> None:
    door = _Door()
    episode = _episode(door)
    episode.say(action_id="a1", tool="say", message="I can refund that for you.")
    assert door.calls[0].tool == "say"
    assert episode.observe().messages == ("I can refund that for you.",)


def test_a_refused_message_is_not_recorded_as_said() -> None:
    """The transcript must only hold what the participant could actually have heard."""
    door = _Door(Outcome(outcome="refused", status=403, refusal_code="not_permitted"))
    episode = _episode(door)
    episode.say(action_id="a1", tool="say", message="never delivered")
    assert episode.observe().messages == ()


def test_the_observation_exposes_no_routes_or_effects() -> None:
    """A published tool is a name, a description and argument names. Nothing operational."""
    fields = set(PublicTool.__dataclass_fields__)
    assert fields == {"name", "description", "arguments"}
    observation = _episode().observe()
    assert {tool.name for tool in observation.tools} == {"look_up_order", "refund", "say"}


def test_an_episode_with_no_published_tools_can_do_nothing() -> None:
    episode = Episode(
        episode_id="ep-2",
        prompt="nothing to do",
        door=_Door(),
        catalogue=_Catalogue(tools=[]),
    )
    assert episode.observe().tools == ()
    with pytest.raises(ToolNotPermitted):
        episode.step(action_id="a1", tool="refund")
