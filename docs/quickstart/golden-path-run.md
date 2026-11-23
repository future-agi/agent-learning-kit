---
kind: agent-learning.docs-page.v1
track: quickstart
backing: []
artifact_kinds:
  - agent-learning.run.v1
commands:
  - agent-learn init . --preset run
  - agent-learn run manifests/run.json --output artifacts/run.json
postcondition: python -c "import json; p=json.load(open('artifacts/run.json')); assert p['kind']=='agent-learning.run.v1', p['kind']; assert p['status']=='passed', p['status']; print('ok')"
claims: []
doctor_checks:
  - missing_engine_modules
  - api_key_configured
opt_in_lane: false
---

# Golden path: first simulation run

Run a single conversation and read what the agent said. The example uses a
scripted reply and scores it locally. Once you've [installed ALK](../../README.md#install),
you can finish this guide without a network connection, Docker or credentials.

## Create a scenario

Run these commands in an empty directory. These settings use the bundled model prices, keep account sync local and store the run history alongside this example.

```bash
export LITELLM_LOCAL_MODEL_COST_MAP=True
export AGENT_LEARNING_SYNC=local
export AGENT_LEARNING_LEDGER_PATH="$PWD/artifacts/ledger"
agent-learn init . --preset run
```

Open `manifests/run.json`. It describes Kai, a simulated user who needs a
CLI smoke test to pass. The manifest supplies a scripted answer and the
minimum evaluation score for the run.

## Run the scenario and read the conversation

```bash
agent-learn run manifests/run.json --output artifacts/run.json
```

ALK prints the path to the saved result. To read it in Python:

```python
import json
from pathlib import Path

result = json.loads(Path("artifacts/run.json").read_text())
print(result["status"], result["summary"]["case_count"])
print(result["report"]["results"][0]["transcript"])
```

The first line reads `passed 1`. The transcript ends with
`Agent: The local CLI smoke test passes.` The agent repeated the expected
reply, and the evaluation passed. You now have a working installation and a
record of exactly what ALK evaluated.

## Run the same scenario from Python

To use the result directly in your application, pass the generated manifest
to `run_manifest_file`. It returns the run as a Python dictionary. Use the same shell so the local settings still apply.

```python
import asyncio
from fi.alk import simulate

result = asyncio.run(simulate.run_manifest_file("manifests/run.json"))
print(result["status"], result["summary"]["case_count"])
```

## If the first run fails

- If `agent-learn` is missing, activate the Python environment where you installed ALK
- If `init` reports that files already exist, choose a new empty directory to preserve your work
- If an import fails, run `agent-learn doctor` to see which module is missing

To replace the scripted reply, [connect your own agent](../simulate/simulate-any-framework.md).
To change what counts as a passing answer, [write an evaluation](../eval/overview.md).
For a new environment, [try the order-status plugin](../simulate/environment-authoring.md#register-a-custom-environment).
