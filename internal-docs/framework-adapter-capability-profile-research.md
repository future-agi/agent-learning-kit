# Framework Adapter Capability Profile Research

Date: 2026-06-10

## Why This Increment Exists

The previous native adapter matrix proved local contracts for many frameworks,
but the artifact was still contract-centric. The v1 trinity needs one portable
payload that simulate-sdk, ai-evaluation, and agent-opt can all consume without
installing LangChain, LiveKit, Pipecat, OpenAI Agents, or other optional
framework packages.

This increment adds:

- `simulate.framework_adapter_capability_profile(...)`
- `simulate.framework_adapter_capability_profiles(...)`
- `agent-learning.framework-adapter-capability-profile.v1`
- `agent-learning.framework-adapter-capability-profiles.v1`
- ai-evaluation extraction support for profile artifacts
- agent-opt proof coverage for profile bindings in framework matrix optimization
- `examples/sdk_framework_adapter_capability_profiles.py`

## Primary-Source Notes

- LangChain documents the Runnable API through `invoke` / `ainvoke` style
  execution surfaces in the official Python API reference:
  https://api.python.langchain.com/
- LiveKit Agents documents `AgentSession` as the orchestrator that manages user
  input, the voice pipeline, LLM invocation, output, events, observability, and
  control:
  https://docs.livekit.io/agents/logic/sessions/
- Pipecat documents Pipeline as the core orchestration component connecting
  frame processors, and Frames as the data/control units that move through a
  pipeline:
  https://docs.pipecat.ai/pipecat/learn/pipeline
  https://docs.pipecat.ai/api-reference/server/frames/overview
- OpenAI Agents SDK documents `Runner.run`, `Runner.run_sync`, and
  `Runner.run_streamed` as the execution entrypoints for agent workflows,
  including tools, handoffs, sessions, and streaming:
  https://openai.github.io/openai-agents-python/running_agents/
  https://openai.github.io/openai-agents-python/ref/run/

## Design

The profile is derived from `agent-learning.framework-adapter-contract.v1`.
It adds three explicit bindings:

- `simulate-sdk`: `wrap_framework`, `probe_framework_adapter`, and
  `framework_adapter_contract_matrix`
- `ai-evaluation`: `framework_adapter_contract_quality`
- `agent-opt`: `OptimizationTarget`, `AgentCandidate`, framework/integration/
  harness/evaluator layers, and shared search paths

The profile remains import-free. It is not a LangChain, LiveKit, or Pipecat
adapter implementation. It is a first-party capability declaration and proof
artifact for local simulation, evaluation, and optimization.

## Follow-Up Bar

The next increment should let run manifests attach
`framework_adapter_capability_profiles` beside the matrix directly, then expose
profile-level cards in reports and promotion manifests. The evaluator already
accepts the profile payload as evidence, and the optimizer proof now verifies
that selected framework matrices carry all three trinity bindings.
