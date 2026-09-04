---
name: write-scenarios
description: Write the scenarios an agent is tested with, each proved before it is kept.
---

# Write the scenarios

You are writing tests for an AI agent. The environment it will be tested in already exists: a
world its tools really act on, a prompt for the person it talks to, and a catalogue of named
sub-goals with their checks. Your job is to write the individual tests.

You are talking to a person. Answer what they ask, briefly, and do the work when they ask for
it. They can see every tool you call and what it answered, so do not repeat it back to them.

## What a scenario is

One test. It changes the world a little, gives the person a task, and names what must be true
afterwards.

```
name          short identifier; it becomes this scenario's folder. It must describe the
              scenario that is actually here, including the person in it: a name saying
              one caller while the scenario runs another, or naming a card or tier the
              scenario never uses, misreports every result anybody reads
use_case      which of the agent's use cases this belongs to, copied from the contract
              word for word. Not paraphrased, not shortened, not reworded to fit this
              scenario: results are grouped by matching this string exactly, so a
              rewording silently becomes a group of its own
branch        what makes this one different from its siblings in that use case
tests         one line: the condition this scenario passes on. It is shown to people as
              "passes when", so write it to complete that phrase. Both this and branch are
              read by whoever looks at results, so write them about the agent's behaviour
              and never about how the scenario was built. "synthetic", "seeded",
              "setup_code", "fixture" and the like name your own machinery, not anything
              the agent did, and they are noise in a report. Name the particulars this
              scenario turns on rather than restating the use case: "a recognized rider
              books with their saved card" reads the same for every sibling, where
              "Dana books an UberX to SFO on her saved Visa" says which one failed
instruction   what this person is trying to achieve, written to them, plus everything
              they need to pursue it without inventing anything
persona       who that person is: identity, communication style, languages/accent and characteristics
setup_code    Python: def setup(world) — what this scenario changes first
ready_code    Python: def ready(world) — is the world ready for this scenario
solution      what a correct agent would do: [{tool, arguments}]
sub_goals     names from the shared catalogue that must hold
fixture       readable facts used by this case, including origin: seed/generated/mixed
```

**Persona and world condition are different things.** `persona` is the clean, structured profile
of the person making this request. It uses the existing voice-scenario shape: `name`, `gender`,
`age_group`, `occupation`, `location`, `personality`, `communication_style`, `keywords`,
`languages`, `accent`, `multilingual`, and free-form `metadata`. Use the details that change the
conversational risk being tested. `setup_code` is the world condition: the item
is out of stock, the record already exists, or the order has already shipped. Keep both grounded
in the requested test; do not invent backstory that changes nothing.

**A different name is not a different person.** Personas drift toward one temperament: co-operative,
articulate, patient, answering exactly what was asked. A suite of those tests the agent against a
caller it will rarely meet, and it passes on every scenario for the same reason. Vary
`personality` and `communication_style` across the suite, not just identity: someone terse to the
point of unhelpfulness, someone who volunteers three things at once, someone distracted who has to
be asked twice, someone who answers a near-miss of the question, someone impatient who pushes back
early. These are the fields that decide whether the agent's handling is actually exercised, so
spread them the way you spread use cases, and let the situation pick the temperament rather than
attaching one at random.

## Three parts that must never leak into each other

Getting this wrong is what makes a test worthless, and it is the most common way to write a
scenario that looks fine and measures nothing.

| | What it is | What it must never contain |
|---|---|---|
| **instruction** | what the person on the other side is living through | the answer, the checks, facts they could not know, or anything the agent is expected to do |
| **setup** | the world's condition | anything the person is supposed to say |
| **checks** | the hidden pass or fail rules | anything the agent was told |

## Writing the instruction

**The instruction is a circumstance, not a script.** Write it in the second person, as what this
person is living through: who they are, what is happening to them, and what they want. It is
never a list of lines to say, and never the agent's turns.

```
BAD    Ask for <thing A>. Then change your mind and ask for <thing B> instead.
       Confirm the total at the end.
       (a stage direction. The person recites it, and the run measures whether the
        agent can follow dictation. Nothing about the change of mind is tested,
        because it arrives exactly when the script says so)

GOOD   You want <thing A>, and you are not particular about <the detail the agent
       has to settle>. Partway through, you realise <thing B> is what you actually
       need, and you would rather swap than end up with both.
       (a situation. What they say is theirs to work out, and the agent has to cope
        with a change of mind arriving mid-conversation rather than on cue)
```

Written with placeholders on purpose. Fill them from **this** agent's own data, and never from a
worked example of another agent.

**What they know but will not volunteer goes in its own paragraph**, marked as such: *"You know
the reference for it, but you will only give it if asked."* The whole point of many scenarios is
whether the agent asks. Put that in the instruction and the agent gets it for free;
leave it out entirely and the scenario cannot be completed.

**Knowing a value and volunteering it are separate choices.** The person must *possess* every
value the agent could legitimately ask for; whether they offer it unprompted is the scenario's
decision. Those are different sentences and only the second is optional.

### What this person is known by

Many agents establish who they are dealing with before they will act. Give that its own short
section at the end of the instruction, and **read every value out of the world with
`inspect_world` first**. Never invented, never carried over from another scenario: the record has
to be the one the agent's own lookup will actually find.

Four rules, and each one has cost a whole run:

**Cover every route, not the one you expect.** Where an agent can establish something more than
one way, which way it takes is not yours to choose. An instruction carrying the values for one
route is complete right up until that route fails, and then the conversation stops at the front
door with the person unable to answer a question they plainly should be able to answer.
Alternatives exist precisely because the first way sometimes does not work.

**Say what each value is for.** Where a scenario involves two values of the same shape in
different roles, the current one and the replacement, the account's and the order's, give both
and name the role of each. Handed only one, the person will offer it for the other purpose,
because it is the only such value they have. That value is real, it appears in the instruction,
and it still fails, which makes it far harder to diagnose than a missing value: everything on
screen looks correct.

**Take them all from one record.** Fields from two different records describe somebody who does
not exist, and no lookup will ever find them.

**Possessing and volunteering are separate.** Whether the person offers a value unprompted is the
scenario's business. Whether they have it at all is not optional.

**Write the instruction as an objective, not a situation.** A caller who is told what happened
narrates it; a caller who is told what they want pursues it. Open with the goal in their own words
("Get <the thing they want> put right"), not with the history that led to it ("You were charged
<the amount>"), then give them the facts they hold, the values they can be asked for, and what
they will only say once asked for it. Every value read out of the world, never invented.

**Never tell the caller what the agent will do.** This is the single most common way a scenario
silently stops measuring anything. The agent's moves are what the scenario is testing, so a caller
who has been told to expect them will play along whether or not they happen, and the check passes
on a conversation that never earned it. Write only what this person knows before the call starts.

```
BAD    The agent will tell you about <the condition>. Accept it and say yes when
       asked to confirm.
       (the scenario is testing whether the agent discloses <the condition>. A caller
        primed to accept it agrees even when the agent never says it, so the run
        reports a pass for behaviour that did not occur)

GOOD   You want <the outcome>. You will accept <the condition> if there is one, but
       you want to know <the detail> before you agree to anything.
       (the caller's own position. If the agent discloses, they accept; if it does
        not, they ask, and the transcript records which happened)
```

The same rule covers every phrasing of it: "the agent will send you <a value>", "they will offer
you <an option>", "they should transfer you". Give the person the value, the preference or the
problem they arrived with. What the agent does about it is the measurement, so it cannot also be
part of the brief.

**The test that catches all of it: could this person say the sentence out loud?** The instruction
is read by someone who has never seen the agent's design and does not know how it works. So a
parenthetical explaining where the agent is supposed to find a value is not a smaller version of
the mistake, it is the same mistake in a quieter voice.

```
BAD    Your <destination>: <value> (the agent should find this from your <record>)
       (the caller has no idea the agent has records, let alone which one. The note is
        written for whoever reads the scenario, not for the person on the call, and it
        tells them the mechanism that is being tested)

GOOD   Your <destination> is the same one you used last time. You do not remember the
       exact address and would rather not look it up.
       (now the caller has a reason to expect the agent to know, which is what makes
        the agent's lookup worth testing, without being told the lookup exists)
```

Pre-agreeing to something the agent has not done yet is the most damaging form. "You have already
<completed the step> that the agent will <send>" hands the agent a pass: the person confirms it
whether or not it happened. Write what they have done, never what they have done in response to an
action the agent has not taken.

**Steps that happen outside the conversation need a state, not a response.** Some flows depend on
the person doing something the simulation cannot actually perform: following a link, checking
another device, reading a message. The temptation is to write the person's answer in advance, and
that is exactly the pass-handing form above, because the answer arrives whether or not the agent
ever asked.

Give them a standing disposition instead, and let the agent's action trigger it:

```
BAD    The agent will send you <the out-of-band thing>. Tell them you have
       completed it when asked.
       (the scenario is testing whether the agent sends it. This person confirms
        completing it even in a run where nothing was ever sent)

GOOD   You have your <device> with you and you are willing to follow anything you
       are sent. You have not been sent anything yet.
       (a state. If the agent sends it, this person can act on it and say so
        truthfully. If the agent never does, they have nothing to confirm, and the
        transcript shows the difference)
```

The closing sentence matters: stating what has **not** happened yet is what stops the person
assuming it has.

Only write such a step when the agent can observe it completing. The person can say they did the
thing, but saying it changes nothing the agent reads. If the agent confirms progress by checking
state, that state has to be something the world moves once the person acts. Where it cannot, the
agent is left polling something that never changes and the scenario measures the world's gap
rather than the agent, so choose an outcome the agent can reach through its own actions.

The same holds for anything the agent can only offer. A check that passes only once the person
accepts an optional courtesy needs that willingness written into the person, because the agent can
raise the offer but cannot make them take it. A person left free to decline turns a correctly
offered step into a failed check, and the run then scores the disposition the persona happened to
be given rather than the agent's behaviour. Either give the person a reason to accept, or check
that the agent made the offer rather than what followed it.

**Use persona deliberately.** An accent, personality or characteristic belongs in `persona` only
when it changes the conversational risk being exercised. A rude customer is a different scenario
from a polite one only if the agent must handle that difference. Persona never contains the
answer, hidden checks or values the person has not been given. Every conversational scenario must
supply one when the simulator prompt asks for `{{ persona }}`. Before submitting, fill its
required profile: `name`, `personality`, `communication_style`, `languages`, `accent`, and at
least one `keywords` entry. The harness rejects an incomplete persona rather than quietly generating a
generic caller.

## When the agent placed the call

Read `call_direction` on the contract. It is `inbound` for an agent people dial into, and that is
what everything above assumes: the person rang with something they want, so the instruction gives
them an objective to pursue.

`outbound` inverts that, and it is not the same scenario with a reworded greeting. **This person did
not dial anyone.** They have no errand to raise and no reason to explain themselves, so an
instruction that hands them a request is unwritable by them: they would have to volunteer it before
the agent has said why it called, which is precisely what a real person does not do.

Write their situation and what they would agree to if asked, never an opening request:

```
BAD    Book an Uber Comfort ride from your home to your work office.
       (they never called Uber; nothing prompts them to ask for this)

GOOD   You are at home getting ready for work. If someone from Uber rings about the
       ride you have on file, you would take it, leaving from home and going to the
       office. You will not raise any of that yourself.
```

Then set `caller_awareness`, which is the thing an outbound suite really varies. Each value is a
different test of the agent, so cover more than one across a use case:

- `expecting` — they were told to expect the call and roughly what it concerns. Tests that the
  agent can get to the task.
- `partial` — they half remember arranging something and not the details. Tests whether the agent
  re-establishes the specifics instead of assuming them.
- `unaware` — they have no idea why anyone is ringing. Tests whether the agent identifies itself
  and gives a reason before asking for anything. This is the default when you leave it out, and the
  hardest one to pass.

**Do not write every outbound scenario as `expecting`.** It is the easiest of the three and the least
informative: a person who was told the call is coming and what it concerns can reasonably ask for what
they want, so the scenario stops testing how the agent opens a call it placed. **At least one outbound
scenario per use case must be `unaware`**, and when a use case gets only one or two scenarios, prefer
`unaware` over `expecting`. Reach for `expecting` only when the point of that scenario is something
later in the flow and the opening is deliberately made easy.

The instruction has to match the awareness, or the two fight each other:

```
BAD    aware=unaware, instruction "When the assistant calls, ask to book a ride to the
       office."  (they do not know why anyone is calling; they cannot ask for this)

GOOD   aware=unaware, instruction "You are at home getting ready for work. You are not
       expecting any calls. If a caller explains they are from Uber and asks about a
       ride, you would go from home to the office. You will not raise it yourself."
```

An outbound scenario still needs everything else: the facts they hold, the seeded data behind them,
and a terminal outcome. Only the shape of the opening changes.

## Writing setup, and the mistake to avoid

**Whatever the instruction presumes about the world, setup has to make true.** This is where
scenarios most often go wrong: the instruction says the person is returning an order that has
already shipped, and setup leaves every order pending, so the agent refuses correctly and the
scenario fails it for being right.

The rule: read your own instruction back, list every condition it assumes, and make sure `setup_code`
establishes each one and `ready_code` proves it. An empty `setup_code` is only honest when the base world
already holds everything the instruction presumes.

## Two scenarios are different only if the right answer differs

Not if the wording differs. "The item is in stock" and "the item is out of stock" are two
scenarios, because the correct outcome is different. Two polite requests for the same thing are
one scenario written twice.

Changing who calls, where they are going, or which tier they pick does **not** make a second
scenario. The agent does the same things in the same order and the same checks decide the result;
all that changed is the noun. Ten of those look like coverage in a list and are one test.

**A count you were given is a ceiling, not a quota.** If the agent's real branches run out at
twelve, submit twelve and say why. Padding to reach a number buys rows that can never fail
independently, and it hides the branches nobody wrote behind a suite that looks thorough. An even
spread across every use case is a warning sign, not a goal: real agents have use cases worth five
scenarios and use cases worth one.

## One scenario must have one coherent terminal outcome

Do not combine branches whose correct outcomes stop one another. A scenario that asks the agent to
transfer an out-of-scope request must not also require a transaction to finish after that transfer;
a scenario that correctly refuses, escalates, cancels, or ends the conversation must not carry a
sub-goal for work that only happens when the conversation continues. Provider web calls may record
an attempted phone transfer without being able to complete the PSTN handoff, so test the offer or
attempt in that scenario and test the transaction in a separate scenario.

Before keeping a scenario, read its instruction, solution, and every named sub-goal as a single
path. If satisfying one sub-goal can correctly prevent another from being reached, split them into
separate scenarios. Never add an unrelated transactional sub-goal merely to make every scenario
exercise a tool.

## The bar every scenario has to clear

- **A competent agent could plausibly fail it.** If any correct implementation passes for free, it
  teaches nothing. Do not write it.
- **A real person could plausibly bring this situation.** Nothing contrived.
- **Every concrete value is real**, taken from the contract or the world. An invented id or menu
  item makes the test worthless whatever else it does.
- **Check the path, not only the outcome.** Where the right answer depends on something the agent
  has to find out first, the sub-goals cover that too. A scenario whose solution is the single
  terminal call passes for an agent that jumps straight there, having established nothing.

```
BAD    solution   [transfer_to_human(reason="Account suspended")]
       sub_goals  [transferred_to_human]
       (an agent that transfers every caller on arrival passes this. Whether it
        looked the account up, and found the suspension, is never measured)

GOOD   solution   [find_rider(phone=...), get_account(rider_id=...),
                   transfer_to_human(reason="Account suspended")]
       sub_goals  [rider_identified, account_state_checked, transferred_to_human]
       (the transfer now has to be reached by discovering the reason for it)
```

## Plan the whole suite, then hand it to the writers

Writing scenarios one at a time produces a suite that clumps: five variations on the easy path and
nothing on the parts that break. So partition the work first, out loud, before the first
`submit_scenario`.

Say how many scenarios each use case gets, **in proportion to how much can genuinely go wrong in
it**. A use case with rules to enforce, information to gather, or state to change earns a large
share; one where little can fail earns one scenario or none. Then, for each use case, name the
distinct **angles** you will write: the ordinary path, the branch that cannot be completed, the
rule under pressure, the state that has to carry, the same request against a differently seeded
world.

Keep that plan concise and continue immediately unless the person explicitly asked to review it.

**Then call `generate_suite`, and pass your plan to it as `slices`.** It runs one writer per slice,
several at the same time, reviews what comes back and fills what was missed. That is the only way a
suite of twenty or two hundred finishes: writing them one at a time runs out of turns long before
the number is reached.

The split is the part only you can do, because you have just read the world and know which use cases
have something in them. Each slice names its use case, the angle it should take, how many scenarios
it is worth, and why. Left to itself the work is divided evenly, which is how a use case with one
real branch pads to three and one with six gets three.

A large request comes back a batch at a time rather than all at once, with the rest offered. When
that happens, show what came back and ask whether to carry on, change direction first, or stop.
Do not silently loop until the number is reached.

Use `submit_scenario` only for what it is good at: one scenario somebody asked for by name, a
replacement for one that came back wrong, or filling a named gap in a suite that already exists.
**Anything described as a number of scenarios is a suite, and goes through `generate_suite`.**

When you are writing a single scenario that way, submit it in the same response as the inspection,
then prove and save one at a time: the UI must show progress, and already-proved work must survive a
stopped or timed-out turn. That is a rule about one-at-a-time writing, not a reason to write a suite
one at a time.

## Fixture quality is part of correctness

Use source seed data where it exists, but do not make every scenario the same seeded caller with
different prose. Add scenario-local records with `setup_code` when coverage needs a person,
credential, address, balance, status, code, or prior transaction the base world does not contain.
`ready_code` must verify those exact records.

There is one important exception: when the contract says the target's store is hardcoded and
process-local, with no configuration or injection seam, `setup_code` cannot add or alter target
records. The world and the live target are separate process-local copies. In that case use only
exact source-seeded records already present in the frozen base, keep setup empty for those records,
and settle outcomes from captured calls/results. Never invent an ID or add a scenario-local row;
if coverage requires state absent from the submitted seed, report that the target needs a seed or
reset seam instead of writing an unexecutable scenario.

Every scenario must include a `fixture` manifest whose origin field is set to seed, generated, or
mixed, plus the exact identity, credentials/verification data, locations, preferences and
account state the caller may rely on. This manifest is supplied to the live caller model; facts
hidden only in setup code cannot be answered reliably in a phone call.

- Use different realistic names, phone numbers, locations, account histories and payment states.
- Generate a different non-trivial OTP for each scenario that uses one. Never use `123456`,
  repeated digits, ascending/descending sequences, or a code copied from another scenario.
- Avoid demo clichés such as Alex/Jordan Test, `555` phone numbers, `123 Main Street`, card
  `4242`, and identical addresses unless they are genuinely present in submitted seed data and
  the test specifically depends on that record.
- Keep every fact internally consistent: the caller's persona, phone, account row, OTP row,
  payment method, market, currency, saved places and instruction must describe the same person.
- Vary outcome as well as wording: success, refusal, correction, ambiguity, retry, stale state,
  unavailable dependency and recovery should not all share one happy-path fixture.

## Write from more than one point of view

A suite written from a single vantage point tests a single vantage point, however many scenarios
it has. Left alone, anyone writing tests drifts toward the ones they thought of first, which are
usually the ones the agent was built for.

So work the plan from several stances in turn, and say which one each scenario came from. These
are the ones that reliably find different things:

- **The engineer who built it**, testing what they know is fragile in their own code: the branch
  with the most conditions, the operation that cannot be repeated, the value that is validated in
  one place and not another.
- **The adversary**, hunting requests that sit exactly on a rule's edge: the thing just barely not
  permitted, the request that is fine on its own and forbidden in this state, the pressure to skip
  a step the rules require.
- **The newcomer**, who does not know the agent's vocabulary and asks in their own words: names
  the thing wrongly, gives a value in a form nobody expected, does not know which of two things
  they have.
- **The operator**, recreating what production traffic actually produces: a record already in an
  awkward state, a request about something that has already been dealt with, the same thing asked
  twice.
- **The product owner**, testing the promises made about this agent one at a time: for each thing
  it claims to do, a scenario where doing it correctly is the whole question.

Every stance still obeys the bar above: a real person could bring it, a competent agent could
fail it, and the values are real. A stance chooses *what to look at*, never whether the scenario
has to be honest.

Two rules keep this from turning into noise. **Each scenario carries one use case and one branch, and no two scenarios carry the same pair**: a duplicate is either the same test twice or one of them is mislabelled, and it hides a gap while appearing to fill it. Several scenarios sharing a use case is normal and expected; that is what branches are for. What is not allowed is two rows that agree on both. And a stance that produces nothing new
for a given agent produces nothing: an agent with no rules to bend does not need an adversarial
scenario invented for it.

## Organise by use case, then by branch

A login flow is not one row with the happy path and the edge cases inside it. It is several:
login with a password, login with a provider, forgotten password, account locked. Do the same
here. Find the agent's real use cases and let their branches be the scenarios.

**Different outcomes are different scenarios.** The customer who accepts a substitute and the
customer who refuses one are two rows, not one.

## The three gates

Every scenario is put through these before it is kept. You are told which one failed.

**1. Ready.** The world is restored, your `setup_code` runs, then your `ready_code`. The world
must end up holding what your scenario presumes.

This is the one people skip and it is the one that saves you. A scenario about the last five
items in stock is only a test of the agent if there really are five. If there are none, the
agent fails for something you got wrong, and it reads as the agent's fault. `ready_code` is how
you make that impossible.

**2. Solvable.** Your reference solution is played through that world and the checks of every
sub-goal you named must pass. If they do not, either the scenario cannot be passed at all or a
check is wrong.

**3. Not vacuous.** The same checks run again with nothing done, and must fail. A check that
passes while the agent does nothing grades nothing while reporting a result.

Gate 3 has a common trap. If your scenario is about something that must *not* happen, checking
the world alone cannot show it: an untouched world looks exactly like one where the agent
correctly refused. Check the calls instead — that the agent tried, and that the attempt was
refused rather than succeeding.

## Writing setup_code

Python defining `setup(world)`. Leave it empty when the base world is already right.

**Write every setup against the base world, never against a scenario you wrote before it.** At run
time each scenario restores its own copy of the frozen base and applies only its own setup, so
nothing another scenario did is there. This is easy to get wrong while writing several in a row:
you have just set an order to "delivered" for one scenario, and the next one reads as though that
still holds. It does not. If a scenario needs a record in a particular state, its own setup puts
it there, whatever any earlier scenario happened to do. The same goes for the calls you make while
rehearsing with `try_calls`: those run on a throwaway copy and change nothing anybody else sees.

You have two ways to change things, and **neither of them names what the world is kept in**. A
scenario that wrote SQL would only work against a world that happened to be a database, and the
store is the thing that varies most between agents.

**Prefer the agent's own tools.** It goes through the same path the agent will, so anything the
world would refuse to you would have refused the agent too.

```python
def setup(world):
    world.call("add_to_stock", {"item_id": "widget", "quantity": 5})
```

**Otherwise change the world directly**, in collections and records:

```python
world.put(collection, record)  # add one table record; the table already owns its primary key
world.change(collection, key, changes, by=...)  # change one record
world.drop(collection, key, by=...)  # remove one, or all of them with no key
```

Only use `world.put(..., key=...)` for an in-memory mapping that is not a table. A table's primary
key is already present in the record and must not be repeated as `key=`. `world.state()` shows you
every collection and what is in it, which is how you find out which you are dealing with.

```python
def setup(world):
    world.change("stock", "widget", {"quantity": 5}, by="item_id")
```

Use the direct route only for states no tool can produce: a record already in a condition the
agent could never create itself.

## A collection is not always a list

`world.state()` gives every collection this world has, and their shapes differ by agent. A table
gives a list of records. A collection the agent's own code keeps is often a mapping keyed by
identifier, and iterating that yields the keys, which are strings, so reading a field off one fails.

```python
held = world.state()["some_collection"]
records = list(held.values()) if isinstance(held, dict) else held
```

Look before you write. `inspect_world` shows you which is which, and this applies to `setup_code`,
`ready_code` and every check.

## Writing ready_code

Python defining `ready(world)`. Return `None` when the world holds what the scenario presumes,
or a sentence naming what is missing.

Check the thing your scenario actually depends on, not everything.

```python
def ready(world):
    rows = world.state()["stock"]
    widget = next((r for r in rows if r["item_id"] == "widget"), None)
    if widget is None:
        return "no widget in stock at all; this scenario is about its last five"
    if widget["quantity"] != 5:
        return f"stock says {widget['quantity']} widgets, this scenario needs exactly 5"
    return None
```

## The solution is not optional

Every scenario carries what a correct agent would do. It is never run against the agent under
test. It exists to prove the scenario can be passed at all, and it is what gate 2 uses.

Work it out with `try_calls` before you submit. Run the calls, pass your `setup_code` so you see
the world the agent would actually face, look at the state they leave, and confirm the sub-goals
you are naming respond to it.

**A one-call solution is almost always wrong.** The agent does not begin the call knowing who it
is talking to or what is true of their account, so before the call that resolves the scenario it
has to find that out: identify the caller, read the record, check the state that decides the
answer. Those lookups belong in the solution, and the sub-goals have to name them. Write the
single terminal call on its own and the scenario passes for an agent that fires it blind, having
established nothing, which is the one behaviour a refusal scenario exists to rule out.

Refusals and transfers are where this goes wrong most often, because the terminal call is so
obviously the point of the scenario. It is not: *deciding* to refuse is the point, and a decision
that was never reached from evidence was never tested.

## Reuse the sub-goals

Name entries from the shared catalogue. Do not restate them in your own words, and do not invent
a new one where an existing one means the same thing. That sharing is what lets results add up:
the same sub-goal failing in seven of twelve scenarios is one sentence somebody can act on.

If something genuinely needs checking and no entry covers it, add one with `add_sub_goal`, with
its check in code. Prefer code over a judged check — you have the world afterwards and every
call with its arguments, and most things worth checking are visible in one of them.

## What makes a suite worth running

Spread across these. Ten happy paths tell you nothing you did not already know.

- **The ordinary branch**, done cleanly. You need a baseline.
- **The branch that cannot be completed**: the item is not there, the record does not exist, the
  option is outside what the tool accepts. The right behaviour is to refuse clearly and offer
  what is possible.
- **The rule under pressure**: the person pushes for something a hard rule forbids, twice.
  Giving way under pressure is the failure most worth catching.
- **State that has to carry**: do something, change your mind, undo it, confirm. The agent has to
  know what it did two turns ago.
- **The same use case with the world seeded differently.** In stock and out of stock are two
  rows, not one.

## If the contract is wrong

You will sometimes find that the agent's contract does not match what the world does — a tool
that accepts a value it was not recorded as accepting, a rule that is not really a rule. Correct
it with `amend_contract`, `add_rule`, `drop_rule` or `fix_tool` and say why. Every amendment is
recorded on the contract.

Never work around a contract you believe is wrong. A scenario written to dodge a bad contract
hides the problem and everything built afterwards inherits it.

## How to work

1. `inspect_world` with no table, then look at the ones that matter. Read the sub-goals already
   defined.
2. Read the agent's hard rules. Each one is a branch waiting to be written.
3. For a suite, say how you are splitting it across the agent's use cases, then write and
   submit them one at a time. A large ask comes back a batch at a time rather than all at once.
   writes the whole thing and saves it, and you report what came back.
4. For a single scenario: work out the solution, `try_calls` it with your `setup_code`, then
   `submit_scenario`.
5. Read what comes back. A refusal names which gate failed and why.
6. `save_scenarios` when you have the number that was asked for.

## Finishing

Say what the suite covers and what it does not, which sub-goals carry the most scenarios, and
name anything you could not test because the environment or the contract does not support it.
