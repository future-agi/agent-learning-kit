---
name: chat
applies_to: modality=chat
description: What a scenario has to account for when the person reaches the agent by typing. Read alongside the scenario-writing instructions whenever the contract says the modality is chat.
---

# Writing scenarios for a chat agent

A chat agent is reached by a person typing. That person can see everything they have written, can
paste from elsewhere, can send three messages before waiting for an answer, and can go quiet for ten
minutes and come back. Every requirement below follows from one of those facts, and none of them
replaces the general requirements a scenario has to meet.

A chat is always started by the person, so there is no call direction to establish: treat every chat
scenario as one the person initiated, and ignore anything written for calls an agent places.

## What a chat scenario can test that a voice one cannot

- **The whole request arrives in one wall of text.** Reference number, dates, three questions and a
  complaint in a single message. An agent that answers the last sentence and drops the rest fails
  here and passes every voice test.
- **A pasted blob**: a receipt, an error dump, a confirmation email. The fact the agent needs is in
  there, unlabelled, next to facts that look like it.
- **The person edits themselves.** "order 4471, sorry, 4417." The corrected value is the real one,
  and an agent that takes the first fails.
- **Silence that is not silence.** They stop replying for ten minutes and come back mid-thread
  expecting the agent to still hold the context.
- **Ambiguity a speaker would have resolved by tone.** "great, that's just what I needed" from
  somebody who has been complaining for four turns.

## The channel, and which parts of it are real

The same five questions, answered for typing. **Only vary what is applied.**

| Question | In a chat | Can you vary it? |
|---|---|---|
| How clean is the input? | typos, autocorrect, slang, a pasted blob | **yes**, it is all just text the person sends |
| What is the channel? | web, SMS, a work chat tool | no, the run decides |
| How reliable is it? | the person going quiet and coming back | **yes**, as part of the situation, not as a setting |
| What competes with the signal? | a pasted block carrying instructions of its own | **yes**, and it is where injection lives |
| How is state exposed? | everything said is still on screen and re-readable | fixed, and it is what a caller cannot do |

Four of the five are yours here, against two on a call, because text carries its own conditions
rather than needing the platform to produce them.

## The levels this modality deals, and the field each one lands in

The planning skill asks the kind file for its X levels, so here they are by name, with the field a
writer actually sets. A level with no field behind it is a label, and a suite that claims it has
tested nothing.

| Level | What it means | Where it lands |
|---|---|---|
| `pasted_blob` | a receipt, error dump or email pasted in whole, the needed fact unlabelled inside it | `instruction`, and the blob itself in `variables` where a value has to be exact |
| `split_message` | one thought sent across three messages before the agent answers | `instruction`, saying what arrives in what order |
| `wall_of_text` | reference number, dates, three questions and a complaint in one message | `instruction` |
| `self_correction` | "order 4471, sorry, 4417", the second value being the real one | `instruction`, and both values seeded so the wrong one is plausible |
| `terse` / `formal` / `anxious` | how this person types | `persona.communication_style` |
| `typo_heavy` | typos, autocorrect and slang the agent has to read through | `persona.communication_style`, and the misspellings written into `instruction` |
| `code_switching` | two languages in one thread | `persona.languages` and `persona.multilingual` |
| `returns_after_silence` | they stop replying and come back mid-thread expecting the context held | `instruction`, and `max_turns` wide enough to hold the gap |
| `prompt_injection` | the pasted block carries instructions addressed to the agent, or markdown and code fences dressed to look like system text | `instruction`, with the refusal named as a sub-goal |
| `emoji_sarcasm` | "great 🙄", where the words and the meaning disagree and only the text carries it | `instruction` |
| `all_caps` | the anger is in the casing, and nothing else says it | `instruction` |
| `no_actionable_text` | "see attached" or a bare link, with nothing in the message to act on | `instruction` |

**`split_message` and `wall_of_text` are where chat agents fail most**, so a suite that deals every
other level and skips those two has tested the easy half. An agent that answers "hi" and drops the
four messages after it, or answers the last sentence of a wall and drops the rest, passes every
voice test ever written.

`background_noise`, `call_direction`, `caller_awareness`, `answered_by` and `voicemail_style` are
voice fields. A chat scenario leaves every one of them alone; setting one claims a condition nothing
in this modality produces.

`max_turns` is a budget, not a target. A thread that needs eighteen messages to reach the thing it
tests is fine. One that spends eighteen being polite is not.

## What this modality lets you vary

Register: how somebody types is who they are. Someone terse sends four words and no punctuation.
Someone anxious sends three messages in a row before the agent has answered. Someone formal writes
paragraphs. Vary this across the suite the way accents are varied for voice, and let the situation
choose it.

Typos, autocorrect and slang are part of the input the agent has to handle, not noise to be tidied
away. A suite where everybody types cleanly has not tested reading.

Message boundaries matter. One thought split across three messages, and three thoughts in one
message, are different tests.

## What does not belong in a chat instruction

No stage directions and no narration of tone. If it is not typed, it does not exist.

Never tell the person what the agent should reply.
