"""Returns desk, spoken: the same coordinator and workers as agent.py, reached as a LiveKit voice agent.

Nothing about the topology changes: each user turn is answered by agent.py's `reply`, which runs the one
`COORDINATOR` with its two workers, so a voice call delegates, refuses and refunds exactly as a chat call does.
Speech in and out is the only addition.

Run it: `python voice_agent.py start` (or `dev`), with the LiveKit worker settings (`LIVEKIT_URL`, `LIVEKIT_API_KEY`,
`LIVEKIT_API_SECRET`), `DEEPGRAM_API_KEY` for speech, and everything agent.py reads (its docstring lists each one).

Environment it reads beyond agent.py's, by name:
    LIVEKIT_AGENT_NAME  optional. The dispatch name. Default `returns-desk-voice`.
    DEEPGRAM_API_KEY    required. Speech to text and text to speech.
    AGENT_STT_MODEL, AGENT_TTS_MODEL
                        optional. Default `nova-3` and `aura-2-andromeda-en`.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli
from livekit.plugins import deepgram, openai, silero

import agent as returns_desk

server = AgentServer()


def conversation(chat_ctx: Any) -> list[dict[str, str]]:
    """The spoken turns so far as agent.py's `reply` reads a chat conversation."""
    turns = []
    for item in getattr(chat_ctx, "items", ()):
        role = getattr(item, "role", None)
        if getattr(item, "type", "message") != "message" or role not in ("user", "assistant"):
            continue
        text = getattr(item, "text_content", None) or ""
        if text.strip():
            turns.append({"role": role, "content": text})
    return turns


class ReturnsVoiceAgent(Agent):
    """Every turn goes to the returns coordinator; the session's own model is never asked."""

    def __init__(self) -> None:
        super().__init__(instructions="Answer through the returns coordinator.")

    async def llm_node(self, chat_ctx: Any, tools: Any, model_settings: Any) -> str:
        # `reply` blocks on the coordinator's own loop, so it runs off this session's loop.
        return await asyncio.to_thread(returns_desk.reply, conversation(chat_ctx))


@server.rtc_session(agent_name=os.environ.get("LIVEKIT_AGENT_NAME", "returns-desk-voice"))
async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    key = os.environ["DEEPGRAM_API_KEY"]
    session = AgentSession(
        stt=deepgram.STT(api_key=key, model=os.environ.get("AGENT_STT_MODEL", "nova-3")),
        # A pipeline session needs a model to run its reply step; `llm_node` above answers instead, so this one is
        # never called and needs no working key.
        llm=openai.LLM(model="gpt-4o-mini", api_key=os.environ.get("OPENAI_API_KEY") or "unused"),
        tts=deepgram.TTS(api_key=key, model=os.environ.get("AGENT_TTS_MODEL", "aura-2-andromeda-en")),
        vad=silero.VAD.load(),
    )
    await session.start(agent=ReturnsVoiceAgent(), room=ctx.room)


if __name__ == "__main__":
    returns_desk._tracing()
    cli.run_app(server)
