---
name: writing-a-runner
description: "Read when no existing transport fits the agent and you must write the code that places its calls: an assistant on a platform ALK has never integrated, a custom protocol, anything where livekit and repository_chat both fail to claim it. Covers the runner contract, how to declare it so the run stage finds it, what the receipt must carry, and the footgun that omitting speech timing makes the platform render zeros. Do NOT write a runner when an existing transport already claims the agent; reuse beats rewriting."
---

# Writing a runner for a transport nobody implemented

> **Selection check.** You are in the right file only if you have already established that neither
> `livekit` nor `repository_chat` claims this agent. If one does, reuse it: a second implementation
> of a working transport is a defect, because the platform will grade your copy rather than the
> path production uses.

This is the seam that makes the harness general. You decide how the agent is reached and you write
the code; from then on that code runs every scenario identically, with no model in the loop. Be
inventive here, and be a machine once the first call is placed.

## The contract

A runner is any object with this method. There is no base class to inherit.

```python
class MyCallRunner:
    def __init__(self, adapter, context):
        # `adapter` is the outbound channel; `context` is a CallRunnerContext carrying job,
        # bundle_dir, work_directory, secret values and attempt_number.
        self.adapter, self.context = adapter, context

    async def run(self, scenario, runtime, *, world=None):
        ...
        return CallOutcome(
            calls=(),                      # tool calls observed, as evidence
            turns=len(messages),
            started_at="2026-08-30T12:00:00.000Z",
            ended_at="2026-08-30T12:02:00.000Z",
            duration_ms=120_000,
            transcript_artifact="sha256:...",
            recording_artifacts=("sha256:...",),
        )
```

`world` is optional and is passed when the signature accepts it. Take it if your tools execute
against the leased world, so that setup, checks and your calls all see the same state.

## Declare it, or nothing will find it

Write `transport.json` into the bundle directory. This is how the run stage resolves a runner
without any branch in ALK knowing your transport exists.

```json
{
  "transport": "whatsapp_business",
  "runner": "runners.whatsapp:WhatsappCallRunner",
  "requires": ["turns", "transcript", "timing"]
}
```

- `runner` is `module:Attribute`, imported with the bundle on `sys.path`. The module must sit in
  the bundle and import cleanly on its own.
- `requires` is what you promise the platform. Omit it and the built-in default for a named
  transport applies; declare it and you are held to exactly that.
- Naming only `transport` with no `runner` selects a transport ALK already implements.

An agent whose transport resolves to nothing fails **before any world is leased**, with a message
naming what to declare. That is deliberate: a runner that refuses once per scenario wastes the run
and tells the operator nothing.

## Reference implementations

Read one before writing yours. They are the known-good shape:

- `fi/alk/harness/call_runner.py` (`CallRunnerImpl`) — voice over LiveKit: places the call, drives
  the simulator, collects transcript and recordings.
- `fi/alk/harness/chat_call_runner.py` (`HostedChatCallRunner`) — text over HTTP, and the example
  of a runner that executes response-carried tools against the leased world.

## What the receipt must carry, and why

Whatever you return is validated. Missing evidence is rejected with instructions rather than
passed on, because a silently incomplete receipt is indistinguishable from a run that went fine
and had nothing to say.

**The footgun: speech timing.** Every message in the transcript needs real
`started_speaking_at` and `stopped_speaking_at`. The platform derives talk ratio, words per minute
and agent latency from those fields. Populate them with row indexes, or leave them null, and every
conversation metric renders as **zero** while nothing errors and no test fails. It stays broken
until somebody asks why the dashboard is empty.

The same fields are how a dead text-to-speech key is told apart from a stalled agent: caller turns
carrying text with null timing mean nothing was ever spoken, so the agent heard silence and is not
at fault. Run `scripts/check_call_evidence.py` on the transcript before believing any result.

## When the platform calls you rather than the other way round

For a hosted assistant, the call is placed by their infrastructure and your tools are reached by
webhook. So the runner has two jobs the LiveKit one does not:

1. **Stand up the endpoint their assistant hits**, backed by the real tool implementations from
   the submitted repository, on an address reachable from outside the sandbox.
2. **Start the call through their API**, then wait for their webhooks and their end-of-call event,
   and assemble the transcript from what they report.

Credentials are the usual blocker. An assistant id, an API key and a phone number cannot be
inferred from a repository. When they are absent, ask with `AskUserQuestion` naming exactly what
you need and what it is for. Do not invent a placeholder and do not skip the scenario: a run that
silently tested nothing is worse than one that stopped and asked.
