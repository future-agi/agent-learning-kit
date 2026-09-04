---
name: chat
applies_to: modality=chat
---

# Writing scenarios for a chat agent

The craft is the same. What changes is that the person is typing, can see what they wrote, and can
paste. These are the parts of a scenario that only exist because of that.

A chat is always started by the person, so there is no call direction to read and none to set. The
outbound guidance in the write skill does not apply here.

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

## The dials this world has

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
