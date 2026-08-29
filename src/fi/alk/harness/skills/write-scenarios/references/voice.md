---
name: scenarios-voice
description: "Axis VALUES for a voice agent: someone speaks to it in real time over a phone or WebRTC line. Use when contract.modality is voice or the agent joins a room and talks. Read _framework.md first for the invariant six axes and the 12 operations; this file supplies only the values that differ for speech. NOT for a text agent, and not for a browser agent that happens to have TTS bolted on."
---

# Voice: the axis values

> **Selection check.** You are in the right file if a person speaks to this agent and hears it
> reply, in real time. If the exchange is typed, read `chat.md`.

**T — domain objects.** Read them off the contract's tools. For a ride-booking agent they are
ride, schedule, route, ride-type, driver, fare, payment, account, safety/sharing, support-issue,
receipt. Cross each with the 12 operations. **The Execute cell is "confirm and pay a ride" or
"place and cancel a booking"** and it is the one that must always be covered.

**W — counterparty.** A human caller. Traits that change the run: age, literacy, language, accent,
role, authentication state. The load-bearing value is **proxy / on-behalf** — someone calling for
another person, which breaks any agent that assumes caller identity equals account identity.

**D — disposition.** Valence, urgency, coherence, cooperativeness, and trajectory (does the mood
move during the call?). One dominant affect per call; a caller who is calm, then furious, then
calm again is three scenarios pretending to be one.

**X — the five questions, in speech.**
- x1 fidelity: background noise, accent, codec quality
- x2 medium: PSTN, VoIP, WebRTC
- x3 stability: packet loss, jitter, latency
- x4 interference: cross-talk, background media, a second voice in the room
- x5 presentation: audio only, so nothing can be shown, only said

**I — interaction dynamics.** Real-time turn taking. The levers are **barge-in** (the caller
interrupts mid-sentence), **long-pause endpointing** (a silence that is thinking, not finishing),
and **backchannel** ("mhm", "right") that must not be treated as a turn.

**O — overlay.** Attack surface is spoken content and background audio. Dominant harm classes:
PII read aloud, social engineering and auth bypass, emergency routing, fraud.

## Voice-specific cautions

- **Numbers reach tools as digits.** A scenario that varies how a number is spoken is testing
  normalisation, which is worth testing, but only if the world normalises the way the agent's own
  code does. Otherwise you have written a false failure.
- **An axis value the TTS cannot express is not a scenario.** If the caller's accent or language
  is not something the configured voice can actually produce, the run tests nothing and the
  transcript will not show you why.
- **Do not put the resolution in the caller's opening turn.** A caller who states the problem, the
  account number and the desired outcome in one breath tests retrieval, not conversation.
