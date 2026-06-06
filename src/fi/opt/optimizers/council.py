from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Callable, Iterable, List, Mapping, Optional, Sequence

from ..base.base_optimizer import BaseOptimizer
from ..components import ComponentDiagnosis, relevant_search_paths
from ..targets import AgentCandidate, CandidateEvaluation, OptimizationTarget
from ..types import EvaluationResult, IterationHistory, OptimizationResult
from .agent import (
    _dedupe_diagnoses,
    _diagnose_candidate_evaluation,
    _dump_model,
    _history_from_candidate,
    _normalize_candidate_evaluation,
    _normalize_diagnoses,
)

logger = logging.getLogger(__name__)


DEFAULT_COUNCIL_ROLES = ("explorer", "critic", "synthesizer", "steward")
SOCIETY_ROLES = (
    "explorer",
    "critic",
    "synthesizer",
    "steward",
    "specialist",
    "adversary",
)


@dataclass(frozen=True)
class AgentSearchProposal:
    """One deterministic candidate patch proposed by an agent-search strategy."""

    patch: dict[str, Any]
    role: str
    parent_ids: tuple[str, ...]
    reason: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentSearchState:
    """Read-only state passed to a pluggable agent-search strategy."""

    seed_candidate: AgentCandidate
    evaluations: Sequence[CandidateEvaluation]
    search_space: Mapping[str, List[Any]]
    search_paths: Sequence[str]
    diagnoses: Sequence[ComponentDiagnosis]
    beam_width: int
    max_proposals: int
    round_number: int


@dataclass(frozen=True)
class AgentSocietyRole:
    """One role node in a deterministic society-search proposal graph."""

    name: str
    proposal_kind: str
    phase: int = 1
    depends_on: tuple[str, ...] = ()
    path_prefixes: tuple[str, ...] = ()
    archetype: str = ""
    description: str = ""

    def to_metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "proposal_kind": self.proposal_kind,
            "phase": self.phase,
            "depends_on": list(self.depends_on),
            "path_prefixes": list(self.path_prefixes),
            "archetype": self.archetype,
            "description": self.description,
        }


ROLE_GRAPH_PROPOSAL_KINDS = {
    "adversary",
    "coverage_synthesis",
    "critic",
    "explorer",
    "specialist",
    "steward",
    "synthesizer",
}


DEFAULT_SOCIETY_ROLE_GRAPH = (
    AgentSocietyRole(
        name="sutradhara",
        proposal_kind="specialist",
        phase=1,
        path_prefixes=("multi_agent", "orchestration", "router", "graph"),
        archetype="orchestrator",
        description="Bundle coordination, routing, handoff, and graph repairs.",
    ),
    AgentSocietyRole(
        name="smriti",
        proposal_kind="specialist",
        phase=1,
        path_prefixes=("memory", "retrieval", "retriever"),
        archetype="working_memory",
        description="Bundle memory, retrieval, and retained context repairs.",
    ),
    AgentSocietyRole(
        name="arjuna",
        proposal_kind="explorer",
        phase=1,
        archetype="focused_action",
        description="Probe one controllable path at a time under metric feedback.",
    ),
    AgentSocietyRole(
        name="hanuman",
        proposal_kind="specialist",
        phase=1,
        path_prefixes=("tools", "framework", "voice", "browser", "cua", "implementation"),
        archetype="bridge_builder",
        description="Bundle tool, framework, world-interface, and runtime repairs.",
    ),
    AgentSocietyRole(
        name="vidura",
        proposal_kind="adversary",
        phase=1,
        path_prefixes=("security", "policy", "trust", "environment"),
        archetype="prudent_critic",
        description="Stress policy, security, trust-boundary, and environment choices.",
    ),
    AgentSocietyRole(
        name="krishna",
        proposal_kind="critic",
        phase=2,
        depends_on=("arjuna", "sutradhara", "smriti"),
        archetype="charioteer_counsel",
        description="Test one more change against current strong partial candidates.",
    ),
    AgentSocietyRole(
        name="sangha",
        proposal_kind="coverage_synthesis",
        phase=2,
        depends_on=("sutradhara", "smriti", "hanuman", "vidura", "arjuna"),
        archetype="collective_synthesis",
        description="Combine best path representatives across role evidence.",
    ),
    AgentSocietyRole(
        name="dharma_steward",
        proposal_kind="steward",
        phase=3,
        depends_on=("sangha", "krishna"),
        archetype="minimal_process_guardian",
        description="Remove one change at a time to keep only metric-proven repairs.",
    ),
)


class AgentSearchStrategy:
    """Proposal-generation strategy for framework-neutral agent optimization."""

    name = "agent_search_strategy"
    roles: Sequence[str] = ()

    def propose(self, state: AgentSearchState) -> List[AgentSearchProposal]:
        raise NotImplementedError


class DeterministicCouncilStrategy(AgentSearchStrategy):
    """Current council search: explore, critique, synthesize, and steward."""

    name = "deterministic_council_search"
    roles = DEFAULT_COUNCIL_ROLES

    def propose(self, state: AgentSearchState) -> List[AgentSearchProposal]:
        return _build_round_proposals(
            seed_candidate=state.seed_candidate,
            evaluations=state.evaluations,
            search_space=dict(state.search_space),
            search_paths=state.search_paths,
            beam_width=state.beam_width,
            max_proposals=state.max_proposals,
            round_number=state.round_number,
        )


class SocietySearchStrategy(AgentSearchStrategy):
    """
    Deterministic role-diverse search for multi-interaction agent systems.

    The strategy keeps metric-bound candidate evaluation, but allocates proposal
    slots across social roles so search can test isolated mutations, component
    bundles, stress combinations, synthesis, critique, and simplification.
    """

    name = "deterministic_society_search"
    roles = SOCIETY_ROLES

    def propose(self, state: AgentSearchState) -> List[AgentSearchProposal]:
        return _build_society_proposals(
            seed_candidate=state.seed_candidate,
            evaluations=state.evaluations,
            search_space=dict(state.search_space),
            search_paths=state.search_paths,
            diagnoses=state.diagnoses,
            beam_width=state.beam_width,
            max_proposals=state.max_proposals,
            round_number=state.round_number,
        )


class SocietyRoleGraphSearchStrategy(AgentSearchStrategy):
    """
    Deterministic society search with explicit role graph metadata.

    Role names and archetypes are inspiration labels only. Candidate acceptance
    still depends entirely on the provided metric/evaluator contract.
    """

    name = "deterministic_role_graph_society_search"

    def __init__(
        self,
        role_graph: Optional[Sequence[AgentSocietyRole | Mapping[str, Any]]] = None,
    ) -> None:
        self.role_graph = _normalize_society_role_graph(role_graph)
        self.roles = tuple(role.name for role in self.role_graph)

    def propose(self, state: AgentSearchState) -> List[AgentSearchProposal]:
        return _build_role_graph_society_proposals(
            seed_candidate=state.seed_candidate,
            evaluations=state.evaluations,
            search_space=dict(state.search_space),
            search_paths=state.search_paths,
            diagnoses=state.diagnoses,
            beam_width=state.beam_width,
            max_proposals=state.max_proposals,
            round_number=state.round_number,
            role_graph=self.role_graph,
        )

    def to_metadata(self) -> dict[str, Any]:
        return {
            "role_graph": [role.to_metadata() for role in self.role_graph],
            "role_graph_inspiration": (
                "human social coordination, metacognition, and Hindu mythic "
                "archetypes used only as deterministic proposal metadata"
            ),
        }


class CouncilAgentOptimizer(BaseOptimizer):
    """
    Optimizes agent configs with deterministic multi-round social search.

    `AgentOptimizer` is best when exhaustive candidate enumeration is acceptable.
    This optimizer is intended for multi-interaction agents where useful fixes
    are often combinations of partial changes: one role explores isolated
    mutations, one critiques the current best candidate, one synthesizes strong
    partial candidates, and one steward tests whether combined patches can be
    simplified without losing score.
    """

    def __init__(
        self,
        target: Optional[OptimizationTarget] = None,
        *,
        evaluate_candidate: Optional[
            Callable[[AgentCandidate], CandidateEvaluation | EvaluationResult | float]
        ] = None,
        simulation_evaluator: Any = None,
        diagnoses: Optional[Iterable[ComponentDiagnosis | dict[str, Any]]] = None,
        max_rounds: int = 3,
        beam_width: int = 4,
        max_proposals_per_round: int = 16,
        target_score: float = 1.0,
        include_seed: bool = True,
        auto_diagnose: bool = True,
        diagnostic_score_threshold: float = 0.85,
        search_strategy: Optional[AgentSearchStrategy | str] = None,
    ) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds must be at least 1.")
        if beam_width < 1:
            raise ValueError("beam_width must be at least 1.")
        if max_proposals_per_round < 1:
            raise ValueError("max_proposals_per_round must be at least 1.")

        self.target = target
        self.evaluate_candidate = evaluate_candidate
        self.simulation_evaluator = simulation_evaluator
        self.diagnoses = _normalize_diagnoses(diagnoses)
        self.max_rounds = max_rounds
        self.beam_width = beam_width
        self.max_proposals_per_round = max_proposals_per_round
        self.target_score = target_score
        self.include_seed = include_seed
        self.auto_diagnose = auto_diagnose
        self.diagnostic_score_threshold = diagnostic_score_threshold
        self.search_strategy = _resolve_search_strategy(search_strategy)
        super().__init__()

    def optimize(
        self,
        evaluator: Any = None,
        data_mapper: Any = None,
        dataset: Optional[List[dict[str, Any]]] = None,
        metric: Optional[Callable] = None,
        *,
        target: Optional[OptimizationTarget] = None,
        evaluate_candidate: Optional[
            Callable[[AgentCandidate], CandidateEvaluation | EvaluationResult | float]
        ] = None,
        simulation_evaluator: Any = None,
        diagnoses: Optional[Iterable[ComponentDiagnosis | dict[str, Any]]] = None,
        max_rounds: Optional[int] = None,
        beam_width: Optional[int] = None,
        max_proposals_per_round: Optional[int] = None,
        target_score: Optional[float] = None,
        include_seed: Optional[bool] = None,
        auto_diagnose: Optional[bool] = None,
        diagnostic_score_threshold: Optional[float] = None,
        search_strategy: Optional[AgentSearchStrategy | str] = None,
        **kwargs: Any,
    ) -> OptimizationResult:
        active_target = target or self.target
        if active_target is None:
            raise ValueError("CouncilAgentOptimizer requires a target.")

        active_evaluator = (
            evaluate_candidate
            or self.evaluate_candidate
            or getattr(simulation_evaluator, "evaluate_candidate", None)
            or getattr(self.simulation_evaluator, "evaluate_candidate", None)
        )
        if active_evaluator is None:
            raise ValueError(
                "CouncilAgentOptimizer requires evaluate_candidate or simulation_evaluator."
            )

        active_diagnoses = _normalize_diagnoses(diagnoses)
        if diagnoses is None:
            active_diagnoses = list(self.diagnoses)
        active_max_rounds = self.max_rounds if max_rounds is None else max_rounds
        active_beam_width = self.beam_width if beam_width is None else beam_width
        active_max_proposals = (
            self.max_proposals_per_round
            if max_proposals_per_round is None
            else max_proposals_per_round
        )
        if active_max_rounds < 1:
            raise ValueError("max_rounds must be at least 1.")
        if active_beam_width < 1:
            raise ValueError("beam_width must be at least 1.")
        if active_max_proposals < 1:
            raise ValueError("max_proposals_per_round must be at least 1.")
        active_target_score = (
            self.target_score if target_score is None else target_score
        )
        use_include_seed = self.include_seed if include_seed is None else include_seed
        use_auto_diagnose = self.auto_diagnose if auto_diagnose is None else auto_diagnose
        active_diagnostic_threshold = (
            self.diagnostic_score_threshold
            if diagnostic_score_threshold is None
            else diagnostic_score_threshold
        )
        active_search_strategy = (
            self.search_strategy
            if search_strategy is None
            else _resolve_search_strategy(search_strategy)
        )

        seed_candidate = active_target.seed_candidate()
        evaluated: dict[str, CandidateEvaluation] = {}
        history: List[IterationHistory] = []
        role_counts: dict[str, int] = {}
        round_summaries: List[dict[str, Any]] = []
        best: CandidateEvaluation | None = None

        if use_include_seed:
            seed_evaluation = self._evaluate(
                seed_candidate,
                active_evaluator,
                evaluated,
                history,
                role_counts,
                role="seed",
                round_number=0,
            )
            best = seed_evaluation
            if use_auto_diagnose and not active_diagnoses:
                active_diagnoses = _diagnose_candidate_evaluation(
                    seed_evaluation,
                    failing_threshold=active_diagnostic_threshold,
                )

        search_paths = _ordered_search_paths(active_target, active_diagnoses)
        if not search_paths:
            raise ValueError("CouncilAgentOptimizer target search space cannot be empty.")

        for round_number in range(1, active_max_rounds + 1):
            proposals = active_search_strategy.propose(
                AgentSearchState(
                    seed_candidate=seed_candidate,
                    evaluations=list(evaluated.values()),
                    search_space=active_target.search_space,
                    search_paths=search_paths,
                    diagnoses=active_diagnoses,
                    beam_width=active_beam_width,
                    max_proposals=active_max_proposals,
                    round_number=round_number,
                )
            )
            proposals = [
                proposal
                for proposal in proposals
                if _candidate_id_for_patch(seed_candidate, proposal.patch)
                not in evaluated
            ]
            logger.info(
                "Council round %s evaluating %s proposal(s)",
                round_number,
                len(proposals),
            )

            round_best = best
            round_evaluated = 0
            for proposal in proposals:
                candidate = seed_candidate.with_patch(
                    proposal.patch,
                    metadata={
                        "kind": "council_proposal",
                        "optimizer": self.__class__.__name__,
                        "proposal_role": proposal.role,
                        "proposal_reason": proposal.reason,
                        "proposal_round": round_number,
                        "proposal_parent_ids": list(proposal.parent_ids),
                        "proposal_metadata": dict(proposal.metadata),
                    },
                )
                evaluation = self._evaluate(
                    candidate,
                    active_evaluator,
                    evaluated,
                    history,
                    role_counts,
                    role=proposal.role,
                    round_number=round_number,
                )
                if round_best is None or evaluation.score > round_best.score:
                    round_best = evaluation
                round_evaluated += 1
                if best is None or evaluation.score > best.score:
                    best = evaluation
                    logger.info(
                        "New best council candidate %s score=%.4f",
                        candidate.id,
                        evaluation.score,
                    )
                if best.score >= active_target_score:
                    break

            if round_best is not None and use_auto_diagnose:
                round_diagnoses = _diagnose_candidate_evaluation(
                    round_best,
                    failing_threshold=active_diagnostic_threshold,
                )
                if round_diagnoses:
                    active_diagnoses = _dedupe_diagnoses(
                        [*active_diagnoses, *round_diagnoses]
                    )
                    search_paths = _ordered_search_paths(active_target, active_diagnoses)

            round_summaries.append(
                {
                    "round": round_number,
                    "proposals": len(proposals),
                    "evaluated": round_evaluated,
                    "best_score": best.score if best is not None else None,
                    "search_paths": list(search_paths),
                }
            )
            if best is not None and best.score >= active_target_score:
                break

        if best is None:
            raise ValueError("CouncilAgentOptimizer did not evaluate any candidates.")

        strategy_name = getattr(
            active_search_strategy,
            "name",
            active_search_strategy.__class__.__name__,
        )
        strategy_roles = list(getattr(active_search_strategy, "roles", ()))
        metadata = {
            "optimizer": self.__class__.__name__,
            "strategy": strategy_name,
            "roles": strategy_roles,
            "target_name": best.candidate.target_name,
            "best_candidate_id": best.candidate.id,
            "search_paths": list(search_paths),
            "rounds": round_summaries,
            "beam_width": active_beam_width,
            "max_proposals_per_round": active_max_proposals,
            "role_evaluations": role_counts,
        }
        strategy_metadata = _strategy_metadata(active_search_strategy)
        if strategy_metadata:
            metadata["strategy_metadata"] = strategy_metadata
            if "role_graph" in strategy_metadata:
                metadata["role_graph"] = strategy_metadata["role_graph"]
        if active_diagnoses:
            metadata["diagnostics"] = [_dump_model(item) for item in active_diagnoses]
            metadata["auto_diagnosed"] = use_auto_diagnose

        return OptimizationResult(
            best_generator=best.candidate,
            best_candidate=best.candidate,
            history=history,
            final_score=best.score,
            total_iterations=len(history),
            total_evaluations=len(history),
            metadata=metadata,
        )

    def _evaluate(
        self,
        candidate: AgentCandidate,
        evaluator: Callable[[AgentCandidate], CandidateEvaluation | EvaluationResult | float],
        evaluated: dict[str, CandidateEvaluation],
        history: List[IterationHistory],
        role_counts: dict[str, int],
        *,
        role: str,
        round_number: int,
    ) -> CandidateEvaluation:
        if candidate.id in evaluated:
            return evaluated[candidate.id]

        value = evaluator(candidate)
        evaluation = _normalize_candidate_evaluation(value, candidate)
        evaluation.metadata = {
            **candidate.metadata,
            **evaluation.metadata,
            "proposal_role": role,
            "proposal_round": round_number,
        }
        evaluated[candidate.id] = evaluation
        history.append(_history_from_candidate(evaluation))
        role_counts[role] = role_counts.get(role, 0) + 1
        return evaluation


class SocietyAgentOptimizer(CouncilAgentOptimizer):
    """
    Council optimizer preset using role-diverse society search.

    It is deterministic by default and uses the same `OptimizationTarget` and
    evaluator contracts as `AgentOptimizer`/`CouncilAgentOptimizer`.
    """

    def __init__(
        self,
        *args: Any,
        search_strategy: Optional[AgentSearchStrategy | str] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            *args,
            search_strategy=search_strategy or SocietySearchStrategy(),
            **kwargs,
        )


def _ordered_search_paths(
    target: OptimizationTarget,
    diagnoses: Sequence[ComponentDiagnosis],
) -> List[str]:
    allowed_paths = relevant_search_paths(target.search_space, diagnoses)
    return [path for path in target.search_space if path in allowed_paths]


def _resolve_search_strategy(
    strategy: Optional[AgentSearchStrategy | str],
) -> AgentSearchStrategy:
    if strategy is None or strategy == "council":
        return DeterministicCouncilStrategy()
    if strategy == "society":
        return SocietySearchStrategy()
    if strategy in {"role_graph", "society_role_graph"}:
        return SocietyRoleGraphSearchStrategy()
    if isinstance(strategy, AgentSearchStrategy):
        return strategy
    if hasattr(strategy, "propose"):
        return strategy  # type: ignore[return-value]
    raise ValueError(
        "search_strategy must be 'council', 'society', 'role_graph', "
        "'society_role_graph', or an AgentSearchStrategy."
    )


def _strategy_metadata(strategy: AgentSearchStrategy) -> dict[str, Any]:
    to_metadata = getattr(strategy, "to_metadata", None)
    if callable(to_metadata):
        metadata = to_metadata()
        if isinstance(metadata, Mapping):
            return dict(metadata)
    return {}


def _normalize_society_role_graph(
    role_graph: Optional[Sequence[AgentSocietyRole | Mapping[str, Any]]],
) -> tuple[AgentSocietyRole, ...]:
    roles: List[AgentSocietyRole] = []
    for item in role_graph or DEFAULT_SOCIETY_ROLE_GRAPH:
        if isinstance(item, AgentSocietyRole):
            role = item
        elif isinstance(item, Mapping):
            role = AgentSocietyRole(
                name=str(item["name"]),
                proposal_kind=str(item["proposal_kind"]),
                phase=int(item.get("phase", 1)),
                depends_on=tuple(str(value) for value in item.get("depends_on", ())),
                path_prefixes=tuple(
                    str(value) for value in item.get("path_prefixes", ())
                ),
                archetype=str(item.get("archetype", "")),
                description=str(item.get("description", "")),
            )
        else:
            raise TypeError("role_graph entries must be AgentSocietyRole or mappings")
        if role.proposal_kind not in ROLE_GRAPH_PROPOSAL_KINDS:
            raise ValueError(
                f"Unsupported society role proposal_kind '{role.proposal_kind}'."
            )
        if role.phase < 1:
            raise ValueError("society role phase must be at least 1.")
        roles.append(role)

    names = [role.name for role in roles]
    if len(names) != len(set(names)):
        raise ValueError("society role names must be unique.")
    return tuple(roles)


def _build_round_proposals(
    *,
    seed_candidate: AgentCandidate,
    evaluations: Sequence[CandidateEvaluation],
    search_space: dict[str, List[Any]],
    search_paths: Sequence[str],
    beam_width: int,
    max_proposals: int,
    round_number: int,
) -> List[AgentSearchProposal]:
    proposals: List[AgentSearchProposal] = []
    seen: set[str] = set()
    ranked = sorted(
        evaluations,
        key=lambda item: (item.score, -len(item.candidate.patch), item.candidate.id),
        reverse=True,
    )
    changed_ranked = [item for item in ranked if item.candidate.patch]
    beam = ranked[:beam_width] or [
        CandidateEvaluation(candidate=seed_candidate, score=0.0)
    ]

    if round_number > 1:
        for proposal in _synthesis_proposals(changed_ranked[:beam_width], search_paths):
            _append_proposal(proposals, seen, proposal, max_proposals)

    if round_number > 1:
        for evaluation in beam:
            if not evaluation.candidate.patch:
                continue
            for proposal in _critic_proposals(
                evaluation.candidate,
                search_space,
                search_paths,
            ):
                _append_proposal(proposals, seen, proposal, max_proposals)

    for proposal in _explorer_proposals(seed_candidate, search_space, search_paths):
        _append_proposal(proposals, seen, proposal, max_proposals)

    if round_number > 1:
        for evaluation in changed_ranked[:beam_width]:
            for proposal in _steward_proposals(evaluation.candidate):
                _append_proposal(proposals, seen, proposal, max_proposals)

    return proposals


def _build_society_proposals(
    *,
    seed_candidate: AgentCandidate,
    evaluations: Sequence[CandidateEvaluation],
    search_space: dict[str, List[Any]],
    search_paths: Sequence[str],
    diagnoses: Sequence[ComponentDiagnosis],
    beam_width: int,
    max_proposals: int,
    round_number: int,
) -> List[AgentSearchProposal]:
    ranked = sorted(
        evaluations,
        key=lambda item: (item.score, -len(item.candidate.patch), item.candidate.id),
        reverse=True,
    )
    changed_ranked = [item for item in ranked if item.candidate.patch]
    beam = ranked[:beam_width] or [
        CandidateEvaluation(candidate=seed_candidate, score=0.0)
    ]

    streams: List[Iterable[AgentSearchProposal]] = []
    if round_number > 1:
        streams.append(_coverage_synthesis_proposals(changed_ranked, search_paths))
        streams.append(_synthesis_proposals(changed_ranked[:beam_width], search_paths))
        streams.append(
            proposal
            for evaluation in beam
            if evaluation.candidate.patch
            for proposal in _critic_proposals(
                evaluation.candidate,
                search_space,
                search_paths,
            )
        )

    streams.extend(
        [
            _specialist_proposals(
                seed_candidate,
                search_space,
                search_paths,
                diagnoses,
            ),
            _explorer_proposals(seed_candidate, search_space, search_paths),
            _adversary_proposals(
                seed_candidate,
                ranked[:beam_width],
                search_space,
                search_paths,
            ),
        ]
    )

    if round_number > 1:
        streams.append(
            proposal
            for evaluation in changed_ranked[:beam_width]
            for proposal in _steward_proposals(evaluation.candidate)
        )

    return _interleave_proposal_streams(streams, max_proposals)


def _build_role_graph_society_proposals(
    *,
    seed_candidate: AgentCandidate,
    evaluations: Sequence[CandidateEvaluation],
    search_space: dict[str, List[Any]],
    search_paths: Sequence[str],
    diagnoses: Sequence[ComponentDiagnosis],
    beam_width: int,
    max_proposals: int,
    round_number: int,
    role_graph: Sequence[AgentSocietyRole],
) -> List[AgentSearchProposal]:
    ranked = sorted(
        evaluations,
        key=lambda item: (item.score, -len(item.candidate.patch), item.candidate.id),
        reverse=True,
    )
    changed_ranked = [item for item in ranked if item.candidate.patch]
    beam = ranked[:beam_width] or [
        CandidateEvaluation(candidate=seed_candidate, score=0.0)
    ]
    evaluated_roles = {
        str(evaluation.metadata.get("proposal_role"))
        for evaluation in evaluations
        if evaluation.metadata.get("proposal_role")
    }

    streams: List[Iterable[AgentSearchProposal]] = []
    for role in _ordered_role_graph_roles(role_graph, round_number):
        if not _society_role_is_active(role, evaluated_roles, round_number):
            continue
        role_paths = _role_search_paths(role, search_paths)
        if role.proposal_kind != "steward" and not role_paths:
            continue
        stream = _role_graph_stream(
            role,
            seed_candidate=seed_candidate,
            ranked=ranked,
            changed_ranked=changed_ranked,
            beam=beam,
            search_space=search_space,
            search_paths=role_paths,
            diagnoses=diagnoses,
            beam_width=beam_width,
            round_number=round_number,
        )
        streams.append(stream)

    return _interleave_proposal_streams(streams, max_proposals)


def _ordered_role_graph_roles(
    role_graph: Sequence[AgentSocietyRole],
    round_number: int,
) -> List[AgentSocietyRole]:
    if round_number <= 1:
        return list(role_graph)
    priority = {
        "coverage_synthesis": 0,
        "synthesizer": 0,
        "critic": 1,
        "adversary": 2,
        "specialist": 2,
        "explorer": 2,
        "steward": 3,
    }
    return [
        role
        for _, role in sorted(
            enumerate(role_graph),
            key=lambda item: (priority.get(item[1].proposal_kind, 2), item[0]),
        )
    ]


def _society_role_is_active(
    role: AgentSocietyRole,
    evaluated_roles: set[str],
    round_number: int,
) -> bool:
    if role.phase > round_number:
        return False
    if not role.depends_on:
        return True
    return bool(set(role.depends_on) & evaluated_roles)


def _role_graph_stream(
    role: AgentSocietyRole,
    *,
    seed_candidate: AgentCandidate,
    ranked: Sequence[CandidateEvaluation],
    changed_ranked: Sequence[CandidateEvaluation],
    beam: Sequence[CandidateEvaluation],
    search_space: dict[str, List[Any]],
    search_paths: Sequence[str],
    diagnoses: Sequence[ComponentDiagnosis],
    beam_width: int,
    round_number: int,
) -> Iterable[AgentSearchProposal]:
    if role.proposal_kind == "specialist":
        proposals = _specialist_proposals(
            seed_candidate,
            search_space,
            search_paths,
            diagnoses,
        )
    elif role.proposal_kind == "explorer":
        proposals = _explorer_proposals(seed_candidate, search_space, search_paths)
    elif role.proposal_kind == "adversary":
        proposals = _adversary_proposals(
            seed_candidate,
            ranked[:beam_width],
            search_space,
            search_paths,
        )
    elif role.proposal_kind == "critic" and round_number > 1:
        proposals = (
            proposal
            for evaluation in beam
            if evaluation.candidate.patch
            for proposal in _critic_proposals(
                evaluation.candidate,
                search_space,
                search_paths,
            )
        )
    elif role.proposal_kind == "coverage_synthesis" and round_number > 1:
        proposals = _coverage_synthesis_proposals(changed_ranked, search_paths)
    elif role.proposal_kind == "synthesizer" and round_number > 1:
        proposals = _synthesis_proposals(changed_ranked[:beam_width], search_paths)
    elif role.proposal_kind == "steward" and round_number > 1:
        allowed = set(search_paths)
        proposals = (
            proposal
            for evaluation in changed_ranked[:beam_width]
            for proposal in _steward_proposals(evaluation.candidate)
            if not allowed or allowed & set(proposal.patch)
        )
    else:
        proposals = ()

    return _annotate_role_graph_proposals(role, proposals)


def _annotate_role_graph_proposals(
    role: AgentSocietyRole,
    proposals: Iterable[AgentSearchProposal],
) -> Iterable[AgentSearchProposal]:
    for proposal in proposals:
        metadata = {
            **dict(proposal.metadata),
            "role_kind": role.proposal_kind,
            "role_phase": role.phase,
            "role_archetype": role.archetype,
            "role_description": role.description,
            "role_path_prefixes": list(role.path_prefixes),
            "role_depends_on": list(role.depends_on),
        }
        yield AgentSearchProposal(
            patch=proposal.patch,
            role=role.name,
            parent_ids=proposal.parent_ids,
            reason=f"{role.proposal_kind}:{proposal.reason}",
            metadata=metadata,
        )


def _role_search_paths(
    role: AgentSocietyRole,
    search_paths: Sequence[str],
) -> List[str]:
    if not role.path_prefixes:
        return list(search_paths)
    return [
        path
        for path in search_paths
        if any(path == prefix or path.startswith(f"{prefix}.") for prefix in role.path_prefixes)
    ]


def _interleave_proposal_streams(
    streams: Sequence[Iterable[AgentSearchProposal]],
    max_proposals: int,
) -> List[AgentSearchProposal]:
    proposals: List[AgentSearchProposal] = []
    seen: set[str] = set()
    iterators = [iter(stream) for stream in streams]
    active = [True for _ in iterators]

    while len(proposals) < max_proposals and any(active):
        for index, iterator in enumerate(iterators):
            if not active[index]:
                continue
            while True:
                try:
                    proposal = next(iterator)
                except StopIteration:
                    active[index] = False
                    break
                before = len(proposals)
                _append_proposal(proposals, seen, proposal, max_proposals)
                if len(proposals) > before:
                    break
                if len(proposals) >= max_proposals:
                    break
            if len(proposals) >= max_proposals:
                break
    return proposals


def _specialist_proposals(
    seed_candidate: AgentCandidate,
    search_space: dict[str, List[Any]],
    search_paths: Sequence[str],
    diagnoses: Sequence[ComponentDiagnosis],
) -> Iterable[AgentSearchProposal]:
    for group_key, paths in _path_groups(search_paths, diagnoses).items():
        patch: dict[str, Any] = {}
        for path in paths:
            value = _first_non_seed_value(seed_candidate, search_space, path)
            if value is not _NO_VALUE:
                patch[path] = value
        if not patch:
            continue
        yield AgentSearchProposal(
            patch=patch,
            role="specialist",
            parent_ids=(seed_candidate.id,),
            reason=f"apply_component_bundle:{group_key}",
        )


def _adversary_proposals(
    seed_candidate: AgentCandidate,
    ranked: Sequence[CandidateEvaluation],
    search_space: dict[str, List[Any]],
    search_paths: Sequence[str],
) -> Iterable[AgentSearchProposal]:
    boundary_patch: dict[str, Any] = {}
    for path in search_paths:
        value = _last_non_seed_value(seed_candidate, search_space, path)
        if value is not _NO_VALUE:
            boundary_patch[path] = value
        if len(boundary_patch) >= 3:
            break
    if boundary_patch:
        yield AgentSearchProposal(
            patch=boundary_patch,
            role="adversary",
            parent_ids=(seed_candidate.id,),
            reason="stress_boundary_combination",
        )

    for evaluation in ranked:
        source_patch = dict(evaluation.candidate.patch)
        if not source_patch:
            continue
        for path in search_paths:
            value = _last_non_seed_value(evaluation.candidate, search_space, path)
            if value is _NO_VALUE:
                continue
            patch = {**source_patch, path: value}
            yield AgentSearchProposal(
                patch=patch,
                role="adversary",
                parent_ids=(evaluation.candidate.id,),
                reason="stress_candidate_with_boundary_change",
            )


def _path_groups(
    search_paths: Sequence[str],
    diagnoses: Sequence[ComponentDiagnosis],
) -> dict[str, List[str]]:
    groups: dict[str, List[str]] = {}
    for path in search_paths:
        group_key = _diagnostic_group_key(path, diagnoses) or path.split(".", 1)[0]
        groups.setdefault(group_key, []).append(path)
    return groups


def _diagnostic_group_key(
    path: str,
    diagnoses: Sequence[ComponentDiagnosis],
) -> Optional[str]:
    for diagnosis in diagnoses:
        for suggested_path in diagnosis.suggested_paths:
            if path == suggested_path or path.startswith(f"{suggested_path}."):
                return f"{diagnosis.component}:{suggested_path}"
        if path == diagnosis.component or path.startswith(f"{diagnosis.component}."):
            return diagnosis.component
    return None


class _NoValue:
    pass


_NO_VALUE = _NoValue()


def _first_non_seed_value(
    seed_candidate: AgentCandidate,
    search_space: dict[str, List[Any]],
    path: str,
) -> Any:
    current = seed_candidate.get_path(path)
    for value in search_space.get(path, []):
        if value != current:
            return value
    return _NO_VALUE


def _last_non_seed_value(
    candidate: AgentCandidate,
    search_space: dict[str, List[Any]],
    path: str,
) -> Any:
    current = candidate.get_path(path)
    for value in reversed(search_space.get(path, [])):
        if value != current:
            return value
    return _NO_VALUE


def _synthesis_proposals(
    evaluations: Sequence[CandidateEvaluation],
    search_paths: Sequence[str],
) -> Iterable[AgentSearchProposal]:
    if len(evaluations) < 2:
        return

    allowed = set(search_paths)
    all_sources = tuple(evaluations)
    yield AgentSearchProposal(
        patch=_merge_ranked_patches(all_sources, allowed),
        role="synthesizer",
        parent_ids=tuple(item.candidate.id for item in all_sources),
        reason="combine_best_partial_candidates",
    )
    for left, right in combinations(evaluations, 2):
        yield AgentSearchProposal(
            patch=_merge_ranked_patches((left, right), allowed),
            role="synthesizer",
            parent_ids=(left.candidate.id, right.candidate.id),
            reason="combine_pairwise_partial_candidates",
        )


def _coverage_synthesis_proposals(
    evaluations: Sequence[CandidateEvaluation],
    search_paths: Sequence[str],
) -> Iterable[AgentSearchProposal]:
    if not evaluations:
        return

    allowed = set(search_paths)
    patch: dict[str, Any] = {}
    parent_ids: List[str] = []
    for path in search_paths:
        path_evaluations = [
            evaluation
            for evaluation in evaluations
            if path in evaluation.candidate.patch and path in allowed
        ]
        if not path_evaluations:
            continue
        selected = max(
            path_evaluations,
            key=lambda item: (
                item.score,
                -len(item.candidate.patch),
                item.candidate.id,
            ),
        )
        patch[path] = selected.candidate.patch[path]
        parent_ids.append(selected.candidate.id)

    if patch:
        yield AgentSearchProposal(
            patch=patch,
            role="synthesizer",
            parent_ids=tuple(dict.fromkeys(parent_ids)),
            reason="combine_best_path_representatives",
        )


def _critic_proposals(
    source_candidate: AgentCandidate,
    search_space: dict[str, List[Any]],
    search_paths: Sequence[str],
) -> Iterable[AgentSearchProposal]:
    source_patch = dict(source_candidate.patch)
    for path in search_paths:
        for value in search_space.get(path, []):
            if source_candidate.get_path(path) == value:
                continue
            patch = {**source_patch, path: value}
            yield AgentSearchProposal(
                patch=patch,
                role="critic",
                parent_ids=(source_candidate.id,),
                reason="test_next_change_against_current_candidate",
            )


def _explorer_proposals(
    seed_candidate: AgentCandidate,
    search_space: dict[str, List[Any]],
    search_paths: Sequence[str],
) -> Iterable[AgentSearchProposal]:
    for path in search_paths:
        for value in search_space.get(path, []):
            if seed_candidate.get_path(path) == value:
                continue
            yield AgentSearchProposal(
                patch={path: value},
                role="explorer",
                parent_ids=(seed_candidate.id,),
                reason="isolate_single_path_effect",
            )


def _steward_proposals(
    source_candidate: AgentCandidate,
) -> Iterable[AgentSearchProposal]:
    if len(source_candidate.patch) < 2:
        return
    for path in source_candidate.patch:
        patch = {
            key: value
            for key, value in source_candidate.patch.items()
            if key != path
        }
        yield AgentSearchProposal(
            patch=patch,
            role="steward",
            parent_ids=(source_candidate.id,),
            reason="remove_one_change_to_check_minimality",
        )


def _merge_ranked_patches(
    evaluations: Sequence[CandidateEvaluation],
    allowed_paths: set[str],
) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    for evaluation in evaluations:
        for path, value in evaluation.candidate.patch.items():
            if path in allowed_paths and path not in patch:
                patch[path] = value
    return patch


def _append_proposal(
    proposals: List[AgentSearchProposal],
    seen: set[str],
    proposal: AgentSearchProposal,
    max_proposals: int,
) -> None:
    if len(proposals) >= max_proposals or not proposal.patch:
        return
    key = _canonical_patch(proposal.patch)
    if key in seen:
        return
    seen.add(key)
    proposals.append(proposal)


def _candidate_id_for_patch(
    seed_candidate: AgentCandidate,
    patch: dict[str, Any],
) -> str:
    return seed_candidate.with_patch(patch).id


def _canonical_patch(patch: dict[str, Any]) -> str:
    return json.dumps(patch, sort_keys=True, default=str)
