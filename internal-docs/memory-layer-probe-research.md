# Memory Layer Probe Research Note

Date: 2026-06-08

## Why This Exists

The framework adapter probe now closes local method/input evidence before a
full manifest. Memory layers need the same preflight path: a user should be
able to hand the SDK a local memory backend or manifest-style memory candidate,
prove current retrieval, read/write/recall lineage, and governance locally, then
promote the selected candidate into the normal `agent-learning.run.v1` memory
simulation path.

## Current Memory Signals

- LangGraph separates checkpoints from long-term stores: checkpointers persist
  graph state within threads, while the Store interface retains information
  across threads. Probe implication: memory evidence must distinguish state
  checkpoint/history from long-term namespaced memory and preserve namespace
  evidence. Source: https://docs.langchain.com/oss/python/langgraph/persistence
- Mem0 exposes agent-oriented add, search, list, update, and delete memory
  operations through structured CLI/API outputs. Probe implication: a local
  memory layer probe should normalize CRUD/search evidence without assuming one
  vendor-specific method name. Source: https://docs.mem0.ai/platform/cli
- Zep exposes high-level session memory with `memory.add`/`memory.get` and
  lower-level graph retrieval for custom memory context. Probe implication:
  framework memory outputs should preserve session/thread ids, graph/search
  documents, and context provenance. Source: https://help.getzep.com/v2/memory
- LoCoMo evaluates very long-term conversational memory across multi-session
  question answering, summarization, and multimodal dialogue. Probe implication:
  retrieval evidence alone is too weak; simulations need source attribution,
  temporal freshness, and cross-session recall signals. Source:
  https://arxiv.org/abs/2402.17753
- LoCoMo-Plus emphasizes latent constraints and cue-trigger semantic disconnect,
  not just factual recall. Probe implication: memory scoring should preserve
  constraints and policy/governance signals, because later tasks may depend on
  implicit memory state. Source: https://arxiv.org/abs/2602.10715
- MemoryBank-style long-term memory systems organize user history, persona, and
  evolving memory records for future interactions. Probe implication: memory
  records should include lineage, source IDs, and update operations rather than
  only retrieved text. Source: https://arxiv.org/abs/2305.10250

## Implementation Rule

Keep memory support local-first:

- Accept manifest-style memory candidates and simple local objects with common
  write/search/read/recall method names.
- Reject HTTP/HTTPS targets by default.
- Emit `retrieval_memory` and `agent_memory_lineage` environments so existing
  `retrieval_memory_attribution`, `agent_memory_lineage_quality`, and
  `memory_integrity` metrics can score the promoted run.
- When memory evidence comes back through a framework adapter, normalize
  explicit checkpoint, memory operation, store, memory record, retrieval/search,
  and governance-policy fields into `framework_memory`, `retrieval_memory`, and
  `agent_memory_lineage` state so framework optimization and memory-layer
  evaluation use the same proof shape.
- Require current-document citations, freshness checks, source attribution,
  audited read/write/recall operations, tenant isolation, audit, retention,
  deletion, redaction, canaries, observability, and artifacts before a probe is
  considered closed.
- Use `optimize.optimize_memory_layer_probe()` to select among local memory
  candidates cheaply, then use
  `optimize.build_memory_run_manifest_from_probe_optimization()` when the
  selected candidate should become a normal evaluated memory simulation.
