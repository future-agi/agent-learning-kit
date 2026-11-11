import os

from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

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


async def agent_callback(agent_input):
    # The runner puts the new user message at the end of `messages`. Pydantic AI takes it
    # as the prompt and the turns before it as history, so it is sent once, not twice.
    *earlier, latest = agent_input.messages
    history = []
    for message in earlier:
        if message["role"] == "user":
            history.append(ModelRequest(parts=[UserPromptPart(content=message["content"])]))
        elif message["role"] == "assistant":
            history.append(ModelResponse(parts=[TextPart(content=message["content"])]))
    # Each request runs in its own event loop, and the provider does not close a client it
    # was handed, so the client lives and dies with the call.
    async with AsyncOpenAI(
        base_url=os.getenv("OPENAI_BASE_URL"), api_key=os.getenv("OPENAI_API_KEY")
    ) as client:
        # A model object, because a name string is resolved by prefix: `openai:` would use
        # the Responses API, which the model gateway serves only for OpenAI-format routes,
        # and a bare vertex_ai/... name is rejected.
        model = OpenAIChatModel(MODEL, provider=OpenAIProvider(openai_client=client))
        result = await agent.run(latest["content"], message_history=history, model=model)
    # Content only: tool calls in the reply would be run by the caller against its own
    # world and the agent called again.
    return {"content": str(result.output)}
