import contextvars
import copy
import os

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm

MODEL = os.getenv("OPENAI_MODEL", "vertex_ai/gemini-3.7-flash")

_REFUSAL = (
    "OPENAI_API_KEY is set but OPENAI_BASE_URL is empty: set OPENAI_BASE_URL to the "
    "endpoint the key belongs to."
)


def _setting(name: str) -> str:
    # Blank counts as unset. Read where it's used, so no module name holds the key.
    return os.getenv(name, "").strip()


def _key_without_base_url() -> bool:
    return bool(_setting("OPENAI_API_KEY")) and not _setting("OPENAI_BASE_URL")


# With no base URL LiteLLM's openai/ provider falls back to api.openai.com and would send the key there.
if _key_without_base_url():
    raise RuntimeError(_REFUSAL)


# A key set after import reaches LiteLLM with no api_base, so the check runs again on each call.
def _refuse_key_without_base_url(callback_context, llm_request):
    if _key_without_base_url():
        raise RuntimeError(_REFUSAL)
    return None


_SEED_ORDERS = {
    "A1001": {"item": "trail running shoes", "status": "delivered", "return": None},
    "A1002": {"item": "rain jacket", "status": "shipped", "return": None},
    "A1003": {"item": "water bottle", "status": "delivered", "return": None},
}

# Each conversation gets its own copy of the orders, so a return started in one scenario is
# not there in the next. ADK runs the agent on its own server, so the conversation is named
# just before each tool call, from ADK's session; the tools read it.
_CONVERSATION = contextvars.ContextVar("conversation", default="")
_ORDERS_BY_CONVERSATION: dict[str, dict] = {}


def _orders() -> dict:
    conversation = _CONVERSATION.get()
    if conversation not in _ORDERS_BY_CONVERSATION:
        _ORDERS_BY_CONVERSATION[conversation] = copy.deepcopy(_SEED_ORDERS)
    return _ORDERS_BY_CONVERSATION[conversation]


def _name_the_conversation(tool, args, tool_context):
    # Set in the task that runs the tool: ADK runs it on the loop there, or copies this
    # context into the thread it uses for one.
    _CONVERSATION.set(tool_context.session.id)
    return None


# ADK builds each tool's schema from the signature and the docstring, so the parameters stay
# typed and the docstrings stay the tool descriptions. The functions are plain `def`s: ADK
# calls them directly on its event loop, which is where the kit's tool tracer sees them.
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


# LiteLLM picks the provider from the name's prefix. `openai/` sends the rest of the name
# unchanged to `api_base` on chat completions, which is how the model gateway is reached; a
# bare `vertex_ai/...` name would call Vertex directly and skip the gateway. The URL and key
# may both be unset at import, so building the model never touches the network.
root_agent = Agent(
    name="support_agent",
    model=LiteLlm(
        model="openai/" + MODEL,
        api_base=_setting("OPENAI_BASE_URL") or None,
        api_key=_setting("OPENAI_API_KEY") or None,
    ),
    # No braces in the text: ADK fills `{name}` from session state and fails on a missing key.
    instruction=(
        "You are a customer support agent for an online store. Use the tools to look up "
        "orders and start returns, and never guess an order's status. Answer briefly."
    ),
    tools=[look_up_order, start_return, return_policy],
    before_model_callback=_refuse_key_without_base_url,
    before_tool_callback=_name_the_conversation,
)
