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
import re
from dataclasses import dataclass, field
from typing import Any

from . import prompts
from .contract import AgentContract, extract_contract
from .dedup import near_duplicate
from .emit import write_outputs
from .explorer import explore_contract
from .llm import LLMClient
from .sources import AgentSource
from .validators import repair_hint, validate_scenario

logger = logging.getLogger(__name__)

_ACCEPT_FLOOR = (
    3  # every reviewer score must reach this, and the verdict must not be reject
)


@dataclass
class GenerationConfig:
    n: int = 20
    max_row_rounds: int = 4
    max_repairs: int = 3
    max_suite_rounds: int = 2
    max_explore_turns: int = 20
    critic_enabled: bool = True
    guidance: str = ""
    out_dir: str = "artifacts/generated-scenarios"


@dataclass
class GenerationResult:
    contract: AgentContract
    catalog: list[dict] = field(default_factory=list)
    records: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return slug[:60] or "scenario"


def build_contract(
    source: AgentSource, llm: LLMClient, config: GenerationConfig
) -> AgentContract:
    """Prefer the exploration loop when the source exposes a filesystem root."""
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
    contract: AgentContract, llm: LLMClient, config: GenerationConfig
) -> list[dict]:
    """Partition the target count across use-case nodes. The plan is O(use cases), never O(n),
    so planning context stays bounded at any scenario count."""
    raw = llm.complete_json(
        prompts.COVERAGE_PLAN_SYSTEM,
        prompts.coverage_plan_prompt(
            contract.brief(), total=config.n, guidance=config.guidance
        ),
        temperature=0.3,
        max_tokens=16_000,
    )
    nodes = raw.get("nodes", raw) if isinstance(raw, dict) else raw
    plan: list[dict] = []
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict) or not node.get("use_case"):
            continue
        count = node.get("count")
        node["count"] = max(1, int(count)) if isinstance(count, (int, float)) else 1
        plan.append(node)
    total = sum(node["count"] for node in plan)
    if plan and total != config.n:  # renormalise counts to the requested total
        scaled = [max(1, round(node["count"] * config.n / total)) for node in plan]
        while sum(scaled) > config.n:
            scaled[scaled.index(max(scaled))] -= 1
        while sum(scaled) < config.n:
            scaled[scaled.index(min(scaled))] += 1
        for node, count in zip(plan, scaled):
            node["count"] = count
    return plan


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
    for round_index in range(rounds):
        remaining = want - len(rows)
        if remaining <= 0:
            break
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
    return rows[:want]


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
                modality=contract.modality,
                conversational=contract.conversational,
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
        ):
            record.setdefault(key, row.get(key))
        record["id"] = _slugify(record.get("id") or row.get("id", ""))

        problems = validate_scenario(record, contract)
        if problems:
            best, reason = record, f"validator: {problems[:6]}"
            hint = repair_hint(problems)
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
    if best is not None and not validate_scenario(best, contract):
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

    catalog = derive_catalog(contract, llm)
    records: list[dict] = []
    rejected: list[dict] = []
    feedback = ""

    def _flush() -> None:
        write_outputs(
            config.out_dir,
            contract=contract,
            catalog=catalog,
            records=records,
            rejected=rejected,
            usage=llm.usage.as_dict(),
        )

    def _materialize_batch(rows: list[dict]) -> None:
        for row in rows:
            record, reason = materialize_row(contract, row, catalog, llm, config)
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

    def _node_rows(node: dict) -> list[dict]:
        """Existing rows belonging to one coverage node (its local dedup context)."""
        label = str(node.get("use_case", "")).strip().lower()
        return [
            r
            for r in records + rejected
            if str(r.get("use_case", "")).strip().lower() == label
        ]

    try:
        # Coverage-tree planning: partition n across use-case nodes, then plan each node
        # separately. Planning context is bounded by the node, never by the whole suite;
        # cross-node overlap is prevented structurally and by the deterministic dedup filter.
        plan = derive_coverage_plan(contract, llm, config)
        logger.info("coverage plan", extra={"nodes": len(plan)})
        for node in plan:
            rows = derive_rows(
                contract,
                llm,
                config,
                want=int(node["count"]),
                existing=_node_rows(node),
                node=node,
            )
            _materialize_batch(rows)

        # Replenishment: coverage review names gaps and near-duplicates, then plans the
        # shortfall suite-wide until the target count or the round cap is reached.
        for suite_round in range(config.max_suite_rounds):
            want = config.n - len(records)
            if want <= 0 and suite_round > 0:
                break
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
            _materialize_batch(rows)
    except Exception:
        _flush()
        raise

    result = GenerationResult(
        contract=contract,
        catalog=catalog,
        records=records,
        rejected=rejected,
        usage=llm.usage.as_dict(),
    )
    _flush()
    return result
