# LiveKit support agent

A customer support voice agent built on LiveKit Agents, written as LiveKit's documentation
writes one. An `AgentServer` worker runs an `AgentSession` for each call:
- it hears the caller with OpenAI's speech-to-text;
- it answers with an OpenAI-compatible chat model;
- it speaks with OpenAI's text-to-speech.

The `SupportAgent` has three `@function_tool` methods over a small set of orders:
`look_up_order`, `start_return` and `return_policy`.

## How the kit runs it

The kit starts the worker with `python -m agent start`. The worker registers with the LiveKit
server under the name in `LIVEKIT_AGENT_NAME`, which livekit-agents reads itself, and joins a
room when it is dispatched there. Each call is a job with its own `SupportAgent`, so each call
has its own copy of the orders.

Tool calls are recorded from the session's own `function_tools_executed` events, so the
methods are traced as they are written. The kit binds its test world to the methods
themselves (`SupportAgent.look_up_order` and so on).

Speech in, the model and speech out all go to the endpoint named by `OPENAI_BASE_URL`, on its
plain routes:
- `/v1/audio/transcriptions`, with Silero's voice activity detection cutting the caller's
  turns;
- `/v1/chat/completions`;
- `/v1/audio/speech`.

No realtime socket is used: speech-to-text is built with `use_realtime=False`. Silero's model
is loaded once per worker process, in `prewarm`, and each call reads it from
`ctx.proc.userdata`.

## Environment

- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`: the LiveKit server, read by
  livekit-agents itself.
- `OPENAI_API_KEY`: the key for the endpoint, read by the OpenAI client each plugin builds.
- `OPENAI_BASE_URL`: the endpoint's base URL, required whenever a key is set. Set it to the
  model gateway.
  - With a key and no base URL the module refuses to import, and a call refuses to start,
    naming the missing setting.
  - The check reads `OPENAI_BASE_URL` only, so set it even where `OPENAI_API_BASE` is set.
- `OPENAI_MODEL`: the chat model, default `vertex_ai/gemini-3.7-flash` (a gateway name).

The speech models are written in the code: `gpt-4o-mini-transcribe` and `gpt-4o-mini-tts`.
The gateway's speech routes reach only its OpenAI and Azure OpenAI providers, so speech needs
an OpenAI or Azure OpenAI key on the gateway with those two models. With only another
provider's key, the chat model can still work (set `OPENAI_MODEL` to one of its routes), but
the caller is never heard or answered aloud.

Install with `pip install -r requirements.txt` on Python 3.12.

## Known limits

- Whether a given gateway has speech models set up decides whether a call works end to end.
  Reading the agent doesn't need them.
