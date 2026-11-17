import asyncio
import contextvars
import copy
import os
import shutil
import tempfile

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ProcessError,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
    tool,
)

MODEL = os.getenv("ANTHROPIC_MODEL", "vertex_ai/gemini-3.7-flash")
# Below the kit's 300 s per-turn deadline, so a hung CLI is stopped and its folder removed.
TURN_TIMEOUT_SECONDS = 280.0
_MESSAGE_LIMIT = 500

_SEED_ORDERS = {
    "A1001": {"item": "trail running shoes", "status": "delivered", "return": None},
    "A1002": {"item": "rain jacket", "status": "shipped", "return": None},
    "A1003": {"item": "water bottle", "status": "delivered", "return": None},
}

# Each conversation gets its own copy of the orders, so a return started in one scenario is
# not there in the next. The callback names the conversation; the tools read it.
# A conversation is the thread the kit names, one per scenario, so a scenario run twice on one
# agent process would see its first run's returns.
_CONVERSATION = contextvars.ContextVar("conversation", default="")
_ORDERS_BY_CONVERSATION: dict[str, dict] = {}


def _orders() -> dict:
    conversation = _CONVERSATION.get()
    if conversation not in _ORDERS_BY_CONVERSATION:
        _ORDERS_BY_CONVERSATION[conversation] = copy.deepcopy(_SEED_ORDERS)
    return _ORDERS_BY_CONVERSATION[conversation]


SYSTEM_PROMPT = (
    "You are a customer support agent for an online store. Use the tools to look up "
    "orders and start returns, and never guess an order's status. Answer briefly."
)

# The tracer binds to these plain module-level functions, not to the async `@tool` handlers
# below (which take an `args` dict): a traced coroutine would record an extra entry at every
# suspension and the SDK's argument and result wrappers instead of the tool's own.


def look_up_order(order_id: str) -> str:
    """Look up an order by its id and report its item, status and any return."""
    order = _orders().get(order_id)
    if order is None:
        return f"No order {order_id} exists."
    returned = order["return"] or "none"
    return f"Order {order_id}: {order['item']}, status {order['status']}, return: {returned}."


def start_return(order_id: str, reason: str) -> str:
    """Start a return for a delivered order, giving the customer's reason."""
    order = _orders().get(order_id)
    if order is None:
        return f"No order {order_id} exists."
    if order["status"] != "delivered":
        return f"Order {order_id} is {order['status']} and cannot be returned yet."
    if order["return"] is not None:
        return f"Order {order_id} already has a return started."
    order["return"] = reason
    return f"Return started for order {order_id}: {reason}."


def return_policy() -> str:
    """State the store's return policy."""
    return "Delivered items can be returned within 30 days for a full refund."


def _text(result: str) -> dict:
    return {"content": [{"type": "text", "text": result}]}


@tool("look_up_order", look_up_order.__doc__, {"order_id": str})
async def _look_up_order_tool(args):
    return _text(look_up_order(args["order_id"]))


@tool("start_return", start_return.__doc__, {"order_id": str, "reason": str})
async def _start_return_tool(args):
    return _text(start_return(args["order_id"], args["reason"]))


@tool("return_policy", return_policy.__doc__, {})
async def _return_policy_tool(args):
    return _text(return_policy())


SERVER = create_sdk_mcp_server(
    name="support",
    version="1.0.0",
    tools=[_look_up_order_tool, _start_return_tool, _return_policy_tool],
)

ALLOWED_TOOLS = [
    "mcp__support__look_up_order",
    "mcp__support__start_return",
    "mcp__support__return_policy",
]


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(
        isinstance(part, dict) and part.get("type") == "text" for part in content
    ):
        return "".join(str(part.get("text", "")) for part in content)
    return str(content)


def _prompt(messages) -> str:
    # The SDK sends only user frames, so earlier turns go in as a transcript.
    if not messages:
        raise ValueError("agent_input.messages is empty")
    *earlier, last = messages
    if last.get("role") != "user":
        raise ValueError("the last message must be from the user")
    turns = [
        f"{'User' if m['role'] == 'user' else 'Assistant'}: {_content_text(m.get('content'))}"
        for m in earlier
        if m.get("role") in ("user", "assistant")
    ]
    new = _content_text(last.get("content"))
    if not turns:
        return new
    return "Conversation so far:\n" + "\n".join(turns) + "\n\nUser: " + new


_REFUSAL = (
    "ANTHROPIC_AUTH_TOKEN is set but ANTHROPIC_BASE_URL is empty: set ANTHROPIC_BASE_URL to "
    "the endpoint the token belongs to."
)


def _setting(name: str) -> str:
    # Blank counts as unset.
    return os.environ.get(name, "").strip()


def _safe(text: str) -> str:
    # Error text can echo a request; keep credentials out of it, and keep it short.
    for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        secret = _setting(name)
        if len(secret) >= 8:
            text = text.replace(secret, "[redacted]")
    return text if len(text) <= _MESSAGE_LIMIT else text[:_MESSAGE_LIMIT] + "..."


def _env(config_dir: str) -> dict:
    # `ClaudeAgentOptions.env` is merged over the process's own, so the base URL and the
    # token the job supplies are inherited rather than copied here.
    env = {
        "ANTHROPIC_MODEL": MODEL,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": MODEL,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": MODEL,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": MODEL,
        "ANTHROPIC_SMALL_FAST_MODEL": MODEL,
        "CLAUDE_CODE_SUBAGENT_MODEL": MODEL,
        "CLAUDE_CODE_ENABLE_TELEMETRY": "0",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CONFIG_DIR": config_dir,
    }
    if "claude" not in MODEL.lower():
        # The CLI cannot look up a non-Claude model's context window.
        env.update(
            {
                "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT": "1",
                "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "1000000",
            }
        )
    if _setting("ANTHROPIC_AUTH_TOKEN"):
        # Gateway mode: the Bearer token must be the only credential.
        env.update(
            {
                "CLAUDE_CODE_USE_VERTEX": "",
                "CLAUDE_CODE_USE_BEDROCK": "",
                "CLAUDE_CODE_USE_FOUNDRY": "",
                "CLAUDE_CODE_USE_ANTHROPIC_AWS": "",
                "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD": "",
                "CLAUDE_CODE_USE_MANTLE": "",
                "CLAUDE_CODE_USE_GATEWAY": "",
                "ANTHROPIC_UNIX_SOCKET": "",
                "ANTHROPIC_API_KEY": "",
                "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
                "CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING": "1",
                "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
            }
        )
    return env


async def agent_callback(agent_input):
    _CONVERSATION.set(str(getattr(agent_input, "thread_id", "") or ""))
    # With no base URL the CLI would send the gateway token to Anthropic's own API. Checked on
    # every call, because the env is read per call.
    if _setting("ANTHROPIC_AUTH_TOKEN") and not _setting("ANTHROPIC_BASE_URL"):
        raise RuntimeError(_REFUSAL)
    prompt = _prompt(agent_input.messages)
    config_dir = tempfile.mkdtemp(prefix="claude-config-")
    try:
        options = ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            mcp_servers={"support": SERVER},
            allowed_tools=list(ALLOWED_TOOLS),
            tools=[],
            strict_mcp_config=True,
            setting_sources=[],
            max_turns=8,
            model=MODEL,
            env=_env(config_dir),
        )
        result = None
        last_text = ""
        try:
            async with asyncio.timeout(TURN_TIMEOUT_SECONDS) as deadline:
                # A client per turn rather than query(): observability instrumentors (traceAI's
                # among them) wrap ClaudeSDKClient, so a turn made through query() makes no spans.
                async with ClaudeSDKClient(options=options) as client:
                    await client.query(prompt)
                    async for message in client.receive_response():
                        if isinstance(message, AssistantMessage):
                            text = "".join(
                                b.text
                                for b in message.content
                                if isinstance(b, TextBlock)
                            )
                            if text:
                                last_text = text
                        elif isinstance(message, ResultMessage):
                            result = message
        except TimeoutError:
            if not deadline.expired():
                raise
            raise RuntimeError(
                f"the Claude Agent SDK turn did not finish within {TURN_TIMEOUT_SECONDS:g}s"
            ) from None
        except ProcessError as exc:
            # The SDK raises after an error result's frame; report it as the turn failing.
            if result is None or not (result.is_error or result.subtype != "success"):
                raise RuntimeError(
                    f"the Claude CLI failed: {_safe(str(exc))}"
                ) from None
    finally:
        shutil.rmtree(config_dir, ignore_errors=True)
    if result is None:
        raise RuntimeError("the Claude Agent SDK returned no result")
    if result.is_error or result.subtype != "success":
        raise RuntimeError(
            f"the Claude Agent SDK turn failed ({result.subtype}): "
            + _safe(str(result.result or last_text))
        )
    # Content only: tool calls in the reply would be run again by the caller.
    return {"content": result.result or last_text}
