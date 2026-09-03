"""What a harness backend is, said without naming any vendor.

A stage of this harness is a conversation: a system prompt, a set of tools the model may call,
a turn budget, and a loop that feeds tool results back until the model stops. Today that loop is
Claude Code's; tomorrow it may be Gemini's, Bedrock's, or a harness of our own. The stages do
not care, so nothing they say may mention a vendor.

This module is that neutrality, in four pieces:

- ``ToolSpec`` / ``ToolServer``: a tool as the harness defines one, with the async handler that
  executes it. Backends adapt these to whatever their loop natively speaks.
- ``SessionSpec``: everything a stage asks of a session. This is the real contract the ten
  construction sites were already expressing through a vendor options class.
- The reply vocabulary (``SessionOpened``, ``ModelReply``, ``ToolReturned``, ``StageDone``):
  what a running session emits, which ``Stage`` renders into events. A backend translates its
  provider's stream into these and nothing else leaks through.
- ``HarnessBackend`` / ``HarnessSession``: the two protocols a new backend implements. A backend
  with its own loop supplies its own session type; the harness never sees inside it.

Nothing here imports a provider SDK, so a deployment that uses one backend does not need the
other's dependencies installed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol, runtime_checkable

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

# How many workers a backend may run at once. Both SDKs cap this themselves (one at twenty by
# default), so this is the harness's own ceiling, set below theirs and shared so that the turn
# budget reserved for a fan-out matches what can actually be in flight.
# The one ceiling on fan-out: how many workers may run at the same time. It protects the
# machine, and it is a constant rather than an environment variable because a setting
# nobody remembers makes every run behave differently for reasons nothing on screen
# explains. The scenarios stage imports this as its own ceiling and tells the model.
# The hard ceiling on writers running at once, enforced by claim_slice rather than asked
# for: a prompt is guidance and this is a safety limit on the machine. Ten is the number
# a stage is told to aim at; the extra two are headroom so a brief overshoot is not a
# refusal in the middle of a suite.
MOST_WORKERS_AT_ONCE = 12


def qualified(server: str, tool_name: str) -> str:
    """The fully qualified name a session grants and a model calls.

    The ``mcp__{server}__{tool}`` convention comes from the first backend, but the skills and
    gates all speak it, so every backend keeps it. Renaming per backend would mean rewriting
    every prompt that names a tool.
    """
    return f"mcp__{server}__{tool_name}"


@dataclass
class ToolSpec:
    """One tool: its contract for the model, and the code that executes it.

    ``input_schema`` is either a JSON Schema dict or the shorthand ``{"arg": str}`` mapping the
    tool decorator accepts. ``handler`` receives the arguments dict and returns
    ``{"content": [{"type": "text", "text": ...}], "is_error"?: bool}``, the shape every
    existing tool already returns.
    """

    name: str
    description: str
    input_schema: Any
    handler: ToolHandler


@dataclass
class ToolServer:
    """A named group of tools granted to a session together."""

    name: str
    version: str = "0.1.0"
    tools: list[ToolSpec] = field(default_factory=list)


def tool(
    name: str, description: str, input_schema: Any
) -> Callable[[ToolHandler], ToolSpec]:
    """Declare a tool. Same signature the stages have always used, no vendor behind it."""

    def decorator(handler: ToolHandler) -> ToolSpec:
        return ToolSpec(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
        )

    return decorator


def tool_server(
    name: str, version: str = "0.1.0", tools: list[ToolSpec] | None = None
) -> ToolServer:
    """Group tools under a server name, as the stages have always done."""
    return ToolServer(name=name, version=version, tools=list(tools or []))


# Host tools a backend may be asked to supply itself. Claude Code ships these; a backend without
# a host CLI implements them from files.py. Anything else asked for as a builtin is refused at
# session build time rather than silently dropped.
FILE_TOOLS = ("Read", "Glob", "Grep")

# Everything a backend without a host CLI can offer under the names Claude Code uses, so a
# stage's skill text means the same thing whichever loop is running it. Reading tells you what
# an agent is meant to do and running tells you what it does; the scenarios worth writing come
# from the gap, so a stage trusted with one is trusted with both. Writing is absent on purpose:
# a stage able to edit the agent under test could make its scenarios pass by changing the agent.
HOST_TOOLS = (*FILE_TOOLS, "Bash")
ASK_TOOL = "AskUserQuestion"
DELEGATE_TOOL = "Delegate"
KNOWN_BUILTINS = (*FILE_TOOLS, ASK_TOOL, DELEGATE_TOOL)


@dataclass
class WorkerSpec:
    """A worker the model may run to do part of its stage, in its own session.

    Both backends we ship can start a second model session and hand its answer back as a tool
    result, and both decide at call time how many to run; only the vocabulary differs. This is
    that capability said once, so a stage declares a worker and every backend honours it.

    ``instructions`` is the worker's own system prompt. ``servers`` and ``builtins`` are its
    tools, named exactly as ``SessionSpec`` names them, and default to the parent's when left
    empty. ``max_turns`` bounds one worker; ``model`` overrides the parent's for it.

    A worker never inherits the parent's conversation. Everything it needs comes from
    ``instructions`` plus the brief the model writes when it calls, which is what keeps a large
    fan-out from copying the whole stage history N times.
    """

    description: str
    instructions: str
    servers: dict[str, ToolServer] = field(default_factory=dict)
    builtins: tuple[str, ...] = ()
    max_turns: int = 40
    model: str = ""
    # How hard this worker may think. Empty leaves the model's own default alone. Held here
    # rather than taken from the stage because a worker's job is not the stage's job.
    effort: str = ""


@dataclass
class SessionSpec:
    """Everything a stage asks of a session, with no vendor vocabulary in it.

    ``builtins`` are host tools by bare name (``Read``, ``Glob``, ``Grep``,
    ``AskUserQuestion``); ``servers`` are the harness's own tools. ``ask`` is the operator
    callback consulted when the model asks a question; None means the run is unattended.
    ``gated`` selects the deny-by-default permission regime every tool-bearing stage runs
    under; the one stage that runs bare (the simulated customer, which has no tools) turns it
    off to keep its behaviour byte-identical.
    ``thinking`` opts into the harness's thinking policy (config.thinking_config); stages that
    never set one keep their backend's default.
    """

    system_prompt: str
    servers: dict[str, ToolServer] = field(default_factory=dict)
    builtins: tuple[str, ...] = ()
    cwd: str | None = None
    max_turns: int = 40
    model: str = ""
    ask: Any = None
    gated: bool = True
    thinking: bool = False
    # A ready-made permission callable that replaces the backend's own gate wholesale. One
    # stage (understand, interactive) passes its gate in fully built; backends without a
    # permission callback concept ignore it, which is safe because their gating is structural.
    permission_override: Any = None
    # Workers this session may run, by name. Declaring any of these is what lets the model
    # divide its own work; a stage that declares none behaves exactly as before.
    workers: dict[str, WorkerSpec] = field(default_factory=dict)

    def worker_turns(self) -> int:
        """Turns to reserve beyond the parent's own, so a fan-out cannot run the budget dry.

        One backend bills every worker's turns to the session that started them, so a budget
        sized for the parent alone stops a fan-out partway through and loses the work. Reserving
        the worst case here keeps that a backend detail rather than a stage's problem.
        """
        if not self.workers:
            return 0
        widest = max(worker.max_turns for worker in self.workers.values())
        return widest * MOST_WORKERS_AT_ONCE

    def granted(self) -> list[str]:
        """Every tool name this session may call, qualified the way the model calls it."""
        names = [*self.builtins]
        for server_name, server in self.servers.items():
            names.extend(qualified(server_name, spec.name) for spec in server.tools)
        return names

    def grant(self, server_name: str, server: ToolServer) -> None:
        """Add a tool server before the session opens."""
        self.servers[server_name] = server


# -- what a running session emits --------------------------------------------------------------


@dataclass
class SessionOpened:
    """The session exists and has an identity, if the backend assigns one."""

    session_id: str | None = None


@dataclass
class Say:
    """The model said something."""

    text: str


@dataclass
class Call:
    """The model called a tool."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelReply:
    """One assistant message: text and tool calls, in the order they were produced."""

    parts: list[Any] = field(default_factory=list)
    model: str = ""


@dataclass
class ToolReturned:
    """What a tool call produced, flattened to text."""

    id: str
    text: str
    is_error: bool = False


@dataclass
class StageDone:
    """The exchange is over. The raw facts; Stage turns them into words."""

    outcome: str = "success"
    turns: int = 0
    cost_usd: float | None = None
    session_id: str | None = None
    models: set[str] = field(default_factory=set)
    is_error: bool = False
    api_error_status: Any = None
    errors: list[Any] = field(default_factory=list)


# -- what a backend implements -----------------------------------------------------------------


@runtime_checkable
class HarnessSession(Protocol):
    """One open session. Backends with their own loop implement this around it."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def send(self, message: str) -> None: ...

    def replies(self) -> AsyncIterator[Any]:
        """Everything the session emits for the message just sent, ending with StageDone."""
        ...


@runtime_checkable
class HarnessBackend(Protocol):
    """A way of running stages. Selected by name through the registry."""

    name: str
    default_model: str

    def create(self, spec: SessionSpec) -> HarnessSession: ...

    def can_drive(self, model: str) -> bool:
        """Whether this backend can actually run the named model.

        A backend handed a model it cannot reach must refuse loudly here. Left unchecked it
        produces a session that answers nothing, which downstream reads as an agent that ignored
        the person rather than as a configuration mistake.
        """
        ...
