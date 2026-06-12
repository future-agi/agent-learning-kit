# Persona & Scenario Studio Readiness — Research Note

Distilled from the Phase-7 research pass (RESEARCH.md): Eval4Sim two-sided
fidelity (the triple — adherence / consistency / naturalness); PPol behavior
policies (2604.00026) as the executable, searchable persona representation;
PICon interrogation (internal / external / retest); the 2511.00222 three-way
drift decomposition (prompt→line / line→line / probe); Crescendo in-character
escalation (2605.04019); AgentAssay three-valued admission (2605.11030);
2602.18462 no-population-representativeness; 2604.23600 trait×demographic bias
amplification; 2606.04425 stored-injection channel; the 2601.15290
persona/task separation; SafeAudit budgeted residual enumeration.

## The claim the gate makes falsifiable

"Our simulated users are realistic" is the claim every voice/agent test vendor
makes and none of them measure. The `persona_scenario_studio_readiness` gate
(#71) refuses the marketing claim and replaces it with an **executable
falsifiability contract**: every behavior axis a persona declares ships paired
with a single transcript-observable realization metric, and a persona that
cannot be measured against its own declaration cannot back a release claim.

- **Five typed layers** (identity / temperament / behavior_policy / knowledge /
  provenance), all optional so legacy embedded-dict personas validate and run
  byte-identically (back-compat) — they simply stay untyped and produce no
  fidelity evidence.
- **Six canon behavior axes** (`patience`, `disclosure`, `interruption`,
  `escalation`, `cooperation`, `repair`) paired 1:1 and ordered with their
  realization metrics (`turns_to_escalation`, `info_withholding_rate`,
  `interruption_count`, `intensity_trajectory_match`, `compliance_rate`,
  `repair_turn_fraction`). A parameter without a shipped metric DOES NOT SHIP
  (R§3.4 limit 4). The verbosity/tempo dials are post-v1.x for exactly this
  reason — they have no v1 realization metric.
- **Three-valued fidelity** (`pass` / `fail` / `inconclusive`): floors met →
  pass; floors violated → **inconclusive** (a broken simulator says nothing
  about the agent — the 2605.11030 admission move), quarantined and excluded
  from the score matrix but COUNTED; `fail` reserved for measurement
  impossibility on a typed persona. Above the `0.5` epidemic rate the SIMULATOR,
  not the agent, is declared unusable.

## What the gate executes (no network, no keys)

The gate exec-loads `examples/sdk_persona_scenario_studio.py` against the frozen
`examples/persona_library/` fixtures and audits its evidence payload into nine
error arrays:

- **class contract** — typed round-trip + content-address stability; legacy row
  validates and upgrades losslessly (provenance=legacy); an adversarial scenario
  is refused without its attack_type / attack_surface / escalation arc.
- **fidelity admission loop** — the clean fixture passes and is admissible; the
  **drifted** fixture is quarantined as `inconclusive` (the PRD §4.6 negative);
  the **over-acted** fixture is failed by the two-sided naturalness check
  (Directive Amplification caught, caricature_index high); record fields and
  verdicts are pinned to the canon.
- **calibration lifecycle** — sampled → validated → interrogated → admitted; the
  PICon battery (internal / external / retest); the seeded-drift fixture forks
  on REPLAY and fails the retest leg; admitted class is monotone.
- **coverage + residual** — obligation coverage per axis (never a global count;
  `library_size` / `scenario_count` are forbidden headline keys), the budgeted
  residual estimator's plateau curve, and k-way expansion lineage.
- **bias lint** — the stereotyped fixture set MUST fail (behavioral variance
  collapses onto demographic labels); the clean set passes; the lint re-runs
  per locale.
- **vendor import parity** — Vapi/Retell fixtures parse→render byte-exact;
  goals land on the ScenarioGoal stub, never the persona (2601.15290).
- **scan refusal** — tampered (checksum mismatch), unpinned (missing pin
  fields), and injection-markered downloads are all refused; the refused
  artifact lands in `quarantine/` and is never loadable.

## Honest limits (binding — RESEARCH §3.4)

1. The guṇa temperament axes are a scholarly design device used as deterministic
   engineering metadata, never a psychometric claim about simulated users.
2. No class ever claims population representativeness (2602.18462) — stated in
   the schema (`representativeness_claim: "none"`), not just the docs.
3. Demographics explain ~1.5% of behavioral variance and are always lint-flagged.
4. An axis ships ONLY with a transcript-observable realization metric.

## Persona-conditioned red-teaming (the wedge — §9.7)

The certification gate's persona-conditioned campaign proves the wedge no vendor
measures: an adversarial turn is a realistic attack only if the simulated
attacker held character while pressing. Per-attack fidelity records ride in the
campaign artifact; character-broken attacks are flagged and down-weighted, never
dropped (a successful out-of-character attack is still a finding, just a less
realistic one).
