---
kind: agent-learning.docs-page.v1
track: simulate
objective: behavior
stage: simulate
backing:
  - examples/sdk_spec_runner_quickstart.py
artifact_kinds: []
commands:
  - python examples/sdk_spec_runner_quickstart.py artifacts/spec-and-runner.json
postcondition: python -c "import json; p=json.load(open('artifacts/spec-and-runner.json')); assert 'COMPLETED' in p['status'], p['status']; print('ok')"
claims: []
doctor_checks:
  - missing_engine_modules
  - public_boundary_passed
opt_in_lane: false
---

# The Simulation Spine — One Spec, One Runner

> **Twin:** [`examples/sdk_spec_runner_quickstart.py`](../../examples/sdk_spec_runner_quickstart.py)
> · offline, no credentials.
> For the full end-to-end tour (real LLM, voice, platform submit, custom
> worlds) see [`examples/agent_learning_sdk_demo_v2.ipynb`](../../examples/agent_learning_sdk_demo_v2.ipynb).

The spec-based simulation runtime accepts a frozen `SimulationSpec` through
`SimulationRunner`. Environment plugins and endpoint/simulator profiles use
registries. This page exercises that runtime directly; it is not the same
executor as the source-authoring harness's `HarnessExecutor`, and does not
establish whole-harness or bundle portability. See
[environment authoring and extension contracts](environment-authoring.md).

The example is checkout-only; installing the wheel does not install `examples/`.
A custom environment's `run` currently returns a legacy `TestReport`, which the
runner converts into a `SimulationReport`. The
[registry test](../../tests/runtime/test_registry.py) demonstrates this with a
dummy plugin and no provider calls.

| Piece | In the SDK | You supply |
| --- | --- | --- |
| **Environment** | a registered plugin (`chat`, `voice`, or your own) that owns the world + action space | pick one, or `@register_environment` your own |
| **Target** | an ActorSource (`system_prompt` \| callable \| `factory` \| `http` \| `framework`), or any object with `.call()` | drop in your agent |
| **Scenario** | personas + situations + desired outcomes | describe the test |
| **Contract** | a frozen `SimulationSpec` tying the three together | declarative, secret-free |
| **Runner** | one `SimulationRunner` — same spine for chat and voice | `.run(spec, target=...)` |

## 1. Install and check

```bash
git clone https://github.com/future-agi/agent-learning-kit
cd agent-learning-kit
uv sync                     # or: pip install -e .
uv run agent-learn doctor   # inspect import/consolidation status; not a live readiness test
```

## 2. Run the smallest simulation

A synthetic user drives a conversation against a plain `.call()` target through
the runner. No API key, no network.

```python
import asyncio
import fi.alk.simulate as S
from fi.simulate.agent.wrapper import AgentInput, AgentResponse


class EchoAgent:
    async def call(self, agent_input: AgentInput) -> AgentResponse:
        last = agent_input.messages[-1]["content"] if agent_input.messages else "hi"
        return AgentResponse(content=f"You said: {last}. How can I help further?")


async def main():
    print(sorted(S.environment_registry.names()))   # ['chat', 'voice']
    spec = S.SimulationSpec(
        run_id="spec_runner_quickstart",
        environment=S.EnvironmentSpec(
            adapter=S.EnvironmentAdapters.CHAT,
            world_kind=S.WorldKinds.CONVERSATION,
            config={"max_turns": 3, "min_turns": 1},
        ),
        target=S.AgentEndpointSpec(adapter=S.TargetAdapters.CALLABLE),
        simulator=S.SimulatorPolicySpec(adapter=S.SimulatorAdapters.SYNTHETIC_USER),
        scenario=S.Scenario(name="late-delivery", dataset=[
            S.Persona(persona={"name": "Morgan", "role": "customer"},
                      situation="A delivery is 3 days late; ask for status and ETA.",
                      outcome="Get a clear status and a concrete next step.")]),
    )
    report = await S.SimulationRunner().run(spec, target=EchoAgent())
    print(report.status)
    print(report.test_cases[0].result.transcript)


asyncio.run(main())
```

The backing twin is the same run, writing the finished report to a path:

```bash
export AGENT_LEARNING_SYNC=local
export AGENT_LEARNING_LEDGER_PATH="$PWD/artifacts/spec-runner-ledger"
python examples/sdk_spec_runner_quickstart.py artifacts/spec-and-runner.json
python -c "import json; p=json.load(open('artifacts/spec-and-runner.json')); assert 'COMPLETED' in p['status']; assert p['transcript']; print('ok')"
```

The twin writes a compact summary, not the full runtime report or the CLI
`agent-learning.run.v1` envelope. `COMPLETED` is an execution status, not a quality
verdict. Use the [evaluation guide](../eval/overview.md) to score real evidence.

Every adapter slot accepts the enum (`S.EnvironmentAdapters.CHAT`) **or** the
plain string it equals (`"chat"`) — same `spec_hash`. Enums give you
autocomplete and typo-safety for the built-ins; custom registered names stay
plain strings.

## 3. Drop in your own agent

`EchoAgent` was passed straight to the runner. Every other way you'd hand the
kit an agent is an ActorSource resolved through one registry — you declare what
you have, you never edit the engine:

```python
S.get_profile("system_prompt").resolve_target({"system_prompt": "...", "model": "gpt-4o"})
S.get_profile("factory").resolve_target({"target": "mypkg.agents:Support", "factory": True})
S.get_profile("http").resolve_target({"url": "https://my-agent/turn"})
S.get_profile("framework").resolve_target({"target": "mypkg:graph"})   # LangGraph / CrewAI / ...
```

Code-loading kinds (`factory`, `import_object`, `framework`, callable) carry
`runs_caller_code == True` and are deny-by-default in hosted runs — locally they
resolve in-process, hosted they route through the sandbox. `http` and
`system_prompt` are the hosted-safe kinds. Check with
`S.get_profile("http").runs_caller_code`.

## 4. Where to go next

| Task | Spec + Runner API |
| --- | --- |
| Real-LLM chat | `SimulationRunner().run(spec, target=your_llm_object)` |
| Voice (Vapi / Retell / WebRTC) | `SimulationRunner().run(voice_spec)` — see the v2 notebook §7 |
| Score a run | `evaluate_agent_report(report.to_legacy())` |
| Register your own world | `@register_environment("name")` |
| Submit to the platform | `result_sink=FutureAGIResultSink(...)` |

- Full runnable tour: [`examples/agent_learning_sdk_demo_v2.ipynb`](../../examples/agent_learning_sdk_demo_v2.ipynb).
- The older manifest/CLI front door: [`first-run.md`](first-run.md) — same
  concepts, assembled into a `SimulationSpec` underneath.
- Frameworks as targets: [`simulate-any-framework.md`](simulate-any-framework.md).
