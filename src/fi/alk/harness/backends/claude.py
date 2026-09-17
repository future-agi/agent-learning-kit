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
    WorkerSpec,
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
                    **_tokens(getattr(received, "model_usage", None)),
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


def _tokens(model_usage: Any) -> dict[str, int]:
    """Input and output tokens across every model a stage used, for the ledger to audit against.

    Read defensively: this is the SDK's shape, not ours, and a stage must not fail over accounting.
    """
    read = 0
    written = 0
    for usage in (model_usage or {}).values():
        if isinstance(usage, dict):
            read += int(usage.get("inputTokens") or usage.get("input_tokens") or 0)
            written += int(usage.get("outputTokens") or usage.get("output_tokens") or 0)
        else:
            read += int(getattr(usage, "input_tokens", 0) or 0)
            written += int(getattr(usage, "output_tokens", 0) or 0)
    return {"tokens_in": read, "tokens_out": written}


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

        # Everything the session or any of its workers may call. A worker's calls are made
        # inside this session, so building the gate from the parent's tools alone would deny a
        # worker the very tools it was given.
        allowed = [name for name in spec.granted_anywhere() if name != DELEGATE_TOOL]
        servers = {**spec.servers}
        for worker in spec.workers.values():
            servers.update(worker.servers)
        environment = dict(provider_env(spec.model))
        if spec.workers:
            allowed.extend(DELEGATION_TOOLS)
            environment["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] = str(MOST_WORKERS_AT_ONCE)
        options = ClaudeAgentOptions(
            system_prompt=spec.system_prompt,
            allowed_tools=allowed,
            mcp_servers={
                server_name: _sdk_server(server)
                for server_name, server in servers.items()
            },
            agents={
                name: _definition(worker, spec)
                for name, worker in spec.workers.items()
            }
            or None,
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
        return ClaudeSession(options)
