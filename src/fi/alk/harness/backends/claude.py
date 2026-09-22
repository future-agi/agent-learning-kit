"""The Claude Code backend: the loop this harness grew up on.

Adapts the neutral ``SessionSpec`` to ``ClaudeAgentOptions`` exactly the way the stages built
them before the seam existed: same gate hooks, same permission callback, same provider env,
same disallowed list. With ``ALK_HARNESS`` unset this backend runs, so nothing here may drift
from what the stages did on their own.

Claude Code supplies Read/Glob/Grep and AskUserQuestion itself, so builtins are granted by
name rather than implemented here.
"""

from __future__ import annotations
import os

import os
from typing import Any, AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SdkMcpTool,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
)

from .base import (
    ASK_TOOL,
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


def _gateway_compatible_schema(value: Any) -> Any:
    """Translate JSON-Schema nullable unions to the scalar form Gemini accepts.

    Claude accepts ``type: [string, null]``. Vertex function declarations use a scalar enum for
    ``type`` and reject that otherwise-valid JSON Schema before the model can run. Optional
    properties remain optional because their names are absent from ``required``; only explicit
    null loses validation support on this provider path.
    """
    if isinstance(value, list):
        return [_gateway_compatible_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {
        key: _gateway_compatible_schema(item) for key, item in value.items()
    }
    kind = result.get("type")
    if isinstance(kind, list):
        concrete = [item for item in kind if item != "null"]
        if len(concrete) == 1:
            result["type"] = concrete[0]
    enum = result.get("enum")
    if isinstance(enum, list) and None in enum:
        result["enum"] = [item for item in enum if item is not None]
    return result


def _sdk_server(server: ToolServer, *, gateway_compatible: bool = False) -> Any:
    """A ToolServer as the in-process MCP server the SDK routes calls to."""
    return create_sdk_mcp_server(
        name=server.name,
        version=server.version,
        tools=[
            SdkMcpTool(
                name=spec.name,
                description=spec.description,
                input_schema=(
                    _gateway_compatible_schema(spec.input_schema)
                    if gateway_compatible
                    else spec.input_schema
                ),
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

    def __init__(
        self,
        options: ClaudeAgentOptions,
        *,
        reported_model: str | None = None,
        streaming: bool = False,
    ) -> None:
        self._options = options
        self._reported_model = reported_model
        self._streaming = streaming
        self._client: ClaudeSDKClient | None = None
        self._mirror_errors: list[str] = []

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

    async def interrupt(self) -> bool:
        if self._client is None:
            return False
        await self._client.interrupt()
        return True

    async def resume(self, invocation_id: str) -> None:
        del invocation_id
        raise RuntimeError(
            "Claude Agent SDK resumes sessions, not interrupted invocations"
        )

    async def replies(self) -> AsyncIterator[Any]:
        if self._client is None:
            raise RuntimeError("session is not open")
        async for received in self._client.receive_response():
            for reply in self._translate(received):
                yield reply

    def _translate(self, received: Any) -> list[Any]:
        if isinstance(received, StreamEvent):
            event = received.event
            delta = event.get("delta") if isinstance(event, dict) else None
            if (
                isinstance(event, dict)
                and event.get("type") == "content_block_delta"
                and isinstance(delta, dict)
                and delta.get("type") == "text_delta"
                and delta.get("text")
            ):
                return [
                    ModelReply(
                        parts=[
                            Say(
                                text=str(delta["text"]),
                                partial=True,
                                event_id=received.uuid,
                            )
                        ]
                    )
                ]
            return []
        if type(received).__name__ == "MirrorErrorMessage":
            self._mirror_errors.append(
                str(getattr(received, "error", "session transcript mirror failed"))
            )
            return []
        if isinstance(received, SystemMessage):
            data = received.data if isinstance(received.data, dict) else {}
            return [SessionOpened(session_id=data.get("session_id"))]
        if isinstance(received, AssistantMessage):
            parts: list[Any] = []
            for block in received.content:
                if isinstance(block, TextBlock) and not self._streaming:
                    parts.append(Say(text=block.text))
                elif isinstance(block, ToolUseBlock):
                    parts.append(
                        Call(id=block.id, name=block.name, arguments=block.input)
                    )
            return [
                ModelReply(
                    parts=parts,
                    model=self._reported_model
                    or getattr(received, "model", "")
                    or "",
                )
            ]
        if isinstance(received, ResultMessage):
            # subtype alone is not the outcome. A call that failed upstream still arrives with
            # subtype "success", so the error facts ride along and Stage decides what failed.
            errors = [
                *list(getattr(received, "errors", None) or []),
                *self._mirror_errors,
            ]
            return [
                StageDone(
                    outcome=received.subtype,
                    turns=received.num_turns,
                    cost_usd=received.total_cost_usd,
                    **_tokens(getattr(received, "model_usage", None)),
                    session_id=received.session_id,
                    models=(
                        {self._reported_model}
                        if self._reported_model
                        else set(getattr(received, "model_usage", None) or {})
                    ),
                    is_error=bool(
                        getattr(received, "is_error", False) or self._mirror_errors
                    ),
                    api_error_status=getattr(received, "api_error_status", None),
                    errors=errors,
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
        # Agent CC's native Anthropic endpoint accepts the Claude Agent SDK wire format and
        # translates it for the provider named by the model. Without a gateway, this backend
        # still only advertises models supported by Claude Code directly.
        named = (model or "").lower()
        if "claude" in named:
            return True
        if os.environ.get("AGENTCC_API_KEY", "").strip():
            return True
        gateway_ready = bool(
            os.environ.get("ALK_CLAUDE_GATEWAY_URL", "").strip()
            and os.environ.get("ALK_CLAUDE_GATEWAY_API_KEY", "").strip()
        )
        return gateway_ready and "gemini" in named

    def create(self, spec: SessionSpec) -> ClaudeSession:
        from ..config import (
            UNWANTED,
            gate_hooks,
            permission_gate,
            provider_env,
            thinking_config,
        )

        allowed = [
            *(name for name in spec.builtins if name != ASK_TOOL),
            *(
                qualified(server_name, tool_spec.name)
                for server_name, server in spec.servers.items()
                for tool_spec in server.tools
            ),
        ]
        wire_model = spec.model
        gateway_compatible = bool(
            os.environ.get("AGENTCC_API_KEY", "").strip()
            and "claude" not in wire_model.lower()
        )
        if gateway_compatible:
            # Claude Code validates model names locally before making an HTTP request. AgentCC
            # resolves this Claude-shaped alias to the requested non-Anthropic provider model.
            wire_model = os.environ.get(
                "AGENTCC_CLAUDE_MODEL_ALIAS", "claude-sonnet-4-6"
            ).strip()
        context = spec.conversation
        env = provider_env(wire_model)
        if context is not None:
            env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
            env["CLAUDE_CODE_PROJECT_DIR_NAME"] = context.session_id
            if context.config_dir:
                env["CLAUDE_CONFIG_DIR"] = context.config_dir
        options = ClaudeAgentOptions(
            tools=list(spec.builtins),
            system_prompt=spec.system_prompt,
            allowed_tools=allowed,
            mcp_servers={
                server_name: _sdk_server(
                    server, gateway_compatible=gateway_compatible
                )
                for server_name, server in spec.servers.items()
            },
            strict_mcp_config=True,
            setting_sources=[],
            max_turns=spec.max_turns,
            model=wire_model,
            env=env,
            include_partial_messages=bool(context and context.streaming),
            resume=context.resume_session_id if context is not None else None,
            session_store=context.transcript_store if context is not None else None,
            session_store_flush="eager",
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
        return ClaudeSession(
            options,
            reported_model=spec.model if gateway_compatible else None,
            streaming=bool(context and context.streaming),
        )
