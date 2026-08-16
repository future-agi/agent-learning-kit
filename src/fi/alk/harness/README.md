# The harness

Point it at an agent. It reads the agent, builds a real database its tools run against, writes
test scenarios, runs them as conversations, and tells you what held and what did not.

Nothing here is written for a particular agent. Every stage takes the contract and the world as
input, so a different agent is the same commands with a different name.

---

# Part 1 — Setting up, from nothing

If you have never run this before, do these five steps in order. They take about ten minutes,
most of which is waiting for the install.

## Before you start

You need four things on your machine:

| What | Check it with | If missing |
|---|---|---|
| Python 3.10 or newer | `python3 --version` | install from python.org, or `brew install python` |
| `uv` (the package manager this repo uses) | `uv --version` | `brew install uv` |
| The `claude` command | `claude --version` | `npm install -g @anthropic-ai/claude-code` |
| A Google Cloud service-account key file (`.json`) for Vertex AI | you were given one, or ask | ask whoever set up your GCP access |

The `claude` command matters: the harness talks to the model through the Claude Agent SDK, and
that SDK runs the `claude` binary under the hood. If it is not installed, every stage fails
immediately with a connection error.

## Step 1 — Go to the repo

Every command in this document is run from the **root of the repo**, not from this folder:

```bash
cd path/to/agent-learning-kit
```

Wherever you cloned it, that directory is the one containing `pyproject.toml`. Check you are in
the right place:

```bash
ls pyproject.toml        # should print: pyproject.toml
```

If that errors, you are in the wrong directory. Do not continue until it works.

## Step 2 — Install the dependencies

```bash
uv sync --extra livekit --group dev
```

This reads `pyproject.toml`, downloads everything, and creates a folder called `.venv` in the
repo root. That folder is the "virtual environment": a private copy of Python with this
project's packages in it, so they do not collide with anything else on your machine.

The `--extra livekit` matters even though the harness never makes a voice call. The harness
builds on `fi.simulate.environment`, and importing anything from `fi.simulate` runs that
package's `__init__`, which pulls in its LiveKit scenario generator. Plain `uv sync` leaves that
out and every command dies with `No module named 'livekit'`.

It takes a few minutes the first time. You only do this once.

## Step 3 — Use the virtual environment

Two ways. **Pick one and stick with it.**

**Option A — no activation (what this document uses).** Call the Python inside `.venv` directly:

```bash
.venv/bin/python -m fi.alk.harness
```

Nothing to remember, nothing to undo, works in a fresh terminal every time. Every command below
is written this way.

**Option B — activate it.** If you prefer typing plain `python`:

```bash
source .venv/bin/activate     # your prompt now shows (agent-learning-kit)
python -m fi.alk.harness      # plain "python" now means the one in .venv
deactivate                    # when you are done
```

Activation only lasts for that terminal window. Open a new tab and you must activate again. If a
command ever fails with `No module named fi`, you almost certainly forgot.

## Step 4 — Credentials

The harness reaches the model through Vertex AI, which needs your Google Cloud service-account
key. Nothing is hardcoded and no key is ever read from source.

Create a local env file from the template that ships with the repo:

```bash
cp oss/simulation-acceptance/.env.example .env.acceptance
```

Open `.env.acceptance` in an editor and fill in two lines:

```bash
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/your-service-account.json
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
```

`.env.acceptance` is git-ignored. It holds a path to a private key: **never commit it, never
paste its contents into Slack or a PR.**

Now load it into your terminal, and pick a model:

```bash
set -a; . ./.env.acceptance; set +a
export CLOUD_ML_REGION=global
export ALK_HARNESS_MODEL=claude-haiku-4-5
```

- `set -a; . ./file; set +a` means "read this file and export everything in it". The leading
  `. ` (dot space) is what runs it in your *current* shell, so the variables stick around.
- `ALK_HARNESS_MODEL` picks the model. `claude-haiku-4-5` is cheapest and fine for trying things
  out. Use `claude-sonnet-4-6` when you want better scenarios.

These last only for the current terminal window. Every new terminal, run these three lines again.

## Step 5 — Check it works

```bash
.venv/bin/python -m pytest tests/test_harness.py -q
```

These are offline tests: no model calls, no credentials, no network. If they pass, your
install is fine. If they fail, the problem is Step 2, not your credentials.

Then check the credentials separately, with the cheapest thing that talks to the model:

```bash
.venv/bin/python -m fi.alk.harness
```

Say hello. If it answers, the credentials work; type `q` to leave before it spends anything
real.

---

# Part 2 — Using it

## The short version

```bash
cd path/to/agent-learning-kit
set -a; . ./.env.acceptance; set +a
export CLOUD_ML_REGION=global ALK_HARNESS_MODEL=claude-haiku-4-5

.venv/bin/python -m fi.alk.harness
```

That last line is the whole interface. It opens with "which agent would you like to test, and
where is it?", and everything after that is a conversation. It finds the agent, reads it, builds
the world, writes the scenarios, and runs them, moving on as each stage produces its artifact.

While you are in it:

- type what you want and press enter
- press enter on an **empty** line to move to the next stage
- type `q` to leave

Everything it produces is written to `artifacts/environments/<agent-name>/`.

## The same stages, one at a time

Useful when you want to redo one thing without walking the whole conversation. Each of these
stays open for corrections until you type `q`; add `--once` to run it unattended and exit.

```bash
# read an agent's source and write down what it verifiably is
.venv/bin/python -m fi.alk.harness understand --name my_agent --path ../my-agent-repo

# build the environment: the world, the simulator prompt, the sub-goal catalogue
.venv/bin/python -m fi.alk.harness build --name my_agent

# write the test scenarios, each proved before it is kept
.venv/bin/python -m fi.alk.harness scenarios --name my_agent --count 10

# run them against the world here, and grade
.venv/bin/python -m fi.alk.harness run --name my_agent

# or run them against the real hosted agent, as a conversation
.venv/bin/python -m fi.alk.harness live --name my_agent
```

`--name` is just a label for the folder your artifacts go in. `--path` is where the agent's code
lives — a path to another repo on your disk.

Useful extras:

- `run --only <name> [<name> ...]` runs a single scenario instead of all of them
- `run --quiet` hides the conversation and prints only verdicts
- `scenarios` without `--count` uses however many already exist, because coming back to change
  one is not a request for a different number of them

## What each stage does

**understand** reads the agent's source and produces `contract.json`: its tools, the exact
argument names and permitted values, its hard rules, its real data. Everything downstream is
confined to this, which is what stops later stages inventing tools or menu items. Anything
changed later goes through an amendment tool and is recorded with its reason, so what came from
the agent and what came from us stay distinguishable.

**build** produces everything common to every test of this agent:

- **the world** — a real database behind the agent's tools, with one handler per tool that can
  genuinely refuse: a nonexistent id, an unavailable item, an argument outside what the tool
  accepts. A refusal is the world working; a crash is a defect, and the two are never confused.
- **the simulator prompt** — for a conversational agent, the person on the other side, written
  once with `{{ slot }}` variables each scenario fills.
- **the sub-goal catalogue** — the named things this agent can be checked on, each carrying its
  check **as code** wherever the answer is observable, and marked judged only where nothing is.

It is exercised before it can be saved — every tool probed with a valid call, a bogus id and a
missing argument, plus declared sequences where state must carry across calls — and `save_world`
refuses a world that fails, has no sequences, no sub-goals, only judged sub-goals, no simulator
prompt for a conversational agent, or rows left over from its own testing.

**scenarios** writes each test as a **delta** on that base: a few rows changed after reset, an
instruction that fills the simulator prompt, a reference solution, and which catalogue sub-goals
must hold. Before a scenario is kept it is **proved**, twice, with no model involved:

1. reset → setup → run the solution → run the checks — they must **pass** (it is solvable)
2. reset → setup → run **nothing** → run the checks — they must **fail** (they grade something)

**run** gives each scenario its own restored copy of the world and grades from what is left
behind: the state of the world plus every tool call with its arguments. `run` converses with the
agent locally, rebuilt from its contract. `live` is the same grading against the **real hosted
agent**: the webhook its own tools call is answered by the world, so a call for something that
is not there is refused rather than mocked into success.

## How it grades

Deterministic by default, a judge only as the fallback.

Every sub-goal with a check in code is settled by running that check against two things the run
left behind: the world afterwards, and the recorded tool calls with their arguments — so "booked
10 PM when 11 PM was asked" is caught without any judgement. Sub-goals marked judged are handed
to a model with three kinds of evidence: what was said, what the agent actually did, and the
state afterwards. An unanswered claim counts as failed, never as passed, and judged results are
always reported as judged rather than blended into the code-settled score.

```
PASS  quantity_and_unavailable  3/3 sub-goals settled by code
  [x] quantity_honored
  [x] unavailable_drink_refused
  [x] regular_item_placed_correctly
  [?] no_unrequested_items — judged, not settled by code

what the agent actually did:
  order_regular_item({'item_id': 'hamburger'}) -> ok
  order_regular_item({'item_id': 'hamburger'}) -> ok
```

A run where the world crashed is `VOID`, not `FAIL` — that says nothing about the agent. A check
that raises is a **broken check**, reported as ours, never scored against the agent.

## What it refuses to do

These are the parts worth understanding, because they are what make a result mean something.

- A world that fails its own probes will not save; nor will one with no sequences, no sub-goals,
  only judged sub-goals, or rows left over from building it.
- A scenario is not kept until its own solution passes its own checks, and those checks fail
  when nothing is done. Unsolvable scenarios and vacuous checks die here, at write time.
- A scenario naming a sub-goal nobody defined, or a table nobody built, is rejected and told
  what does exist.
- A suite where no sub-goal is shared between scenarios will not save, because nothing would
  roll up across it.
- Changing the contract is allowed but never silent: every widening, added rule or corrected
  tool is recorded with its reason in `amendments[]`.

If a stage tells you it will not do something, that is the design, not a bug to route around.

## Rough costs

On Haiku: reading an agent about $0.15, writing three proved scenarios about $0.12, a graded
local run a few cents per scenario. Building the environment is the expensive stage — about
$1.80 on Sonnet, which is worth using there even when everything else runs on Haiku
(`ALK_HARNESS_MODEL` per stage).

## When something goes wrong

| What you see | What it means |
|---|---|
| `No module named fi` | Wrong directory, or you are using system `python` instead of `.venv/bin/python` |
| `command not found: uv` | `brew install uv` |
| `No module named 'livekit'` | You ran plain `uv sync`. Run `uv sync --extra livekit --group dev` |
| Fails instantly on any model call | The `claude` command is not installed, or your env vars are not loaded in this terminal |
| `Could not load the default credentials` | `GOOGLE_APPLICATION_CREDENTIALS` is unset or points at a file that is not there |
| `No contract at ...` | Run `understand` first |
| `No world at ...` | Run `build` first |
| A stage does nothing and exits | It ran out of turns. Look at the last few lines: it usually says what it was stuck on |

Everything a stage did is printed as it happens, and every run is kept in
`artifacts/environments/<agent>/runs.json`, including the transcript and every tool call.

---

# Part 3 — For developers

## Adding to it

- A new **agent** is nothing: the same stages read its contract.
- A new **kind of world** is a class and a registration in `world/kinds.py`. Browser is registered
  and stubbed; sqlite is the one built out.
- A new **place the agent runs** is a class and a registration in `run/targets.py`. `local` runs
  the agent here from its contract; the live voice path answers a hosted assistant's webhook from
  the same `world.handle_tool_call`, so the world, the scenarios and the grading do not change.
- A change to **how a stage works** is an edit to its `skills/<stage>/SKILL.md`. The markdown is
  the method; code holds only what must be exact.

## Not done yet

- Browser worlds are registered but not built.
- Snapshots are local files, not object storage.
- Judged sub-goals on the live path are reported as judged, not yet sent to a judge.
- Nothing reports which of the contract's use cases have no scenario.

## Tests

```bash
.venv/bin/python -m pytest tests/test_harness.py -q     # offline, no credentials needed
```
