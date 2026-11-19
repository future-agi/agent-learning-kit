import copy
import os

from langchain_openai import ChatOpenAI
from langgraph.config import get_config
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

MODEL = os.getenv("OPENAI_MODEL", "vertex_ai/gemini-3.7-flash")

SYSTEM_PROMPT = (
    "You are a customer support agent for an online store. Use the order tools to look up "
    "orders and start returns, and never guess an order's status. Answer briefly."
)


def _setting(name: str) -> str:
    # Blank counts as unset. Read where it's used, so no module name holds the key.
    return os.getenv(name, "").strip()


# With no base URL ChatOpenAI falls back to api.openai.com and would send the key there.
if _setting("OPENAI_API_KEY") and not _setting("OPENAI_BASE_URL"):
    raise RuntimeError(
        "OPENAI_API_KEY is set but OPENAI_BASE_URL is empty: set OPENAI_BASE_URL to the "
        "endpoint the key belongs to."
    )

_SEED_ORDERS = {
    "A1001": {"item": "trail running shoes", "status": "delivered", "return": None},
    "A1002": {"item": "rain jacket", "status": "shipped", "return": None},
    "A1003": {"item": "water bottle", "status": "delivered", "return": None},
}
_ORDERS_BY_CONVERSATION: dict[str, dict] = {}


def _orders() -> dict:
    # Each conversation has its own copy of the orders. The conversation is the run's thread,
    # which the caller passes as `config={"configurable": {"thread_id": ...}}`.
    conversation = str(get_config().get("configurable", {}).get("thread_id") or "")
    return _ORDERS_BY_CONVERSATION.setdefault(conversation, copy.deepcopy(_SEED_ORDERS))


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


_TOOLS = [look_up_order, start_return, return_policy]

# Built once at import, which is how a graph named in langgraph.json is loaded. The client
# is built eagerly and refuses a missing key, so a placeholder keeps the import alive and
# the turn is what fails without a real key. `use_responses_api=False` because ChatOpenAI
# switches to the Responses API by itself for some models and arguments, and the gateway
# serves that only for OpenAI-format routes.
_model = ChatOpenAI(
    model=MODEL,
    base_url=_setting("OPENAI_BASE_URL") or None,
    api_key=_setting("OPENAI_API_KEY") or "unset",
    use_responses_api=False,
).bind_tools(_TOOLS)

# ToolNode runs a plain tool on a worker thread, so the tools above don't block the loop.
_tool_node = ToolNode(_TOOLS)


async def _respond(state: MessagesState) -> dict:
    # The prompt goes in front on every call and is never stored in the state, so the history
    # the kit sends each turn is all the graph sees.
    reply = await _model.ainvoke([("system", SYSTEM_PROMPT), *state["messages"]])
    return {"messages": [reply]}


# The model answers, or asks for tools; the tools' results go back to the model until it
# answers without asking.
_builder = StateGraph(MessagesState)
_builder.add_node("agent", _respond)
_builder.add_node("tools", _tool_node)
_builder.add_edge(START, "agent")
_builder.add_conditional_edges("agent", tools_condition)
_builder.add_edge("tools", "agent")
graph = _builder.compile()
