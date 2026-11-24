"""One turn at a time, for a driver outside the submitted agent's process.

The hosted scheduler drives a submitted agent to completion. An external RL trainer cannot
work that way: it needs to take one action, see what happened, and decide the next (T28). This
module is that seam, and deliberately nothing more -- it holds no transport, no session store
and no reward. ``WorldDoor`` already owns permission, idempotency, faults and the action log,
so every action goes through it unchanged rather than through a second execution path that
could drift from the one simulations use.

What this layer is actually for is the boundary. A trainer is an untrusted caller inside our
world, so three rules are enforced here and tested as security properties, not conveniences:

* **Identity is server-derived.** The caller never says who it is. Every action is attributed
  to the agent, with ``proven=True`` because this layer vouched for it. A trainer cannot act
  as a simulated customer, a worker, or another episode's agent.
* **Only declared tools, with validated arguments.** A name absent from the published schema
  is refused before dispatch, as is an unknown argument. Reaching the world's SQL, workspace,
  reset or actor roster is not a tool, so it is unreachable by construction.
* **Public and private are separate types.** ``PublicResult`` is what a trainer may read;
  world events, reference answers and private reasons live on ``PrivateEvidence`` and are
  never fields of the public one. Keeping them apart is what stops an error path leaking what
  the response body does not.

Actions are serialized per episode: a world is a mutable thing and two concurrent writes from
the same prior state are never valid. A repeated ``action_id`` replays the first result
through the door's own idempotency rather than acting twice.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence

from .world_door import AGENT, Outcome, ToolCall, WorldDoor

# Outcomes the door reports for an action that reached the world and settled.
_SETTLED = frozenset({"committed", "duplicate"})


class EndReason(str, Enum):
    """Why an episode stopped. Kept apart from the verdict: a finished episode may still be grading."""

    COMPLETED = "completed"
    STEP_LIMIT = "step_limit"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "infrastructure_failure"


class EpisodeClosed(RuntimeError):
    """An action arrived after the episode stopped accepting them."""


class ToolNotPermitted(RuntimeError):
    """The action named a tool this release does not publish, or arguments it does not declare."""


@dataclass(frozen=True)
class PublicTool:
    """A tool as the participant sees it. Routes, services and effects stay out."""

    name: str
    description: str = ""
    arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class PublicResult:
    """What a trainer may read back from one action.

    Every field here is disclosed, so nothing private may be added to this type. A refusal or
    fault is reported as ``ok=False`` with ``error``, which is a code and never a reason built
    from world state.
    """

    action_id: str
    sequence: int
    tool: str
    ok: bool
    output: Any = None
    error: str | None = None


@dataclass(frozen=True)
class PrivateEvidence:
    """References to what really happened, for grading. Never serialized to a trainer."""

    action_id: str
    sequence: int
    world_action: str
    outcome: str
    status: int
    reason: str = ""
    fault: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class StepReceipt:
    """One action's two halves, kept as separate types so neither can carry the other."""

    public: PublicResult
    private: PrivateEvidence


@dataclass(frozen=True)
class PublicObservation:
    """The participant's view of the episode: the task, its tools, and the dialogue so far."""

    episode_id: str
    prompt: str
    tools: tuple[PublicTool, ...]
    messages: tuple[str, ...] = ()
    sequence: int = 0
    closed: bool = False
    end_reason: EndReason | None = None


class ToolCatalogue(Protocol):
    """The release's published tools. Resolved server-side; a caller never supplies it."""

    def published(self) -> Sequence[PublicTool]: ...

    def world_action(self, tool: str) -> str:
        """The world action a tool dispatches to (``<service>.<action>``)."""
        ...

    def effect(self, tool: str) -> str:
        """``read`` or ``write`` as the contract declares it; empty lets the door decide."""
        ...


@dataclass
class Episode:
    """One trainer's episode against one leased world.

    The world and door are passed in, not provisioned here: leasing, capacity and expiry
    belong to whoever owns the slot, and keeping them out makes this seam testable on its own.
    """

    episode_id: str
    prompt: str
    door: WorldDoor
    catalogue: ToolCatalogue
    step_limit: int | None = None

    _sequence: int = field(default=0, init=False)
    _closed: bool = field(default=False, init=False)
    _end_reason: EndReason | None = field(default=None, init=False)
    _messages: list[str] = field(default_factory=list, init=False)
    # One action at a time: a world is mutable, so two writes from the same prior state are
    # never both valid. A retried action_id still replays through the door's idempotency.
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def observe(self) -> PublicObservation:
        """The participant's view. Safe to call on a closed episode."""
        return PublicObservation(
            episode_id=self.episode_id,
            prompt=self.prompt,
            tools=tuple(self.catalogue.published()),
            messages=tuple(self._messages),
            sequence=self._sequence,
            closed=self._closed,
            end_reason=self._end_reason,
        )

    def step(
        self, *, action_id: str, tool: str, arguments: Mapping[str, Any] | None = None
    ) -> StepReceipt:
        """Execute one action and return what the trainer sees and what grading needs.

        ``action_id`` is the trainer's own key. The same key replays the first result instead
        of acting again, which is what makes a lost response safe to retry.
        """
        if not action_id:
            raise ToolNotPermitted("an action_id is required so a retry cannot act twice")
        with self._lock:
            if self._closed:
                raise EpisodeClosed(
                    f"episode {self.episode_id} stopped accepting actions ({self._end_reason})"
                )
            self._reject_unpublished(tool, arguments or {})
            if self.step_limit is not None and self._sequence >= self.step_limit:
                # Truncation is the episode's end, not a tool error: the trainer must be able to
                # tell "I ran out of steps" from "the world refused me".
                self._stop(EndReason.STEP_LIMIT)
                raise EpisodeClosed(f"episode {self.episode_id} reached its step limit")
            self._sequence += 1
            sequence = self._sequence
            call = self._call_for(tool, arguments or {}, action_id)
            outcome = self.door.enter(call)
        return self._receipt(action_id, sequence, tool, call.action, outcome)

    def say(self, *, action_id: str, tool: str, message: str) -> StepReceipt:
        """Speak to the simulated participant through a declared speech tool.

        Dialogue is an action like any other so that who spoke, and in what order, survives in
        the same log grading reads. The message is recorded as participant-visible.
        """
        receipt = self.step(action_id=action_id, tool=tool, arguments={"message": message})
        if receipt.public.ok:
            self._messages.append(message)
        return receipt

    def freeze(self, reason: EndReason = EndReason.COMPLETED) -> PublicObservation:
        """Stop accepting actions and record why. Idempotent, and never changes a recorded reason.

        Freezing is not grading and not cleanup. Keeping the three apart is what lets a failed
        world teardown leave a successful grade standing.
        """
        with self._lock:
            self._stop(reason)
        return self.observe()

    # --- internals ---------------------------------------------------------------------

    def _stop(self, reason: EndReason) -> None:
        """Close the episode, preserving the first reason. Caller holds the lock."""
        if not self._closed:
            self._closed = True
            self._end_reason = reason

    def _reject_unpublished(self, tool: str, arguments: Mapping[str, Any]) -> None:
        """Refuse anything the release does not publish, before the world is touched.

        Validating here rather than in the world is what keeps SQL, workspace access, reset and
        the actor roster unreachable: none of them is a published tool, so none can be named.
        """
        published = {one.name: one for one in self.catalogue.published()}
        declared = published.get(tool)
        if declared is None:
            raise ToolNotPermitted(f"{tool!r} is not a tool this release publishes")
        if declared.arguments:
            unknown = sorted(set(arguments) - set(declared.arguments))
            if unknown:
                raise ToolNotPermitted(
                    f"{tool!r} does not declare the argument(s) {', '.join(unknown)}"
                )

    def _call_for(self, tool: str, arguments: Mapping[str, Any], action_id: str) -> ToolCall:
        """The world call for one action, with identity this layer vouches for.

        ``actor`` and ``proven`` are set here and never read from the caller: that is the whole
        reason a trainer cannot impersonate a simulated customer or another episode's agent.
        """
        action = self.catalogue.world_action(tool)
        return ToolCall(
            action=action,
            name=action.rsplit(".", 1)[-1] or tool,
            arguments=dict(arguments),
            actor=AGENT,
            key=action_id,
            proven=True,
            tool=tool,
            effect=self.catalogue.effect(tool),
        )

    def _receipt(
        self, action_id: str, sequence: int, tool: str, action: str, outcome: Outcome
    ) -> StepReceipt:
        """Split one outcome into what the trainer reads and what grading keeps.

        A refusal or fault returns its code, never ``outcome.reason``: a reason is written for
        an operator and can quote world state a participant must not see.
        """
        settled = outcome.outcome in _SETTLED
        return StepReceipt(
            public=PublicResult(
                action_id=action_id,
                sequence=sequence,
                tool=tool,
                ok=settled,
                output=outcome.result if settled else None,
                error=None if settled else (outcome.refusal_code or outcome.outcome),
            ),
            private=PrivateEvidence(
                action_id=action_id,
                sequence=sequence,
                world_action=action,
                outcome=outcome.outcome,
                status=outcome.status,
                reason=outcome.reason,
                fault=outcome.fault,
            ),
        )
