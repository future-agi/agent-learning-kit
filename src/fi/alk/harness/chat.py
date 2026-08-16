"""One conversation, from pointing at an agent to a world you can test against.

You say what you want, it does it, you say the next thing. Stages are not commands you invoke;
they are what the harness moves through while you keep talking. When one produces its artifact
the next opens on the same agent, and anything already built stays correctable by saying so.

Underneath, each stage is still its own session with its own instructions and its own tools, so
context stays small and a stage can be re-entered later without redoing the ones before it. That
is an implementation detail, not something to make somebody manage.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import build as build_stage
from . import understand as understand_stage
from .config import artifact_dir
from .contract import AgentContract
from .session import Stage
from .sources import AgentSource, resolve

UNDERSTAND = "understand"
BUILD = "build"
DONE = "done"

_NEXT = {UNDERSTAND: BUILD, BUILD: DONE}


@dataclass
class Conversation:
    """The whole thing, held open."""

    source: AgentSource
    out: Path
    ask: Callable[..., Any] | None = None
    stage_name: str = UNDERSTAND
    stage: Stage | None = None
    spent_usd: float = 0.0
    history: list[str] = field(default_factory=list)

    # -- what exists so far ----------------------------------------------------------

    @property
    def contract(self) -> AgentContract | None:
        return understand_stage.load(self.out)

    @property
    def world_built(self) -> bool:
        return (self.out / "world.sqlite").exists()

    def _artifact_for(self, stage_name: str) -> bool:
        return {
            UNDERSTAND: self.contract is not None,
            BUILD: self.world_built,
            DONE: True,
        }[stage_name]

    # -- moving between stages -------------------------------------------------------

    async def _close(self) -> None:
        if self.stage is not None:
            self.spent_usd += self.stage.spent_usd
            await self.stage.__aexit__(None, None, None)
            self.stage = None

    async def _open(self, stage_name: str) -> str:
        """Open a stage and return the message that starts it."""
        await self._close()
        self.stage_name = stage_name
        if stage_name == UNDERSTAND:
            self.stage, _ = understand_stage.open_stage(
                self.source, out=self.out, ask=self.ask
            )
            opening = understand_stage.opening(self.source)
        else:
            contract = self.contract
            if contract is None:
                raise RuntimeError("cannot build a world before there is a contract")
            self.stage, _ = build_stage.open_stage(contract, out=self.out, ask=self.ask)
            opening = build_stage.opening(contract)
        await self.stage.__aenter__()
        return opening

    def next_stage(self) -> str | None:
        """The stage that follows the current one, once this one has produced its artifact."""
        if not self._artifact_for(self.stage_name):
            return None
        following = _NEXT.get(self.stage_name)
        return None if following in (None, DONE) else following

    # -- talking ---------------------------------------------------------------------

    async def start(self, on_event: Callable[..., Any] | None = None) -> None:
        opening = await self._open(self._resume_at())
        await self.stage.say(opening, on_event=on_event)  # type: ignore[union-attr]

    def _resume_at(self) -> str:
        """Pick up where the artifacts say this agent got to."""
        if self.contract is None:
            return UNDERSTAND
        return BUILD

    async def say(self, message: str, on_event: Callable[..., Any] | None = None) -> None:
        """Send a message to whichever stage is open."""
        self.history.append(message)
        if self.stage is None:
            await self.start(on_event=on_event)
        await self.stage.say(message, on_event=on_event)  # type: ignore[union-attr]

    async def advance(self, on_event: Callable[..., Any] | None = None) -> str | None:
        """Move to the next stage and start it. Returns the stage entered, or None."""
        following = self.next_stage()
        if following is None:
            return None
        opening = await self._open(following)
        await self.stage.say(opening, on_event=on_event)  # type: ignore[union-attr]
        return following

    async def close(self) -> None:
        await self._close()


def open_conversation(
    *,
    name: str,
    path: str,
    kind: str = "repo",
    out: Path | None = None,
    ask: Callable[..., Any] | None = None,
) -> Conversation:
    source = resolve(kind, name=name, root=path)
    return Conversation(source=source, out=out or artifact_dir(name), ask=ask)


async def _demo() -> None:  # pragma: no cover - convenience for manual runs
    conversation = open_conversation(name="demo", path=".")
    await conversation.start()
    await conversation.close()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_demo())
