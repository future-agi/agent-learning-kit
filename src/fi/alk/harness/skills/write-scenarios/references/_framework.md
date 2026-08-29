---
name: scenario-framework
description: "Read this FIRST, before any per-type reference, whenever you are choosing what scenarios to write. It is the invariant part: the six orthogonal axes, the 12 canonical operations that make task coverage exhaustive, the compatibility mask and the sampling strategy. Every agent type uses it unchanged; only the axis VALUES differ, and those live in the per-type file. Do NOT hand-pick scenarios from intuition without reading this, and do NOT re-derive it per agent."
---

# The scenario framework, invariant across agent types

> **Selection check.** This file always applies. If you are writing scenarios for anything, read
> this first, then the reference for the agent type in front of you.

A scenario is **a coordinate in orthogonal axes**, not a hand-written label. This matters because
hand-written labels silently fuse independent dimensions: "frustrated elderly caller on a bad line"
is three separate facts stapled together, and a suite of fifteen such labels leaves most of the
space untested while looking thorough.

Decompose instead. Within an axis the values are mutually exclusive; across axes they are
independent. Then coverage is a property you can measure rather than a feeling.

## The six axes

| Axis | What it varies | Where the values come from |
|---|---|---|
| **T** Task intent | what the person is trying to get done | the agent's domain objects × the 12 operations |
| **W** Counterparty | who or what the agent faces | per-type reference |
| **D** Disposition / state | affect, urgency, cooperativeness; and for embodied agents, the scene's volatility | per-type reference |
| **X** Interface & environment | the five questions below | per-type reference |
| **I** Interaction dynamics | the loop model and its tempo | per-type reference |
| **O** Adversarial / safety overlay | the attack surface and dominant harm class | per-type reference |

### T is derived, never listed

Task intent is the **12 canonical operations** applied to the agent's own domain objects:

**Retrieve · Compare · Explain · Diagnose · Create · Update · Cancel · Execute · Configure ·
Authenticate · Navigate · Handoff**

The objects change per agent; the operation set does not. That is what makes intent coverage
exhaustive rather than ad hoc: read the contract's tools and data to get the objects, cross them
with the twelve, and you have the grid. Anything you cannot place on it is either not a real task
or an object you missed.

**Execute is the highest-stakes cell** in every type, because it is the irreversible one: confirm
and pay, process the refund, submit the form, run the migration, complete the physical handover.
Weight it accordingly.

### X is five questions, and only five

Onboarding a new agent type means answering these with its levels. Nothing else in the framework
moves.

1. **x1 Fidelity** — how clean is the input? (noise, accent, codec; typos and paste; DPI and theme)
2. **x2 Medium** — what substrate? (PSTN/VoIP/WebRTC; SMS/web/Slack; browser/OS/mobile)
3. **x3 Stability** — how reliable is timing? (packet loss, jitter, latency; delivery delay; races)
4. **x4 Interference** — what competes for signal? (cross-talk, background media; popups, CAPTCHA)
5. **x5 Presentation** — what is exposed or hidden? (audio only; markdown limits; dynamic DOM ids)

## From grid to a suite worth running

The full enumeration is large by design and most of it should never run.

1. **Enumerate** the grid: objects × 12 operations × the other axes' values.
2. **Mask** the invalid. Axes are orthogonal, which does not make every cell realistic. A
   combination that could not occur, or that tests nothing the agent controls, is masked out and
   the reason recorded.
3. **Sample** deliberately rather than uniformly. Cover every operation at least once. Cover every
   Execute cell. Then spend what remains on the axis values most likely to break this agent,
   which you know from having built its world.
4. **Never pad to a number.** Twelve scenarios that each test something distinct beat fifty where
   forty are the same coordinate wearing different names. If the useful grid yields twelve, submit
   twelve and say why.

Two scenarios sharing a use case is normal, that is what varying the other axes means. Two
scenarios agreeing on **every** axis are the same test twice.

## What the axes are not for

- Not a naming scheme. Do not write "T3-W2-D1" into a scenario name; the coordinate decides what
  you write, the person reading the suite needs a sentence.
- Not a licence to vary what the agent cannot observe. If changing an axis value changes nothing
  the agent could ever perceive or act on, it produces two identical runs and one wasted call.
- Not a substitute for the world. An axis value that the built world cannot actually produce is
  fiction; either seed the world so it can, or drop the value and say so.
