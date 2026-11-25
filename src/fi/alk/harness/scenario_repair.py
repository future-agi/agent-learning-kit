"""Constrained, value-free receipts for generated scenario repairs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection
from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator

from .scenario import Scenario

SCENARIO_REPAIR_SCHEMA_VERSION = "futureagi.scenario-repair.v1"


class ScenarioChangeKind(str, Enum):
    ADDED = "added"
    REPLACED = "replaced"
    # A held case given up for a new case in the same plan cell (`replaceable`).
    REMOVED = "removed"


class ScenarioChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_key: str
    kind: ScenarioChangeKind
    before_hash: str | None = None
    after_hash: str


class ScenarioRepairReceipt(BaseModel):
    """Proof that a scenario repair added/replaced tests without deleting or weakening them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = SCENARIO_REPAIR_SCHEMA_VERSION
    diagnostic_codes: tuple[str, ...]
    before_suite_hash: str
    after_suite_hash: str
    changes: tuple[ScenarioChange, ...]
    fingerprint: str

    @model_validator(mode="after")
    def _canonical(self) -> "ScenarioRepairReceipt":
        if self.schema_version != SCENARIO_REPAIR_SCHEMA_VERSION:
            raise ValueError("scenario_repair_schema_version_unsupported")
        if self.diagnostic_codes != tuple(sorted(set(self.diagnostic_codes))):
            raise ValueError("scenario_repair_diagnostics_not_canonical")
        if self.changes != tuple(
            sorted(self.changes, key=lambda item: item.scenario_key)
        ):
            raise ValueError("scenario_repair_changes_not_canonical")
        expected = _fingerprint(self.model_dump(mode="json", exclude={"fingerprint"}))
        if self.fingerprint != expected:
            raise ValueError("scenario_repair_fingerprint_mismatch")
        return self


def _scenario_hash(scenario: Scenario) -> str:
    return _fingerprint(scenario.model_dump(mode="json", exclude={"scenario_id"}))


def _suite_hash(scenarios: list[Scenario]) -> str:
    return _fingerprint(
        {
            item.scenario_key: item.model_dump(mode="json", exclude={"scenario_id"})
            for item in sorted(scenarios, key=lambda value: value.scenario_key)
        }
    )


def certify_scenario_repair(
    before: list[Scenario],
    after: list[Scenario],
    *,
    expected_count: int,
    diagnostic_codes: set[str],
    replaceable: Collection[str] = (),
) -> ScenarioRepairReceipt:
    """Reject deletions, renamed tests, reduced checks, count drift, and no-op repair. A case named in `replaceable`
    (a held case) may be removed when a case added in the same repair covers the same plan cell (`coverage`) and names
    every sub-goal it named, one added case for each one removed."""

    if len(after) != expected_count:
        raise ValueError("scenario_repair_count_mismatch")
    before_by_key = {item.scenario_key: item for item in before}
    after_by_key = {item.scenario_key: item for item in after}
    if len(before_by_key) != len(before) or len(after_by_key) != len(after):
        raise ValueError("scenario_repair_key_duplicate")
    removed = sorted(set(before_by_key) - set(after_by_key))
    unreplaced = _unreplaced(removed, before_by_key, after_by_key, set(replaceable))
    if unreplaced:
        raise ValueError("scenario_repair_deleted_scenarios: " + ", ".join(unreplaced))
    changes: list[ScenarioChange] = [
        ScenarioChange(
            scenario_key=key,
            kind=ScenarioChangeKind.REMOVED,
            before_hash=_scenario_hash(before_by_key[key]),
            after_hash="",
        )
        for key in removed
    ]
    for key, current in sorted(after_by_key.items()):
        previous = before_by_key.get(key)
        after_hash = _scenario_hash(current)
        if previous is None:
            changes.append(
                ScenarioChange(
                    scenario_key=key,
                    kind=ScenarioChangeKind.ADDED,
                    after_hash=after_hash,
                )
            )
            continue
        before_hash = _scenario_hash(previous)
        if before_hash == after_hash:
            continue
        if current.name != previous.name:
            raise ValueError(f"scenario_repair_renamed_scenario: {key}")
        missing_checks = sorted(set(previous.sub_goals) - set(current.sub_goals))
        if missing_checks:
            raise ValueError(
                f"scenario_repair_weakened_checks: {key}: " + ", ".join(missing_checks)
            )
        changes.append(
            ScenarioChange(
                scenario_key=key,
                kind=ScenarioChangeKind.REPLACED,
                before_hash=before_hash,
                after_hash=after_hash,
            )
        )
    if not changes:
        raise ValueError("scenario_repair_no_material_change")
    raw = {
        "schema_version": SCENARIO_REPAIR_SCHEMA_VERSION,
        "diagnostic_codes": tuple(sorted(diagnostic_codes)),
        "before_suite_hash": _suite_hash(before),
        "after_suite_hash": _suite_hash(after),
        "changes": tuple(sorted(changes, key=lambda item: item.scenario_key)),
    }
    raw["fingerprint"] = _fingerprint(raw)
    return ScenarioRepairReceipt.model_validate(raw)


def _cell(scenario: Scenario) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(axis), str(value)) for axis, value in (scenario.coverage or {}).items()))


def _unreplaced(
    removed: list[str], before: dict[str, Scenario], after: dict[str, Scenario], replaceable: set[str]
) -> list[str]:
    """The removed cases nothing stands in for, each with why: not replaceable, or no added case left in its cell that
    names every sub-goal it named."""
    added = [one for key, one in after.items() if key not in before]
    unreplaced = []
    for key in removed:
        held = before[key]
        cell = _cell(held)
        if key not in replaceable:
            unreplaced.append(key)
            continue
        stand_in = next(
            (one for one in added if cell and _cell(one) == cell and set(held.sub_goals) <= set(one.sub_goals)),
            None,
        )
        if stand_in is None:
            unreplaced.append(f"{key} (a replacement covers its plan cell and names its sub-goals)")
        else:
            added.remove(stand_in)
    return unreplaced


def _fingerprint(value: object) -> str:
    def jsonable(item: object) -> object:
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        if isinstance(item, Enum):
            return item.value
        if isinstance(item, tuple):
            return [jsonable(child) for child in item]
        if isinstance(item, dict):
            return {str(key): jsonable(child) for key, child in sorted(item.items())}
        return item

    encoded = json.dumps(
        jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


__all__ = [
    "SCENARIO_REPAIR_SCHEMA_VERSION",
    "ScenarioChange",
    "ScenarioChangeKind",
    "ScenarioRepairReceipt",
    "certify_scenario_repair",
]
