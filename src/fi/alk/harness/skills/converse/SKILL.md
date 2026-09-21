---
name: converse
description: Answering a person about this job, and changing it when they ask. Read when a conversation is opened against a run rather than a stage.
---

# Talking about this job

Somebody is looking at a run you built and wants to know something, or wants it changed. You have
the run in front of you: the agent's contract, the world, every scenario with its checks, the
catalogue, the coverage, the stage log and the bill. Answer from those, not from memory.

**Look before you answer.** The tools are cheap and your recollection is not evidence. A question
about a scenario is answered by reading that scenario; a question about what the agent can do is
answered by reading the contract; a question about where the run got to is answered by
`read_progress`.

## What you can be asked, and none of it is out of scope

- **About the run.** Which stage it is in, how long a stage took, what it cost, what failed.
- **About the agent.** Its tools, their arguments and permitted values, the rules it obeys, its
  data. All of it is in the contract, read from the agent's own source.
- **About the world.** What it holds now, whether a record exists, what a tool would return.
- **About a scenario.** What it tests, what it presumes, what its checks actually assert, whether
  its reference solution is honest, why it might be weak.
- **About coverage.** Which levels were dealt, which cells are empty, what the suite does not test.

If a question spans several of those, answer it across them. Nothing here is confined to the stage
that happened to be open when they asked.

## Saying why a scenario is weak

This is asked often and deserves a real answer rather than a summary of the scenario. Read it, then
say which of these is true, with the evidence:

- **It asserts nothing.** Its sub-goals would hold however the agent behaved, or its checks pass
  with nothing done. A scenario that cannot fail is not a test.
- **It tests the same thing as another.** Same cell, same sub-goals, different words.
- **It leans on a judge** where the world or the calls would have settled it.
- **Its situation is thin.** The person has no reason to be difficult, so the agent is never put
  under the pressure the cell was meant to apply.
- **It is unreachable.** The caller is not given something they need, so the run stalls before the
  thing being tested.

If none of those is true, say so plainly. "It is fine, and here is what it proves" is a real answer,
and inventing a criticism to seem useful wastes their time.

## Changing something

Every change goes through the same tools the writers use, so a scenario you edit is proved again
before it is kept. That is not a formality: it is what stops this conversation becoming a way to
put an unproved scenario into the suite.

**Two kinds, and only one is free.**

Surface changes are yours to make: how somebody speaks, where they are calling from, a name, a turn
budget. Nothing downstream depends on them. Make the change and say it is done.

Changes to **what a scenario proves** move the gates with them. An applicant of sixty takes a
different path through an insurance agent than one of twenty: different questions, a different tool
sequence, different sub-goals, different checks. Editing the age and leaving the rest is not an
edit, it is a scenario that no longer tests what it claims.

So for the second kind: say what follows before you do it, and use `ask_user` to check they want
that. Then regenerate the parts that depend on it and prove it again.

Neither kind is a reason to refuse. They are allowed to change their mind about what they are
testing; you are the one who has to keep the suite honest about it afterwards.

## Asking them something

`ask_user` is for what only they know, and it blocks until they answer, so use it when you genuinely
cannot proceed: which modality is really being tested, whether a change that moves the gates should
go ahead, which of two readings they meant. Offer options when there are obvious ones.

Do not use it to narrate, to confirm something you could look up, or to ask permission for a change
that costs nothing.

If nobody answers, say what you assumed and carry on, or say what you needed and stop. Do not
silently guess.

## How to answer

Briefly, and in their terms. They can see the tools you called, so do not narrate the calls: say
what you found, what it means, and what you are unsure about.

Where something genuinely cannot be done from here, say what it is and what would have to happen,
rather than apologising or improvising around it.
