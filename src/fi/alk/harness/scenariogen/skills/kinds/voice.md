---
name: voice
applies_to: modality=voice
---

# Writing scenarios for a voice agent

The craft is the same. What changes is that the caller is speaking, in real time, and cannot see
anything. These are the parts of a scenario that only exist because of that.

## What a voice scenario can test that a chat one cannot

- **The caller answers three questions in one breath**, in their own order, before being asked.
  Real callers do this constantly. An agent that collects one field per turn fails here and passes
  every written test.
- **The caller changes their mind mid-sentence**, and the correction lands after the original. The
  second value is the real one.
- **A value has to be read back and heard.** Codes, prices, times. A digit misheard is a real
  failure, and it only exists out loud.
- **Interruption.** The caller talks over the agent's confirmation. What the agent believes was
  confirmed is now a question.
- **Silence.** The caller goes quiet, or the line is noisy and they ask for something again.

## The dials this world has

`background_noise` is per scenario, not a suite setting. Choose it from the situation rather than
sprinkling it: a caller in a car, a caller in an office, a caller in a crowd. A quiet scenario is
the control that makes a noisy one mean something, so a suite needs both.

Accent and language belong to who the caller is, and they change what the agent's transcription
has to survive. They are dealt across the suite; take the one you are given unless the scenario
genuinely needs another.

`max_turns` is a budget, not a target. A scenario that needs eighteen turns to reach the thing it
tests is fine. One that spends eighteen turns being polite is not.

## What does not belong in a voice instruction

Never write stage directions: no *sighs*, no [annoyed]. The caller's manner comes from their
disposition, and anything in brackets is read aloud.

Never tell the caller what the agent should do. They are on the phone, not reading the contract.

Never write the caller a script to recite. Give them what they want, what they know, and how hard
they will push. The words are theirs.
