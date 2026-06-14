"""First-class Task + TaskDataset — the benchmark surface (beat-HUD B1).

A Task is a THIN COMPOSITION over models that already exist and are content-
addressed (no new invention — empirically validated by spike):

  * the existing typed ``Scenario(kind="task")`` (one effective task row) carries
    goal + verification (fi.simulate.simulation.models);
  * the objective is compiled by ``loss.compile_objective`` VERBATIM, so the
    Goodhart-guard discipline ("There is no override.") holds for a Task exactly
    as for any training loss — a guardless objective is REJECTED here too;
  * the world kind must be a member of ``contract.resolved_world_kinds()`` (the
    frozen closed set + R4 extras) — never widened here;
  * ``execution_class`` is DERIVED from the world kind (+ a fixture flag), never
    asserted above the substrate's truth: ``browser``/``computer_use``/
    ``code_exec``/``voice_telephony`` are TYPED-ONLY in v1 and can be at most
    ``typed_only``; only ``conversation``/``tool_api`` may be ``executable``.
    This is the kit's honesty moat vs HUD: a typed-only task can NEVER masquerade
    as a live-executed one.

Tasks and datasets are content-addressed (sha256 over the canonical payload minus
``version``), using the same rounding/canonicalization idiom as ``loss.py``. NO
provenance/capture fields are baked in yet (the real-execution fork is deferred;
keeping them out means a later fork decision costs nothing here).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from .loss import ObjectiveError, compile_objective

AGENT_LEARNING_TASK_KIND = "agent-learning.task.v1"
AGENT_LEARNING_TASK_DATASET_KIND = "agent-learning.task-dataset.v1"

# closed sets (home here; trinity.py mirrors literals for the gate — GUNA_AXES
# cross-pin pattern; trinity never imports this module).
V1_TASK_DIFFICULTIES = ("easy", "medium", "hard")
V1_TASK_EXECUTION_CLASSES = ("executable", "typed_only", "fixture")


class TaskError(ValueError):
    """Raised when a Task violates the §B1 contract. A ``ValueError`` subclass so
    callers can ``except ValueError`` exactly as for ``ObjectiveError``."""


class TaskDatasetError(TaskError):
    """Raised when a TaskDataset violates the contract."""


# --- canonicalization (the loss.py idiom, factored locally) -----------------
def _round_floats(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, Mapping):
        return {k: _round_floats(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_floats(v) for v in value]
    return value


def _content_hash(payload: Mapping[str, Any]) -> str:
    rounded = _round_floats(dict(payload))
    canonical = json.dumps(rounded, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _resolved_world_kinds() -> tuple[str, ...]:
    """Lazy downward import (the image_loop.py idiom) — the closed set + R4
    extras, never widened here."""
    from fi.simulate.simulation import contract as _contract

    return tuple(_contract.resolved_world_kinds())


def _executable_world_kinds() -> tuple[str, ...]:
    from fi.simulate.simulation import contract as _contract

    return tuple(_contract.EXECUTABLE_WORLD_KINDS_V1)


def derive_execution_class(world_kind: str, *, fixture_only: bool = False) -> str:
    """Derive the HONEST substrate stamp. ``fixture_only`` (a task that only ever
    replays a committed fixture) → ``fixture``; a world kind that EXECUTES in v1
    (``conversation``/``tool_api``) → ``executable``; everything else (the
    typed-only kinds) → ``typed_only``. NEVER returns a class above the
    substrate's real capability."""

    if fixture_only:
        return "fixture"
    if world_kind in _executable_world_kinds():
        return "executable"
    return "typed_only"


def _task_anchor_terms(objective: Mapping[str, Any]) -> list[str]:
    """Terms explicitly flagged as deterministic ground-truth anchors
    (``anchor: true``). The reward-hacking-resistance-by-construction rule below
    requires >= 1; an objective scored only by un-anchored (e.g. judge) terms is
    a Task contract violation — the Task analogue of the image judge-only ban."""

    return [
        str(term.get("eval"))
        for term in (objective.get("evals") or [])
        if isinstance(term, Mapping) and term.get("anchor") is True and term.get("eval")
    ]


def compile_task(payload: Mapping[str, Any]) -> dict:
    """Validate + stamp a Task. Enforces, ON TOP of the verbatim
    ``loss.compile_objective`` Goodhart guard:

      (a) ``scenario.kind == "task"`` with exactly ONE effective task row;
      (b) ``world.kind`` is a member of ``resolved_world_kinds()``;
      (c) ``difficulty`` in ``V1_TASK_DIFFICULTIES``;
      (d) the objective carries >= 1 deterministic ground-truth ANCHOR term
          (``anchor: true``) — reward-hacking-resistance by construction;
      (e) ``execution_class`` is DERIVED, never asserted above the substrate
          (a caller-supplied class that overclaims the world kind is rejected).
    Then delegates objective compilation to ``loss.compile_objective`` VERBATIM."""

    raw = dict(payload)

    task_id = str(raw.get("id") or "").strip()
    if not task_id:
        raise TaskError("task.id is required")
    title = str(raw.get("title") or "").strip()
    if not title:
        raise TaskError("task.title is required")

    # (b) world.kind
    world = dict(raw.get("world") or {})
    world_kind = str(world.get("kind") or "")
    resolved = _resolved_world_kinds()
    if world_kind not in resolved:
        raise TaskError(
            f"task.world.kind {world_kind!r} not in resolved world kinds {resolved}"
        )

    # (c) difficulty
    difficulty = str(raw.get("difficulty") or "medium")
    if difficulty not in V1_TASK_DIFFICULTIES:
        raise TaskError(f"task.difficulty {difficulty!r} not in {V1_TASK_DIFFICULTIES}")

    # (a) scenario.kind == "task", exactly one effective row
    scenario = dict(raw.get("scenario") or {})
    if str(scenario.get("kind") or "") != "task":
        raise TaskError("task.scenario.kind must be 'task'")
    dataset_rows = list(scenario.get("dataset") or [])
    if len(dataset_rows) != 1:
        raise TaskError(
            f"task.scenario must carry exactly one task row; got {len(dataset_rows)}"
        )

    # objective: compile VERBATIM (guards enforced; guardless REJECTED here too)
    objective_payload = raw.get("objective")
    if not isinstance(objective_payload, Mapping):
        raise TaskError("task.objective is required (a declared ObjectiveSpec)")
    try:
        compiled_objective = compile_objective(objective_payload)
    except ObjectiveError as exc:
        # surface as a Task error while preserving the guard message
        raise TaskError(f"task.objective invalid: {exc}") from exc

    # (d) >= 1 deterministic anchor term
    anchors = _task_anchor_terms(objective_payload)
    if not anchors:
        raise TaskError(
            "task.objective must carry >= 1 deterministic ground-truth anchor "
            "term (mark it `anchor: true`); a task scored only by un-anchored "
            "(e.g. judge) terms is reward-hackable by construction"
        )

    # (e) execution_class derived; reject overclaim
    fixture_only = bool(raw.get("fixture_only"))
    derived_class = derive_execution_class(world_kind, fixture_only=fixture_only)
    asserted = raw.get("execution_class")
    if asserted is not None:
        asserted = str(asserted)
        if asserted not in V1_TASK_EXECUTION_CLASSES:
            raise TaskError(
                f"task.execution_class {asserted!r} not in {V1_TASK_EXECUTION_CLASSES}"
            )
        rank = {"fixture": 0, "typed_only": 1, "executable": 2}
        if rank[asserted] > rank[derived_class]:
            raise TaskError(
                f"task.execution_class {asserted!r} overclaims the substrate; "
                f"world.kind {world_kind!r} (fixture_only={fixture_only}) supports "
                f"at most {derived_class!r}"
            )

    compiled: dict[str, Any] = {
        "kind": AGENT_LEARNING_TASK_KIND,
        "id": task_id,
        "title": title,
        "world": {"kind": world_kind, **({"spec": dict(world["spec"])} if world.get("spec") else {})},
        "difficulty": difficulty,
        "tags": [str(t) for t in (raw.get("tags") or [])],
        "scenario": scenario,
        "objective": compiled_objective,
        "anchor_terms": anchors,
        "execution_class": derived_class,
    }
    if raw.get("goal") is not None:
        compiled["goal"] = dict(raw["goal"])
    if raw.get("verification") is not None:
        compiled["verification"] = dict(raw["verification"])
    compiled["version"] = _content_hash(
        {k: v for k, v in compiled.items() if k != "version"}
    )
    return compiled


def compile_task_dataset(payload: Mapping[str, Any]) -> dict:
    """Validate + stamp a TaskDataset: every task compiles via ``compile_task``;
    ids are unique; any ``splits`` reference existing ids; coverage by world.kind
    and difficulty is computed. Content-addressed over the compiled tasks."""

    raw = dict(payload)
    name = str(raw.get("name") or "").strip()
    if not name:
        raise TaskDatasetError("dataset.name is required")

    task_payloads = list(raw.get("tasks") or [])
    if not task_payloads:
        raise TaskDatasetError("dataset.tasks must list >= 1 task")

    compiled_tasks: list[dict] = []
    seen_ids: set[str] = set()
    by_world_kind: dict[str, int] = {}
    by_difficulty: dict[str, int] = {}
    for index, task_payload in enumerate(task_payloads, start=1):
        if not isinstance(task_payload, Mapping):
            raise TaskDatasetError(f"dataset.tasks[{index}] must be a mapping")
        try:
            task = compile_task(task_payload)
        except TaskError as exc:
            raise TaskDatasetError(f"dataset.tasks[{index}] invalid: {exc}") from exc
        if task["id"] in seen_ids:
            raise TaskDatasetError(f"duplicate task id {task['id']!r}")
        seen_ids.add(task["id"])
        compiled_tasks.append(task)
        by_world_kind[task["world"]["kind"]] = by_world_kind.get(task["world"]["kind"], 0) + 1
        by_difficulty[task["difficulty"]] = by_difficulty.get(task["difficulty"], 0) + 1

    splits = raw.get("splits")
    normalized_splits: dict[str, list[str]] = {}
    if splits is not None:
        if not isinstance(splits, Mapping):
            raise TaskDatasetError("dataset.splits must be a mapping of name -> [id]")
        for split_name, ids in splits.items():
            id_list = [str(i) for i in (ids or [])]
            missing = [i for i in id_list if i not in seen_ids]
            if missing:
                raise TaskDatasetError(
                    f"dataset.splits[{split_name!r}] references unknown ids {missing}"
                )
            normalized_splits[str(split_name)] = id_list

    compiled: dict[str, Any] = {
        "kind": AGENT_LEARNING_TASK_DATASET_KIND,
        "name": name,
        "tasks": compiled_tasks,
        "coverage": {
            "by_world_kind": dict(sorted(by_world_kind.items())),
            "by_difficulty": dict(sorted(by_difficulty.items())),
            "count": len(compiled_tasks),
        },
    }
    if normalized_splits:
        compiled["splits"] = normalized_splits
    if raw.get("license") is not None:
        compiled["license"] = str(raw["license"])
    compiled["version"] = _content_hash(
        {k: v for k, v in compiled.items() if k != "version"}
    )
    return compiled


def task_world_kinds(dataset: Mapping[str, Any]) -> Sequence[str]:
    """Convenience: the sorted set of world kinds a (compiled) dataset spans."""

    return sorted({task["world"]["kind"] for task in (dataset.get("tasks") or [])})
