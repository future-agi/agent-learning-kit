# fi.alk.generation

Point it at an agent and get back test scenarios that can actually be run and graded: what the
caller says, what the tools return, and the exact checks that decide pass or fail.

```bash
python -m fi.alk.generation --environment voice --repo /path/to/agent --n 20 --out artifacts/scenarios
```

## Why this exists

Writing good tests for a conversational agent is slow, and most generated ones are unusable for the
same two reasons: they reference things the agent does not have, and they are graded by asking a
model whether the conversation "seemed fine". This package fixes both. Every value in a scenario has
to exist in the agent's own code, and most checks are settled by comparing recorded tool calls and
final state in plain Python.

## Quick start

```bash
# 1. generate
python -m fi.alk.generation \
  --environment voice \
  --repo /path/to/agent \
  --n 20 \
  --out artifacts/scenarios

# 2. see what you got
open artifacts/scenarios/report.md

# 3. check nothing was invented
python scripts/audit_generated_scenarios.py artifacts/scenarios /path/to/agent
```

The audit takes two positional paths. `grounding failures: 0` is the number that matters: anything
higher means a scenario references a tool or value the agent does not have.

### Useful flags

| Flag | What it does |
|---|---|
| `--environment` | **Required.** `voice` or `chat`. Anything else is refused by name, because a scenario is only worth generating if a runtime can stage and grade it |
| `--contract` | Reuse a previous run's `contract.json` and skip re-reading the agent. The biggest time and cost saving available |
| `--traces` | A file or folder of real recorded conversations. The harness explores it, picks the ones that went wrong, and builds tests that recreate them |
| `--guidance` | One instruction in your own words. Scenarios answering it are generated before generic coverage |
| `--n` | Target count, delivered exactly, or an explicit statement that fewer distinct ones exist |
| `--budget-usd` | Hard spend ceiling. Partial results are already on disk when it stops |
| `--workers` | Parallel scenario writers, default 8 |
| `--model` | Any litellm model string |

## What you get

For each scenario, three strictly separated parts:

- **The input** - what the simulated user is told: their situation, their goal, and the facts they
  hold. Facts carry a disclosure rule, so information the agent is supposed to *ask* for is withheld
  until it does. Never contains the answer or anything the user could not know.
- **The environment** - starting state plus a mock response per tool, so the test runs without
  touching anything real.
- **The checks** - one per sub-goal, each asserting a tool call with its arguments, a final state, a
  value the agent had to say, or an action it must not have taken. A model judges only what none of
  those can witness.

Written to your output directory:

```
contract.json          what the harness worked out about the agent; pass to --contract next time
scenarios/<id>.json    the readable test
alk/<id>.json          a typed fi.simulate Scenario for the runtime
report.md              what each test catches, where it came from, what had to be assumed
usage.json             calls, tokens, spend
```

Each scenario records where it came from: a real recorded call, the neighbourhood around a real
failure, your instruction, or baseline coverage.

## How it works

```
agent source
   └── read the code, write down the real tools, values and rules      (model, file tools)
         └── name the shared milestones a suite reuses                 (model)
               ├── real recordings: explore, pick failures, recreate,  (model, file tools)
               │   and surround each failure with neighbours
               ├── your instruction                                    (model)
               └── everything else a complete suite needs              (model)
                     └── per scenario: write it, then four gates
                           validators   every id and tool exists       (code)
                           oracle       does it contradict itself      (code)
                           dedup        is it a test we already have   (code)
                           reviewer     could a good agent fail it     (model)
                     └── suite review: what is the set still missing   (model)
```

The model is called at the named steps and nowhere else. Everything between them is ordinary code,
including every decision about when a loop stops, so behaviour stays stable as the count grows.

## Running a scenario against a live voice agent

Generation produces the test. Running it needs a provider and a room. The supported path today is a
Vapi assistant over the web transport, with no phone number involved.

```python
from fi.alk.generation.simulate_bridge import simulator_prompt, persona_from_record
from fi.alk.generation.vapi_live import ScenarioMockServer, assistant_payload
```

Three moving parts:

1. **`ScenarioMockServer`** serves one scenario's mock responses over HTTP, applies its state
   updates, and records every tool call with the arguments the agent passed. Providers execute tools
   by calling a public webhook, so the server needs a public URL - any HTTP tunnel works.
2. **`assistant_payload`** builds the assistant from the extracted contract: the same system prompt,
   the same rules, the same tools with their real argument names, each pointing at that URL.
3. **`persona_from_record`** turns the scenario into the simulated caller, including the disclosure
   rules that make an elicitation test mean anything.

One command runs the whole thing: it serves the scenario's mocks, points the assistant at them,
places the call, grades the checkpoints and writes the trace.

```bash
python oss/simulation-acceptance/run_voice_case.py 2.1.2 --scenario <scenario>.json --dry-run
python oss/simulation-acceptance/run_voice_case.py 2.1.2 --scenario <scenario>.json
```

`2.1.2` is inbound, where the caller speaks first; `2.2.2` is outbound, where the agent does. Both
work with a generated scenario. Without `--scenario` the command behaves exactly as before;
`--no-mock-tools`, `--no-grade` and `--no-trace` turn off each added part.

Alongside the usual `manifest.json`, `report.json` and `recordings/`, the run writes `checks.json`
(each checkpoint and its verdict) and `trace.json` / `trace.md` (the turns, the tool calls with
their arguments, the resulting state, and the verdict). The exit code is non-zero when the
scenario's own checks fail, even if the conversation completed, so it can gate CI directly.

To grade a run yourself:

```python
from fi.alk.generation.checks import evaluate_scenario

evaluate_scenario(
    record,
    tool_calls=mock_server.log.snapshot(),
    transcript_turns=[m["content"] for m in messages if m["role"] == "assistant"],
    final_state=mock_server.final_state,
)
```

Tool calls come from the mock server rather than the provider's post-call artifact, because the
server saw every call as it happened while provider evidence can lag or be dropped.

A run that reports `completed` means a conversation happened. It does not mean the agent behaved
correctly - that is what the checks above are for, and the two can disagree.

## Configuration

Credentials are environment variables only and are never written into a scenario. Generation needs
credentials for whichever model provider the `--model` string names. A live voice run additionally
needs the provider API key, an assistant id, a speech provider key, and a LiveKit URL with its key
and secret; the acceptance runner names each one it is missing.

## Extending

| Want | Do |
|---|---|
| A new agent connection | implement `AgentSource` (three members) and `@register_source("name")` |
| A new environment | add one `EnvironmentProfile` to `environments.py`, once the runtime carries a plugin for it |
| A new checkpoint kind | add it in four places: the vocabulary in `prompts.py`, `validators.py`, `checks.py`, and the emit mapping. All four, or the kind gets written and never graded |
| A different similarity measure | replace `similarity` in `dedup.py`; callers are unaffected |
| A different model backend | `LLMClient` is a two-method protocol |

## Tests

```bash
python -m pytest tests/test_generation_pipeline.py -q
```

Fully offline against a fake model, so they cost nothing. Each one encodes a defect that actually
occurred.
