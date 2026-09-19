"""The Claude Code backend: the loop this harness grew up on.

Adapts the neutral ``SessionSpec`` to ``ClaudeAgentOptions`` exactly the way the stages built
them before the seam existed: same gate hooks, same permission callback, same provider env,
same disallowed list. With ``ALK_HARNESS`` unset this backend runs, so nothing here may drift
from what the stages did on their own.

Claude Code supplies Read/Glob/Grep and AskUserQuestion itself, so builtins are granted by
name rather than implemented here.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any, AsyncIterator, Callable

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SdkMcpTool,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
)

from .base import (
    DELEGATE_TOOL,
    MOST_WORKERS_AT_ONCE,
    Call,
    ModelReply,
    Say,
    SessionOpened,
    SessionSpec,
    StageDone,
    ToolReturned,
    ToolServer,
    ToolSpec,
    WorkerSpec,
    qualified,
)

DEFAULT_MODEL = "claude-sonnet-4-6"

# What this SDK calls the tool that runs a worker. It reports itself under both names depending
# on the version, and the gate matches on the reported name, so both are granted or delegation
# is denied by the very gate the workers exist to pass.
DELEGATION_TOOLS = ("Agent", "Task")


def _sdk_server(server: ToolServer) -> Any:
    """A ToolServer as the in-process MCP server the SDK routes calls to."""
    return create_sdk_mcp_server(
        name=server.name,
        version=server.version,
        tools=[
            SdkMcpTool(
                name=spec.name,
                description=spec.description,
                input_schema=spec.input_schema,
                handler=spec.handler,
            )
            for spec in server.tools
        ],
    )


def _definition(worker: WorkerSpec, parent: SessionSpec) -> AgentDefinition:
    """A ``WorkerSpec`` as this SDK's own sub-agent definition.

    ``model="inherit"`` rather than a name: a worker doing the parent's kind of work on a
    different model is a difference nobody asked for and nothing on screen would explain.
    """
    tools = [name for name in worker.granted(parent) if name != DELEGATE_TOOL]
    if DELEGATE_TOOL in (worker.builtins or parent.builtins):
        tools.extend(DELEGATION_TOOLS)
    return AgentDefinition(
        description=worker.description,
        prompt=worker.instructions,
        tools=tools,
        mcpServers=list(worker.servers or parent.servers),
        model=worker.model or "inherit",
        maxTurns=worker.max_turns,
        # Blocking, so the delegating turn receives the worker's report rather than a handle to
        # a run that outlives the stage that started it.
        background=False,
    )


# The server and tool a session publishes when it runs its workers itself. Qualified the way
# every other harness tool is, so the gate, the ledger and the skills all read it the same way.
DELEGATION_SERVER = "workers"
DELEGATION_HANDOFF = "delegate"


class _Desk:
    """Runs a stage's workers in child sessions of its own, instead of the CLI's sub-agents.

    The bundled CLI has its own sub-agent tool, and on a gateway route it does not finish: a
    worker with no tools reports back, and a worker that calls one runs, executes the tool, and
    then the delegating turn never receives a result. The gateway answered every request it was
    given and the CLI simply stopped asking, so the stall is inside a lifecycle nobody documents.

    A stage only ever needed "brief these workers and give me what they wrote". That is a tool,
    and a child session is a thing this backend already knows how to build, so the capability is
    put back on the two pieces that demonstrably work rather than on an internal we cannot see.

    What a worker did still reaches the stage: its calls are replayed onto the parent's stream
    carrying its name, and its tokens and cost are folded into the parent's ``StageDone``. Without
    that the ledger would attribute a stage's whole spend to the loop and report a fraction of it.
    """

    def __init__(
        self, spec: SessionSpec, open_child: Callable[[SessionSpec], "ClaudeSession"]
    ) -> None:
        self._spec = spec
        self._open_child = open_child
        self._seen: list[Any] = []
        self._lock = asyncio.Lock()
        # The ceiling is the harness's, not this backend's: a stage plans against the same number.
        self._room = asyncio.Semaphore(MOST_WORKERS_AT_ONCE)
        self.tokens_in = 0
        self.tokens_out = 0
        self.tokens_cached = 0
        self.cost_usd = 0.0
        self.models: set[str] = set()

    @property
    def tool_name(self) -> str:
        return qualified(DELEGATION_SERVER, DELEGATION_HANDOFF)

    def server(self) -> ToolServer:
        """The one tool a delegating stage is given, described by the workers it can run."""
        roster = "\n".join(
            f"  - `{name}`: {worker.description}"
            for name, worker in self._spec.workers.items()
        )
        return ToolServer(
            name=DELEGATION_SERVER,
            tools=[
                ToolSpec(
                    name=DELEGATION_HANDOFF,
                    description=(
                        "Hand part of this stage to a worker, which does it in a session of its "
                        "own and reports back. Name the worker and write its brief: what to "
                        "cover, how much of it, and what makes its part different from what the "
                        "others were given. The call returns what that worker wrote, so several "
                        "briefs in one turn run at the same time.\n\n"
                        f"The workers you may run:\n{roster}"
                    ),
                    input_schema={"worker": str, "brief": str},
                    handler=self._handle,
                )
            ],
        )

    async def _handle(self, args: dict[str, Any]) -> dict[str, Any]:
        named = str(args.get("worker") or "").strip()
        brief = str(args.get("brief") or "").strip()
        worker = self._spec.workers.get(named)
        if worker is None:
            return _said(
                f"there is no worker called {named!r}. The ones you may run are: "
                + ", ".join(self._spec.workers)
                or "none",
                is_error=True,
            )
        if not brief:
            return _said(
                f"{named} was given no brief, so there is nothing for it to do. Say what to "
                "cover, how much, and what makes it different from the other briefs.",
                is_error=True,
            )
        try:
            return _said(await self._run(named, worker, brief))
        except Exception as broke:  # noqa: BLE001 - a worker that dies is the stage's news
            return _said(f"{named} did not finish: {type(broke).__name__}: {broke}", is_error=True)

    async def _run(self, named: str, worker: WorkerSpec, brief: str) -> str:
        async with self._room:
            session = self._open_child(_child_of(self._spec, worker))
            await session.start()
            try:
                await session.send(brief)
                said: list[str] = []
                seen: list[Any] = []
                async for reply in session.replies():
                    if isinstance(reply, ModelReply):
                        parts = []
                        for part in reply.parts:
                            if isinstance(part, Call):
                                # Stamped here because nothing downstream can know it: a child
                                # session has no idea it is one, and the ledger is keyed on this.
                                parts.append(dataclasses.replace(part, by=named))
                            else:
                                if isinstance(part, Say) and part.text.strip():
                                    said.append(part.text)
                                parts.append(part)
                        seen.append(dataclasses.replace(reply, parts=parts))
                    elif isinstance(reply, ToolReturned):
                        seen.append(reply)
                    elif isinstance(reply, StageDone):
                        self.tokens_in += reply.tokens_in
                        self.tokens_out += reply.tokens_out
                        self.tokens_cached += reply.tokens_cached
                        self.cost_usd += reply.cost_usd or 0.0
                        self.models.update(reply.models)
                        if reply.outcome != "success" and not said:
                            said.append(
                                f"{named} ended on {reply.outcome} with nothing written."
                            )
                async with self._lock:
                    self._seen.extend(seen)
                return "\n".join(one.strip() for one in said if one.strip()).strip() or (
                    f"{named} finished without saying anything."
                )
            finally:
                await session.stop()

    def drain(self) -> list[Any]:
        """What the workers have done since this was last asked, for the parent's own stream."""
        taken, self._seen = self._seen, []
        return taken


def _said(text: str, *, is_error: bool = False) -> dict[str, Any]:
    """A tool result in the shape every harness tool already returns."""
    reply: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        reply["is_error"] = True
    return reply


def _child_of(parent: SessionSpec, worker: WorkerSpec) -> SessionSpec:
    """The session one worker runs in.

    Everything the worker did not name falls back to the parent's, which is what makes a worker a
    part of its stage rather than a session with its own opinions. It gets no workers of its own:
    a stage hands work out once, and a worker that could hand out again would spend the stage's
    budget somewhere nobody is watching.
    """
    return SessionSpec(
        system_prompt=worker.instructions,
        servers=dict(worker.servers or parent.servers),
        builtins=tuple(
            name for name in (worker.builtins or parent.builtins) if name != DELEGATE_TOOL
        ),
        cwd=parent.cwd,
        max_turns=worker.max_turns,
        model=worker.model or parent.model,
        ask=parent.ask,
        gated=parent.gated,
        thinking=parent.thinking,
        permission_override=parent.permission_override,
        idle_timeout_seconds=parent.idle_timeout_seconds,
    )


def _flattened(content: Any) -> str:
    """A tool result's content as one string, however the SDK packaged it."""
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return content if isinstance(content, str) else str(content)


class ClaudeSession:
    """One Claude Code session, translated to the neutral reply vocabulary."""

    def __init__(self, options: ClaudeAgentOptions, desk: "_Desk | None" = None) -> None:
        self._options = options
        self._client: ClaudeSDKClient | None = None
        # None for every session that runs no workers, which is every session this backend built
        # before delegation existed.
        self._desk = desk

    async def start(self) -> None:
        self._client = ClaudeSDKClient(options=self._options)
        await self._client.connect()

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def send(self, message: str) -> None:
        if self._client is None:
            raise RuntimeError("session is not open")
        await self._client.query(message)

    async def replies(self) -> AsyncIterator[Any]:
        if self._client is None:
            raise RuntimeError("session is not open")
        async for received in self._client.receive_response():
            for reply in self._translate(received):
                # A worker's calls belong on this stream before the stage is told it ended, or
                # the ledger sees the totals and never sees what they were spent on.
                if self._desk is not None and isinstance(reply, StageDone):
                    for worked in self._desk.drain():
                        yield worked
                    reply = self._with_workers(reply)
                yield reply

    def _with_workers(self, done: StageDone) -> StageDone:
        """The stage's own figures plus everything its workers spent inside it."""
        desk = self._desk
        if desk is None:
            return done
        priced = done.cost_usd
        if desk.cost_usd:
            priced = (priced or 0.0) + desk.cost_usd
        return dataclasses.replace(
            done,
            tokens_in=done.tokens_in + desk.tokens_in,
            tokens_out=done.tokens_out + desk.tokens_out,
            tokens_cached=done.tokens_cached + desk.tokens_cached,
            cost_usd=priced,
            models=done.models | desk.models,
        )

    def _translate(self, received: Any) -> list[Any]:
        if isinstance(received, SystemMessage):
            data = received.data if isinstance(received.data, dict) else {}
            return [SessionOpened(session_id=data.get("session_id"))]
        if isinstance(received, AssistantMessage):
            parts: list[Any] = []
            for block in received.content:
                if isinstance(block, TextBlock):
                    parts.append(Say(text=block.text))
                elif isinstance(block, ToolUseBlock):
                    parts.append(
                        Call(id=block.id, name=block.name, arguments=block.input)
                    )
            return [ModelReply(parts=parts, model=getattr(received, "model", "") or "")]
        if isinstance(received, ResultMessage):
            # subtype alone is not the outcome. A call that failed upstream still arrives with
            # subtype "success", so the error facts ride along and Stage decides what failed.
            counted = _tokens(getattr(received, "model_usage", None))
            return [
                StageDone(
                    outcome=received.subtype,
                    turns=received.num_turns,
                    cost_usd=_cost(
                        getattr(received, "model_usage", None),
                        counted,
                        received.total_cost_usd,
                    ),
                    **counted,
                    session_id=received.session_id,
                    models=set(getattr(received, "model_usage", None) or {}),
                    is_error=bool(getattr(received, "is_error", False)),
                    api_error_status=getattr(received, "api_error_status", None),
                    errors=list(getattr(received, "errors", None) or []),
                )
            ]
        blocks = getattr(received, "content", None)
        if isinstance(blocks, list):
            returned = []
            for block in blocks:
                if isinstance(block, ToolResultBlock):
                    returned.append(
                        ToolReturned(
                            id=block.tool_use_id,
                            text=_flattened(block.content),
                            is_error=bool(getattr(block, "is_error", False)),
                        )
                    )
            return returned
        return []


def _cost(model_usage: Any, counted: dict[str, int], reported: float | None) -> float | None:
    """What the run cost, priced here rather than taken from the loop that ran it.

    The CLI prices every call from its own table, which holds Claude models. Given a Gemini id it
    does not recognise, it still returns a number, and that number was 14x the truth on the first
    run measured. Where the harness has a price for the model it is the one that stands; where it
    has none, the CLI's figure is passed through rather than replaced by silence.
    """
    from .vertex_gemini import priced

    named = [str(name) for name in (model_usage or {})]
    ours = [
        priced(name, counted["tokens_in"], counted["tokens_out"], counted["tokens_cached"])
        for name in named
    ]
    known = [one for one in ours if one is not None]
    if not known:
        return reported
    # One price per model, and a stage runs on one: summing would multiply the same tokens by
    # however many ids the SDK happened to report.
    return max(known)


def _tokens(model_usage: Any) -> dict[str, int]:
    """Input and output tokens across every model a stage used, for the ledger to audit against.

    Read defensively: this is the SDK's shape, not ours, and a stage must not fail over accounting.
    """
    read = 0
    written = 0
    cached = 0
    for usage in (model_usage or {}).values():
        if isinstance(usage, dict):
            read += int(usage.get("inputTokens") or usage.get("input_tokens") or 0)
            written += int(usage.get("outputTokens") or usage.get("output_tokens") or 0)
            cached += int(
                usage.get("cacheReadInputTokens")
                or usage.get("cache_read_input_tokens")
                or 0
            )
        else:
            read += int(getattr(usage, "input_tokens", 0) or 0)
            written += int(getattr(usage, "output_tokens", 0) or 0)
            cached += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    # The SDK reports cache reads beside fresh input, the way the Messages API does; the ledger
    # reads tokens_cached as a part of tokens_in. Left unadded, a turn served almost entirely from
    # cache looks like a turn that barely sent anything.
    return {"tokens_in": read + cached, "tokens_out": written, "tokens_cached": cached}


class ClaudeBackend:
    name = "claude"
    default_model = DEFAULT_MODEL

    def can_drive(self, model: str) -> bool:
        return "claude" in (model or "").lower()

    def create(self, spec: SessionSpec) -> ClaudeSession:
        return ClaudeSession(self._options(spec, None))

    def _options(self, spec: SessionSpec, desk: "_Desk | None") -> ClaudeAgentOptions:
        """The SDK options for one session, with or without a desk running its workers.

        ``desk`` is None on the route that hands workers to the SDK's own sub-agents, which is
        every session this backend built before delegation moved into the harness.
        """
        from ..config import (
            UNWANTED,
            gate_hooks,
            permission_gate,
            provider_env,
            thinking_config,
        )

        # Everything the session or any of its workers may call. A worker's calls are made
        # inside this session, so building the gate from the parent's tools alone would deny a
        # worker the very tools it was given.
        allowed = [name for name in spec.granted_anywhere() if name != DELEGATE_TOOL]
        servers = {**spec.servers}
        for worker in spec.workers.values():
            servers.update(worker.servers)
        environment = dict(provider_env(spec.model))
        if spec.workers and desk is None:
            allowed.extend(DELEGATION_TOOLS)
            environment["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] = str(MOST_WORKERS_AT_ONCE)
        if desk is not None:
            # A worker's own tools are run by its own session, so the parent is granted the one
            # tool that hands work out and nothing the workers hold.
            allowed = [name for name in spec.granted() if name != DELEGATE_TOOL]
            allowed.append(desk.tool_name)
            servers = {**spec.servers, DELEGATION_SERVER: desk.server()}
        options = ClaudeAgentOptions(
            system_prompt=spec.system_prompt,
            allowed_tools=allowed,
            mcp_servers={
                server_name: _sdk_server(server)
                for server_name, server in servers.items()
            },
            agents=(
                {
                    name: _definition(worker, spec)
                    for name, worker in spec.workers.items()
                }
                or None
                if desk is None
                else None
            ),
            setting_sources=[],
            max_turns=spec.max_turns,
            model=spec.model,
            env=environment,
        )
        if spec.cwd is not None:
            options.cwd = spec.cwd
        if spec.gated:
            # Not acceptEdits: that auto-approves Edit and Write before the permission callback
            # is consulted, so a stage could rewrite an artifact by hand and skip the tool whose
            # whole job is to validate that change.
            options.permission_mode = "default"
            options.disallowed_tools = list(UNWANTED)
            options.hooks = gate_hooks(allowed)
            options.can_use_tool = spec.permission_override or permission_gate(
                spec.ask, allowed
            )
        if spec.thinking:
            options.thinking = thinking_config()
        return options


# The Gemini model this variant runs on unless one is named. Carries its route, because the
# gateway resolves a bare id to a different provider than the prefixed one, and kept here rather
# than borrowed from the ADK backend so that changing one route's default never changes the
# other's.
GATEWAY_DEFAULT_MODEL = "vertex_ai/gemini-3.5-flash"


class ClaudeGatewayBackend(ClaudeBackend):
    """The same loop, pointed at a gateway that speaks Anthropic Messages in front of Gemini.

    A backend of its own rather than a mode of the one above, because the two differ in the only
    thing that matters here: which provider gets billed. Selecting it by name is a decision
    somebody made; a mode that turns itself on from an env var is a decision nobody made.

    It refuses to start without a gateway, and refuses any model the harness may not spend on.
    Both refusals are here rather than at the first call because the failure they prevent is a
    bill, and a bill is only visible after the run.
    """

    name = "claude-gemini"
    default_model = GATEWAY_DEFAULT_MODEL

    def can_drive(self, model: str) -> bool:
        from ..config import refuse_a_model_we_cannot_afford

        try:
            refuse_a_model_we_cannot_afford(model)
        except ValueError:
            return False
        return True

    def create(self, spec: SessionSpec) -> ClaudeSession:
        import os

        if not os.environ.get("ALK_HARNESS_GATEWAY_URL", "").strip():
            raise ValueError(
                "the claude-gemini backend needs ALK_HARNESS_GATEWAY_URL pointing at a gateway "
                "that serves POST /v1/messages from a Gemini model. Without it the CLI would "
                "reach Anthropic directly, which is the bill this backend exists to prevent."
            )
        if not spec.workers:
            return ClaudeSession(self._options(spec, None))
        # This route runs its workers itself. The SDK's own sub-agent never returns a result to
        # the delegating turn once the worker calls a tool, and a child session is a thing this
        # backend already builds correctly.
        desk = _Desk(spec, lambda child: ClaudeSession(self._options(child, None)))
        return ClaudeSession(self._options(spec, desk), desk)
