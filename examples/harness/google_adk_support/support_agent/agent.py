import os

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm

MODEL = os.getenv("OPENAI_MODEL", "vertex_ai/gemini-3.7-flash")

_ORDERS = {
    "A1001": {"item": "trail running shoes", "status": "delivered", "return": None},
    "A1002": {"item": "rain jacket", "status": "shipped", "return": None},
    "A1003": {"item": "water bottle", "status": "delivered", "return": None},
}


# ADK builds each tool's schema from the signature and the docstring, so the parameters stay
# typed and the docstrings stay the tool descriptions. The functions are plain `def`s: ADK
# calls them directly on its event loop, which is where the kit's tool tracer sees them.
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


# LiteLLM picks the provider from the name's prefix. `openai/` sends the rest of the name
# unchanged to `api_base` on chat completions, which is how the model gateway is reached; a
# bare `vertex_ai/...` name would call Vertex directly and skip the gateway. The URL and key
# may be unset at import (LiteLLM then reads its own environment when a call is made), so
# building the model never fails or touches the network.
root_agent = Agent(
    name="support_agent",
    model=LiteLlm(
        model="openai/" + MODEL,
        api_base=os.getenv("OPENAI_BASE_URL"),
        api_key=os.getenv("OPENAI_API_KEY"),
    ),
    # No braces in the text: ADK fills `{name}` from session state and fails on a missing key.
    instruction=(
        "You are a customer support agent for an online store. Use the tools to look up "
        "orders and start returns, and never guess an order's status. Answer briefly."
    ),
    tools=[look_up_order, start_return, return_policy],
)
