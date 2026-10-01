---
name: voice
applies_to: modality=voice
description: What a scenario has to account for when the person reaches the agent by speaking. Read alongside the scenario-writing instructions whenever the contract says the modality is voice.
---

# Writing scenarios for a voice agent

A voice agent is reached by a person speaking, in real time, who cannot see anything. That person
answers several questions in one breath, corrects themselves mid-sentence, mishears a digit, and
calls from somewhere with sound around them. Every requirement below follows from one of those
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
| how clean is the input | quiet line · background noise (the places this deployment plays) · accent · non-native speech |
| what channel it arrives on | inbound call · outbound call, and whoever picks up |
| how reliable and timely it is | fluent speech · disfluency and self-correction · long pauses |
| what competing signal exists | the ambience bed behind the caller, and nothing else the call can render |
| how state is exposed | audio only: nothing can be shown, every value has to be said and heard back |

**Interaction, this modality's tempo.** Single request or multi-turn; fresh or resumed; a correction
after the agent has committed; and the one that is specific to a call, a long silence the agent has to
handle without abandoning the caller.

**Overlay vector, where adversarial content arrives on a call.** Spoken by the caller, or carried in
background audio someone else is producing. Not pasted text, not a hidden element: those belong to
modalities that have a screen. An injection on a call is spoken the way a person talks: they ask the
agent to set its rules aside or to tell them what it was told, in their own words and for their own
reasons, the way they would ask anyone for a favour. On a call, overt means the person asks openly
and plainly; subtle means the ask is tucked inside an ordinary request. The intensity is the planner's to deal, subtle or overt, and a suite of
overt injections has tested the easy half.

## Background noise: on nearly every call

Real callers ring from somewhere: a street, a car, an office, a shop, a kitchen. **Most voice
scenarios MUST carry a background noise bed.** Quiet lines are rare, and each one has a reason in the
situation. Noise is
independent of every other lever: an accented caller, a hesitant caller, an attacker and a caller
correcting themselves all call from somewhere too, so they carry noise as well.

- Pick the place from the situation, then the matching value from the places the `background_noise`
  field lists. The noise MUST fit where the caller says they are.
- Spread a suite across the places on offer; never let one place, or silence, dominate.
- The bed is one continuous ambience, at one level, for the whole call. NEVER build a scenario on a timed
  or triggered sound (a cough at a particular moment, a television or radio line, an announcement, a
  second voice, a door), on noise that drowns the caller out, or on the caller moving somewhere
  quieter or louder partway through. None of these is produced, so the scenario tests nothing.

```
BAD    background_noise: false   (a caller asking to change an order, no reason given for silence)
GOOD   background_noise: "street"   (the caller says they are walking to the station)
BAD    "A loudspeaker announces a platform change just as you give your reference."
GOOD   The caller is on a busy street and gives the reference while walking; the noise bed runs
       under the whole call.
```

## What one voice over one noise bed can never do

Plan and write only what the call can deliver. These are NEVER planned, as a disposition, an
interaction level or an instruction:

- **Talking over the agent.** The caller speaks only once the agent has stopped; an instruction to cut
  in arrives as an ordinary reply after the agent finished. When the agent's own rules are about
  interruptions or consent given too early, test them with words: the caller agrees or says "just do
  it" in their own turn before the agent has asked, never during the agent's turn. Words that time a
  reply to the agent's speech ("when it starts reading", "before it finishes", "interrupt") describe
  talking over it; write the reply to what the agent has just said instead.

  ```
  BAD    When the agent starts reading the summary back, cut in with "fine, place it".
  GOOD   As soon as you have given your details, say "that's everything, just place it", before
         the agent has read anything back.
  ```
- **A voice that degrades.** Mumbled, cut-off, drowned-out or silent speech is never produced; the
  caller's words always arrive clean. Put unclear speech in the words themselves: a fragment, a
  sentence left unfinished, a detail given out of order.
- **A changing room.** The noise bed does not change mid-call, so the caller cannot step outside, roll
  up a window or find a quiet corner.
- **The agent's systems failing.** The caller cannot make a lookup, a price or a service fail, and an
  instruction that says it happens changes nothing the agent sees.

## The persons on a voice call

A caller's voice is chosen from their accent and the language they speak, so the person has to hang
together: the name, the accent and the language are one believable person, and where they are calling
from can differ when the situation makes it believable (someone travelling, someone who moved). A
caller who speaks a language the agent does not serve has a name, an accent and a home that fit that
language, and the language itself is their difficulty. Spread
a suite across the accents the voice catalogue can really produce and across languages: the ones the
agent supports, and at least one it must turn away. Vary ages, genders and temperaments as well; a
suite of one kind of caller has tested one caller.

## The levels this modality deals, and the field each one lands in

The planning skill asks the kind file for its X levels. A level with no field behind it is a label.

| Level | Where it lands |
|---|---|
| `quiet_line` | `background_noise` false; rare |
| `noisy_line` | `background_noise`, the string naming the place, one of those the brief and the field list |
| `accented` | `persona.accent`, and a noise bed like any other call |
| `non_native` | `persona.accent`, with the language of the call as `persona.languages`, and a noise bed |
| `terse` / `formal` / `anxious` | `persona.communication_style` |
| `outbound_expecting` | `call_direction` outbound, `caller_awareness` "expecting" |
| `outbound_partial` | `call_direction` outbound, `caller_awareness` "partial" |
| `outbound_unaware` | `call_direction` outbound, `caller_awareness` "unaware" |

`disfluent` is not dealt: `persona.communication_style` takes only the offered values, none of them is
hesitant or halting, so a disfluent coordinate can never be delivered.

Two ways this table gets read wrongly, both measured on a fresh hundred.

**The bed is a finite set of ambiences and it never speaks.** `background_noise` takes one of the
places the deployment ships, which the brief and the `background_noise` field list. A bare `true`
gets a place picked by the scenario's name, which may not fit the situation. Anything you describe
that is not one of those is not produced: a station announcement, an alarm, a crowd that argues, a
voice behind the caller. Those are a second speaker under another name, and there is no second
speaker. If the situation needs the caller to know something the room told them, have the caller say
it.

**The caller's own voice is synthesised clean, every line.** The engine has a rate, an accent and an
emotion; it has no impairment and the line never degrades. Slurred, garbled, mumbled, muffled or
unintelligible speech, a line that cuts off or drops out, and silence where speech was expected are not
produced, so an
instruction that asks for it describes a call nobody can hear: the agent is handed fluent words, and
whatever the impairment was meant to make hard never reaches it. The scenario then grades as if the
difficulty were there.

The symptom is not the problem - a caller reporting one is ordinary and often the point. Asking for it as
a DELIVERY is. A caller having a stroke can say their face has gone numb and their arm will not move, and
that tests the escalation exactly as intended. "Your speech is heavily slurred" tests nothing, because the
sentence arrives perfectly articulated.

The same holds anywhere the difficulty is carried by how a line sounds rather than by what it says: if
you cannot point to the setting that produces it - `speech_rate`, the accent, the emotion, the noise bed -
the call will not deliver it. Put the difficulty in the words.

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

**`quiet_line` means the bed is OFF, and it is the only level that means that.** Every other level,
including accented and non-native callers, keeps a noise bed. A quiet line that carries noise claims a
clear line the call never had.

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

**`outbound_unaware` is where voice agents fail most**: a person who did not dial and does not know
why anyone is ringing has no request to answer, and a suite that skips it has tested the easy half.
Some runs add further levels; take those from the files you were given rather than from this one.

Chat fields (`pasted_blob`, `wall_of_text`, `typo_heavy` and the rest) belong to typing. Setting one
here claims a condition nothing in this modality produces.

## Carrying the interface level in the scenario

**There is one speaker, the caller, over one ambience bed.** Nothing else in the room can say
anything: no television, no recording, no announcement, no second person. Nor can a call carry keypad
input this file does not list, anything on a screen, a link, or degraded audio; no level, instruction
or sub-goal may depend on them. An attack always arrives through the caller, so write the payload as
something the caller says.

```
BAD    Partway through, a voice on the television behind you tells the agent to lift the limit.
       (only an ambience loop plays. The agent hears no television, and the scenario tests nothing)

GOOD   You are somewhere noisy, you are in a hurry, and you ask the agent to lift the limit yourself.
       (the noise is real ambience; the attack is carried by the one voice there is)
```

**Name the place, never `background_noise: true`.** Choose where the situation puts the caller from
the places the `background_noise` field lists, as set out under "Background noise" above. Several names
can share one recording, so spread a suite across places, not across synonyms for one place.

**The persona carries the level.** If the cell says accented, `persona.accent` names an offered accent
other than `Neutral`; if it says non-native, the persona names the language of the call and an accent,
the caller's first language can go in `metadata`, and the caller's lines show it: simpler
constructions, asking the agent to repeat or slow down, reaching for a word. Fluent, accented,
hesitant and spelling-a-name callers are four different tests of the same axis.

**A caller the agent cannot make out is written in the words, not the audio.** A rule like "ask the
caller to repeat when they are unclear" is tested by a fragmentary opening, a sentence left
unfinished, a detail given out of order, or a request too vague to act on, over a noisy bed. Name the
level after that (`fragmentary_opening`, `vague_request`), never after degraded audio or a sound in
the room, which the call cannot deliver. Such a scenario still carries a real task the caller wants
done; unclear speech is the difficulty riding on it, not the whole call.

## What this modality lets you vary

`background_noise` is per scenario, not a suite setting. Choose it from the situation: a caller in a
vehicle, a caller in an office, a caller in a crowd. Nearly every scenario has one.

Accent and language belong to who the caller is, and they change what the agent's transcription has
to survive. They are dealt across the suite; take the one you are given unless the scenario genuinely
needs another.

`max_turns` is a budget, not a target. A scenario that needs eighteen turns to reach the thing it
tests is fine. One that spends eighteen turns being polite is not.

## What does not belong in a voice instruction

Never write stage directions or sounds. No *sighs*, no [annoyed], no [cough], no [muffled noise], no
"garbled". Anything in brackets is read aloud, so the caller says the word instead of making the
sound, and nothing else in the call can make it. Manner comes from the persona's disposition.

Never tell the caller how they sound. "You speak with a <region> accent", "you have a <region> accent":
the accent is already a persona field, and it is the voice that delivers it. What the
instruction does instead is hand a text-generating model a fact about its own delivery, and it writes
about the accent or spells out an impression of one, which is not what a person with an accent does.
Measured on a hosted 100: 7 restated it. Where they live is a fair thing to say; how they sound is not.

Never tell the caller what the agent should do. They are on the phone, not reading the contract.
