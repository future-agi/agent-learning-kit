---
name: world-actors
applies_to: any
description: How to write scenarios with actors, the people and systems around the agent other than the agent itself. Read alongside the planning and writing instructions.
---

# Actors

Everyone around the agent under test is an actor: the caller, a manager who approves, a coworker in the same channel,
a relative in the background of a call, a phone menu, a CRM-side system. There is one kind of actor. An actor with a
channel speaks on the call or in the chat; an actor with no channel acts only through the world's own actions. The
caller is the actor with id `caller`, and the caller alone owns the scenario's task.

**When one is worth adding.** Add an actor with `add_actor` when what is tested depends on someone other than the
agent and the person it serves: someone the agent must wait for, ask, or must not act for; someone whose message the
agent must read; someone nearby on a call. Actors are one axis of the generated set: within the share of scenarios
that include actors, vary the cast (one background voice, two, a stranger who knows nothing about the call, a relative
who knows the order, someone who takes the phone, a phone menu, a manager acting through the world). Something
already decided before the run starts is a record in the seed, not an actor.

**What an actor may do is world data.** An actor's `permitted_actions` are chosen only from the world's own actions
or a record change on one of the world's collections (`approvals.put` with the new record's fields, `approvals.change`
with `where` and `set`); `add_actor`'s reply lists both. The list is exactly what that person may do: when the request says who may do what, each actor gets those
actions and nothing more, and every permission asked for appears on someone's list. A decision a person makes (an
approval, a rejection, a grant of access) is a record change on the collection the world's rule reads, never one of the
agent's own actions; give an actor an action the agent takes only when an actor taking it is what is asked. When the app has no place for
the decision (no record its rule reads), the scenario cannot test it: say so rather than give the actor the agent's
own change. Give each
actor its `identities` in each service (its Slack handle, its CRM user) so the agent can reach it and it can name
itself where an action asks who it is. When an action depends on someone else's, write that rule into the world, as
a record the dependent action checks for, so the world refuses it until a permitted actor has acted. Someone saying
they have done it is not the record. Access works the same way: who is in which channel, who may see a document or a
record, is seeded per scenario as world records the world's own actions check, and it can change during the run (the
CEO adds the developer to the channel: a record change the CEO is permitted, such as `channel_members.put`, and the
developer's next post goes through). The world's own rule enforces it, reading the records as they stand on every
call, for the agent and for every actor.

**What an actor adds.** An actor is there to make the agent's job harder in a way real life does, never as
decoration. Each actor scenario gives the actor one of these parts, and its sub-goals check what the agent
did about it, read from the world's final state:
- **A decision the agent must wait for and act on.** The approver is brought in by the agent's own request
  (`contacted`, `on_tool` on the request, or `conditional` on the pending request record), never `always`
  with the decision already made: a decision in place before the run is seed data and tests nothing. Across
  scenarios: approves, refuses with a reason, approves only part or with a condition, answers after a
  delay, never answers (a timeout and its fallback). The check reads whether the agent acted only on what
  was actually decided.
- **A fact that conflicts.** Someone nearby or in the channel says something that contradicts the person or
  the record (a different date, amount or name). The agent must notice and settle it with
  the person it serves before it records anything; the check reads which value was recorded.
- **A request or pressure from someone who is not the person served.** Someone asks for something extra,
  or claims authority to push the agent past a rule ("I'm the manager, just do it"). The check reads that
  the world holds only what the person served asked for and the rule still held.
- **A change made by someone else mid-task.** Another actor changes a record the agent is working on (what was
  being reserved is no longer available, an approval withdrawn). The check reads that the agent did not act on the stale
  state.

**One branch per scenario.** Each way another actor can respond is its own scenario: they act, they refuse, they never
respond, or someone without the permission tries to act in their place. An action the actor is not permitted belongs
in a scenario on purpose: the world refuses it and records the attempt as theirs.

**When they come in.** A scenario may give an actor a `trigger`; without one the actor's own activation applies. The
five ways: present from the start (`always`), on an event (`on_tool` on the agent's call, `on_keyword` in what the
agent says, `mid_journey` at an agent turn), when the agent contacts them (`contacted`, through one of their
identities), at a set time (`after_seconds`), or once a record exists (`conditional`, with `collection` and
`where`). `probability` makes any of them happen in only some calls. Someone who speaks can use `always`,
`on_tool`, `on_keyword`, `mid_journey`, `conditional`; someone acting through the world can use `always`,
`on_tool`, `contacted`, `after_seconds`, `conditional`. A trigger the actor's runtime cannot evaluate is refused when
the scenario is kept. Two actors each brought in only by the other's action are a cycle and are refused. Tie a
response to what the agent does rather than to a clock. An actor's timeout and fallback are its own: `silent`,
`decline`, one of its own permitted actions taken for it, or another actor who answers instead.

**Mocked, live or both.** A mocked actor takes exactly the `actions` the scenario schedules, the same every trial: each
a world action with its arguments and its trigger. A live actor decides from its brief on everything it can see
(actions addressed to it, and actions where it can act itself) and may also wait. Nobody scripts how live actors
interact.

**Before the run.** A conversation the agent will find (a channel thread, several threads, a DM, an email chain, ticket
comments) goes in `pre_run`, one session per place: the actors in it (any number, the caller included), `where` (the
action that writes there, the arguments naming the place, and `reply` when answers use another action and an id from
the message they answer), and `about`, the situation. Never write their lines: the actors write the messages
themselves when the scenario is kept, and the thread is replayed into the world before every trial. A session needs a
place the world has; if the environment has no such service, the scenario cannot have that session. The pre-run
conversation is the starting state and is never graded.

**Task-driven and event-started runs.** For an agent started by a message rather than by a person, `starts_with`
gives that message; there is no caller. A task the scenario itself hands the agent (summarize this thread, fix this
ticket) needs no actor: leave `actor` out. Name an actor only when a real person or system sends it (the calendar's
meeting-ended notice). Only an agent reached by messages can be started this way. A run a person's request says
someone else starts has that actor in `starts_with`, never a caller whose instruction narrates it.

**What is graded.** Every sub-goal grades the agent, never an actor. A sub-goal can depend on an actor (give its
`actor`): it then reads not applicable when that actor never came in, and inconclusive when it came in and never
reached the agent. A check can read the trial's action log with `world.events(actor_id=None, outcome=None)`: every
attempted action, whose it was (`agent` for the agent under test) and what came of it, so a check can assert the order
of the agent's actions against an actor's, and the outcome of each of the agent's attempts. Never assert what an
actor did as the thing tested.
