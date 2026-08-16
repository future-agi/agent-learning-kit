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
from . import reception as reception_stage
from . import scenarios as scenario_stage
from . import understand as understand_stage
from .run import stage as run_stage
from .config import artifact_dir
from .contract import AgentContract
from .session import Stage
from .sources import AgentSource, resolve

RECEPTION = "reception"
UNDERSTAND = "understand"
BUILD = "build"
SCENARIOS = "scenarios"
RUN = "run"
DONE = "done"

_NEXT = {
    RECEPTION: UNDERSTAND,
    UNDERSTAND: BUILD,
    BUILD: SCENARIOS,
    SCENARIOS: RUN,
    RUN: DONE,
}


@dataclass
class Conversation:
    """The whole thing, held open."""

    # Both unknown until somebody says which agent this is about, which is itself a stage.
    source: AgentSource | None = None
    out: Path | None = None
    ask: Callable[..., Any] | None = None
    wanted: int = 10
    # Where to look for an agent. Almost never inside this repo: the harness lives in one place
    # and the agent being tested lives in another, so looking only at our own root means the
    # first thing anybody types cannot be found.
    workspace: Path | None = None
    stage_name: str = ""
    stage: Stage | None = None
    spent_usd: float = 0.0
    history: list[str] = field(default_factory=list)
    _found: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Read off the artifacts rather than defaulting to the first stage. An agent whose world
        # is already built is at the scenarios, and saying otherwise before anything has been
        # opened makes every question about where this conversation is answer wrongly.
        self.stage_name = self.stage_name or self._resume_at()

    # -- what exists so far ----------------------------------------------------------

    @property
    def contract(self) -> AgentContract | None:
        return understand_stage.load(self.out) if self.out else None

    @property
    def world_built(self) -> bool:
        return bool(self.out) and (self.out / "world.sqlite").exists()

    @property
    def scenarios_written(self) -> bool:
        return bool(self.out) and bool(scenario_stage.load(self.out))

    @property
    def anything_run(self) -> bool:
        return bool(self.out) and bool(run_stage.load(self.out))

    def _artifact_for(self, stage_name: str) -> bool:
        return {
            # A contract already on disk settles which agent this is just as well as being told,
            # so coming back to an agent does not mean pointing at its repository again.
            RECEPTION: self.source is not None or self.contract is not None,
            UNDERSTAND: self.contract is not None,
            BUILD: self.world_built,
            SCENARIOS: self.scenarios_written,
            RUN: self.anything_run,
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
        if stage_name == RECEPTION:
            self.stage, self._found = reception_stage.open_stage(
                cwd=self.workspace, ask=self.ask
            )
            await self.stage.__aenter__()
            return reception_stage.opening()

        # Deliberately not "is there a source": a contract on disk settles which agent this is,
        # and every stage after the first works from the contract rather than from the source.
        # Only re-reading the agent needs to know where it lives.
        if self.source is None and self.contract is None:
            raise RuntimeError("nobody has said which agent this is about yet")
        if stage_name == UNDERSTAND:
            self.stage, _ = understand_stage.open_stage(
                self.source, out=self.out, ask=self.ask
            )
            opening = understand_stage.opening(self.source)
            await self.stage.__aenter__()
            return opening

        contract = self.contract
        if contract is None:
            raise RuntimeError("cannot go further before there is a contract")
        if self.source is None and stage_name == UNDERSTAND:
            raise RuntimeError(
                "cannot re-read the agent without knowing where it lives"
            )
        if stage_name == BUILD:
            self.stage, _ = build_stage.open_stage(contract, out=self.out, ask=self.ask)
            opening = build_stage.opening(contract)
        elif stage_name == RUN:
            if not self.scenarios_written:
                raise RuntimeError("cannot run anything before there are scenarios")
            self.stage, _ = run_stage.open_stage(contract, out=self.out, ask=self.ask)
            opening = run_stage.opening(contract, self.out)
        else:
            if not self.world_built:
                raise RuntimeError("cannot write scenarios before there is a world")
            written = len(scenario_stage.load(self.out))
            wanted = written or self.wanted
            self.stage, _ = scenario_stage.open_stage(
                contract, out=self.out, wanted=wanted, ask=self.ask
            )
            opening = scenario_stage.opening(contract, wanted, written)
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
        """Open the stage this agent is up to, and set it going."""
        opening = await self._open(self._resume_at())
        await self.stage.say(opening, on_event=on_event)  # type: ignore[union-attr]

    async def open_quietly(self) -> None:
        """Open the stage without telling it to start.

        A stage's opening message is an instruction to do the stage's work. Sending it because
        somebody said hello means a greeting kicks off a build, so it is only sent when the work
        is actually what was asked for.
        """
        await self._open(self._resume_at())

    def _resume_at(self) -> str:
        """Pick up where the artifacts say this agent got to."""
        if self.source is None and self.contract is None:
            return RECEPTION
        if self.contract is None:
            return UNDERSTAND
        if not self.world_built:
            return BUILD
        if not self.scenarios_written:
            return SCENARIOS
        return RUN

    async def say(
        self, message: str, on_event: Callable[..., Any] | None = None
    ) -> None:
        """Send a message to whichever stage is open."""
        self.history.append(message)
        if self.stage is None:
            await self.open_quietly()
        await self.stage.say(message, on_event=on_event)  # type: ignore[union-attr]
        await self._settle(on_event=on_event)

    async def _settle(self, on_event: Callable[..., Any] | None = None) -> None:
        """Take up whatever the open stage just established, and keep going.

        Reception is the only stage whose result is not a file, so it is the only one the
        conversation has to read back. Once it knows the agent there is nothing to decide, so it
        goes straight on rather than making somebody confirm what they already said.
        """
        settled = self._found.pop("source", None)
        if settled is None:
            return
        self.source = settled
        self.out = self.out or artifact_dir(settled.name)
        await self.advance(on_event=on_event)

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
    name: str = "",
    path: str = "",
    kind: str = "repo",
    out: Path | None = None,
    ask: Callable[..., Any] | None = None,
    wanted: int = 10,
    workspace: Path | None = None,
) -> Conversation:
    """Open the harness. With nothing, it starts by asking which agent you mean.

    Naming the agent up front is a shortcut for coming back to one already in progress, not the
    way in. Everything it needs can be said.
    """
    source = resolve(kind, name=name, root=path) if name and path else None
    return Conversation(
        source=source,
        out=out or (artifact_dir(name) if name else None),
        ask=ask,
        wanted=wanted,
        workspace=workspace,
    )


async def _demo() -> None:  # pragma: no cover - convenience for manual runs
    conversation = open_conversation(name="demo", path=".")
    await conversation.start()
    await conversation.close()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_demo())
