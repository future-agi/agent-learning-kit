---
name: scenarios-chat
description: "Axis VALUES for a text agent: the exchange is typed, asynchronous, and the agent may reply with structure. Use when contract.modality is chat or the agent is reached over HTTP and answers in text. Read _framework.md first for the invariant axes; this file supplies only what differs for text. NOT for voice, and not for an agent whose real work is driving a browser."
---

# Chat: the axis values

> **Selection check.** You are in the right file if the exchange is typed. If someone speaks and
> listens, read `voice.md`.

**T — domain objects.** Typically ticket, order, subscription, account, policy, KB-article,
tool-result, crossed with the 12 operations. **The Execute cell is "process a refund or
cancellation" or "execute an account change".**

**W — counterparty.** A human user. Traits: age, literacy, language, tenure, entitlement tier,
authentication state. The load-bearing value is **VIP / entitlement**, because it changes what the
agent is allowed to do rather than merely how it speaks.

**D — disposition.** Affect and urgency as in voice, but weaker: text hides tone, so a scenario
resting on subtle mood is testing your prompt rather than the agent.

**X — the five questions, in text.**
- x1 fidelity: typos, odd formatting, pasted blobs
- x2 medium: SMS, web widget, WhatsApp, Slack (each with different length and formatting limits)
- x3 stability: delivery delay, genuine asynchrony, a reply arriving after the user gave up
- x4 interference: a multi-party thread where not every message is for the agent
- x5 presentation: markdown support, character limits

**I — interaction dynamics.** Async multi-turn. The levers are **burst messaging** (three messages
before the agent answers), **send-before-finish** (a message that completes the previous one), and
**long delays** mid-conversation.

**O — overlay.** Attack surface is text and pasted content. Dominant harm classes: PII, jailbreak,
regulated advice, self-harm and crisis handling.
