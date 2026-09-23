---
name: voice
applies_to: modality=voice
description: What a scenario has to account for when the person reaches the agent by speaking. Read alongside the scenario-writing instructions whenever the contract says the modality is voice.
---

# Writing scenarios for a voice agent

A voice agent is reached by a person speaking, in real time, who cannot see anything. That person
answers several questions in one breath, corrects themselves mid-sentence, mishears a digit, talks
over a confirmation, and sometimes goes silent. Every requirement below follows from one of those
facts, and none of them replaces the general requirements a scenario has to meet.

Whether the agent placed this call or answered it changes how the person is written. The contract
carries that as `CALL DIRECTION`, and the general instructions say what each direction requires: read
it there rather than deciding it here.

## What a voice scenario can test that a chat one cannot

- **The caller answers three questions in one breath**, in their own order, before being asked.
  Real callers do this constantly. An agent that collects one field per turn fails here and passes
  every written test.
- **The caller changes their mind mid-sentence**, and the correction lands after the original. The
  second value is the real one.
- **A value has to be read back and heard.** Codes, prices, times. A digit misheard is a real
  failure, and it only exists out loud.
- **Silence.** The caller goes quiet, or the line is noisy and they ask for something again.

A suite of voice scenarios that could all have been typed has not tested the modality.

## An attempted transfer is not a completed one

A voice run may record that the agent tried to hand the call to a person without the receiving side
ever picking it up, because completing that handoff needs telephony the run does not have. So a
scenario about handing off tests the offer or the attempt, and the work that would follow it belongs
in a separate scenario. A sub-goal that only holds once somebody answers will fail on a correct
handoff.

## The channel, and which parts of it are real

Five questions describe any channel. Answered for a phone call, with what this harness can actually
make happen and what it cannot. **Only vary what is applied.** A scenario that claims a condition
nothing produces is a test of nothing, and it reads as coverage.

| Question | On a call | Can you vary it? |
|---|---|---|
| How clean is the input? | accent, non-native speech, disfluency | **yes**, through who the caller is |
| What is the channel? | PSTN or WebRTC leg | no, the run decides |
| How reliable is it? | latency, jitter, packet loss | **no.** Nothing exposes these. Do not write them |
| What competes with the signal? | `background_noise` | **yes**, per scenario |
| How is state exposed? | audio only, nothing is visible | fixed, and it is the point |

So the two levers are who is calling and what is behind them. That is less than a phone network can
do to a call, and writing the rest anyway would be inventing coverage.

## This modality's answers to the five interface questions

The planning skill asks every kind file the same five questions about the interface axis, and each
answers in its own terms. These are voice's answers; a level from another modality claims a condition
a call cannot produce. Add to this table when the runtime grows a lever; the planner reads whatever is
here.

| question | voice answers with |
|---|---|
| how clean is the input | quiet line · background noise (vehicle, street, crowd, retail) · accent · non-native speech · code-switching |
| what channel it arrives on | inbound call · outbound call, and whoever picks up |
| how reliable and timely it is | fluent speech · disfluency and self-correction · long pauses |
| what competing signal exists | the ambience bed behind the caller, and nothing else the call can render |
| how state is exposed | audio only: nothing can be shown, every value has to be said and heard back |

**Interaction, this modality's tempo.** Single request or multi-turn; fresh or resumed; a correction
after the agent has committed; and the one that is specific to a call, a long silence the agent has to
handle without abandoning the caller.

**Overlay vector, where adversarial content arrives on a call.** Spoken by the caller, or carried in
background audio someone else is producing. Not pasted text, not a hidden element: those belong to
modalities that have a screen. The intensity is the planner's to deal, subtle or overt, and a suite of
overt injections has tested the easy half.

## The levels this modality deals, and the field each one lands in

The planning skill asks the kind file for its X levels. A level with no field behind it is a label.

| Level | Where it lands |
|---|---|
| `quiet_line` | `background_noise` false, the control a noisy scenario is measured against |
| `noisy_line` | `background_noise`, the string naming the place: vehicle, street, crowd, retail |
| `accented` | `persona.accent` |
| `non_native` | `persona.accent` with `persona.languages` |
| `code_switching` | `persona.languages` and `persona.multilingual` |
| `disfluent` | `persona.communication_style`, with both values seeded where one is corrected aloud |
| `terse` / `formal` / `anxious` | `persona.communication_style` |
| `outbound_expecting` | `call_direction` outbound, `caller_awareness` "expecting" |

Two ways this table gets read wrongly, both measured on a fresh hundred.

**The bed is a finite set of ambiences and it never speaks.** `background_noise` takes one of the
places the deployment ships - vehicle, street, crowd, retail and whatever else its catalogue lists - or
`true` to leave the choice to the fixture. Anything you describe that is not one of those is not
produced: a station announcement, an alarm, a crowd that argues, a voice behind the caller. Those are a
second speaker under another name, and there is no second speaker. If the situation needs the caller to
know something the room told them, have the caller say it. Refused at submit.

**The caller's own voice is synthesised clean, every line.** The engine has a rate, an accent and an
emotion; it has no impairment. Slurred, garbled, mumbled or unintelligible speech is not produced, so an
instruction that asks for it describes a call nobody can hear: the agent is handed fluent words, and
whatever the impairment was meant to make hard never reaches it. The scenario then grades as if the
difficulty were there.

The symptom is not the problem - a caller reporting one is ordinary and often the point. Asking for it as
a DELIVERY is. A caller having a stroke can say their face has gone numb and their arm will not move, and
that tests the escalation exactly as intended. "Your speech is heavily slurred" tests nothing, because the
sentence arrives perfectly articulated.

The same holds anywhere the difficulty is carried by how a line sounds rather than by what it says: if
you cannot point to the setting that produces it - `speech_rate`, the accent, the emotion, the noise bed -
the call will not deliver it. Put the difficulty in the words. Refused at submit.

**Barge-in is not something this runtime can do, so do not write it.** The caller is a voice session
whose turn-taking waits for silence: it speaks once it has heard the agent stop, and the only interruption
setting in play governs the agent cutting off the CALLER, not the reverse. Nothing can make the caller emit
audio while the agent is mid-sentence.

So an instruction to talk over a read-back arrives at the agent as an ordinary remark made politely after
the read-back finished. The scenario still runs, still passes, and the coverage report claims a barge-in
that never happened - which is worse than an empty cell, because an empty cell is visible. Measured on a
five-hundred: **thirty-nine scenarios on the interruption level, none of which could interrupt anything.**

What you almost certainly mean is the correction level, and it already exists: the caller changes their
mind, corrects an address, switches product after the quote. That is genuinely hard for an agent and the
call delivers it in full. Refused at submit.

**`quiet_line` means the bed is OFF, and it is the only level that means that.** Sixteen scenarios in
fifty-two carried `quiet_line` with the noise bed switched on. The coordinate then reports that the agent
managed on a clear line when it never had one, and every noisy scenario in the suite loses the control it
was supposed to be measured against. Noise is not a sensible default to leave on: on this level it is the
thing being ruled out.

**An accent counts only if this deployment's voices actually differ on it.** `accented` lands in
`persona.accent`, and that field chooses a voice from the catalogue configured for the run. Accents the
catalogue maps to the same voices as the default are not audible: the coordinate says accented, the caller
sounds exactly like the control, and the level tested nothing. Before dealing `accented`, check which
values in the persona vocabulary reach a different voice, and deal only those. If none does, the level is
not available in this deployment and the honest thing is to leave it out of the plan and say so.

**How to check it, in one step, rather than assuming.** The persona vocabulary offers a list of accents;
the voice catalogue configured for the run maps each to a speaker. Put a persona of each accent through the
voice selection the runtime uses and look at what comes back. Two accents that return the same speaker are
one accent as far as the call is concerned, whatever the coordinate says.

Measured on a live run whose only configured provider had no speaker for one of the vocabulary's accents:
**a caller declared with that accent got the same speaker as every default-accent male caller in the
suite.** The scenario said the line was accented, the grid counted an accented cell, and the audio was
indistinguishable from the control. Meanwhile a second non-default accent in the same vocabulary *did*
reach its own speaker, so the axis was neither fully working nor fully broken - which is exactly the state
that survives unnoticed.

Two consequences for the plan. Deal the accents that are real and **report the number you dealt, not the
number the vocabulary lists** - a lever is covered as many times as it was actually produced. And where an
accent is wanted that the configured voices cannot produce, that is a provisioning question to raise, not
a coordinate to write anyway.
| `outbound_partial` | `call_direction` outbound, `caller_awareness` "partial" |
| `outbound_unaware` | `call_direction` outbound, `caller_awareness` "unaware" |

**`outbound_unaware` is where voice agents fail most**: a person who did not dial and does not know
why anyone is ringing has no request to answer, and a suite that skips it has tested the easy half.
Some runs add further levels; take those from the files you were given rather than from this one.

Chat fields (`pasted_blob`, `wall_of_text`, `typo_heavy` and the rest) belong to typing. Setting one
here claims a condition nothing in this modality produces.

## What this modality lets you vary

`background_noise` is per scenario, not a suite setting. Choose it from the situation rather than
sprinkling it: a caller in a vehicle, a caller in an office, a caller in a crowd. A quiet scenario is
the control that makes a noisy one mean something, so a suite needs both.

Accent and language belong to who the caller is, and they change what the agent's transcription has
to survive. They are dealt across the suite; take the one you are given unless the scenario genuinely
needs another.

`max_turns` is a budget, not a target. A scenario that needs eighteen turns to reach the thing it
tests is fine. One that spends eighteen turns being polite is not.

## What does not belong in a voice instruction

Never write stage directions. No *sighs*, no [annoyed]. Anything in brackets is read aloud, so the
caller says the word "annoyed" instead of sounding it. Manner comes from the persona's disposition.

Never tell the caller how they sound. "You speak with a <region> accent", "you have a <region> accent":
the accent is already a persona field, and it is the voice that delivers it. What the
instruction does instead is hand a text-generating model a fact about its own delivery, and it writes
about the accent or spells out an impression of one, which is not what a person with an accent does.
Measured on a hosted 100: 7 restated it. Where they live is a fair thing to say; how they sound is not.

Never tell the caller what the agent should do. They are on the phone, not reading the contract.
