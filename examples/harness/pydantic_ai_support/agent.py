import contextvars
import copy
import os

from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

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


# No model here: it is chosen per run, once the endpoint's client exists. `instructions`
# rather than `system_prompt`, because Pydantic AI adds a system prompt only when the
# message history is empty, so from the second turn on the persona would be gone.
agent = Agent(
    instructions=(
        "You are a customer support agent for an online store. Use the tools to look up "
        "orders and start returns, and never guess an order's status. Answer briefly."
    ),
    tools=[look_up_order, start_return, return_policy],
)


def _setting(name: str) -> str:
    # Blank counts as unset.
    return os.getenv(name, "").strip()


async def agent_callback(agent_input):
    _CONVERSATION.set(str(getattr(agent_input, "thread_id", "") or ""))
    # The runner puts the new user message at the end of `messages`. Pydantic AI takes it
    # as the prompt and the turns before it as history, so it is sent once, not twice.
    *earlier, latest = agent_input.messages
    history = []
    for message in earlier:
        if message["role"] == "user":
            history.append(
                ModelRequest(parts=[UserPromptPart(content=message["content"])])
            )
        elif message["role"] == "assistant":
            history.append(ModelResponse(parts=[TextPart(content=message["content"])]))
    # With no base URL the client falls back to api.openai.com and would send the key there.
    if _setting("OPENAI_API_KEY") and not _setting("OPENAI_BASE_URL"):
        raise RuntimeError(
            "OPENAI_API_KEY is set but OPENAI_BASE_URL is empty: set OPENAI_BASE_URL to the "
            "endpoint the key belongs to."
        )
    # Each request runs in its own event loop, and the provider does not close a client it
    # was handed, so the client lives and dies with the call.
    # Passed straight in, not held in a variable, so no failure later in the turn shows the key
    # in this frame.
    async with AsyncOpenAI(
        base_url=_setting("OPENAI_BASE_URL") or None,
        api_key=_setting("OPENAI_API_KEY") or None,
    ) as client:
        # A model object, because a name string is resolved by prefix: `openai:` would use
        # the Responses API, which the model gateway serves only for OpenAI-format routes,
        # and a bare vertex_ai/... name is rejected.
        model = OpenAIChatModel(MODEL, provider=OpenAIProvider(openai_client=client))
        result = await agent.run(
            latest["content"], message_history=history, model=model
        )
    # Content only: tool calls in the reply would be run by the caller against its own
    # world and the agent called again.
    return {"content": str(result.output)}
