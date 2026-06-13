"""Unit 13 (BBG U13 / ARCH §2d phases 5-6) — the six-phase practice driver.

``run_practice_loop(manifest) -> dict``: the outer loop per round —
rank deficits → drill (interleaved with due reviews at review_ratio, reviews
ONLY between promotions) → update → consolidate → re-assess; bounded by
max_rounds and the meter. Entry-point checks (before ANY budget): derived-
objective refusal (AD-F), eval_budget presence, extension admission. Determinism
per ARCH §2d (all child seeds derived; every artifact carries seed + parent
hashes). Emits ``agent-learning.practice-result.v1`` through public_payload, so
every episode lands a telemetry ledger row with zero new telemetry code.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, List, Mapping, Optional

from .._schema import public_payload
from .. import loss as _loss
from . import _assess, _diagnose, _drill, _schedule, _store, _update
from ._budget import BudgetExhausted, BudgetMeter
from ._contract import (
    AGENT_LEARNING_PRACTICE_RESULT_KIND,
    BUDGET_PLAN,
    DEFAULT_INNER_OPERATOR_BACKEND,
    DEFAULT_MAX_ROUNDS,
    REVIEW_RATIO,
    SCAFFOLD_FADE_DEFAULT,
    ZPD_BAND,
)


def _hash(payload: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


class PracticeRefusal(ValueError):
    """Raised at the entry point before any budget is spent."""


def run_practice_loop(
    manifest: Mapping[str, Any],
    *,
    cell_scorer: Optional[Callable[[Mapping[str, Any]], Mapping[str, Any]]] = None,
    repeat_scorer: Optional[Callable[[Mapping[str, Any], int], float]] = None,
    replay_row: Optional[Callable[[str], bool]] = None,
    store: Optional[_store.ConsolidationStore] = None,
    extension_admission_check: Optional[Callable[[], Optional[dict]]] = None,
) -> dict:
    """Run the practice loop deterministically. Scorers are injected so the gate
    and capstone run offline; production wires them to the simulation engine."""
    practice = dict(manifest.get("practice") or manifest)
    sim_block = dict(practice.get("simulation") or {})
    simulation = dict(sim_block.get("inline") or {})
    objective = simulation.get("objective")

    # --- entry-point refusals (BEFORE any budget) --------------------------
    # 1. derived-objective refusal (AD-F).
    _loss.refuse_derived_for_training(objective)
    # 2. eval_budget presence (else budget_undeclared).
    eval_budget = practice.get("eval_budget")
    if not isinstance(eval_budget, int) or isinstance(eval_budget, bool) or eval_budget < 1:
        raise PracticeRefusal("budget_undeclared: eval_budget is required and must be an int >= 1")
    # 3. extension admission for the inner operator (before any phase spends).
    if extension_admission_check is not None:
        refusal = extension_admission_check()
        if refusal is not None and not refusal.get("admitted", True):
            raise PracticeRefusal(
                f"extension_evidence_inadmissible: {refusal.get('reason')}"
            )

    seed = practice.get("seed")
    if seed is None:
        raise PracticeRefusal("seed is required (declared-MANDATORY pair: eval_budget + seed)")
    seed = int(seed)

    max_rounds = int(practice.get("max_rounds", DEFAULT_MAX_ROUNDS))
    budget_plan = tuple(practice.get("budget_plan") or BUDGET_PLAN)
    review_ratio = float(practice.get("review_ratio", REVIEW_RATIO))
    zpd = dict(practice.get("zpd") or {})
    band = tuple(zpd.get("band") or ZPD_BAND)
    k = int(zpd.get("k", 8))
    icc_floor = float(zpd.get("icc_floor", 0.5))
    scaffold_fade = dict(practice.get("scaffold_fade") or {})
    fade = tuple(scaffold_fade.get("intensities") or SCAFFOLD_FADE_DEFAULT)
    inner_operator = dict(practice.get("inner_operator") or {})
    operator_backend = str(inner_operator.get("backend", DEFAULT_INNER_OPERATOR_BACKEND))
    frozen_rows = list(practice.get("frozen_rows") or [])
    search_space = dict(practice.get("search_space") or manifest.get("search_space") or {})

    meter = BudgetMeter(eval_budget, budget_plan=budget_plan)
    if store is None:
        store_knob = dict(practice.get("store") or {})
        store = _store.ConsolidationStore(store_knob.get("path"),
                                          active_cap=int(store_knob.get("active_cap", 64)))

    # default deterministic scorers (all-pass) if not injected.
    cell_scorer = cell_scorer or (lambda cell: {"scalar": 1.0, "verdict": "pass", "evidence_class": "local_gate"})
    repeat_scorer = repeat_scorer or (lambda sim, s: 1.0)
    replay_row = replay_row or (lambda row: True)

    rounds: List[dict] = []
    parent_report_hash: Optional[str] = None
    stop_reason = "max_rounds"

    try:
        for round_no in range(max_rounds):
            # ASSESS
            report = _assess.assess(
                simulation, objective, meter=meter, round_no=round_no, seed=seed,
                cell_scorer=cell_scorer, parent_report_hash=parent_report_hash,
            )
            parent_report_hash = _hash(report)
            # DIAGNOSE
            deficits = _diagnose.diagnose(report, search_space=search_space)
            # DRILL (interleaved with due reviews between promotions)
            due = _schedule.due_reviews(store.active_records(), round_no)
            review_slots = int(len(deficits["deficits"]) * review_ratio)
            for review_rec in due[:review_slots]:
                meter.charge("review", 1)
                review_pass = all(replay_row(r) for r in review_rec.get("deck") or [])
                event = "review_pass" if review_pass else "review_fail"
                store.update_record(_schedule.transition(review_rec, event, round_no))

            drill_records: List[dict] = []
            update_records: List[dict] = []
            for deficit in deficits["deficits"]:
                drill = _drill.drill(
                    deficit, _drill_simulation(simulation, deficit), seed=seed, round_no=round_no,
                    repeat_scorer=repeat_scorer, fade_intensities=fade, k=k,
                    icc_floor=icc_floor, band=band,
                )
                drill_records.append(drill)
                # unstable drill ⇒ quarantined, zero update budget (UI-UX E6).
                if drill["zpd_measurement"]["verdict"] == "unstable":
                    continue
                # UPDATE (the D7 promotion sweep)
                upd = _update.update(
                    deficit, allowed_layer=deficit.get("harness_layer", "execution"),
                    allowed_paths=deficit.get("search_paths") or [],
                    proposals=[{"patch": {}, "justification": {"hetu": "drill"}}],
                    store=store, frozen_rows=frozen_rows, replay_row=replay_row,
                    meter=meter, operator_backend=operator_backend,
                )
                update_records.append(upd)
                # CONSOLIDATE (admit a lesson; cap ⇒ cap_deferred)
                if upd["promotion_sweep"]["all_closed"]:
                    rec = _store.build_record(
                        lesson={"kind": "config_patch", "payload": {}, "applies_to_paths": deficit.get("search_paths") or []},
                        source_justification=upd.get("selected_candidate", {}).get("justification", {}) if upd.get("selected_candidate") else {},
                        deck=list(frozen_rows), cells=[deficit.get("cell")],
                        created_round=round_no, seed=seed,
                    )
                    store.admit(rec)
            rounds.append({
                "round": round_no,
                "report": report,
                "deficits": deficits,
                "drills": drill_records,
                "updates": update_records,
            })
    except BudgetExhausted:
        stop_reason = "budget_exhausted"

    result = {
        "kind": AGENT_LEARNING_PRACTICE_RESULT_KIND,
        "name": practice.get("name") or manifest.get("name"),
        "seed": seed,
        "simulation_version": simulation.get("version"),
        "objective_version": (objective or {}).get("version"),
        "stop_reason": stop_reason,
        "rounds_completed": len(rounds),
        "budget_ledger": meter.ledger(),
        "rounds": rounds,
        # headline — AgentCL stability/plasticity/generalization, never best-found.
        "retention_and_transfer_at_equal_budget": {
            "stability": None, "plasticity": None, "generalization": None,
        },
        "promotion_veto_boundary": (
            "all frozen rows replay at every promotion regardless of schedule state (13D-D7)"
        ),
        "detection_latency": {"measured": None, "declared_bound": practice.get("schedule", {}).get("detection_latency_bound")},
    }
    return public_payload(result, kind=AGENT_LEARNING_PRACTICE_RESULT_KIND)


def _drill_simulation(simulation: Mapping[str, Any], deficit: Mapping[str, Any]) -> dict:
    """A derived Simulation narrowed to the target cell (v1 fallback:
    studio_perturbation lineage — a copy carrying the deficit coordinate)."""
    out = dict(simulation)
    out = {**out, "metadata": {**dict(out.get("metadata") or {}), "drill_cell": deficit.get("cell")}}
    return out


def ladder_state(record: Mapping[str, Any]) -> str:
    return _store.ladder_state(record)
