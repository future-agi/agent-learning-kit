"""The shapes a scenario's proof writes and reads across the prove lane: what each control did, each cited source
and rule, each leak, and the proof record saved per case. Files on disk are read as raw JSON and narrowed where used."""

from __future__ import annotations

from typing import Any, Literal, TypedDict


class JudgeVerdict(TypedDict):
    """The judge's call on one sub-goal a judged control names."""

    verdict: Literal["pass", "fail", "inconclusive"]
    explanation: str


class _PlayedControl(TypedDict):
    name: str
    why: str
    # Whether every step of the control ran: False when it was not played, or a step was assumed (recorded unrun).
    ran: bool
    # True or False only for a control that ran; None otherwise, never evidence either way.
    failed: bool | None
    held: list[str]
    assumed: list[str]


class ControlResult(_PlayedControl, total=False):
    """One negative control as played. ``failed`` is None when it could not be played: no evidence either way."""

    broken: list[str]
    # Every check the control's steps made fail, named or not; read only with `ran` or a check of the calls alone.
    failing: list[str]
    judged: bool
    verdicts: dict[str, JudgeVerdict]
    error: str
    # The tools the control called that the reference solution never calls.
    unmatched: list[str]
    # The tools the control tried that the reference never calls and the world refused: nothing committed.
    attempted: list[str]
    # The writes the control committed beyond the reference's own, as `action(arguments)`: the records it changes.
    changed: list[str]


class _CitedSource(TypedDict):
    kind: str
    ref: str
    resolved: bool | None
    problem: str


class SourceResult(_CitedSource, total=False):
    """One cited source and whether it resolved; ``resolved`` None means the platform decides it."""

    resolved_ref: str
    # A resolved rule's place among the agent's rules.
    rule_index: int
    matched_digest: str
    # "agent_source" for a file found in the agent's own source rather than the authoring tree.
    origin: str


class PolicyCheck(TypedDict):
    """One cited rule checked against the expected outcome; ``conflict`` None when the judge decided nothing."""

    rule: str
    ref: str
    conflict: bool | None
    why: str


class LeakRecord(TypedDict):
    """Visible text that repeats grader-only material."""

    who: str
    field: str
    grader_field: str
    excerpt: str


class CheckRecord(TypedDict):
    """One check's outcome as a live proof keeps it."""

    name: str
    held: bool
    said: str
    broken: bool


class LiveResult(TypedDict):
    """One case played on the live runtime: its reference, the empty run and its controls."""

    assumed: list[str]
    refused: list[str]
    with_solution: list[CheckRecord]
    with_nothing: list[CheckRecord]
    outcome: Literal["change", "no_change"]
    controls: list[ControlResult]


class _ProofFields(TypedDict):
    format: int
    holds: bool
    proved: bool
    kept_as_written: bool
    ready: bool
    solvable: bool
    not_vacuous: bool
    why: str
    proved_at: str
    assumed: list[str]
    solution_steps: int
    deferred: bool
    judged_only: bool
    outcome: str
    controls: list[ControlResult]
    leaks: list[LeakRecord]
    sources: list[SourceResult]
    policy_checks: list[PolicyCheck]
    evidence: dict[str, list[str]]
    # Checks the reference failed only because a step it took was assumed (they read the world alone).
    excused: list[str]
    blocked_reason: str | None


class ProofEntry(_ProofFields, total=False):
    """One case's latest proof in ``proofs.json`` (format 2)."""

    trial: dict[str, Any]
    # Whether the case was played on the live runtime (a certification's proving).
    live: bool
    # Whether a submit refuses a case with this proof (`prove.refuses_keeping`): the one rule, so no reader copies it.
    refused: bool
    # Tools the reference solution calls that the agent version under test lacks (`tool_not_on_agent`), by name;
    # empty when none, or when the agent's tools are not known.
    missing_tools: list[str]
