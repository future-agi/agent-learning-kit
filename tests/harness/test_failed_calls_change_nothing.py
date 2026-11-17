"""A failed call changes nothing (plan §3.3), in every Wave A vertical: for each scenario, each
successful call of its reference solution that writes is played again with the store refusing
every write from one of its writes onward, in turn, and the world must come out exactly as it
went in; with the store working again, the same call then succeeds as a new request. A paired
write (an appointment and its event, a grant and its entitlement, a demo and its slot) can never
be left half done, however long the store keeps failing: the harness runs each call as one
transaction, and rolling it back needs no writes.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from fi.alk import environments
from fi.alk.environments import designed
from fi.alk.harness.folder import read_folder
from fi.alk.harness.prove import _run, play_reference_step
from fi.alk.harness.releases import WORLD

VERTICALS = (
    "retail",
    "hr",
    "internal_it",
    "saas_subscriptions",
    "clinic_scheduling",
    "service_scheduling",
    "sales_intake",
    "internal_knowledge",
)
WRITES = ("INSERT", "UPDATE", "DELETE")


def _rows(world) -> dict[str, list[str]]:
    """Every table's rows, in no particular order: a restored row may come back last."""
    return {
        table: sorted(json.dumps(row, sort_keys=True, default=str) for row in rows)
        for table, rows in world.state().items()
    }


class _Failing:
    """Counts the writes that reach the store, and fails every one from the one numbered `at`."""

    def __init__(self, execute, at: int = 0) -> None:
        self.execute, self.at, self.writes = execute, at, 0

    def __call__(self, sql, params=()):
        if sql.lstrip().upper().startswith(WRITES):
            self.writes += 1
            if self.at and self.writes >= self.at:
                raise RuntimeError(f"injected: the store refuses writes from write {self.at}")
        return self.execute(sql, params)


@pytest.mark.parametrize("vertical", VERTICALS)
def test_a_call_that_fails_partway_changes_nothing(vertical: str, tmp_path: Path) -> None:
    here = Path(environments.__file__).parent / vertical
    designed.build_environment(here, tmp_path / "environment")
    shutil.copytree(tmp_path / "environment" / WORLD, tmp_path / WORLD)
    left_behind, tried = [], 0
    for folder in sorted(p for p in (here / "suite" / "scenarios").iterdir() if p.is_dir()):
        scenario = read_folder(here / "suite", folder.name)
        world, *_ = _run(scenario, tmp_path / WORLD, with_solution=False)
        try:
            real = world.store.execute
            for step in scenario.solution:
                before, mark = _rows(world), world.checkpoint()
                counted = _Failing(real)
                world.store.execute = counted
                played = play_reference_step(world, step)
                world.store.execute = real
                if played.ok and counted.writes > 1:
                    for at in range(1, counted.writes + 1):
                        world.revert(mark)
                        world.store.execute = _Failing(real, at)
                        failed = play_reference_step(world, step)
                        world.store.execute = real
                        tried += 1
                        if failed.ok or _rows(world) != before:
                            left_behind.append(f"{folder.name}: {step.tool} failing from write {at}")
                            continue
                        again = play_reference_step(world, step)
                        if not again.ok or (isinstance(again.result, dict) and again.result.get("idempotent")):
                            left_behind.append(f"{folder.name}: {step.tool} not redone after failing from write {at}")
                    world.revert(mark)
                    play_reference_step(world, step)
        finally:
            world.close()
    assert tried, vertical
    assert not left_behind, left_behind[:20]
