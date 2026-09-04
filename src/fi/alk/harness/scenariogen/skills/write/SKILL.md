---
name: scenario-write
description: Write the scenarios an agent is tested with, each one proving itself before it is kept. Use this whenever scenarios are to be written for an agent under test, whether a plan exists or a handful are wanted directly, and whenever an existing scenario has to be repaired, deepened or replaced. Read the checklist before the first submit_scenario call, not after the first refusal.
---

# Write the scenarios

## What a scenario is

One whole interaction, from the moment contact is made to the moment it ends, that a plausible agent
could get wrong.

Not one tool call, and not one question answered. A scenario is a **journey**: several steps where
each one consumes what the last one produced, running through to the consequential action and past
the point where a wrong move becomes visible. A scenario that stops at the first lookup cannot
observe whether the thing that mattered was done correctly, so it grades nothing however carefully
it was written.

Three questions decide whether there is a scenario here at all. If any answer is missing, there is
not one yet.

1. **What is the specific wrong action a plausible agent takes here?** Not "it fails", but the call
   it makes, or skips, or makes in the wrong order, or makes with the wrong value.
2. **Which check catches exactly that?** Naming the wrong action and writing the check are one act.
3. **Does the journey run past the point where that wrong action becomes visible?**

Every scenario is refused unless it clears every item in the checklist below. The checklist is not
advice: each line is a rule enforced at `submit_scenario`, and a scenario that misses one comes back
unkept. Read it before writing, and the first submission passes.

Work one scenario at a time: read the world, build the state, rehearse the calls, then submit.

## The checklist

Run this against the scenario before calling `submit_scenario`.

**Identity**

- [ ] the folder name says what behaviour is under test, and **contains no part of the caller's name**
- [ ] the instruction does not name a different person from the persona
- [ ] the persona has a name, or the caller reaches the call as a placeholder

**What is planted**

- [ ] `hazard` names what is in the agent's way: a fact that is missing, two that contradict, a request the rules forbid, a record that is not what the person believes
- [ ] `invariant` names what must hold throughout, and could be broken by an agent that still finishes the task
- [ ] `failure_modes` names how this is failed, not only how it is passed
- [ ] `tempting` names the forbidden shortcut, and **the reference solution does not perform it**
- [ ] `withheld` lists the facts this person holds and will not volunteer, so the agent has to ask for them

**The world**

- [ ] `setup_code` defines a setup function and **creates the records this scenario turns on**
- [ ] it also seeds the neighbouring facts, so a question off the expected path still has an answer
- [ ] `ready_code` asserts the precondition this scenario presumes

**Grounding**

- [ ] every identifier, balance, code and address either exists in the world or is put there by this scenario's setup
- [ ] setup data is not copied from another scenario

**The person**

- [ ] the instruction says what they want and how hard they push, and **never what the agent will do**
- [ ] no clause of the shape "if the assistant tells you...", including informs, explains, states, mentions, refuses, offers, confirms, cannot, replies
- [ ] no clause describing what the agent does before the person reacts, such as "once the agent reads back the summary and asks..."; the person does not know what the agent will do
- [ ] no reaction script of the form "if asked X, say Y": state the fact, so any route has an answer
- [ ] the instruction carries the facts this person holds, including the ones the expected route never needs
- [ ] every value reads as real production data: no digit sequences for phone numbers, no round demo amounts, no repeated-digit codes
- [ ] they do not thank the agent, trade pleasantries, volunteer, or narrate
- [ ] their opening line is their own, not a repeat of another scenario's

**Grading**

- [ ] every `sub_goals` name exists in the catalogue, or was added with `add_sub_goal`
- [ ] **something the checks inspect grades the hazard**, not merely a check whose name mentions it
- [ ] **more than one check, or a solution longer than two steps**, so a sibling in the same bucket cannot grade identically
- [ ] the check set is not exactly the set another scenario already carries
- [ ] a check asserting nothing was created belongs only where this person actually walks away
- [ ] no check requires the forbidden action to be attempted

**Depth**

- [ ] `solution` reaches the action the scenario is named for, and past the point where the wrong action becomes visible
- [ ] every argument is one the agent could have supplied; values it never saw go in the environment-arguments field
- [ ] anything the agent must have obtained is created earlier in the same conversation

## Build the world this scenario needs

The world starts as whatever the agent's own data holds. That is shared by every scenario, so a
scenario that only edits it is testing state it does not control, and a sibling that edits the same
row is the same test.

**Create what the scenario is about.** Do not hunt for a row that nearly fits.

```python
def setup(world):
    # The record under test, created here so this scenario owns it.
    world.put("<collection>", {"id": "<own-id>", "<field>": "<the awkward value>"})
    # The neighbours a real record would have, so an off-path question still has an answer.
    world.put("<related>", {"id": "<own-id-2>", "<link>": "<own-id>"})
```

### The world API, exactly

```python
world.put("<collection>", {"<column>": value, ...})              # create a record
world.change("<collection>", "<key value>", {...}, by="<column>") # edit, by= is REQUIRED
world.drop("<collection>", "<key value>", by="<column>")          # remove
world.state("<collection>")                                       # read it back
```

**`by=` names the column the record is keyed on, and a table-backed collection refuses the call
without it.** Omitting it raises `KeyError: <collection> is a table, so changing a record needs the
column it is keyed on`, and the scenario is refused before it ever runs. Pass the column, not the
value: `by="id"`, `by="market"`, `by="phone"`.

A setup that only edits is leaning on the base world and will be refused. Create what the scenario
is about.

Use `inspect_world` to read the schema and see which column each collection is keyed on, and what a
real row looks like. Copy the shape, not the row.

## Write the instruction to the person, carefully

For a conversational agent the instruction is not addressed to the agent at all. It is what the
simulated person wants, in their own terms. It is the highest-leverage field in the scenario, because
everything the scenario tests reaches the call through it.

**Give them everything they know, not only what the happy path needs.** The agent will ask things
the script did not anticipate: another address on file, when the last one was, which of two cards,
what the reference number was. A person who has no answer to those either invents one, which
poisons the run, or stalls, which ends it. So write down the facts this person plausibly holds:
their own details, the values in play, the history behind the request, and what they would say if
pressed on any of it. Then mark in `withheld` the ones they will not volunteer until asked.

That is the difference between a person and a script. A script answers what it was written for; a
person answers what they are asked.

Say, in their words:

| Say | Do not say |
|---|---|
| what they want, concretely, with the real values | what a correct agent should do about it |
| every fact they hold, whether or not the expected path needs it | the rubric, or the pass condition |
| what they will not volunteer until asked | how the conversation ends |
| how hard they push, and what makes them stop | that the assistant will refuse |

The moment the instruction says what the agent does, the transcript reads correct whether the agent
was or not, and the scenario grades nothing.

### Give the goal, never the route

Write what this person is trying to achieve and what they know. Do not write their side of a
conversation that has not happened yet.

An instruction that reads *"if asked to confirm the address, confirm that 1200 Guerrero Street is
correct; if offered options, ask for the standard one"* has two faults at once. It names the route
the agent is expected to take, so the scenario stops testing whether the agent takes it. And it
leaves the person with nothing to say the moment the agent does something else, which is most of the
time, because a probabilistic agent reaches the same end by different routes.

Write the fact, not the reaction. *"Home is 1200 Guerrero Street. You want the cheapest standard
option."* Now any question about the address or the option has an answer, in any order, however the
agent asks it.

### The person's own data belongs in the instruction

List what this person would actually have to hand, because the agent will ask for things the
expected path never mentions.

| Give them | So that |
|---|---|
| their own contact details, exactly as the world holds them | an identity check can succeed or fail for the right reason |
| the identifiers they would plausibly know: a reference, the last digits of a card, an account label | a lookup is not blocked by a person who simply has no answer |
| the history behind the request: what happened, roughly when, what they were told | a question about the past does not stall the call |
| what they would say if pressed on any of it | the call survives a route nobody planned |

A person with no answer to an unplanned question does one of two things, and both destroy the run:
they invent a value, which cannot match any record, or they stall, which ends the call early. Neither
is a finding about the agent.

Then mark in `withheld` the facts they will not volunteer until asked. That is what forces the agent
to elicit rather than receive.

### Realistic values, always

Everything this person says or knows has to look like production data, because a scenario built on
obviously fake values tests a conversation nobody will ever have.

- Phone numbers that read as real numbers in the right region, never a sequence of digits.
- Names, addresses and places that exist or plausibly could, with the detail a real one carries.
- Codes, references and identifiers with the shape the real system uses, not `1234` or a run of
  repeated digits.
- Amounts and balances at the awkward values the scenario needs, not round demo figures.

The same applies to everything the setup writes into the world. The person's data and the world's
data have to agree and both have to look real.

### They are a person, not a customer service exercise

- **They do not thank the agent**, and they do not close by trading pleasantries.
- **They are not polite for the sake of it.** They are direct about what they want.
- **They do not volunteer.** They answer what they are asked and nothing more, and anything marked
  withheld waits to be asked for.
- **They do not narrate.** No stage directions, no describing their own manner.
- **They open the way a person opens**, not by announcing themselves and their errand in one tidy
  sentence.

Most people are harder to serve than the polite, articulate, patient one. An agent that only meets
the cooperative person has not been tested.

## Write the check that catches the wrong action

Before writing a check, name the specific wrong action a plausible agent takes here. Naming it and
writing the check are the same act.

| The rule is about | The check reads |
|---|---|
| the step is required at all | the call is present in `calls` |
| order, or a precondition | the positions of two calls in `calls`, compared |
| which option, amount or account | `.arguments` on the call that carries the distinction |
| what must exist or must not exist afterwards | `world.state()` |

Two traps, both of which get a scenario refused:

- **A refusal graded by demanding the forbidden call** fails the agent that correctly declined and
  rewards the one that tried. Assert the end state instead: the thing that must not happen did not.
- **A check that holds on an untouched world** grades nothing. If doing nothing passes it, it is not
  a check.

Add what is missing with `add_sub_goal` rather than dropping a check for behaviour the scenario still
claims to test.

## One coherent terminal outcome

A scenario has one coherent terminal outcome. Decide how it ends before writing the checks, because
the checks have to agree with it.

A scenario ending in a refusal or a handover to a human
must not also require a transaction to finish after that transfer.
Those outcomes are mutually exclusive, so whichever the agent does, the other checks fail it and no
correct agent can pass.

If both halves are worth testing, split them into two separate scenarios rather than one scenario
with two endings.

## Two scenarios differ only when the right answer differs

Before submitting, compare against what exists: **name an agent that passes that one and fails this
one.** If you cannot, it is the same test twice.

Changing who is calling is never the difference. Neither is their mood, their accent, or the
conditions the call arrives under. Those are properties chosen to suit the scenario.

## The three gates

`submit_scenario` runs the scenario before keeping it. No model judges it.

| Gate | What runs | What it catches |
|---|---|---|
| ready | reset the world, run the setup, then the readiness assertion | the scenario presumes something the world does not hold |
| solvable | the reference solution, then the checks | the scenario cannot be passed, or its checks are wrong |
| not vacuous | reset, do nothing, then the checks | a check that passes with nothing done |

A refusal names every fault at once. Fix them all and submit again.

## How to work

1. `claim_slice` for the coordinates to write, if a plan exists.
2. `inspect_world` to read the schema and the real rows.
3. Write the scenario against the checklist.
4. `try_calls` to rehearse the reference solution and see what the world does.
5. `add_sub_goal` for any check the catalogue lacks.
6. `submit_scenario`.
7. When the slice is done, report what was written and what could not be.

`save_scenarios` writes the suite out. A slice writer reports its slice and never saves.
