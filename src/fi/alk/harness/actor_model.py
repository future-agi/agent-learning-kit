"""One way an actor's model is asked to decide, for every runtime: the dispatcher's live turns, a chat actor's turn
and a pre-run session's messages all call `decider`. The voice engine asks its own call's model the same brief."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

# The brief, the actor's id and the model its record names (None for the default); answers the model's text.
Decide = Callable[[str, str, "str | None"], Awaitable[str]]


class ActorModelFailed(RuntimeError):
    """The actor's model did not answer; its error is never the actor's line."""


def routed(model: str | None) -> str:
    """The model a session runs on, routed like the harness's own: an actor names a model bare (as the editor lists
    them), and where the harness's model carries a provider route (`vertex_ai/...`) the actor's takes the same one."""
    from .config import chosen_model

    chosen = chosen_model(model)
    route = chosen_model().rpartition("/")[0]
    return f"{route}/{chosen}" if route and "/" not in chosen else chosen


def decider(meter: Any = None, default_model: str | None = None) -> Decide:
    """One-turn decisions on the model the actor names, else `default_model`, else the harness's own; each call is
    metered when a meter is given."""

    async def decide(brief: str, actor_id: str, model: str | None) -> str:
        from .backends import SessionSpec
        from .session import Stage

        chosen = routed(model or default_model)
        stage = Stage(
            SessionSpec(system_prompt=brief, max_turns=1, model=chosen, gated=False),
            name="actor",
        )
        await stage.__aenter__()
        try:
            turn = await stage.say("Decide now.")
        finally:
            await stage.__aexit__(None, None, None)
        if meter is not None:
            meter.add(actor_id, chosen, turn)
        if getattr(turn, "error", ""):
            raise ActorModelFailed(f"{actor_id}'s model {chosen} did not answer: {turn.error}")
        return turn.text

    return decide
