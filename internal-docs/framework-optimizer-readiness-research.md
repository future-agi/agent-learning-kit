# Framework Optimizer Readiness

## Purpose

The V1 optimizer bar is not prompt-only. A framework optimizer candidate must be
able to change the local adapter configuration, framework evidence bundle, world
contract, memory lineage, multi-agent handoff room, certification import bundle,
or evaluator-facing evidence until the selected candidate passes local evals.

## Local Contract

- Custom framework adapter optimization proves that AgentOptimizer can select a
  proprietary framework adapter method and payload shape, not only prompt text.
- Social-memory framework optimization proves that optimizer-memory synthesis can
  combine adapter and trace patches into the strongest local candidate.
- World/framework/memory optimization proves a single search can choose a
  verified world, framework trace, retrieval, memory-lineage, and multi-agent
  environment stack.
- Multi-agent framework handoff optimization proves captured OpenAI Agents,
  AutoGen, CrewAI, and LangGraph-style transcripts can be optimized with a
  room-level handoff/review/reconciliation contract and native coordination
  proof.
- Framework certification optimization proves lifecycle, capability, probe, and
  portability evidence can be selected as one optimizer candidate.
- Framework import repair proves broken or partial imports can be repaired into a
  complete local framework-import evidence bundle.

## Release Gate

`agent-learn release-check` runs the representative optimizer cookbooks as
`framework_optimizer_readiness`. The gate requires local execution, passing
optimization/evaluation scores, candidate lineage, expected best patch surfaces,
expected best environment types, required quality metrics, optimizer trace
identity, and native proof payloads where the optimizer surface has one.

The gate intentionally uses synthetic local environment values. Real service
keys and live framework endpoints stay outside release metadata unless a user
explicitly selects that live workload.
