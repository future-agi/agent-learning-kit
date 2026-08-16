"""A stage as a live conversation, emitting what happened as it happens.

The operator experiences one continuous session: point at an agent, watch a contract appear,
correct something, move on. Underneath, each stage is its own session so context stays small and
any stage can be re-entered without redoing the ones before it.

A stage stays open across turns, so a correction is the next thing said rather than a re-run,
and it yields typed events rather than a wall of text. A terminal renders those events as lines;
a browser renders the same events as a transcript on one side and the artifact on the other.
Neither is privileged, which is the point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

TEXT = "text"
TOOL = "tool"
RESULT = "result"
ARTIFACT = "artifact"
DONE = "done"


@dataclass
class Event:
    """One observable thing the stage did."""

    kind: str
    text: str = ""
    tool: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        """A terminal-friendly rendering."""
        if self.kind == TEXT:
            return self.text
        if self.kind == TOOL:
            target = self.detail.get("target") or ""
            return f"  [{self.tool}{' ' + target if target else ''}]"
        if self.kind == RESULT:
            marker = "!" if self.detail.get("is_error") else ">"
            body = "\n".join(
                f"  {marker} {row}" for row in self.text.splitlines() if row
            )
            return body or f"  {marker} (no output)"
        if self.kind == ARTIFACT:
            return f"  [saved {self.detail.get('path', '')}]"
        if self.kind == DONE:
            cost = self.detail.get("cost_usd")
            spent = f" ${cost:.4f}" if isinstance(cost, float) else ""
            return f"  [{self.detail.get('outcome', '')} turns={self.detail.get('turns', 0)}{spent}]"
        return self.text


@dataclass
class Turn:
    """What one exchange produced."""

    text: str = ""
    events: list[Event] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    outcome: str = ""
    turns: int = 0
    cost_usd: float | None = None


_TARGET_KEYS = ("file_path", "path", "pattern", "agent", "tool", "table")


def _target(payload: Any) -> str:
    """A short label for what a tool call was aimed at, for display only."""
    if not isinstance(payload, dict):
        return ""
    for key in _TARGET_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value if len(value) <= 80 else value[:77] + "..."
    return ""


def _result_text(block: ToolResultBlock, limit: int = 600) -> str:
    content = block.content
    if isinstance(content, list):
        content = "\n".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    text = content if isinstance(content, str) else str(content)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _saved_path(block: ToolResultBlock) -> str:
    """Our tools report what they wrote; surfacing it lets a UI update the artifact pane."""
    content = block.content
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    if not isinstance(content, str):
        return ""
    for token in content.split():
        if token.endswith((".json", ".py", ".sqlite")):
            return token.rstrip(".,")
    return ""


class Stage:
    """One stage of the harness, held open so it can be talked to."""

    def __init__(self, options: ClaudeAgentOptions, *, name: str = "") -> None:
        self._options = options
        self._client: ClaudeSDKClient | None = None
        self.name = name
        self.session_id: str | None = None
        self.history: list[Turn] = []

    async def __aenter__(self) -> "Stage":
        self._client = ClaudeSDKClient(options=self._options)
        await self._client.connect()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    @property
    def client(self) -> ClaudeSDKClient:
        if self._client is None:
            raise RuntimeError("stage is not open; use it as an async context manager")
        return self._client

    async def stream(self, message: str) -> AsyncIterator[Event]:
        """Send a message and yield events as they arrive."""
        await self.client.query(message)
        turn = Turn()
        async for received in self.client.receive_response():
            for event in self._events(received, turn):
                turn.events.append(event)
                yield event
        self.history.append(turn)

    def _events(self, received: Any, turn: Turn) -> list[Event]:
        if isinstance(received, SystemMessage):
            data = received.data if isinstance(received.data, dict) else {}
            self.session_id = data.get("session_id") or self.session_id
            return []
        if isinstance(received, AssistantMessage):
            events: list[Event] = []
            for block in received.content:
                if isinstance(block, TextBlock):
                    turn.text += block.text
                    events.append(Event(TEXT, text=block.text))
                elif isinstance(block, ToolUseBlock):
                    turn.tools_used.append(block.name)
                    events.append(
                        Event(
                            TOOL,
                            tool=block.name,
                            detail={"target": _target(block.input)},
                        )
                    )
            return events
        if isinstance(received, ResultMessage):
            turn.outcome = received.subtype
            turn.turns = received.num_turns
            turn.cost_usd = received.total_cost_usd
            self.session_id = received.session_id or self.session_id
            return [
                Event(
                    DONE,
                    detail={
                        "outcome": received.subtype,
                        "turns": received.num_turns,
                        "cost_usd": received.total_cost_usd,
                    },
                )
            ]
        blocks = getattr(received, "content", None)
        if isinstance(blocks, list):
            events = []
            for block in blocks:
                if not isinstance(block, ToolResultBlock):
                    continue
                # What a tool said back is the only view a caller has of whether the work is
                # going well. Dropping it leaves a run that can only be diagnosed by guessing.
                events.append(
                    Event(
                        RESULT,
                        text=_result_text(block),
                        detail={"is_error": bool(getattr(block, "is_error", False))},
                    )
                )
                path = _saved_path(block)
                if path:
                    turn.artifacts.append(path)
                    events.append(Event(ARTIFACT, detail={"path": path}))
            return events
        return []

    async def say(
        self, message: str, *, on_event: Callable[[Event], None] | None = None
    ) -> Turn:
        """Send a message and wait for the whole reply."""
        async for event in self.stream(message):
            if on_event:
                on_event(event)
        return self.history[-1]

    @property
    def spent_usd(self) -> float:
        return sum(turn.cost_usd or 0.0 for turn in self.history)
