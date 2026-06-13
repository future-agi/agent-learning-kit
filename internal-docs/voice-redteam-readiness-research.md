# Voice Red-Team Readiness (Phase 12, rung-1)

Date: 2026-06-12

## Why This Exists

Voice is the unguarded modality. The kit's gate-enforced red-team corpus
executes on chat only; Phase 12 adds the voice channel and the rung-1
transcript-level attack surface so the corpus, operators, composed search,
fidelity scoring, capture packs, detection-evidence duality, and authorization
boundary all extend to voice agents — deterministically, no keys, no network.

This document is the distilled research backing for the
`voice_redteam_readiness` gate (#73). The full research lives in the phase-12
program folder (RESEARCH.md + RESEARCH-ACOUSTIC.md + RESEARCH-DIALOGUE.md).

## Research Inputs (the six load-bearing works)

- Aegis (2602.07379) — voice-agent red-team taxonomy.
- JAMA (2603.19127) — joint two-channel optimization beats sequential 1.5–10x.
- AudioHijack (2604.14604) — auditory prompt injection drives unauthorized tool
  calls.
- CodecAttack (2605.20519) — codec-latent attacks survive telephony channels
  waveform attacks die in.
- SpeechJBB (2606.06037) — code-switch / pseudo-word obfuscation.
- Cross-session stored injection (2606.04425) — the base the `stored_voice`
  rows extend to voice-origin / voice-delivery.

## The Honesty Discipline (rung-1)

Rung-1 ships fully gated and no-keys. Acoustic operators and codec-survival
scoring are spec'd now and held by `NotImplementedError` until Phase-9A loopback
lands. No `survives` claim exists without a codec-survival record — every
rung-1 attack carries the pin `{"status": "untested", "tier":
"research_pinned"}`. Ultrasonic carrier families are pinned `status: "dies"` +
`scope_label: "smart_speaker_only"` and never counted as phone coverage.

## The Composition (the headline)

The persona × signal search is ONE optimizer target (not persona-filter-then-
signal-search). Three arms — composed, persona_only, signal_only — run at equal
declared `eval_budget`; the per-seed-unanimity `ab_verdict` is the adjudication
and the numeric lift is an evidence field with the null rules (budget under-run
or quarantine epidemic → lift null). The composed-search paper is written only
after this harness proves `composed_lift` on the gate fixture.

## Detection Duality + Authorization

Every attack family ships its detection-evidence counterpart (evidence fields,
never verdicts). Voice red-team campaigns run only against agents the user owns
or is explicitly authorized to test — the kit-local default auto-stamps
`relationship: "kit_local"`; non-local targets refuse without an authorization
stanza; third-party targets have no override flag.
