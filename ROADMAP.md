# Agent Learning Kit — Roadmap

> What is implemented today, what is planned next. Every "implemented" claim
> below is backed by an executable release gate (`agent-learn release-check`)
> and a passed full release proof (`agent-learning.release-proof.v1`) — the
> kit's rule is that no capability claim ships without a gate that proves it.
> The internal per-gate map lives in [`V1_RELEASE_ROADMAP.md`](V1_RELEASE_ROADMAP.md).

Status date: 2026-06-11. Release candidate: tag `v1.0.0-rc.1`
(Python `agent-learning-kit==0.1.0`, TypeScript `@future-agi/agent-learning-kit==0.2.0`).

---

## Implemented

### Core: one SDK, one CLI, three engines

- Single public surface: `agent_learning` (Python), `agent-learn` (CLI),
  `@future-agi/agent-learning-kit` (TypeScript, evaluation-focused).
- Three engines, four workflows — `simulate`, `evals`, `optimize`, with
  red-teaming riding on simulate + evals.
- One key: `AGENT_LEARNING_API_KEY` (with `FUTURE_AGI_API_KEY` / `FI_API_KEY`
  aliases). Fully offline by default — no credential is required for any
  local workflow, golden path, or release gate.
- 70 executable release gates behind `agent-learn release-check`; the heavier
  `agent-learn release-proof` runs gates + full test suites + package builds
  and emits a verifiable proof artifact.

### Evaluation (evaluate any task)

- Eval suites (promptfoo-style JSON manifests), saved-artifact evaluation,
  raw task-evidence evaluation, and deterministic evaluation-config synthesis
  (criteria/tools/weights inferred from evidence — no hosted judge required).
- Localhost-default evaluation hooks for custom judges (non-local endpoints
  are explicit opt-in).
- Judge-reliability tooling: perturbation checks (formatting / verbosity /
  paraphrase) over scripted judges, taught as a first-class cookbook.
- JSON / JUnit / SARIF / Markdown outputs on every eval surface.

### Simulation (simulate any framework)

- Local worlds, task simulations, world hooks, stateful tool worlds, memory
  layers, multi-agent rooms, orchestration stacks, realtime/voice fixtures,
  browser/CUA traces, multimodal image runs.
- Framework adapters — probe → discover → optimize → promote — for LangChain,
  LangGraph, LlamaIndex, AutoGen, CrewAI, LiveKit, Pipecat, Browser Use, MCP,
  A2A, and custom orchestration objects (probe-promoted coverage); PydanticAI
  and OpenAI Agents via runtime simulation (runtime-simulated coverage).
- OpenEnv/Gymnasium shapes consumed as compatibility inputs (wire format
  only); environment replay is the owned surface, enforced by gate.
- Regression lifecycle: baseline → compare → report → promote-to-regression →
  replay → shrink, all CLI-first and CI-ready.

### Live framework lanes (opt-in, never release prerequisites)

- Real framework processes under one harness contract, behind per-lane env
  flags and extras: LiveKit `AgentSession`, Pipecat `Pipeline`, LangChain/
  LangGraph compiled graphs with real checkpoint stores (including
  cross-session stored-prompt-injection probes), loopback MCP server
  processes, A2A HTTP peers. All five lanes proven against the real
  frameworks.
- Evidence classes (`local_gate` / `live_lane` / `live_stressed` /
  `captured_fixture`) keep live results out of release claims; the
  `live_lane_boundary` gate enforces the boundary statically.
- Untrusted-subprocess isolation with scrubbed env (harness credentials never
  reach lane processes); layer-attributed failures (lane infra never scores
  the agent); n-repeat variance statistics (ICC, divergence step) with
  pass / fail / unstable verdicts; replayable transcripts demotable into
  credential-free regression fixtures with reviewed provenance.

### Red-teaming

- Canonical research-backed corpus and campaign execution, adaptive loops,
  attack evolution with counterexample shrinking, persistent-state /
  cross-session stored-injection scenarios, long-horizon campaigns, causal
  attribution, society-driven scenario optimization, readiness certification,
  and promotion of findings into replayable regression packs.

### Optimization (optimize the whole agent, not just the prompt)

- Path-exact `optimize_target()` family proven across surfaces: world
  transitions, framework adapter method, memory operations, multi-agent
  roster, orchestration spans, workflow traces, adapter matrices.
- Whole-agent search: `base_agent` + `search_space` over model, voice, first
  message, instructions, tools, memory policy, and topology paths — staged
  conditioning (component text → structure/config → global re-polish),
  diagnosis-scoped search locality with harness-layer attribution, declared
  eval budgets with opt-in Elo tournament selection, external-verification-
  only ranking, and an apply-plan artifact for provider application with
  read-back verification (execution of the apply stays platform-side).
- Optimizer profile matrix: 33 declared (framework × target-kind × backend)
  cells, per-cell winners only — the gate rejects any "globally best backend"
  aggregate by construction.
- Capability-profile regression freezing: promoted profiles become frozen,
  content-addressed evidence rows; an optimization win that breaks any frozen
  row is vetoed; security rows are non-tradable.
- Trajectory-profiled backend routing: every backend run emits a fitness
  profile (improvement frequency, locality, dedupe, regressions); routing
  recommendations cite that evidence, with cold-start fallback and explicit
  override (`--backend`).
- Society-of-agents governance: deterministic role graph with asymmetric
  authority, two-chamber (samiti/sabhā) rounds, guṇa temperament parameters
  on roles, structured pañca-avayava proposal justifications, fallacy-class
  (hetvābhāsa) rejection records, pooled diagnosis ledger, full audit trail.

### Developer experience

- 70 born-executable docs pages across 8 tracks: every page opens with a
  YAML-frontmatter "manifest twin" backed by a CI-executed example; the
  `docs_executability` gate re-verifies backing, claims, and the generated
  `docs/llms.txt` machine index on every release check — docs cannot rot.
- `agent-learn init` golden paths: all five presets (`run`, `redteam`, `ci`,
  `optimize`, `all`) reach a first replayable artifact in ≤3 commands,
  offline, with machine-checkable postconditions and doctor mappings in every
  scaffold README.
- Packaging hygiene: the sdist ships only source, tests, examples, docs, and
  standard release files — enforced by gate; `pip install -e .` from source
  today, PyPI/npm at launch.

---

## Planned

### Near term (current program)

- **Voice lane rungs 2–3** — loopback real-transport audio (WebRTC/WS over
  localhost) and real telephony/SIP for the LiveKit/Pipecat lanes, with
  dual-channel barge-in/overlap evidence. Rung 1 (virtual-clock simulated
  user) is implemented; higher rungs currently raise `NotImplementedError`
  by design rather than pretending.
- **Credentialed lane runs** — owner-keyed runs for LiveKit Cloud/SIP and
  provider-applied whole-agent optimization (ElevenLabs-style apply with
  read-back), producing the first reviewed captured fixtures.
- **Live red-team targets** — pointing the persona/corpus generators at live
  lane targets, including repo-conditioned test generation.
- **Platform artifact surface** — Future AGI UI rendering and acting on kit
  artifacts (report cards, action cards, run/red-team/optimization pages),
  with the platform consuming apply-plan artifacts.
- **TypeScript parity** — simulate/optimize/red-team surfaces in the TS
  package (currently evaluation-focused), plus npm publish readiness.

### Release cut (owner actions)

- Security-contact address in `SECURITY.md`; push, tag publication, and
  PyPI/npm publishing from the proved commit.

### Post-v1 queue

- Split the release-gate registry (`trinity.py`) into a `trinity/gates/`
  package (internal refactor; no behavior change).
- Additional framework/provider adapter promotions as the ecosystem moves;
  more per-framework optimizer profile matrix cells beyond the declared 33.
- Generated notebook views of cookbook pages (docs remain script-backed; the
  executability gate stays the source of truth).
- Meta-optimization of society parameters (guṇa mix as an optimizable
  meta-parameter) and live-lane-evidence-informed routing once captured
  fixtures accumulate.

---

## How to verify any claim on this page

```bash
uv run agent-learn release-check --project-root .   # all 70 gates
uv run agent-learn release-proof --project-root . \
  --output /tmp/proof.json --quiet                  # full proof artifact
```

If a claim here ever drifts from what those commands prove, the commands win.
