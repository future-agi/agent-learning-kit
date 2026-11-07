"""One way an actor's model is asked to decide, for every runtime: the dispatcher's live turns, a chat actor's turn
and a pre-run session's messages all call `decider`. The voice engine asks its own call's model the same brief."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

# The brief, the actor's id and the model its record names (None for the default); answers the model's text.
Decide = Callable[[str, str, "str | None"], Awaitable[str]]


def decider(meter: Any = None, default_model: str | None = None) -> Decide:
    """One-turn decisions on the model the actor names, else `default_model`, else the harness's own; each call is
    metered when a meter is given."""

    async def decide(brief: str, actor_id: str, model: str | None) -> str:
        from .backends import SessionSpec
        from .config import chosen_model
        from .session import Stage

        chosen = chosen_model(model or default_model)
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
        return turn.text

    return decide
