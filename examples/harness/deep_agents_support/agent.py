import os

from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI

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


# Built once at import, which is how a graph named in langgraph.json is loaded. The client
# is built eagerly and refuses a missing key, so a placeholder keeps the import alive and
# the turn is what fails without a real key. `use_responses_api=False` because ChatOpenAI
# switches to the Responses API by itself for some models and arguments, and the gateway
# serves that only for OpenAI-format routes.
graph = create_deep_agent(
    model=ChatOpenAI(
        model=MODEL,
        base_url=os.getenv("OPENAI_BASE_URL"),
        api_key=os.getenv("OPENAI_API_KEY") or "unset",
        use_responses_api=False,
    ),
    tools=[look_up_order, start_return, return_policy],
    system_prompt=(
        "You are a customer support agent for an online store. Use the order tools to "
        "look up orders and start returns, and never guess an order's status. Answer "
        "briefly."
    ),
)
