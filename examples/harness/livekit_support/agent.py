import copy
import os

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
)
from livekit.plugins import openai, silero

MODEL = os.getenv("OPENAI_MODEL", "vertex_ai/gemini-3.7-flash")

INSTRUCTIONS = (
    "You are a customer support agent for an online store, speaking with a caller. Use the "
    "order tools to look up orders and start returns, and never guess an order's status. "
    "Answer briefly, in short spoken sentences, and read an order id back one character at a "
    "time."
)


def _setting(name: str) -> str:
    # Blank counts as unset. Read where it's used, so no module name holds the key.
    return os.getenv(name, "").strip()


def _check_base_url() -> None:
    # With no base URL the OpenAI client falls back to api.openai.com and would send the key
    # there.
    if _setting("OPENAI_API_KEY") and not _setting("OPENAI_BASE_URL"):
        raise RuntimeError(
            "OPENAI_API_KEY is set but OPENAI_BASE_URL is empty: set OPENAI_BASE_URL to the "
            "endpoint the key belongs to."
        )


_check_base_url()

_SEED_ORDERS = {
    "A1001": {"item": "trail running shoes", "status": "delivered", "return": None},
    "A1002": {"item": "rain jacket", "status": "shipped", "return": None},
    "A1003": {"item": "water bottle", "status": "delivered", "return": None},
}


class SupportAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        # The worker runs each call as its own job with its own agent, so each call has its own
        # copy of the orders: a return started in one call is not there in the next.
        self._orders = copy.deepcopy(_SEED_ORDERS)

    @function_tool
    async def look_up_order(self, context: RunContext, order_id: str) -> str:
        """Look up an order by its id and report its item, status and any return.

        Args:
            order_id: The order's id, such as A1001.
        """
        order = self._orders.get(order_id)
        if order is None:
            return f"No order {order_id} exists."
        returned = order["return"] or "none"
        return f"Order {order_id}: {order['item']}, status {order['status']}, return: {returned}."

    @function_tool
    async def start_return(
        self, context: RunContext, order_id: str, reason: str
    ) -> str:
        """Start a return for a delivered order, giving the customer's reason.

        Args:
            order_id: The order's id, such as A1001.
            reason: Why the customer is returning it.
        """
        order = self._orders.get(order_id)
        if order is None:
            return f"No order {order_id} exists."
        if order["status"] != "delivered":
            return f"Order {order_id} is {order['status']} and cannot be returned yet."
        if order["return"] is not None:
            return f"Order {order_id} already has a return started."
        order["return"] = reason
        return f"Return started for order {order_id}: {reason}."

    @function_tool
    async def return_policy(self, context: RunContext) -> str:
        """State the store's return policy."""
        return "Delivered items can be returned within 30 days for a full refund."


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    # Loaded once per worker process, before any call, and shared by its calls.
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    # The plugins below read the key and the base URL when they are built, here.
    _check_base_url()
    # Speech in, the model and speech out all go to the OpenAI-compatible endpoint named by
    # OPENAI_BASE_URL, on its plain routes: /v1/audio/transcriptions (with the VAD to cut the
    # caller's turns), /v1/chat/completions and /v1/audio/speech. No realtime socket.
    session = AgentSession(
        stt=openai.STT(model="gpt-4o-mini-transcribe", use_realtime=False),
        llm=openai.LLM(model=MODEL),
        tts=openai.TTS(model="gpt-4o-mini-tts"),
        vad=ctx.proc.userdata["vad"],
    )
    await session.start(agent=SupportAgent(), room=ctx.room)
    await session.generate_reply(
        instructions="Greet the caller and ask how you can help."
    )


if __name__ == "__main__":
    cli.run_app(server)
