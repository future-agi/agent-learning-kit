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

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol, runtime_checkable

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


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
ASK_TOOL = "AskUserQuestion"
# The capability of running part of a stage in a session of its own. Named here rather than by
# whatever each SDK calls its own delegation tool, so a stage asks for it once.
DELEGATE_TOOL = "Delegate"
KNOWN_BUILTINS = (*FILE_TOOLS, ASK_TOOL, DELEGATE_TOOL)

# The one ceiling on fan-out: how many workers may be in flight at once. A safety limit on the
# machine rather than a judgement about the work, so it stays in code while the decision to
# delegate at all stays with the model. Told to the stage as well, so it plans against it.
MOST_WORKERS_AT_ONCE = 12


@dataclass
class WorkerSpec:
    """A worker the model may run to do part of its stage, in its own session.

    Both SDKs behind the current backends can run a sub-session natively, and both do it better
    than the harness could from outside: they own the scheduling, the budget and the failure.
    So a stage declares what a worker is and each backend hands that to its own mechanism.

    ``description`` is what the model reads when deciding whether to delegate, so it says when
    the worker is worth running rather than what it is. ``instructions`` is the worker's own
    system prompt. ``servers`` and ``builtins`` are its tools, which default to the parent's
    when left empty, and are the place to withhold a tool the parent has.

    One definition covers every call: the model writes the brief when it delegates, so the same
    worker takes whichever work it decides to hand out.
    """

    description: str
    instructions: str
    servers: dict[str, ToolServer] = field(default_factory=dict)
    builtins: tuple[str, ...] = ()
    max_turns: int = 40
    # Empty inherits the parent's model, which is what a worker doing the parent's own kind of
    # work should get.
    model: str = ""

    def granted(self, parent: "SessionSpec") -> list[str]:
        """Every tool name this worker may call, falling back to the parent's."""
        servers = self.servers or parent.servers
        names = [*(self.builtins or parent.builtins)]
        for server_name, server in servers.items():
            names.extend(qualified(server_name, spec.name) for spec in server.tools)
        return names


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
    # How long this stage may go without emitting anything before it is treated as hung. Zero
    # takes the harness default. A stage whose work happens inside one long tool call needs its
    # own bound: it is working the whole time and has nothing to say while it does, so the
    # default reads honest work as a hang and kills it.
    idle_timeout_seconds: float = 0.0
    # Workers this session may run, by name. Empty is the ordinary case and every backend
    # behaves exactly as it did before the capability existed. Declaring one is what makes
    # DELEGATE_TOOL mean something; whether to use it is the model's call, not a threshold.
    workers: dict[str, WorkerSpec] = field(default_factory=dict)

    def granted(self) -> list[str]:
        """Every tool name this session may call, qualified the way the model calls it."""
        names = [*self.builtins]
        for server_name, server in self.servers.items():
            names.extend(qualified(server_name, spec.name) for spec in server.tools)
        return names

    def granted_anywhere(self) -> list[str]:
        """Every tool name this session or any of its workers may call.

        A worker's calls are made inside the parent's session, so a deny-by-default gate built
        from ``granted()`` alone refuses the worker its own tools. The gate is built from this.
        """
        names = list(self.granted())
        for worker in self.workers.values():
            names.extend(worker.granted(self))
        return list(dict.fromkeys(names))

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
    # The units behind the price, so a bill can be checked rather than trusted.
    tokens_in: int = 0
    tokens_out: int = 0
    # Input the provider served from its own cache. Included in tokens_in, and billed well below
    # fresh input, so a ledger that does not carry it overstates a rerun. Carried rather than
    # discounted here: inventing a cache rate would be a guess presented as a price.
    tokens_cached: int = 0
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
