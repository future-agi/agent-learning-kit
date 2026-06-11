# Optimizer Profile Matrix Readiness — Research Note

Distilled from the Phase-4 optimizer-expansion research (R§3.1 backend-routing
folklore; R§1 2605.11030 setting-relative orderings; R§1 2604.19440 trajectory
shape as routing evidence; AdaptOrch topology columns; MemMachine/2603.02473
retrieval-dominance priors; RoboPhD declared-budget discipline).

## The claim the gate makes falsifiable

"Which optimizer backend should run for this target?" is answered today by
static defaults — folklore, not evidence. The `optimizer_profile_matrix_readiness`
gate replaces that with a declared, executed 3-axis evidence matrix:

- **Axes**: framework profile (the existing six: langgraph, crewai, llamaindex,
  langchain, pipecat, livekit) × target kind (`prompt`, `whole_agent`,
  `memory_ops`, `multi_agent_roster`, `workflow_trace`, `orchestration_spans`,
  `framework_method`) × backend token (`gepa`, `tpe`, `evolution_elo`,
  `bandit`, `society`, `regression_replay`).
- **Declared subset, not cartesian** (P4-D2): exactly 33 coordinates
  (27 new + 6 inherited workflow cells) pinned in
  `V1_OPTIMIZER_PROFILE_MATRIX_CELLS`; the gate asserts EXACTLY this set —
  growing coverage is a visible constant + example diff.
- **Per-cell winners only**: orderings invert across settings, so the payload
  schema has no global best-backend key and the gate red-flags any
  `global_best`-flavored aggregate (`aggregation_errors`).
- **Declared budgets**: every cell declares `eval_budget` (≤ 24 evaluations);
  actuals (`total_evaluations`) exceeding the declaration are `budget_errors`.
- **Routing table** (4D, asserted in `routing_errors` — no separate gate):
  the matrix example regenerates `examples/optimizer_routing_table.json`
  from its own same-run cells; the gate byte-compares regenerated vs
  committed, requires every recommendation to cite ≥ 1 same-run cell with
  matching axes and a winner equal to the recommendation, and excludes
  non-release-admissible (`live_lane`) evidence from recommendations (P4-D6).
- **Whole-agent cells** (4C) run the staged whole-agent contract
  (`component_text` → `structural_config` → `global_repolish`) and emit
  `agent-learning.apply-plan.v1` artifacts (7 fields; the platform applies and
  read-back-verifies — the kit never applies, P4-D5).

## Deterministic execution notes

Every cell runs the deterministic `local_text` engine with scripted agents —
no credentials, no network. Two backend tokens need deterministic stand-ins
on the release path:

- `gepa` cells declare `reflection_model: scripted_deterministic` and run the
  reflective-text-evolution family mechanics on the deterministic evolution
  engine (the vendored `GEPAOptimizer` requires an external LLM for both task
  generation and reflection, which is inadmissible on the credential-free
  release path).
- `regression_replay` cells run the real `FutureAGIRegressionReplayOptimizer`
  against an in-repo deterministic regression dataset fixture
  (`examples/frozen_profiles/regression_replay_dataset.json` pins the shape),
  delegating repair to the deterministic `agent` backend.

## Engagement contract (ARCH §2d)

Omitted `optimizer` consults the committed routing table by default;
explicit `optimizer=` (SDK) or `--backend` (CLI) always overrides with the
spurned recommendation kept visible; a missing row falls back to the static
default with `selected_by: "cold_start"` and a warning — exit 0, never an
error. The example exercises all three outcomes (`routing_checks`).
