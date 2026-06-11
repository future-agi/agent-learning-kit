---
kind: agent-learning.docs-page.v1
track: frameworks
objective: behavior
stage: simulate
backing:
  - examples/sdk_framework_adapter_a2a_protocol_trace.py
artifact_kinds:
  - agent-learning.run.v1
commands:
  - python examples/sdk_framework_adapter_a2a_protocol_trace.py artifacts/framework-a2a.json
  - agent-learn run artifacts/framework-a2a.manifest.json --output artifacts/framework-a2a-cli.json
postcondition: python -c "import json; p=json.load(open('artifacts/framework-a2a.json')); assert p['kind']=='agent-learning.run.v1', p['kind']; print('ok')"
claims: []
doctor_checks:
  - missing_engine_modules
  - public_boundary_passed
opt_in_lane: false
---

# A2A: offline framework-adapter simulation

> **Twin:** [`examples/sdk_framework_adapter_a2a_protocol_trace.py`](../../examples/sdk_framework_adapter_a2a_protocol_trace.py)
> · emits `agent-learning.run.v1` · offline, no credentials.
> A coding agent can complete this page from the frontmatter alone.

## 1. What you are testing

Agent2Agent (A2A) coverage in the kit is probe-promoted at the protocol layer: what
gets simulated is an A2A protocol trace export, not a string answer. The twin,
[`examples/sdk_framework_adapter_a2a_protocol_trace.py`](../../examples/sdk_framework_adapter_a2a_protocol_trace.py),
builds a local `LocalA2AReviewAgent` whose verified `send_message` entrypoint
returns a typed `A2AProtocolTraceExport`: the agent card (name, url, version,
`protocolVersion: 0.3.0`, `preferredTransport: JSONRPC`, input modes), the A2A
event stream, and the task list with context and task ids. A weak `run(text)` path
with no protocol-task evidence exists on the same object, and promotion records it
as weak.

The failure class this catches is task-state loss: an agent that participates in
A2A delegation can return acceptable text while the harness never confirms the task
lifecycle — created, status updates, completion — that the protocol is built
around. The trace export makes the agent card and per-task event history checkable
fields of the artifact.

There is no separate manifest file for this page: the twin builds its run manifest
in code, writes it next to the output
(`artifacts/framework-a2a.manifest.json`), and executes it through the same
`simulate.run_manifest_file` path the CLI uses. Everything runs on the local
engine: offline, deterministic, no remote agent endpoint and no provider keys.

## 2. Run it

CLI — the twin is executable and writes both the run artifact and the manifest it
ran, which you can then replay through `agent-learn`:

```bash
python examples/sdk_framework_adapter_a2a_protocol_trace.py artifacts/framework-a2a.json
agent-learn run artifacts/framework-a2a.manifest.json \
  --output artifacts/framework-a2a-cli.json
```

SDK, same operation:

```python
from sdk_framework_adapter_a2a_protocol_trace import run  # examples/ on sys.path

result = run("artifacts/framework-a2a.json")
assert result["kind"] == "agent-learning.run.v1"
```

## 3. What you built

Postcondition (machine-checkable — the same check the docs gate pattern uses):

```bash
python -c "import json; p=json.load(open('artifacts/framework-a2a.json')); assert p['kind']=='agent-learning.run.v1', p['kind']; print('ok')"
```

The artifact carries `status`, the simulated transcript, the evaluation report,
and the A2A protocol export — agent card, events, and tasks with their context and
task ids — plus the exact manifest that produced it. It is a replayable record,
not a log line: the same file feeds `baseline`, `compare`, and `replay`.

## 4. When it fails

| Symptom | First-mile class | Doctor check |
| --- | --- | --- |
| `vendored import failed` | infra | `agent-learn doctor` → `summary.missing_engine_modules` |
| manifest replay rejected | config fault | `agent-learn doctor` → `summary.public_boundary_passed` plus the manifest error line |
| export missing the agent card or task events (weak text path) | behavior regression | re-run the twin promotion and compare the protocol export against the text fallback |

## 5. Prove it / keep it

The twin is admitted by the `protocol_adapter_readiness` release gate, so every
`agent-learn release-check` re-executes this exact protocol-trace path — the page
stays true or the release fails. To keep your own A2A endpoint honest, promote the
run artifact into a regression baseline with the `baseline` /
`promote-to-regression` / `compare` command family, and treat the protocol export
as the contract: a card or task-lifecycle change shows up in the artifact diff
before it surprises a peer agent. The reader's job here is maintenance of a living
proof, not a one-off demo.
