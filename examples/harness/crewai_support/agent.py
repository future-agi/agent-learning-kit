import os

# CrewAI starts its telemetry exporter and first-run trace collection when it is imported, so
# the opt-outs have to be in the environment before the import below. `setdefault` leaves a
# value the operator set on purpose alone.
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

from crewai import LLM, Agent  # noqa: E402
from crewai.tools import tool  # noqa: E402

MODEL = os.getenv("OPENAI_MODEL", "vertex_ai/gemini-3.7-flash")

_ORDERS = {
    "A1001": {"item": "trail running shoes", "status": "delivered", "return": None},
    "A1002": {"item": "rain jacket", "status": "shipped", "return": None},
    "A1003": {"item": "water bottle", "status": "delivered", "return": None},
}


def look_up_order(order_id: str) -> str:
    """Look up an order by its id and report its item, status and any return."""
    order = _ORDERS.get(order_id)
    if order is None:
        return f"No order {order_id} exists."
    returned = order["return"] or "none"
    return f"Order {order_id}: {order['item']}, status {order['status']}, return: {returned}."


def start_return(order_id: str, reason: str) -> str:
    """Start a return for a delivered order, giving the customer's reason."""
    order = _ORDERS.get(order_id)
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


async def agent_callback(agent_input):
    # Each request runs in its own event loop on its own thread, and CrewAI's clients are
    # bound to the loop they were first used in, so the model and the agent live for one
    # call. `tool()` wraps here, not at import, so the module's functions stay the plain
    # ones the kit's tool tracer is bound to.
    # `custom_openai` keeps the model on the OpenAI-compatible chat-completions client with
    # the name unchanged. A model string would be routed by its prefix, and
    # `openai/vertex_ai/...` falls back to LiteLLM, which cannot take a gateway base URL.
    llm = LLM(
        model=MODEL,
        custom_openai=True,
        base_url=os.getenv("OPENAI_BASE_URL"),
        api_key=os.getenv("OPENAI_API_KEY"),
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
