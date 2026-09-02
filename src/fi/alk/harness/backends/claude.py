"""The Claude Code backend: the loop this harness grew up on.

Adapts the neutral ``SessionSpec`` to ``ClaudeAgentOptions`` exactly the way the stages built
them before the seam existed: same gate hooks, same permission callback, same provider env,
same disallowed list. With ``ALK_HARNESS`` unset this backend runs, so nothing here may drift
from what the stages did on their own.

Claude Code supplies Read/Glob/Grep and AskUserQuestion itself, so builtins are granted by
name rather than implemented here.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

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
    MOST_WORKERS_AT_ONCE,
    Call,
    ModelReply,
    Say,
    SessionOpened,
    SessionSpec,
    StageDone,
    ToolReturned,
    ToolServer,
    qualified,
)

DEFAULT_MODEL = "claude-sonnet-4-6"

# What this SDK calls the tool that runs a worker. The harness calls the capability
# ``Delegate``; only this line knows the vendor's name for it.
_DELEGATION_TOOL = "Agent"


def _ask_only(ask: Any) -> Any:
    """Allow everything, but route the one tool that has a person on the other end.

    An ungated stage is trusted with its tools; it is not therefore talking to nobody.
    """

    async def gate(tool_name: str, payload: dict[str, Any], context: Any) -> Any:
        from claude_agent_sdk.types import PermissionResultAllow

        if tool_name == "AskUserQuestion":
            return await ask(tool_name, payload, context)
        return PermissionResultAllow(updated_input=payload)

    return gate


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


def _flattened(content: Any) -> str:
    """A tool result's content as one string, however the SDK packaged it."""
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return content if isinstance(content, str) else str(content)


class ClaudeSession:
    """One Claude Code session, translated to the neutral reply vocabulary."""

    def __init__(self, options: ClaudeAgentOptions) -> None:
        self._options = options
        self._client: ClaudeSDKClient | None = None

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
                yield reply

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
            return [
                StageDone(
                    outcome=received.subtype,
                    turns=received.num_turns,
                    cost_usd=received.total_cost_usd,
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


class ClaudeBackend:
    name = "claude"
    default_model = DEFAULT_MODEL

    def can_drive(self, model: str) -> bool:
        return "claude" in (model or "").lower()

    def create(self, spec: SessionSpec) -> ClaudeSession:
        from ..config import (
            UNWANTED,
            gate_hooks,
            permission_gate,
            provider_env,
            thinking_config,
        )

        allowed = [
            *spec.builtins,
            *(
                qualified(server_name, tool_spec.name)
                for server_name, server in spec.servers.items()
                for tool_spec in server.tools
            ),
        ]
        servers = {
            server_name: _sdk_server(server)
            for server_name, server in spec.servers.items()
        }
        agents: dict[str, Any] = {}
        for name, worker in spec.workers.items():
            worker_servers = worker.servers or spec.servers
            for server_name, server in worker_servers.items():
                servers.setdefault(server_name, _sdk_server(server))
            agents[name] = AgentDefinition(
                description=worker.description,
                prompt=worker.instructions,
                tools=[
                    *(worker.builtins or spec.builtins),
                    *(
                        qualified(server_name, tool_spec.name)
                        for server_name, server in worker_servers.items()
                        for tool_spec in server.tools
                    ),
                ],
                mcpServers=list(worker_servers),
                model=worker.model or "inherit",
                maxTurns=worker.max_turns,
                # Blocking, so the call returns the worker's result. Left unset these launch in
                # the background and the caller is told it will be notified later: a stage that
                # dealt fifty scenarios across five writers then ended at its next turn, killing
                # all five, and reported success having saved one. The parent has nothing useful
                # to do while a slice is written, and it must not finish before the work does.
                background=False,
                # Only when asked for: the field has its own default and passing a blank
                # through would override it with nothing.
                **({"effort": worker.effort} if worker.effort else {}),
            )
        if agents:
            # The delegation tool is named for the model, and it has to be granted here or the
            # gate below refuses the very call these workers exist to receive.
            allowed = [*allowed, _DELEGATION_TOOL]
        env = dict(provider_env(spec.model))
        if agents:
            env.setdefault(
                "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", str(MOST_WORKERS_AT_ONCE)
            )
            # One level only. A worker that delegates again multiplies the fan-out by a factor
            # nothing in the stage accounted for, and the depth is free to raise later.
            env.setdefault("CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH", "1")
        # A declared worker is reached through the SDK's own sub-agent tool, which asks *which*
        # kind to run. Nothing otherwise tells the model that ours exists, and left to guess it
        # dispatches the generic kind: that one launches detached, answers "launched
        # successfully, you will be notified", and returns nothing, so a stage dealt eight slices,
        # dispatched eight agents holding none of its tools, declared success and exited having
        # written nothing. On the other backend a worker is a plain tool in the list, which is why
        # this only ever bit here. Naming them costs a line and removes the guess.
        said = spec.system_prompt
        if spec.workers:
            said += (
                "\n\n## Your workers\n\nYou have these sub-agents: "
                + ", ".join(sorted(spec.workers))
                + ". Dispatch one by naming it exactly as the kind of sub-agent to run. They "
                "return what they produced when they finish. Do not dispatch a general-purpose "
                "sub-agent instead: that kind runs detached, holds none of your tools, and its "
                "work is lost when you finish your turn."
            )
        options = ClaudeAgentOptions(
            system_prompt=said,
            allowed_tools=allowed,
            mcp_servers=servers,
            setting_sources=[],
            max_turns=spec.max_turns,
            model=spec.model,
            env=env,
        )
        if agents:
            options.agents = agents
        if spec.cwd is not None:
            options.cwd = spec.cwd
        if not spec.gated:
            # An ungated stage is given the host's whole tool list on purpose, and an unattended
            # run cannot answer a prompt, so approval has to be settled here rather than left to
            # the default. The boundary for such a stage is the sandbox it runs in, not the tool
            # list: it is reading an agent's own repository to write tests against it, and every
            # artifact it produces still goes through the three gates before it is kept.
            options.permission_mode = "bypassPermissions"
            if spec.permission_override is not None:
                options.can_use_tool = spec.permission_override
            elif spec.ask is not None:
                # Ungated does not mean unattended. In a conversation there is a person on the
                # other side, and the stage asking them something is the point of keeping it
                # open. Without this the question is approved silently and never reaches them.
                options.can_use_tool = _ask_only(spec.ask)
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
        return ClaudeSession(options)
