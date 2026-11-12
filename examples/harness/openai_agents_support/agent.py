import os

from agents import (
    Agent,
    OpenAIChatCompletionsModel,
    RunConfig,
    Runner,
    function_tool,
    set_tracing_disabled,
)
from openai import AsyncOpenAI

MODEL = os.getenv("OPENAI_MODEL", "vertex_ai/gemini-3.7-flash")

# The SDK exports its own traces to OpenAI by default, using OPENAI_API_KEY, which here is
# the gateway's key: off, so no trace or key is sent to OpenAI.
set_tracing_disabled(True)

_ORDERS = {
    "A1001": {"item": "trail running shoes", "status": "delivered", "return": None},
    "A1002": {"item": "rain jacket", "status": "shipped", "return": None},
    "A1003": {"item": "water bottle", "status": "delivered", "return": None},
}


@function_tool
def look_up_order(order_id: str) -> str:
    """Look up an order by its id and report its item, status and any return."""
    order = _ORDERS.get(order_id)
    if order is None:
        return f"No order {order_id} exists."
    returned = order["return"] or "none"
    return f"Order {order_id}: {order['item']}, status {order['status']}, return: {returned}."


@function_tool
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


@function_tool
def return_policy() -> str:
    """State the store's return policy."""
    return "Delivered items can be returned within 30 days for a full refund."


agent = Agent(
    name="Support agent",
    instructions=(
        "You are a customer support agent for an online store. Use the tools to look up "
        "orders and start returns, and never guess an order's status. Answer briefly."
    ),
    tools=[look_up_order, start_return, return_policy],
)


async def agent_callback(agent_input):
    # The runner puts the new user message at the end of `messages`, so the list is the
    # whole conversation as it stands.
    input_list = [
        {"role": message["role"], "content": message["content"]}
        for message in agent_input.messages
    ]
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")
    # With no base URL the client falls back to api.openai.com and would send the key there.
    if api_key and not base_url:
        raise RuntimeError(
            "OPENAI_API_KEY is set but OPENAI_BASE_URL is empty: set OPENAI_BASE_URL to the "
            "endpoint the key belongs to."
        )
    # Each request runs in its own event loop, and the SDK does not close a client it was
    # handed, so the client lives and dies with the call.
    async with AsyncOpenAI(base_url=base_url, api_key=api_key) as client:
        # A model object, because the SDK's default provider rejects a prefixed name
        # such as vertex_ai/..., and this endpoint speaks chat completions.
        model = OpenAIChatCompletionsModel(model=MODEL, openai_client=client)
        result = await Runner.run(agent, input_list, run_config=RunConfig(model=model))
    # Content only: tool calls in the reply would be run by the caller against its own
    # world and the agent called again.
    return {"content": str(result.final_output)}
