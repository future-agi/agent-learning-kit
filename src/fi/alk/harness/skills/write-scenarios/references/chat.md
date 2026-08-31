---
name: scenarios-chat
description: "Axis VALUES for a text agent: the exchange is typed, asynchronous, and the agent may reply with structure. Use when contract.modality is chat, or the agent is reached over HTTP and answers in text. Read _framework.md first for the invariant axes and the 12 operations; this file supplies only what differs when the medium is text, and the failure each lever surfaces. NOT for voice, and not for an agent whose real work is driving a browser."
---

# Chat: the axis values

> **Selection check.** You are in the right file if the exchange is typed. If someone speaks and
> listens, read `voice.md`. If the agent's work is clicking a rendered surface, read `cua.md`.

Text is durable, re-readable, and asynchronous. Nothing is lost to a bad line, the user can scroll
back, and neither side has to be present at the same moment. That is what makes its failure modes
different from speech, not merely quieter.

## T, task intent

Typical domain objects: ticket, order, subscription, account, policy, KB-article, tool-result.
Cross them with the 12 operations.

**The Execute cell is "process a refund or cancellation" or "execute an account change".** Always
covered, for the same reason as everywhere: it is the irreversible one.

**Explain deserves more weight here than in voice.** Text is where an agent will confidently
produce a long, well-formatted, wrong answer, and where a user is most likely to act on it because
it looks authoritative.

## W, counterparty

A human user. Traits: age, literacy, language, tenure, entitlement tier, authentication state.

**The load-bearing value is entitlement.** Unlike most traits it changes what the agent is
*allowed* to do, not merely how it should speak, so it is the one most likely to expose a missing
authorisation check.

## D, disposition

Affect and urgency, but weaker than in voice: text hides tone, so a scenario resting on subtle mood
is testing your prompt rather than the agent. Vary cooperativeness and clarity instead, which
survive the medium.

## X, the five questions in text

| Question | Values here | What varying it surfaces |
|---|---|---|
| x1 fidelity | typos, odd formatting, pasted blobs | whether meaning survives messy input, or the agent pattern-matches the noise |
| x2 medium | SMS, web widget, WhatsApp, Slack | length and formatting limits: markdown rendered as literal asterisks in SMS |
| x3 stability | delivery delay, real asynchrony | whether a reply arriving after the user gave up is still coherent |
| x4 interference | a multi-party thread | whether the agent answers messages that were not addressed to it |
| x5 presentation | markdown support, character limits | whether a 900-word answer reaches a 160-character channel |

x2 and x5 interact and are the pair most often missed: an agent that formats well for a web widget
can be unusable over SMS without a single word changing.

## I, interaction dynamics

Async multi-turn. The levers:

- **Burst messaging.** Three messages before the agent answers. Does it respond to all of them, or
  to the first and ignore the rest?
- **Send-before-finish.** A message that completes the previous one. An agent that answers the
  fragment answers the wrong question.
- **Long delays.** A reply an hour later. Does the agent still hold the thread's context?

## O, adversarial and safety

Attack surface is text and pasted content, which is the important difference: a user can paste a
document containing instructions, and prompt injection is a first-class risk here in a way it is
not over a phone line. Dominant harm classes: PII, jailbreak, regulated advice, self-harm and
crisis handling.

## Footguns

- **Do not write a scenario whose only variation is tone.** Text carries tone poorly, so two
  scenarios differing only in politeness usually produce the same run twice.
- **A pasted blob is a scenario, not decoration.** If you vary x1 with pasted content, decide
  whether the paste is meant to be treated as data or as instructions, because that is the actual
  test.
- **Channel limits must be real.** Declaring an SMS scenario while the world imposes no length
  limit tests nothing; the constraint has to exist somewhere the agent can hit.

## Coverage worth having

At minimum: one Execute cell, one entitlement-gated request, one burst or send-before-finish, one
pasted-content injection attempt, one channel with hard length limits, and one where the agent
must refuse.
