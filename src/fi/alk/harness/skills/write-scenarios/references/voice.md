---
name: scenarios-voice
description: "Axis VALUES for a voice agent: someone speaks to it in real time over a phone or WebRTC line and hears it reply. Use when contract.modality is voice, or the agent joins a room and talks. Read _framework.md first for the invariant six axes and the 12 operations; this file supplies only what differs when the medium is speech, and the failure each lever actually surfaces. NOT for a text agent, and not for a browser agent that happens to have speech bolted on."
---

# Voice: the axis values

> **Selection check.** You are in the right file if a person speaks to this agent and hears it
> reply, in real time. If the exchange is typed, read `chat.md`. If the agent's real work is
> clicking a screen, read `cua.md`.

Speech is a lossy, interrupting, single-channel medium with no undo. Almost everything below
follows from that. The axis vocabulary is in `_framework.md`; what follows is what each axis is
worth varying here, and what a wrong value costs you.

## T, task intent

Read the domain objects off the contract's tools, then cross them with the 12 operations. For a
ride-booking agent: ride, schedule, route, ride-type, driver, fare, payment, account,
safety/sharing, support-issue, receipt.

**The Execute cell is "confirm and pay" or "place and cancel".** Always cover it. It is the only
irreversible one, and an agent that is careful everywhere else and careless there is the expensive
kind of broken.

Two operations are disproportionately informative in voice and often missed:

- **Authenticate**, because it is the one place a spoken channel differs structurally: a code read
  aloud, digit by digit, over a lossy line.
- **Handoff**, because "I cannot do this, let me transfer you" is a correct outcome that agents
  routinely get wrong by attempting the thing instead.

## W, counterparty

A human caller. Traits worth varying: age, literacy, language, accent, role, authentication state.

**The load-bearing value is proxy, someone calling on behalf of another person.** It breaks any
agent that assumes caller identity equals account identity, and that assumption is usually
invisible until a proxy call exposes it.

## D, disposition

Valence, urgency, coherence, cooperativeness, and trajectory.

**One dominant affect per call.** A caller who is calm, then furious, then calm again is three
scenarios wearing one coat, and when it fails you will not know which stretch caused it.

## X, the five questions in speech

| Question | Values here | What varying it surfaces |
|---|---|---|
| x1 fidelity | background noise, accent, codec | whether recognition survives a real line, and whether the agent asks for a repeat instead of guessing |
| x2 medium | PSTN, VoIP, WebRTC | latency and audio-quality assumptions baked into turn handling |
| x3 stability | packet loss, jitter, latency | whether a slow reply is treated as a finished turn |
| x4 interference | cross-talk, background media, a second voice | whether the agent answers the caller or the television |
| x5 presentation | audio only, nothing can be shown | whether it tries to read out something that only works on a screen |

x5 is the one people forget. An agent that would have shown a table has to say it, and a spoken
list of six fares is a different failure from a rendered one.

## I, interaction dynamics

Real-time turn taking, and the three levers are specific to it:

- **Barge-in.** The caller interrupts mid-sentence. Tests whether the agent stops talking and
  listens, or finishes its paragraph while the caller repeats themselves.
- **Long-pause endpointing.** A silence that is thinking, not finishing. Tests whether the agent
  waits or talks over someone mid-thought.
- **Backchannel.** "Mhm", "right", "okay" while the agent is still speaking. These are not turns,
  and an agent that treats them as turns will answer a question nobody asked.

## O, adversarial and safety

Attack surface is spoken content and background audio. Dominant harm classes: **PII read aloud**
(anyone in the room hears it), social engineering and auth bypass, emergency routing, fraud.

The voice-specific one is that the channel has no private field: there is no equivalent of a
masked input, so "read me the card number" is a different question here than in text.

## Footguns

Each of these produces a false failure, which is worse than a missed one because it sends you
debugging the agent.

- **Numbers reach tools as digits, not as the words that were spoken.** Vary how a number is said
  only if the world normalises the way the agent's own code does. Otherwise you have written a
  test that the agent cannot pass and that tells you nothing about the agent.
- **An axis value the voice cannot express is not a scenario.** If the configured TTS cannot
  produce the accent or language, the run tests nothing and the transcript will not show you why.
  Check what the simulator can actually speak before writing a persona around it.
- **Do not put the resolution in the caller's opening turn.** A caller who states the problem, the
  account number and the desired outcome in one breath is testing retrieval, not conversation. Real
  callers volunteer one thing and answer the rest when asked.
- **A sub-goal that needs the caller to accept an optional offer is not gradeable.** The agent
  offers a confirmation text, the persona declines, and a correct agent fails. Either write the
  willingness into the person or check that the offer was made, not what followed it.
- **A caller who never concludes can outlast the call.** Two sides trading farewells has run a
  call to 76 turns. Give the person a condition under which they are satisfied and stop.
- **Silence is ambiguous evidence.** A call that ends with the agent saying nothing can mean the
  agent failed, or that the caller's speech was never rendered at all. Before blaming the agent,
  check whether the caller's turns carry real speech timing.

## Coverage worth having

A voice suite that covers only happy-path booking is testing the easiest third of the medium. Aim
to include, at minimum: one Execute cell, one Authenticate over a lossy line, one Handoff, one
barge-in, one proxy caller, and one where the agent must say no.
