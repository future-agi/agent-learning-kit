import contextvars
import copy
import os

# CrewAI starts its telemetry exporter and first-run trace collection when it is imported, so
# the opt-outs have to be in the environment before the import below. `setdefault` leaves a
# value the operator set on purpose alone.
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

from crewai import LLM, Agent  # noqa: E402
from crewai.tools import tool  # noqa: E402

MODEL = os.getenv("OPENAI_MODEL", "vertex_ai/gemini-3.7-flash")

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


def _setting(name: str) -> str:
    # Blank counts as unset.
    return os.getenv(name, "").strip()


async def agent_callback(agent_input):
    _CONVERSATION.set(str(getattr(agent_input, "thread_id", "") or ""))
    # Each request runs in its own event loop on its own thread, and CrewAI's clients are
    # bound to the loop they were first used in, so the model and the agent live for one
    # call. `tool()` wraps here, not at import, so the module's functions stay the plain
    # ones the kit's tool tracer is bound to.
    # `custom_openai` is needed because the default `vertex_ai/...` name has no `openai/`
    # prefix and `vertex_ai` isn't a CrewAI provider alias, so without it the name goes to
    # LiteLLM whatever `base_url` says, and LiteLLM isn't installed here. It also stops CrewAI
    # switching to the Responses API on a "responses-only" error; the gateway serves chat only.
    # The same rule as the other reference agents: a key needs the endpoint it belongs to.
    if _setting("OPENAI_API_KEY") and not _setting("OPENAI_BASE_URL"):
        raise RuntimeError(
            "OPENAI_API_KEY is set but OPENAI_BASE_URL is empty: set OPENAI_BASE_URL to the "
            "endpoint the key belongs to."
        )
    # Passed straight in, not held in a variable, so no failure later in the turn shows the key
    # in this frame.
    llm = LLM(
        model=MODEL,
        custom_openai=True,
        base_url=_setting("OPENAI_BASE_URL") or None,
        api_key=_setting("OPENAI_API_KEY") or None,
    )
    agent = Agent(
        role="Customer support agent",
        goal="Help customers of an online store with their orders and returns.",
        backstory=(
            "You work for an online store. Use the tools to look up orders and start "
            "returns, and never guess an order's status. Answer briefly."
        ),
        tools=[tool(look_up_order), tool(start_return), tool(return_policy)],
        llm=llm,
    )
    # The runner puts the new user message at the end of `messages`. CrewAI takes the last
    # user message as the request and the turns before it as history, so the whole
    # conversation goes in once and the new message is not appended again.
    messages = [
        {"role": m["role"], "content": m["content"]}
        for m in agent_input.messages
        if m["role"] in ("user", "assistant")
    ]
    # `kickoff_async`, because the sync `kickoff` inside a running loop returns a coroutine.
    result = await agent.kickoff_async(messages)
    # Content only: tool calls in the reply would be run by the caller against its own
    # world and the agent called again.
    return {"content": str(result.raw)}
