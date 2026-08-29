---
name: voice-multi-actor
description: "Use when a scenario needs more than one participant with independent goals: a caller and a second person on the line, a transfer between departments, a conference, a supervisor intervention, or an outbound call answered by someone else. Read voice-livekit.md first; this only covers what changes with a second actor. Do NOT use for an ordinary one-caller conversation, which every other voice skill already handles."
---

# Multi-actor voice environments

Use this only when a scenario needs more than one participant with independent goals: an agent,
caller and recipient; a transfer between departments; a conference; a supervisor intervention; or
an outbound call answered by another person.

First identify which participants the submitted system truly supports. Read its room, transfer,
participant identity and webhook code. The environment must preserve those real roles and tool
boundaries. Do not simulate a second actor by having the first caller narrate both sides of a
conversation.

Build shared state that can distinguish each participant and each leg: caller identity, recipient
identity, room or call identifier, transfer state, durable action history and authorization. Seed
both valid and invalid relationships so a transfer to the wrong party, an unauthorized action or a
duplicate handoff can be observed as a refusal.

Scenario `extras` can carry actor-specific instructions and metadata without changing the fixed
scenario shape. Keep those instructions separate: each actor knows only what a real person in that
role would know. Do not leak the expected outcome, private identifiers or another actor's goal into
every persona.

The current simulation specification has one simulator participant. Until a runner can stage the
additional participant, record the multi-actor intent and validate only durable environment state;
do not claim a conference, transfer or handoff was exercised. A runnable multi-actor lane needs
explicit participant lifecycle, turn routing, audio/evidence attribution and reset semantics.
