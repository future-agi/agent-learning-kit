---
name: voice
applies_to: modality=voice
---

# Writing scenarios for a voice agent

The craft is the same. What changes is that the caller is speaking, in real time, and cannot see
anything. These are the parts of a scenario that only exist because of that.

## Which way the call goes changes the instruction

The contract says `direction`. Read it before writing a single instruction, because the two
directions need the person written differently and getting it wrong tests the wrong half of the
conversation.

**Inbound: the person rang the agent.** They have an errand, they know why they are calling, and
they open by saying what they want. The agent greets first and they answer. Write the instruction as
a purpose: what they came for, what they will and will not give up, when they would give up.

**Outbound: the agent rang the person.** This inverts almost everything:

- They have **no errand of their own.** They were doing something else.
- They do **not know who is calling** until the agent says so, and they should not act as if they do.
- They open with a greeting, not a request. "Hello?" is the whole first turn.
- They may be **suspicious**: an unexpected call about their account is what a scam sounds like, so
  asking the agent to prove itself is correct behaviour, not obstruction.
- They may be **busy or unwilling.** Declining to talk now is a legitimate outcome and worth testing.
- What the call is about is the **agent's** purpose. The instruction says how this person reacts to
  it, not what they wanted.

An outbound instruction that opens with a request has been written as inbound, and the scenario then
tests an errand the agent never rang about.

### How much the person already knows, on an outbound call

This is a real axis and it changes the whole call, so choose it deliberately and say which one the
person is:

| They are | What that means in the instruction |
|---|---|
| expecting the call | They know what it is about and roughly what they agreed. Give them their version of it, which may differ from the world's. |
| half remembering | They know something happened but not the detail: not the date, not the amount, not which of two things. Say what they do recall and what they have lost. |
| new to it entirely | They have no context at all. The agent has to establish who they are and why it is calling before anything else can happen. Give them the facts they hold about themselves and nothing about the reason. |

Each needs different data in the instruction. Somebody expecting the call can be asked to confirm a
detail, so they must hold it. Somebody half remembering has to be able to say what they think it was
and be corrected. Somebody new has nothing to confirm, so their instruction carries only their own
details and how they react to an unexpected call.

A person who was not expecting the call and has no facts to offer produces a short, empty
conversation. That is a badly written scenario, not a finding about the agent.

Either way, the person still needs the facts they hold written into the instruction: an outbound
caller asked to confirm something must know what they would say when the agent asks for a detail the
scenario did not anticipate.

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
