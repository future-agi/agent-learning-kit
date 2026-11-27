# How the harness actually works

What happens between you typing a sentence and a graded result appearing. Written to be read
alongside the code, so every claim below names the file it lives in.

The shape is the same at every stage, and worth holding onto:

> **A stage is a model session with a small set of tools and its instructions in a markdown file.
> The model decides what to do; the tools do anything that must be exact and refuse anything that
> must not happen. Nothing reaches disk except through a tool that checked it first.**

There is no pipeline. Each stage is a conversation you can interrupt, correct, and resume.

---

## The pieces

| Piece | Where | What it is |
|---|---|---|
| Stage | `session.py` | A live model session, held open across turns, emitting typed events |
| Instructions | `skills/<stage>/SKILL.md` | How that stage works, in prose. Editable without touching code |
| Tools | `tools.py`, `world/tools.py`, `scenario_tools.py`, `run/tools.py` | The exact half: they execute, validate, and refuse |
| Artifacts | `artifacts/sessions/<id>/` | What each stage leaves behind for the next |
| Conversation | `chat.py` | Holds one agent's journey through the stages |
| Session | `sessions.py` | One conversation, one folder: chat and artifacts together |

The artifacts, each the input to the next stage:

```
contract.json → world.sqlite + handlers/ + simulator_prompt.md + sub_goals.json
             → scenarios/<name>/ (+ scenarios.json as the index) → runs.json
```

All of it, plus the conversation that produced it, lives in one folder per session. There is
nothing held in memory that is not also on disk, so closing the page, restarting the server or
coming back tomorrow all resume the same way: by reading the folder.

---

## 1. Reception — which agent is this?

`reception.py`

A stage with `Read`, `Glob`, `Grep` and one tool, `point_at_agent`. You say where your agent
lives; it looks, confirms the path exists, picks a short name, and calls that tool.

It looks from the **workspace root** (the directory holding your repos), not from inside
`agent-learning-kit`, because the agent under test is almost never inside the harness.

`point_at(name, path, kind)` refuses a path that does not exist, and refuses an unknown kind.
`kind` selects an `AgentSource` from `sources.py` — `repo` (code on disk, gets file tools) or
`spec` (a prompt and tool schema pasted in, gets no file tools). **A new kind of agent is one
registration, not a new code path.**

---

## 2. Understand — what is this agent, verifiably?

`understand.py`, `skills/understand-agent/SKILL.md`, gate in `tools.py`

The session gets read-only file tools and one submission tool. It reads the agent's source and
calls `submit_contract`.

**The contract** (`contract.py`) is the anti-hallucination device for everything downstream:

| Field | Why it matters |
|---|---|
| `tools[]` — name, `args`, `arg_types`, `arg_values`, `arg_units`, description | The agent's action space. `arg_values` are the real permitted values — the menu, the enum, the lookup |
| `hard_constraints[]` | Rules the agent must follow. Told to the agent under test, and graded by the judge |
| `base_environment` | Its real starting data, reproduced row for row |
| `real_use_cases[]` | What it is actually for |
| `notes` | Free-form: whatever else the reader judged worth carrying forward |
| `amendments[]` | Anything **not** read from source — see below |

**How it is written:** `accept_contract` in `tools.py` validates before anything reaches disk. It
refuses a contract with no tools, no use cases, duplicate tool names, types for arguments that do
not exist, or — the one that mattered most in practice — *every* tool having no arguments, which
means the arguments were read and then not recorded. Problems are returned **into the
conversation**, so the model corrects and resubmits rather than a bad contract landing.

The reading answers one of three kinds where an agent's main job is work rather than a
conversation: `browser` (it drives a web browser with a browser library), `computer_use` or `cua`
(it acts through a screen) and `crawler` (it fetches pages by rule). A mixed answer is refused if
any part of it is a refused kind. Any of the three gets one second look first, naming any browser,
crawling or computer-use tool the code check found in the source, and which kind each belongs to
where some differ from the answer. So does a chat or voice reading of source where the check found
one, or where the check stopped before reading all of it. The next submission stands: a
`computer_use` or `crawler` answer is refused to `failure.json` (`agent_type_unsupported`, its
kind in `details.agent_type`) and the build exits 78; a `browser` answer is saved as modality
`chat` with `environment_needs: ["browser"]`; a chat or voice answer is saved as it is. The gate
alone sets `environment_needs`: a value the model writes itself is dropped. `how_it_runs` in
`contract.py` answers how an agent is reached and what it needs.

The look gives a value to send back, which only tells apart calls in one model message: those run
before the model reads any result. The rule and its state are `SecondLook` in
`agent_type_check.py`. An answer to a chat or voice look stands at once, since that look said what
each kind leads to. When calls in one message run one after another, or together once the first
scan is done, a later call is taken as the answer before the model has read the look. Once an
answer with the value has come back, a later answer of another kind without it gets a look again,
so answers that keep switching cost turns, up to the reading's turn limit (70, or 140 for a generic
job read unattended), never a refusal without a look. Once a browser answer stands, in this
reading or in the contract it starts over, a later chat or voice answer would quietly take the
browser away, so it gets a look of its own even when nothing was found; only the value of a look
given after the browser answer counts, and the need goes only with that look's answer.

A reading never refuses a browser agent for want of a browser: a reading knows no job. A build
does, with `browser_environment_required` (exit 78), when the contract needs a browser and the
job's `metadata.browser_allowed_sites` is absent. It checks at the build command's start (a
contract already on disk, else an accepted seed that matches the job), between the reading and the
environment in the staged run, and when the hosted build first sees a saved contract
(`browser_environment.py`). The list itself is read strictly: present, it must be a non-empty list
of host names or `*.` wildcards (`browser_allowed_sites_invalid`), each one the run's egress covers
when the job controls egress (`browser_site_not_in_egress`). One entry means one thing, as the
sandbox firewall reads it: `example.com` is that host only, and `*.example.com` is every host under
it, not `example.com` itself. Cover, not presence: the platform drops a host from the egress list
when a wildcard there covers it.

**The environment's browser** (`kit_browser.py`). Where the job has sites and the contract needs a
browser, the kit starts one headless Chromium for each world (`alk-chromium` from the image, or
`ALK_CHROMIUM` for a local run), as the agent's user with a bare environment, on an empty profile
at each spawn of the world. The profile, which is also the browser's temporary folder, has a short
name: Chromium keeps a socket under it, and on Linux a socket's path holds at most 107 bytes, so a
work folder too long for it fails the browser's start and says so. A reset in a browser environment is therefore a full respawn: every
process of the world starts again and each store is resealed from its baseline, never reset in
place, so the agent never holds a connection to the last trial's browser. That costs a world's
start-up on every trial. The agent's process alone
gets its DevTools address in `ALK_BROWSER_CDP_URL`: a relay in front of the browser's port, fixed
for the world's life, which counts the clients that attach. The browser's requests go through a
refusing proxy of its own whose list is the sites and nothing else, each read as above and never
wider than the firewall (the agent's proxy still reads a name as covering the hosts under it): never
loopback, and never a
site any of whose addresses is loopback or unspecified (an IPv4 one written as IPv6 included);
each address of a site is tried in turn within 30 seconds. Chromium sends loopback through it too,
QUIC is off, and WebRTC is kept to the proxy (no UDP outside it). A refusal is logged as the agent's `egress_blocked` attempt marked
`via: browser` while a browser runs, whoever in it asked and however that client reached it: at the
world's door where its database keeps the log, else in a kit-owned file cut at each trial, capped,
cleaned, with the query part of an address removed. With no client the browser asks only for its own
service name (`kit-browser.invalid`, never logged), so a refusal while it runs is a client's. Nothing
is logged between a browser's stop and the next one's start. The proxy sees a host, not who in the browser
asked, so its count holds a page's own requests (scripts, fonts, a favicon) as well as the agent's
navigations; the browser record tells them apart. A trial says `browser_not_used` when no client
attached through the relay and the browser's proxy was asked for nothing, and `browser_not_started` when the job has sites for an agent not read as needing a
browser; both are reported, not graded. The relay serves as many clients as the egress proxy does
and lets a connection idle both ways go after as long as the longest call allowed. That bound holds
for the environment's browser only: the agent's own code, or a browser it starts itself, is bound by
the sandbox's firewall. It covers what the browser is asked to load over the network, not local
files the agent's user can read, nor a client that changes the browser's own settings through its
DevTools address.

**The browser record** (`browser_record.py`; its WebSocket client is `devtools_socket.py`, and the
refusals file of a world with no door is `browser_blocked_log.py`). Beside each world's browser the kit runs a recorder
on the browser's own port, which never counts as the agent's attach. It keeps, in order and with
times, the pages the agent's tabs opened, what it clicked and typed (the element in words), and
the sites it was refused, in two lists: a page the agent tried to open off the list, kept whole
each time (the browser's own retries within 2 s, in one tab, are one attempt), and a request a
page made off the list, counted for each host. A page that sends its tab off the list itself, by a
redirect or by script, is counted as the page's request; so is a move the agent makes by running
script in the page (`location.href` set) rather than by navigating, which is not counted as its
attempt. The icon the browser asks of a site it was just refused is no request at all. Both lists are capped with the rows. A DevTools client is
not told of the clicks and keys another client sends, so a small listener runs in each page, in a
script world of its own, and reports only trusted events through a binding; the page's own
scripts can neither write to the record nor read it. The world's and the binding's names are
random, say nothing of what they are, and are new at each attach; the listener takes the binding into
its own closure and off the world's globals before the page loads. A client holding the browser's
DevTools address is the limit below. Only trusted events are rows:
an agent that acts by script (an element's `click()` run in the page, or a value set and an `input`
event made up) leaves no click or typing row, though its pages, screenshots and refusals are still
kept; typing sent as input (`Input.insertText`, as a Playwright-style fill does) is kept. Text typed
into a secret field is never kept as text, only that typing happened: a password field, or a field
whose autofill names a password, a card's number, security code or expiry, or a one-time code
(`current-password`, `new-password`, `cc-number`, `cc-csc`, `cc-exp*`, `one-time-code`), a field
the page shows as dots by its own style, and each one-character box of a code split over boxes when
a field near it (in its form, else up to three levels up) has one of those autofill names, including
a field whose kind or autofill is changed after the page loads. A card field with no autofill name,
or a split code none of whose boxes is named, is not known to be one. The job's declared secrets are masked wherever the record holds them, as
it is made. A value is masked whole, and only one of 8 characters or more: a shorter one, a secret left
half-typed, or one cut by the 200-character limit on typed text, is not matched. Text the page shows
back (a click's element text, up to 80 characters) is kept as the page shows it, at any level, and
a secret field inside a custom element's shadow tree is judged by the custom element, which may not
say it is one. Screenshots show whatever the page shows. Every text passes the kit's
cleaner, which drops an address's query and fragment; a token in its path (a reset link's, say) is
kept. Rows are capped (the record then says `truncated`), and a
screenshot is taken at each page load and at the trial's end, capped in count and bytes with one
place kept free for the end's, each named by the id it is stored under. The end's is not kept when it
is larger than all of them together may be, or is answered after the record is sealed. A page is
judged by its address: a blank page, the browser's own error page (shown for any page it could not
load, a refused one over https among them) and an address off the sites (where over http the
proxy's refusal is shown) are neither rows nor screenshots, and never the end's. So an allowed https
page that fails to load leaves no row, and an allowed name the proxy refuses by its address (one
that resolves to loopback) shows the proxy's refusal at the allowed address, kept as a page; the
refusal itself is in the blocked list. The record holds against the page,
not against a client holding the DevTools address: such a client can do whatever DevTools can,
so the record says what the browser did, never that nothing else touched it.

The record is the world's, never the agent's tool calls: it lives in the trial's `world_state`
file, apart from the action log, and the sealed world record carries its counts only. There the
agent's `blocked` list has the browser's refusals split by the record into navigations and page
requests, counted for each host (`what: navigation` or `page_request`); their addresses are in the
file. Where the browser's refusals could not be read, the agent's entry holds none of them and says
`browser_blocked_unreadable`; where their file reached its cap, it says `browser_blocked_truncated`. For a host the record saw, its count stands in for the proxy's, which counts each of the
browser's retries; a host only the proxy refused (a worker's request, a tab watched late) stays as
the proxy counted it. A world
with a door adds it to the end state it stores; a world with no door still stores the file, under
`futureagi.browser-world-state.v1`, saying the world's records were not recorded (`not_held`)
rather than carrying empty tables. Where a door stood but the world's end could not be read, the
same file holds the door's action log and says only the tables are missing. The sealed record names the
world's end state under `end_state_artifact` only when the file holds the world's tables; a file
holding the browser's record without them is named under `browser_state_artifact`. With no client
attached the record says `not_used`, never an empty list, and a refusal list that could not be read says so. The
record reaches disk when a client attaches or leaves, at the seal and when the recorder stops
reading, not row by row: a seal not answered within 5 seconds is read as last written, says
`complete: false`, and may hold none of the rows since the last attach. Typed text
leaves with the trace, wherever the `world_state` file goes; secret fields never hold it. One
setting, `TYPED_TEXT_NEEDS_RECORDINGS`, holds it to `traces-and-recordings` instead: below that each
typing row then keeps where and that it happened, its text is left out, and the file says
`typed_text: not_kept_at_level`. Screenshots are uploaded last, as
the `screenshot` kind, kept from `traces-and-recordings` up; at a lower level the browser still
takes them in the world, none is sent, and the trial says the level is why. Together they take at
most half of the job's upload budget, so traces uploaded after them keep room. One the kit cannot send
whole, one the platform does not keep, or one past what an isolated worker's result can carry never
fails the trial: the trial says so. Once the pages' screenshots are full (one place stays for the
end's), no page is photographed; a page screenshot not kept, or one whose tab closed before it was
taken, leaves its place free. The end's tab is chosen once every event the browser sent before the
seal is handled: of the tabs still open showing a page the agent opened, the one that opened its
page last. A tab that moves on while it is photographed gives no end screenshot.

**A reading on its own** (`agent_reading.py`, the platform's "read this agent" with no build after
it) has one budget of model calls for the whole reading: the main loop's, every reader's, and the
reminder's. The platform sets it in `ALK_READING_MAX_MODEL_CALLS`; a missing, unreadable or
non-positive value means 60, never no limit. Its reader gets 12 turns of its own, and at most 4
readers run at once. The Gemini backend checks the budget before each call and sends nothing past
it. On both, the call that reaches the budget is inside it and finishes what it asked for, a save
included. The Claude backend can only count calls as they arrive, one per provider message id, so
the reading stops when it sees the first call past the budget, the same way it stops at its time
limit, and refuses that call's tools. It ends up to 5 calls past the budget: that call, and one for
each reader still running. Either way a contract or failure already saved stands; otherwise the
reading ends as `agent_reading_call_limit` and is not retried.

What is counted differs by backend. The Gemini count is of calls made. The Claude count is of
replies seen in the session's stream, plus each reader's closing reply the stream did not show. The
CLI may hand that reply back only as the result of the call that sent the reader out, so each such
result counts one more call unless the stream shows there was none: the reader was refused or
failed, its last call in the stream asked for no tool (that call was its answer), or its calls in
the stream reached the turn limit the kit set for it. The result's own text and status are not
read. Readers are followed by the call that sent each out, never by the worker's name, since several
readers of one worker run at once. When a reader's turn limit is not known, the result counts one
call and a warning is logged, so a change in the CLI can make the count high but never quietly low.

Still not counted on Claude, because the CLI makes these calls without putting a reply in the
stream or anywhere else the kit can read, and offers no hook before a call:
- the one call per session it makes for a title of its own, so the gateway shows the kit's count
  plus one;
- its own retry after a failed request;
- its second call when a sub-agent's answer comes back empty: it asks again, once, and neither
  empty reply reaches the stream, so the sub-agent's result counts one call, not two;
- its summary call when it compacts a long conversation.

Each is bounded by the counted calls around it, and the time limit holds the rest.
A request the provider client retries on its own is not counted on either backend. The spend
record keeps the limit in force (`call_limit`) beside the calls counted, for every stage that had
one.

**How it is changed later** (`amend.py`). The contract is not frozen, but every change is
recorded with a reason in `amendments[]`, so months later you can still tell what came from the
agent and what came from us:

- `amend_contract` — let an argument accept a value it did not before
- `add_rule` / `drop_rule` — a hard constraint the source did not state, or one misread
- `fix_tool` — correct argument names, types, description, or remove a tool that does not exist

Each demands a `why`. A contract that can be rewritten invisibly is no longer evidence.

**Where the save's file checks run** (`save_checker.py`). Every `contract_tools(...)` call that
has a `source_root` or a `scan_root` reads the customer's files in a child process, started
through a fork server, so a hostile tree can be cut at the save's deadline and its memory
capped. That child imports the parent's `__main__` module before it serves. A script that calls
the kit at top level, without an `if __name__ == "__main__":` guard, is therefore run a second
time inside the child (model calls included). Its own nested start fails and falls back, and it doesn't
serve until the script's top level returns. After 15 s the parent kills it and runs that
call's checks in its own process from then on, with no deadline cut and no memory cap. Put
the kit's calls under the guard. A script that cannot take the guard sets
`ALK_SAVE_CHECKS=thread`, which runs the checks in the parent's own process, as before.

---

## 3. Build — the world its tools run against

`build.py`, `skills/build-environment/SKILL.md`, tools in `world/tools.py`

**This is the part that makes the whole thing worth doing.** The repository's own services,
migrations, seed process and tool code run in an isolated environment. A call for something that
is not there is refused by the submitted implementation, not by a mock or a rewritten handler.

It builds three things, all shared by every scenario: **the world**, **the simulator prompt** for
a conversational agent, and **the sub-goal catalogue**. The stage has sixteen tools and no file
access at all:

| Tool | Does |
|---|---|
| `create_schema` | Run the CREATE TABLE statements |
| `seed` | Insert rows — the agent's real catalogue |
| `change_data` | One UPDATE or DELETE, for fixing a row put in wrong |
| `adopt_tool` | Bind and smoke-test the agent's own implementation |
| `write_env_file` / `run_env_command` | Container orchestration only; never agent behavior |
| `run_tool` | Call a defined tool and see what the world does |
| `declare_sequence` / `drop_sequence` | A series of calls whose end state must hold |
| `inspect_world` | Look at what is in the world |
| `amend_contract`, `add_rule`, `drop_rule`, `fix_tool` | Correct the contract |
| `check_world` | Run every probe, report without saving |
| `save_world` | Freeze it — refused unless it holds up |

Bindings are small adapters to the callable the repository ships. There is no generated-handler
fallback. If the callable cannot be imported or the shipped service cannot start, the build stops
and reports the missing runtime/configuration seam.

**The distinction the whole design turns on** (`runtime.py`):

- `ToolError` — the world saying *no*. The id does not exist; the item is unavailable. **This is
  the world working.**
- Any other exception — our bug.

They are recorded differently and never confused. `_is_refusal` matches the agent's own refusal
type across the class hierarchy.

### The gate: what `check_world` and `save_world` actually run

`world/probe.py`. Every probe restores the world to a frozen baseline first, so probes cannot
inherit each other's rows.

| Probe | Asks |
|---|---|
| `happy` | A valid call built from the contract's permitted values. A refusal is acceptable; a crash never is |
| `edge` | A nonexistent id → must refuse, not succeed and not crash. A missing required argument → must refuse |
| `coverage` | Every contract tool has a handler; no handler for a tool the agent lacks; **and each handler actually reads the arguments the contract says it takes** |
| `data` | Every identifier the contract permits exists in the world — catches a whole category left unseeded, which otherwise looks exactly like correct strictness |
| `sequence` | Each declared sequence, run from the frozen world, leaves the state it claims |
| unknown tool | Calling a tool that does not exist must refuse |

`save_world` refuses on three counts: **score below 0.85**; **no declared sequence** (calls that
each work alone can still forget what the last one did); and **a dirty world** — rows left over
from building, which would otherwise appear in every scenario as somebody else's order already
in the cart.

Then `world/snapshot.py` writes `world.sqlite`, `handlers/*.py`, `world.py` and `manifest.json`.
**The snapshot is the base state every scenario restores from.**

---

## 4. Scenarios — the conversations worth having

`scenarios.py`, `skills/write-scenarios/SKILL.md`, tools in `scenario_tools.py`

A scenario is a **change on the base environment**, and it owns a folder (`folder.py`):

```
scenarios/<name>/
    scenario.json     the instruction, the reference solution, which sub-goals it names
    setup.py          def setup(world)     what this scenario changes first
    ready.py          def ready(world)     is the world ready for it
    checks/<goal>.py  def check(world, calls)   one per deterministic sub-goal
```

`scenarios.json` is an index over those folders, regenerated from them, so anything wanting the
whole suite at a glance has it.

Code lives in files, never duplicated into JSON, and the file is the artifact: each check file is
written with a `__main__` block so it runs standalone against what a run left behind, and a test
proves the file and the harness give the same answer.

`setup` is **code** rather than a list of rows: what a scenario changes is not necessarily the
database alone. There is no persona and no opening line. Variability comes from **real
conditions** that live in `setup`, and the base world stays the shared starting point.

`sub_goals` are **names from the catalogue the environment step defined**, not restated wording.
That is what makes results roll up: the same sub-goal failing in seven of twelve scenarios is one
sentence.

`solution` is what a correct agent would do. It is never run against the agent — it exists so the
scenario can be proved.

The writer can **look** (`inspect_world`) and **rehearse** (`try_calls` — run calls against a
throwaway copy and see what state they leave), so a solution is written from what was observed.

### The gate: three proofs, no model involved

`prove.py`, called by `submit_scenario` before a scenario is kept:

| | Run | Must |
|---|---|---|
| **Ready** | reset → `setup` → `ready` | **hold** |
| **Solvable** | reset → setup → **the solution** → the checks | **pass** |
| **Not vacuous** | reset → setup → **nothing** → the checks | **fail** |

If the first fails, the world does not hold what the scenario presumes, and running it would test
us rather than the agent: the agent would fail for a precondition we got wrong, and it would read
as a finding about the agent. If the second fails, either the scenario cannot be passed or the
check is wrong. If the third passes, the checks grade nothing while reporting a result, which is
the failure that makes a suite quietly green.

Vacuity is judged on *all* checks passing, because one check surviving an empty run ("no
unavailable item was ordered") is legitimate. A single check that survives is still reported, in
`Proof.weak`: sub-goals are shared, so a check that cannot fail without calls would roll up as a
pass for an agent that did nothing at all.

`save_scenarios` additionally refuses a suite where no sub-goal is shared by two scenarios,
because nothing would roll up.

---

## 5. Run — put someone in front of it

`run/`

The simulation is **not a loop written here**. ALK already owns placing a call, driving the
synthetic user, and producing a transcript; the harness supplies the world, the instruction, and
the grading. Against the real hosted agent that is `run/live.py` and `run/call.py`:

```
world + setup ──► webhook ──► public url ──► the assistant's OWN tools repointed
                                                       │
                     ALK's voice case places the call ──┘
                                                       │
                       the world afterwards + the calls ──► the sub-goals' checks
```

1. **Restore** the frozen world and apply this scenario's `setup`. Its own copy, so nothing leaks
   between scenarios.
2. **Stand up the webhook** (`run/voice.py`) and bind that world to it. A hosted voice agent
   executes its tools by calling a webhook, so answering that webhook from `handle_tool_call` is
   the entire integration.
3. **Expose it** (`cloudflared`, or `HARNESS_WEBHOOK_URL`), because a hosted agent cannot reach
   loopback.
4. **Repoint the assistant.** `pointed_at` copies the agent's **own** tools and changes only
   `server.url`. Nothing about the agent is redefined — rebuilding its tools would mean testing an
   agent we wrote.
5. **Place the call** through ALK's own voice case, with the scenario's filled simulator prompt
   driving the caller.
6. **Grade** from the world afterwards plus the recorded calls, through the same checks the gates
   used. A sub-goal marked `judged` is reported as judged, never silently counted.

`run/alk.py` is the same story for the text path: the world goes in as `environment=` to ALK's
`ChatEnvironment`, which owns the turn loop.

Running is also a **stage of the conversation**, not only a command (`run/stage.py`,
`skills/run-scenarios/SKILL.md`, tools in `run/tools.py`: `preflight`, `list_scenarios`,
`run_scenario`, `read_results`). The stage exists because reading a failure is judgement: it has
to sort every failure into one of four causes — the agent was wrong, the world wrongly refused,
the check is wrong, or the simulated caller never asked for the thing — and only the first is a
finding about the agent. Each run's record lands in `runs.json` with the instruction, the
per-sub-goal verdicts, every tool call, and the transcript.

This is what the environment was built for. The previous run's known issues — *"mocked tools
always succeed, including removing an item that was never added"*, *"mock responses do not vary by
argument"*, *"world state does not change unless a scenario sets `state_updates`"* — are all the
same defect, and a world that really holds rows and can really refuse answers all three.

### Worker events

Every row of a call's sealed tool trace carries `worker_events`: what the row means for the app's
workers, in one shape whatever the framework (`world/runtime.py::worker_events`). A row gives one
event, or two for a hand-off whose worker came back.

| Field | Meaning |
|---|---|
| `type` | `tool_call`, `delegated` (work handed to a worker), `transferred` (control passed; nothing returns) or `returned` |
| `id` | The event's id; a `returned` event shares its hand-off's id |
| `worker_id` | The worker that made the tool call, or the worker handed the work: the contract's worker id when the name the framework gives it matches a declared worker's `name`, else that name; `null` when the runtime did not say |
| `worker_name` | The name the runtime gave that worker, as it gave it |
| `by`, `by_name` | Hand-offs and returns only: the worker that handed the work over, as `worker_id` and `worker_name` |
| `parent_id` | The `id` of the hand-off this event ran in; empty at the top level or when unknown |
| `relationship` | `observed` when the runtime's record links the event to `parent_id`, otherwise `unknown` |
| `tool` | The tool call's name, or the name of the call that made the hand-off |
| `at` | When it started (a `returned` event: when the worker came back), seconds since the epoch |
| `ended_at`, `status` | Tool calls: when the call ended (`null` if unknown) and `ok`, `error` or `refused`; `returned` events carry `status` too |

A hand-off still out when the call ended has no `returned` event. Runs sealed before this field
existed do not have it.

---

## What is exact, and what is judgement

The split is deliberate and worth defending:

| Judgement (the model) | Exact (code) |
|---|---|
| Reading unfamiliar source | Whether the contract is structurally usable |
| Designing a schema | Whether a handler crashes or refuses |
| Choosing what is worth testing | Whether an expectation resolves against real tables |
| Whether a claim held | Whether the state matches |

**The model never decides whether something passed.** It decides what to try.

---

## Where to extend it

- A new **agent kind** → a class in `sources.py` and one registration
- A new **world kind** (browser, filesystem, queue) → a class in `world/kinds.py` implementing
  `values_present` / `mutable_state` / `describe`, and one registration. Browser is registered
  and stubbed
- A new **place the agent runs** (Vapi, LiveKit, a hosted endpoint) → a class in `run/targets.py`
  with `open` / `say` / `close`, whose tool calls reach the same `world.handle_tool_call`
- A change to **how a stage works** → edit its `SKILL.md`. No code

## What is not built

- Browser worlds: registered, not implemented
- Snapshots are local files, not S3
- Judged sub-goals are reported as judged, not actually sent to a judge yet
- Results do not post to the platform
- Nothing reports which of the contract's use cases have no scenario


## Required tool workflows

A `HarnessJob` accepts `guidance`, a list of operator instructions, and
`require_tool_workflow`, which defaults to `false`. Both travel through understanding,
environment authoring, scenario authoring, adjustment rewinds, and runtime repairs.
Use `require_tool_workflow: true` only when every scenario must exercise a tool.

With this requirement enabled, authoring rejects a contract unless a declared tool
has an executable import, constructed callable, or service entrypoint. Runtime
validation checks the contract again after repairs. ALK does not invent tools or
modify submitted agent code to satisfy the requirement.

Declarations do not prove execution. Local, resumed, certified, and hosted execution
require captured tool calls in every scenario. Saved job requirements also apply to
interactive scenario tools and cannot be disabled by a caller passing false. A conversation without tool evidence follows
the existing `evidence_missing` retry and errored-receipt policy, even if its generated
scenario claims tool evidence is optional. Ordinary chat and voice jobs keep their
existing behavior when the requirement is omitted. Scenarios intended to refuse a
request without calling tools belong in a separate job without this requirement.


## Selected runtime and configuration

Use `runtime.execution: native` with a source-owned `command`, or
`runtime.execution: compose` with a repository-relative `compose_file` and `service`.
Omitting execution retains automatic selection. Explicit native selection ignores
unrelated Compose files. Explicit Compose selection preserves the selected service's
transitive dependencies and resolves build contexts relative to its Compose file.
Selection does not add execution support: unsupported images, engines, installers,
and container launch semantics fail with a capability error.

`runtime.required_environment` maps process names to required environment-variable
names. Only values delivered to that process satisfy its declaration. Source-wide
credential discovery remains an inventory of possible requirements. The preflight
response preserves `ready_to_submit` as its legacy inventory result and separately
reports `can_submit_for_authoring` and `configuration_status`. Submission permission
is not proof that the selected runtime will start or complete a conversation.

For an OpenAI-compatible ingress, `runtime.interface.model` records the exact model
or profile identifier accepted by that endpoint. It is passed unchanged in the
`openai_chat` request's `model` field; it may differ from the backing provider model.
Omitting it preserves the legacy `agent-learning-target` default. Authoring should
record a source-evidenced identifier when the endpoint requires one. For a custom
JSON envelope, `json_template` can instead supply a literal `model` and
`"messages": "{{messages}}"` in `request_template`; `openai_chat` does not use templates.

`agent.config.target_model` supplies the caller's desired backing model, for example
`vertex_ai/gemini-3.7-flash`. Authoring and runtime repair receive this preference and
the names of supplied target environment values. They must derive the repository's
provider configuration and initialization requests from source and verify a real
chat. Secret values, vault references, simulator credential names, and arbitrary
connection settings are not included in this briefing. The backing model preference
does not replace `runtime.interface.model`, which may identify an application profile.

The model gateway routes a target model unless it is one of OpenAI's own model families
(`gpt`, `chatgpt`, `o1` to `o9`, `text-embedding`, `codex`, `davinci`, `babbage`,
`whisper`, `tts`, `dall-e`, `omni-moderation`, `computer-use`, or an `ft:` fine-tune).
A routed model gets the Agents SDK's chat-completions mark and guidance.
`agent.config.direct_model_names` lists other bare names that stay direct, such as an
Azure OpenAI deployment name (`prod-gpt4o`) the agent sends to Azure itself.

`runtime.extras` selects optional dependency groups declared in the selected Python
project. For a uv workspace root, a group absent from the root must have exactly
one owner among included members. The installer then includes workspace packages
and only the named extras. Root-owned groups use the root project alone; ambiguous,
unknown, invalid, and unsupported selections fail before installation. Compose
applies these selections only to the control process. Existing pip project paths
install the selected `.[extras]`; requirements-only sources cannot declare extras
without a project manifest. Locked uv installs and their stale-lock retry preserve
the same selection. Source manifests and lockfiles are not rewritten by selection.
`runtime.install` remains descriptive metadata; arbitrary installation instructions
are not executed by the process compiler. Declare supported optional groups through
`runtime.extras` rather than relying on that string. A complete local editable declaration
such as `pip install -e sdk[cloud] -e tools` is checked against the selected
project and its uv workspace. If the exact source owner declares an installable
group omitted from `extras`, compilation returns `generated_runtime_extra_omitted`
through the runtime-plan repair path. A repair must preserve the proven source
owner and select the group; deleting the install text does not resolve the error.
Unsupported install syntax remains documentary and never executes.

`runtime.requirements_files` selects additional Python dependency manifests relative
to the selected component. Both compilers install these into the same environment
after project dependencies and extras, preserving every installed package version.
Conflicting supplemental requirements fail instead of replacing the selected baseline.
Selection accepts registry package requirements
and local `-r` / `-c` includes; paths must remain inside the submitted component,
exist in the build context, and have an acyclic bounded include graph. URLs and
other pip options fail explicitly. The bounded chat-readiness repair may add
source-evidenced manifests while retaining existing selections and all other runtime
fields. `runtime.install` alone does not select a requirements file.

HTTP endpoint paths can reference canonical inputs and earlier setup captures, such
as `/threads/{{session_id}}/turns`. Setup runs first; the main path then renders for
each call without changing the stored endpoint. Each substituted value is encoded
as one path segment, so it cannot introduce a new host, path separator, query, or
fragment. Missing values fail before the dependent request. Placeholders outside
the URL path are unsupported. Traces retain the endpoint template and redact captured
values, including their encoded forms, from failure diagnostics.

## Failure evidence

HTTP status failures take precedence over success-response extraction. For an API
that returns application failures with a successful HTTP status, declare
`runtime.interface.failure_rule`, for example:

```json
{"status_path": "state", "failure_values": ["failed"], "message_path": "detail"}
```

Each `runtime.interface.setup_requests` entry also accepts `failure_rule`. Its
application status is checked after the accepted HTTP status and before captures or
the next request. For example, `{"status_path":"code","success_values":[0]}`
rejects an HTTP 200 setup response whose application code is 102. Comparisons preserve
JSON scalar types; numeric zero does not match boolean false or the string "0".

ALK does not infer failure merely because an arbitrary response contains an `error`
field. Failed calls retain partial conversation evidence and bounded diagnostics.
Excerpts are omitted when the caller cannot supply credential context. HTTP bodies
larger than 1 MiB are rejected explicitly. Process tails survive reset and teardown;
retention reads at most 64 KiB per file, sanitizes before persistence, and retains up
to 8,192 characters per tail with a 512-log attempt budget. This is diagnostic
redaction, not a guarantee that arbitrary personal data has been removed.

Public PEM keys and certificate-only chains may pass source and artifact checks when
the complete bounded content parses as public material. Private, mixed, malformed,
oversized, and credential-named files remain blocked.

## Chat readiness before certification

For generic source-owned HTTP or callable chat agents, ALK runs one representative
opening request after scenario setup and ready checks, before source-data invariant
review. It uses the execution transport, edited persona opening or a greeting, and
the declared model, setup requests, response mapping, and tools. Valid text,
clarification, refusal, or structurally usable tool requests establish interface
readiness. ALK does not replay returned tools or judge task success in this check.
The target itself may execute tools during the request.

Failed requests prevent certification. Candidate configuration errors use the
existing source-backed runtime-plan repair budget. Authentication and unexplained
application failures remain errors; they do not permit rewriting scenario outcomes.
Each attempt retains a bounded sanitized receipt under `generic-harness/chat-readiness`.
ALK resets a successful disposable instance before continuing and closes failed
instances. Certificates require explicit `chat_ingress` evidence. Voice and external
connect-only providers record that this check is not applicable.

One successful opening does not prove every scenario or a complete workflow. Socket
operations share a deadline, but urllib DNS resolution and response-header parsing
are not independently interruptible. The enclosing job and sandbox limits remain
the final execution bounds; cancellation drains the client worker before cleanup.
