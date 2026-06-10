# Workflow Target Profile Matrix Readiness Research Note

Date: 2026-06-10

## Why This Exists

The base workflow target optimizer proves one selected workflow trace can carry
cross-framework source evidence. The next bar is profile-level proof: the same
AgentOptimizer target path must work independently for representative workflow
export shapes instead of depending on one canonical LangGraph-style payload.

## Gate Shape

`workflow_target_profile_matrix_readiness` runs
`examples/sdk_workflow_target_profile_matrix.py` locally. The cookbook executes
three deterministic optimizer profiles over the exact same target path:

- `simulation.environments.0.data.trace`

The profiles are:

- `langgraph`: canonical graph, checkpoint, interrupt, and replay fields.
- `crewai`: CrewAI Flow-style aliases such as `workflow_nodes`,
  `workflow_edges`, `workflow_steps`, `routes`, `workflow_checkpoints`,
  `workflow_interrupts`, `workflow_replay`, `pending_writes`,
  `state_history`, and `workflow_state`.
- `llamaindex`: LlamaIndex workflow/event-style trace fields with route,
  pending-write, state-history, and workflow-state aliases.

Each profile must keep the scripted agent fixed, keep prompt and whole-agent
paths out of the search space, select only
`simulation.environments.0.data.trace`, and pass local workflow trace coverage,
workflow graph quality, tool selection, artifact coverage, and task completion
metrics.

The release gate also renders the result through `agent-learn report`, requires
the `workflow_target_profile_matrix` card, verifies the generated action catalog,
and runs the profile export action. This keeps the matrix visible to Future AGI
UI/report surfaces instead of leaving it as a raw optimizer artifact.

## Evidence Bar

The selected runtime trace for every profile must include graph topology, route
decisions, durable checkpoints, replay, resolved interrupt, writes, final state,
step-level tool evidence, workflow events, and a trace artifact. This keeps the
proof Agent Learning-native while showing that framework-specific workflow
exports can normalize into the same simulator/evaluator/optimizer trinity.
