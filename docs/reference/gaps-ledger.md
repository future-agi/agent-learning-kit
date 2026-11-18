---
kind: agent-learning.docs-page.v1
track: reference
backing: []
artifact_kinds: []
commands: []
postcondition: ""
claims: []
doctor_checks: []
opt_in_lane: false
---

# Environment extension gaps

This ledger records missing ALK extension contracts. Each row states what works
now and links the follow-up that owns the missing behavior. The Future AGI
platform owns public job submission, so that boundary is not an ALK gap.

| Gap | Affected contract | Current behavior | Workaround | Evidence | Ticket | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Installed stage backends have no package discovery | `HarnessBackend` and `HarnessSession` | `backends.register(name, loader)` changes the registry only in the current process. ALK registers `claude` and `vertex-gemini` itself. | Import the backend and call `register` before the harness resolves `ALK_HARNESS`. | [`backends/__init__.py`](../../src/fi/alk/harness/backends/__init__.py) and the [extension matrix](../simulate/environment-authoring.md#supported-extension-seams) | [TH-10648](https://linear.app/future-agi/issue/TH-10648/alk-discover-harness-stage-backends-through-package-entry-points) | Open |
| The harness executor cannot select another runtime provider | `RuntimeProvider` | `runtime.py` defines the protocol, but the CLI build path calls the local provisioner directly. `HarnessExecutor` has no provider parameter or registry. | Use the supported local or hosted runtime path that owns its provider integration. | [`runtime.py`](../../src/fi/alk/harness/runtime.py), [`cli.py`](../../src/fi/alk/harness/cli.py), and the [extension matrix](../simulate/environment-authoring.md#supported-extension-seams) | [TH-10651](https://linear.app/future-agi/issue/TH-10651/alk-make-runtimeprovider-selectable-through-the-harness-cli-and) | Open |
| Alternative executors have no conformance contract | Whole harness execution | `HarnessExecutor` invokes ALK's private `_auto` pipeline. The stage backend and simulation plugin protocols do not replace the whole executor. | Integrate at a tested stage, target, simulation plugin, evidence, or artifact boundary. | [`executor.py`](../../src/fi/alk/harness/executor.py), [harness architecture](../../src/fi/alk/harness/ARCHITECTURE.md), and the [extension matrix](../simulate/environment-authoring.md#supported-extension-seams) | [TH-10649](https://linear.app/future-agi/issue/TH-10649/alk-define-and-test-an-alternative-harness-executor-conformance) | Open |
| V1 and V2 bundles have no interchange policy | `EnvironmentBundle` and `EnvironmentBundleV2` | The bundle versions have separate schemas, loaders, and consumers. `harness environment up --bundle` loads V1. The certified process runtime consumes V2. | Keep the bundle version with its documented consumer. Do not pass one version to the other loader. | [`bundle.py`](../../src/fi/alk/harness/bundle.py), [`bundle_v2.py`](../../src/fi/alk/harness/bundle_v2.py), and the [extension matrix](../simulate/environment-authoring.md#supported-extension-seams) | [TH-10650](https://linear.app/future-agi/issue/TH-10650/alk-define-the-compatibility-policy-for-v1-and-v2-environment-bundles) | Open |

## Boundaries that are not ALK gaps

- Use the Future AGI platform dashboard to create a hosted Environment job,
  wait for it, or read platform-stored results today. Use `futureagi-sdk` only
  after a released version includes Environment operations. ALK's worker and
  sandbox entry points are internal.
- Provider credentials, Docker, and model access are prerequisites for selected
  execution lanes. Their absence does not imply a missing SDK hook.
- The current hosted venv uses Python 3.12.3. The Python 3.13 SDK-smoke job runs
  tracer subprocesses only; it does not certify the hosted venv on Python 3.13.
