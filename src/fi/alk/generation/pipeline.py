"""The generation harness: bounded loops at every level, deterministic code between model calls.

Three nested loops, each with a cap and a feedback path, in the generator-verifier shape:

1. CONTRACT loop (explorer.py): the model reads the agent's repository through tools until a
   contract survives validation; validator problems go back into the conversation.
2. SCENARIO loop (per planned scenario): materialise, run deterministic validators, run the reviewer
   model, feed both back as fix instructions, repeat up to ``max_repairs``.
3. SUITE loop: after a batch is accepted, a coverage review names missing situations and
   near-duplicates; gaps become new plans, duplicates are dropped, and the loop continues until the
   target count or ``max_suite_rounds`` is reached.

The model does semantics. Deterministic code does structure, dedup, grounding checks, budget, and
every loop's exit condition.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from . import environments, prompts
from .contract import AgentContract, extract_contract
from .dedup import near_duplicate
from .emit import write_outputs
from .environments import EnvironmentProfile
from .oracle import oracle_hint, oracle_problems
from .explorer import explore_contract
from .llm import LLMClient
from .sources import AgentSource
from .traces import amplify_plans, explore_traces, load_traces, mine_traces
from .validators import repair_hint, validate_scenario

logger = logging.getLogger(__name__)

_ACCEPT_FLOOR = (
    3  # every reviewer score must reach this, and the verdict must not be reject
)


@dataclass
class GenerationConfig:
    # Which runtime the suite is staged in. Chosen by the operator, never inferred: one agent can be
    # reachable by several, and only the chosen one determines the input shape and the gradable
    # checkpoint kinds.
    environment: EnvironmentProfile = environments.VOICE
    n: int = 20
    max_row_rounds: int = 4
    max_repairs: int = 3
    max_suite_rounds: int = 2
    max_explore_turns: int = 20
    critic_enabled: bool = True
    guidance: str = ""
    max_workers: int = 8
    contract_path: str = ""
    traces_path: str = ""
    out_dir: str = "artifacts/generated-scenarios"


@dataclass
class GenerationResult:
    contract: AgentContract
    catalog: list[dict] = field(default_factory=list)
    records: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return slug[:60] or "scenario"


def build_contract(
    source: AgentSource, llm: LLMClient, config: GenerationConfig
) -> AgentContract:
    """Prefer a cached contract, then the exploration loop, then blob extraction.

    Contract extraction is once-per-agent work; caching it turns every regeneration run into
    planning plus materialization only, which is where the wanted scenarios actually come from.
    """
    if config.contract_path:
        import json as _json

        with open(config.contract_path, encoding="utf-8") as fh:
            return AgentContract.model_validate(_json.load(fh))
    evidence = source.describe()
    root = (evidence.metadata or {}).get("root")
    if root:
        try:
            return explore_contract(root, llm, max_turns=config.max_explore_turns)
        except Exception as exc:  # noqa: BLE001 - fall back to single-shot extraction
            logger.warning(
                "exploration failed, falling back to blob extraction: %s", exc
            )
    return extract_contract(evidence.text, llm)


def derive_catalog(contract: AgentContract, llm: LLMClient) -> list[dict]:
    raw = llm.complete_json(
        prompts.SCENARIO_MODEL,
        prompts.subgoal_catalog_prompt(contract.brief()),
        temperature=0.3,
        max_tokens=16_000,
    )
    catalog = raw.get("catalog", raw) if isinstance(raw, dict) else raw
    entries: list[dict] = []
    seen: set[str] = set()
    for entry in catalog if isinstance(catalog, list) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or name in seen:
            continue
        seen.add(name)
        entries.append(entry)
    return entries


def derive_coverage_plan(
    contract: AgentContract,
    llm: LLMClient,
    config: GenerationConfig,
    *,
    total: int | None = None,
) -> tuple[list[dict], list[str]]:
    """Partition the target count across use-case nodes, and surface what had to be assumed.

    The plan is O(use cases), never O(n), so planning context stays bounded at any scenario count.
    The questions come back with it: this stage makes the largest assumptions in the run, and the
    operator can answer them in the next run's guidance instead of discovering them in the output.
    """
    target = config.n if total is None else total
    raw = llm.complete_json(
        prompts.COVERAGE_PLAN_SYSTEM,
        prompts.coverage_plan_prompt(
            contract.brief(), total=target, guidance=config.guidance
        ),
        temperature=0.3,
        max_tokens=16_000,
    )
    nodes = raw.get("nodes", raw) if isinstance(raw, dict) else raw
    questions = [
        str(q)
        for q in (raw.get("open_questions") or [] if isinstance(raw, dict) else [])
        if str(q).strip()
    ]
    plan: list[dict] = []
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict) or not node.get("use_case"):
            continue
        count = node.get("count")
        node["count"] = max(1, int(count)) if isinstance(count, (int, float)) else 1
        plan.append(node)
    planned = sum(node["count"] for node in plan)
    if plan and planned != target:  # renormalise counts to the requested total
        scaled = [max(1, round(node["count"] * target / planned)) for node in plan]
        while sum(scaled) > target and max(scaled) > 1:
            scaled[scaled.index(max(scaled))] -= 1
        while sum(scaled) < target:
            scaled[scaled.index(min(scaled))] += 1
        for node, count in zip(plan, scaled):
            node["count"] = count
    return plan, questions


def derive_rows(
    contract: AgentContract,
    llm: LLMClient,
    config: GenerationConfig,
    *,
    want: int,
    existing: list[dict],
    feedback: str = "",
    node: dict | None = None,
) -> list[dict]:
    brief = contract.brief()
    rows: list[dict] = []
    seen = {
        (
            str(r.get("use_case", "")).strip().lower(),
            str(r.get("situation", "")).strip().lower(),
        )
        for r in existing
    }
    rounds = max(config.max_row_rounds, -(-want // 25) + 1)
    consecutive_empty = 0
    for round_index in range(rounds):
        remaining = want - len(rows)
        if remaining <= 0:
            break
        added_before = len(rows)
        # rounds differ by contributor stance, so one empty round does not prove dryness
        raw = llm.complete_json(
            prompts.SCENARIO_MODEL,
            prompts.derive_rows_prompt(
                brief,
                want=min(remaining, 25),
                signature_cases=contract.signature_cases,
                real_use_cases=contract.real_use_cases,
                existing=[
                    {"use_case": r.get("use_case"), "situation": r.get("situation")}
                    for r in existing + rows
                ],
                feedback=feedback,
                first_round=round_index == 0 and not existing,
                guidance=config.guidance,
                node=node,
                stance=prompts.CONTRIBUTOR_STANCES[
                    round_index % len(prompts.CONTRIBUTOR_STANCES)
                ],
            ),
            temperature=0.4,
            max_tokens=20_000,
        )
        for row in raw.get("rows", raw if isinstance(raw, list) else []):
            if not isinstance(row, dict) or not row.get("situation"):
                continue
            key = (
                str(row.get("use_case", "")).strip().lower(),
                str(row.get("situation", "")).strip().lower(),
            )
            if key in seen:
                continue
            if near_duplicate(row, existing) or near_duplicate(row, rows):
                continue
            seen.add(key)
            row["id"] = _slugify(row.get("id") or row.get("situation", ""))
            rows.append(row)
        if len(rows) == added_before:
            consecutive_empty += 1
            if consecutive_empty >= 2:
                # Two stances in a row produced nothing new: the space is dry.
                break
        else:
            consecutive_empty = 0
    return rows[:want]


def review_plan(
    contract: AgentContract, rows: list[dict], llm: LLMClient
) -> list[dict]:
    """Blueprint gate: cheap review of plans before the expensive materialize+review spend.

    Fail-open: a malformed reviewer reply keeps the original rows, because this stage exists to
    save cost and lift quality, never to lose work.
    """
    if not rows:
        return rows
    try:
        raw = llm.complete_json(
            prompts.PLAN_REVIEW_SYSTEM,
            prompts.plan_review_prompt(contract.brief(), rows),
            temperature=0.2,
            max_tokens=16_000,
        )
    except Exception as exc:  # noqa: BLE001 - reviewer trouble must not lose plans
        logger.warning("plan review failed open: %s", exc)
        return rows
    reviewed = raw.get("rows", raw) if isinstance(raw, dict) else raw
    originals = {str(row.get("id")): row for row in rows}
    survivors: list[dict] = []
    for row in reviewed if isinstance(reviewed, list) else []:
        if (
            not isinstance(row, dict)
            or not row.get("situation")
            or not row.get("target_failure")
        ):
            continue
        slug = _slugify(row.get("id") or row.get("situation", ""))
        # The reviewer may override fields but never erase them: merge over the original plan.
        merged = {
            **originals.get(slug, {}),
            **{k: v for k, v in row.items() if v not in (None, "")},
        }
        merged["id"] = slug
        survivors.append(merged)
    if not survivors or len(survivors) > len(rows):
        return rows
    logger.info("plan review", extra={"in": len(rows), "kept": len(survivors)})
    return survivors


def materialize_row(
    contract: AgentContract,
    row: dict,
    catalog: list[dict],
    llm: LLMClient,
    config: GenerationConfig,
) -> tuple[dict | None, str]:
    """One scenario through the generate-validate-review-repair loop."""
    brief = contract.brief()
    hint = ""
    best: dict | None = None
    reason = ""
    for _attempt in range(1 + config.max_repairs):
        raw = llm.complete_json(
            prompts.SCENARIO_MODEL,
            prompts.materialize_prompt(
                brief,
                row=row,
                base_environment=contract.base_environment,
                catalog=catalog,
                environment=config.environment,
                hint=hint,
                guidance=config.guidance,
            ),
            temperature=0.35,
            max_tokens=20_000,
        )
        record = (
            raw
            if isinstance(raw, dict)
            else next((x for x in raw if isinstance(x, dict)), {})
        )
        for key in (
            "id",
            "use_case",
            "situation",
            "goal",
            "target_failure",
            "unique_end_state",
            "why_it_matters",
            "provenance",
        ):
            record.setdefault(key, row.get(key))
        record["id"] = _slugify(record.get("id") or row.get("id", ""))

        problems = validate_scenario(record, contract)
        if problems:
            best, reason = record, f"validator: {problems[:6]}"
            hint = repair_hint(problems)
            continue
        inconsistencies = oracle_problems(record)
        if inconsistencies:
            best, reason = record, f"oracle: {inconsistencies[:4]}"
            hint = oracle_hint(inconsistencies)
            continue
        if not config.critic_enabled:
            return record, ""
        verdict = llm.complete_json(
            prompts.CRITIC_SYSTEM,
            prompts.critic_prompt(brief, record),
            temperature=0.2,
            max_tokens=12_000,
        )
        if not isinstance(verdict, dict):
            verdict = {}
        record["_review"] = {
            k: verdict.get(k) for k in ("verdict", "scores", "problems")
        }
        decision = str(verdict.get("verdict", "revise")).lower()
        scores = verdict.get("scores") or {}
        low = [
            k
            for k, v in scores.items()
            if isinstance(v, (int, float)) and v < _ACCEPT_FLOOR
        ]
        if decision == "accept" and not low:
            return record, ""
        if decision == "reject":
            return None, f"reviewer reject: {verdict.get('problems', [])[:4]}"
        best, reason = (
            record,
            f"reviewer revise (low: {low}): {verdict.get('problems', [])[:4]}",
        )
        hint = str(verdict.get("fix_hints") or "") or repair_hint([])
    # Out of repair attempts: keep the best structurally-valid draft, flagged, rather than lose it.
    if (
        best is not None
        and not validate_scenario(best, contract)
        and not oracle_problems(best)
    ):
        best["_review_flag"] = reason
        return best, ""
    return None, reason


def suite_review(
    contract: AgentContract, records: list[dict], llm: LLMClient, *, guidance: str = ""
) -> tuple[list[dict], list[str], str]:
    """Coverage pass over the accepted set: (gap rows feedback, duplicate ids to drop, feedback)."""
    raw = llm.complete_json(
        prompts.SUITE_REVIEW_SYSTEM,
        prompts.suite_review_prompt(contract.brief(), records, guidance=guidance),
        temperature=0.2,
        max_tokens=12_000,
    )
    if not isinstance(raw, dict):
        return [], [], ""
    gaps = [
        g for g in raw.get("gaps") or [] if isinstance(g, dict) and g.get("situation")
    ]
    duplicate_ids: list[str] = []
    known = {str(r.get("id")) for r in records}
    for pair in raw.get("near_duplicates") or []:
        if (
            isinstance(pair, list)
            and len(pair) == 2
            and all(str(p) in known for p in pair)
        ):
            duplicate_ids.append(str(pair[1]))
    feedback = "; ".join(
        f"missing: {g['situation']} ({g.get('why_it_matters', '')})" for g in gaps
    )
    return gaps, duplicate_ids, feedback


def generate(
    source: AgentSource,
    llm: LLMClient,
    config: GenerationConfig | None = None,
) -> GenerationResult:
    config = config or GenerationConfig()
    contract = build_contract(source, llm, config)
    logger.info(
        "contract ready", extra={"agent": contract.agent, "tools": len(contract.tools)}
    )
    # The operator's environment choice is authoritative; a disagreement with what the source looks
    # like is worth saying out loud, because the scenarios will follow the choice either way.
    mismatch = environments.modality_mismatch(config.environment, contract.modality)
    if mismatch:
        logger.warning("environment mismatch: %s", mismatch)
        print(f"[generation] NOTE: {mismatch}", flush=True)

    catalog = derive_catalog(contract, llm)
    records: list[dict] = []
    rejected: list[dict] = []
    open_questions: list[str] = []
    feedback = ""

    def _flush() -> None:
        write_outputs(
            config.out_dir,
            contract=contract,
            catalog=catalog,
            records=records,
            rejected=rejected,
            open_questions=open_questions,
            environment=config.environment,
            usage=llm.usage.as_dict(),
        )

    lock = threading.Lock()

    def _materialize_one(row: dict) -> None:
        with lock:
            if len(records) >= config.n:
                # Target reached: spend nothing, but account for the skipped plan.
                rejected.append(
                    {**row, "_reject_reason": "surplus: target already reached"}
                )
                return
        record, reason = materialize_row(contract, row, catalog, llm, config)
        with lock:
            if record is not None and len(records) >= config.n:
                rejected.append(
                    {**row, "_reject_reason": "surplus: target already reached"}
                )
                return
            if record is not None:
                records.append(record)
            else:
                rejected.append({**row, "_reject_reason": reason})
            _flush()  # runs are long; keep every artifact inspectable while they go
            print(
                f"[generation] accepted={len(records)} rejected={len(rejected)} "
                f"spent={llm.usage.as_dict().get('usd', 0)}",
                flush=True,
            )

    def _materialize_batch(rows: list[dict]) -> None:
        if not rows:
            return
        if config.max_workers <= 1 or len(rows) == 1:
            for row in rows:
                _materialize_one(row)
            return
        with ThreadPoolExecutor(max_workers=min(config.max_workers, len(rows))) as pool:
            list(pool.map(_materialize_one, rows))

    def _node_rows(node: dict) -> list[dict]:
        """Existing rows belonging to one coverage node (its local dedup context)."""
        label = str(node.get("use_case", "")).strip().lower()
        return [
            r
            for r in records + rejected
            if str(r.get("use_case", "")).strip().lower() == label
        ]

    try:
        # Production traces first: a scenario that recreates a real interaction outranks an
        # invented one, so mined plans take their share of N before coverage planning fills
        # the remainder. Mined plans pass the same gates as everything else.
        if config.traces_path:
            # A folder of recordings has an unknown shape, so the model navigates it and chooses
            # what is worth mining, failing interactions first. A single file needs no exploring.
            raw_traces: list[dict] = []
            if os.path.isdir(config.traces_path):
                raw_traces = explore_traces(config.traces_path, llm)
            if not raw_traces:
                raw_traces = load_traces(config.traces_path)
            if raw_traces:
                mined = mine_traces(contract, raw_traces, llm, guidance=config.guidance)
                # Amplification: a recreation of a real failure protects that one interaction.
                # Its neighbours fence the class the failure belongs to, so the suite behaves like
                # a regression suite rather than a single pinned data point.
                headroom = config.n - len(mined)
                neighbours = (
                    amplify_plans(contract, mined, llm, limit=headroom)
                    if headroom > 0
                    else []
                )
                mined = mined + neighbours
                for row in mined:
                    row["id"] = _slugify(row.get("id") or row.get("situation", ""))
                if config.critic_enabled:
                    mined = review_plan(contract, mined, llm)
                logger.info(
                    "trace mining",
                    extra={
                        "traces": len(raw_traces),
                        "plans": len(mined),
                        "amplified": len(neighbours),
                    },
                )
                _materialize_batch(mined[: config.n])

        # Operator request next: scenarios answering what the requester explicitly asked to
        # test claim their share of N before baseline coverage. Same schema, same gates,
        # provenance-marked so the report separates "what you asked for" from "what a full
        # suite must contain anyway".
        if config.guidance and len(records) < config.n:
            raw = llm.complete_json(
                prompts.SCENARIO_MODEL,
                prompts.request_plan_prompt(
                    contract.brief(),
                    request=config.guidance,
                    want=config.n - len(records),
                ),
                temperature=0.3,
                max_tokens=16_000,
            )
            requested = raw.get("rows", raw) if isinstance(raw, dict) else raw
            requested = [
                row
                for row in (requested if isinstance(requested, list) else [])
                if isinstance(row, dict)
                and row.get("situation")
                and row.get("target_failure")
            ]
            for row in requested:
                row["id"] = _slugify(row.get("id") or row.get("situation", ""))
                row["provenance"] = {"kind": "operator_request"}
            if config.critic_enabled:
                requested = review_plan(contract, requested, llm)
            logger.info("request planning", extra={"plans": len(requested)})
            _materialize_batch(requested[: config.n - len(records)])

        # Coverage-tree planning: partition n across use-case nodes, then plan each node
        # separately. Planning context is bounded by the node, never by the whole suite;
        # cross-node overlap is prevented structurally and by the deterministic dedup filter.
        remaining_target = config.n - len(records)
        plan, questions = (
            derive_coverage_plan(contract, llm, config, total=remaining_target)
            if remaining_target > 0
            else ([], [])
        )
        open_questions.extend(questions)
        logger.info("coverage plan", extra={"nodes": len(plan)})

        # Nodes planned in parallel cannot see each other, so overlap is caught here instead.
        claimed: list[dict] = []

        def _claim(rows: list[dict]) -> list[dict]:
            with lock:
                kept = []
                for row in rows:
                    if near_duplicate(row, claimed):
                        continue
                    claimed.append(row)
                    kept.append(row)
                return kept

        def _plan_node(node: dict) -> list[dict]:
            rows = derive_rows(
                contract,
                llm,
                config,
                want=int(node["count"]),
                existing=_node_rows(node),
                node=node,
            )
            if config.critic_enabled:
                rows = review_plan(contract, rows, llm)
            return _claim(rows)

        # Each node flows plan -> review -> materialize on its own, with no barrier between the
        # stages: a node whose planning finished early has its scenarios being written while a
        # slower node is still planning. Wall-clock becomes the slowest single node rather than
        # the sum of the slowest stage in each.
        if plan and config.max_workers > 1:
            planners = ThreadPoolExecutor(
                max_workers=min(config.max_workers, len(plan))
            )
            writers = ThreadPoolExecutor(max_workers=config.max_workers)
            try:

                def _node_flow(node: dict) -> list:
                    # Submits and returns; never waits, so a planner thread cannot be blocked
                    # behind the writer pool it is feeding.
                    return [
                        writers.submit(_materialize_one, row)
                        for row in _plan_node(node)
                    ]

                node_futures = [planners.submit(_node_flow, node) for node in plan]
                for node_future in node_futures:
                    for write_future in node_future.result():
                        write_future.result()
            finally:
                planners.shutdown(wait=True)
                writers.shutdown(wait=True)
        else:
            for node in plan:
                _materialize_batch(_plan_node(node))

        # Replenishment: coverage review names gaps and near-duplicates, then plans the
        # shortfall suite-wide. Termination is by PROGRESS, not a fixed round count: the loop
        # continues while rounds still produce accepted scenarios and ends the first time a
        # round yields none, which is the empirical signal that the agent's genuinely distinct
        # scenario space is exhausted below the requested target.
        suite_round = 0
        while suite_round < max(config.max_suite_rounds, 8):
            suite_round += 1
            want = config.n - len(records)
            if want <= 0 and suite_round > 1:
                break
            accepted_before = len(records)
            gaps, duplicate_ids, feedback = suite_review(
                contract, records, llm, guidance=config.guidance
            )
            if duplicate_ids:
                dropped = [r for r in records if str(r.get("id")) in set(duplicate_ids)]
                records[:] = [
                    r for r in records if str(r.get("id")) not in set(duplicate_ids)
                ]
                for record in dropped:
                    record["_reject_reason"] = "near-duplicate of an accepted scenario"
                    rejected.append(record)
            want = config.n - len(records)
            if want <= 0:
                break
            rows = derive_rows(
                contract,
                llm,
                config,
                want=want,
                existing=records + rejected,
                feedback=feedback,
            )
            if not rows:
                break
            if config.critic_enabled:
                rows = review_plan(contract, rows, llm)
            _materialize_batch(rows)
            if len(records) == accepted_before:
                logger.warning(
                    "scenario space exhausted at %d of %d requested; a round produced no accepts",
                    len(records),
                    config.n,
                )
                print(
                    f"[generation] EXHAUSTED: {len(records)} of {config.n} requested; "
                    "the last replenishment round produced no new accepted scenario",
                    flush=True,
                )
                break
    except Exception:
        _flush()
        raise

    result = GenerationResult(
        contract=contract,
        catalog=catalog,
        records=records,
        rejected=rejected,
        open_questions=open_questions,
        usage=llm.usage.as_dict(),
    )
    _flush()
    return result
