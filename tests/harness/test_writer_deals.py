"""A suite is written in deals: the plan's cells divided across writers, each brief the same size at any suite size,
and a plan too thin for the count says so instead of grinding."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from fi.alk.harness import scenario_tools as tools_module
from fi.alk.harness.authoring_runtime_validation import supported_count
from fi.alk.harness.catalogue import Catalogue, save_catalogue
from fi.alk.harness.contract import AgentContract, ToolSpec
from fi.alk.harness.scenario import Scenario
from fi.alk.harness.scenario_tools import (
    CANONICAL_AXES,
    cases_per_cell,
    cases_per_writer,
    deal_brief,
    deal_cells,
    scenario_tools,
    suite_summary,
)
from fi.alk.harness.world import GeneratedWorld
from fi.alk.harness.world.snapshot import save

TASKS = ["t1", "t2", "t3", "t4", "t5"]
MOODS = ["hurried", "confused", "sceptical"]


def _grid(tasks: list[str] = TASKS) -> dict[str, list[str]]:
    return {
        **{axis: ["plain"] for axis in CANONICAL_AXES},
        "task": list(tasks),
        "disposition": list(MOODS),
    }


def _environment(root: Path) -> AgentContract:
    class Notes(GeneratedWorld):
        name = "notes"
        tools = [{"name": "put"}]
        handlers = {"put": "def handle(args, db):\n    return {'ok': 1}\n"}

    world = Notes(":memory:")
    world.connection.executescript("CREATE TABLE note(id TEXT);")
    save(world, root, notes="test", sequences=[])
    save_catalogue(Catalogue(sub_goals=[]), root)
    return AgentContract(
        agent="notes", real_use_cases=["keep a note"], tools=[ToolSpec(name="put")]
    )


def _kept_as_given(
    args: dict[str, Any], *, kept: list[Scenario], **_: Any
) -> dict[str, Any]:
    """Keeps what it is given, standing in for the proof so only the suite's own bookkeeping is under test."""
    one = Scenario(
        name=args["name"],
        instruction="i",
        coverage=args.get("coverage") or {},
        persona=args.get("persona"),
    )
    kept[:] = [other for other in kept if other.name != one.name] + [one]
    return {"content": [{"type": "text", "text": f"{one.name} kept"}]}


@pytest.fixture(autouse=True)
def _no_job(monkeypatch):
    monkeypatch.delenv("ALK_HARNESS_JOB_ID", raising=False)


def _server(
    tmp_path: Path, monkeypatch, wanted: int, grid: dict[str, list[str]], **kwargs: Any
):
    monkeypatch.setattr(tools_module, "accept_scenario", _kept_as_given)
    server, kept = scenario_tools(
        _environment(tmp_path), tmp_path, tmp_path, wanted=wanted, **kwargs
    )
    handlers = {one.name: one.handler for one in server.tools}
    said = asyncio.run(handlers["aim_for"]({"count": wanted, "axes": grid}))
    assert not said.get("is_error"), said
    return handlers, kept


def _text(reply: dict[str, Any]) -> str:
    return reply["content"][0]["text"]


def test_a_deal_splits_the_cells_across_writers_and_never_more_writers_than_run_at_once():
    grid = _grid()
    cells_dealt = 5 * 3

    capped = deal_cells(grid, [], 200, writers_at_once=4)
    assert len(capped) == 4 and all(
        sum(one.values()) <= cases_per_writer() for one in capped
    )
    assert sum(sum(one.values()) for one in capped) == 4 * cases_per_writer()

    wide = deal_cells(grid, [], 200, writers_at_once=48)
    assert len(wide) == -(-200 // cases_per_writer())
    assert sum(sum(one.values()) for one in wide) == 200
    # Every cell of the plan is dealt, each about as often as the others.
    per_cell = Counter()
    for one in wide:
        per_cell.update(one)
    assert (
        len(per_cell) == cells_dealt
        and max(per_cell.values()) - min(per_cell.values()) <= 1
    )

    # A round of fewer cases than cells gives each writer cells of its own.
    small = deal_cells(grid, [], 12, writers_at_once=3, per_writer=4)
    seen = [cell for one in small for cell in one]
    assert len(small) == 3 and len(seen) == len(set(seen)) == 12


def test_the_cells_the_suite_holds_least_of_are_dealt_first():
    grid = _grid()
    plain = {axis: "plain" for axis in CANONICAL_AXES}
    full = [
        Scenario(
            name=f"{task}-{mood}", coverage={**plain, "task": task, "disposition": mood}
        )
        for task in ("t1", "t2")
        for mood in MOODS
    ]

    [share] = deal_cells(grid, full, 9, writers_at_once=1)

    assert not {cell[0] for cell in share} & {"t1", "t2"} and len(share) == 9


def test_suite_progress_deals_at_most_the_worker_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_module, "MOST_WORKERS_AT_ONCE", 3)
    handlers, _ = _server(tmp_path, monkeypatch, 200, _grid(), can_grow=True)

    said = _text(asyncio.run(handlers["suite_progress"]({"deal": True})))

    assert "3 deals" in said and said.count("Deal d") == 3 and "Deal d4" not in said


def test_writers_submitting_at_once_neither_lose_nor_duplicate_and_keep_to_their_deals(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(tools_module, "MOST_WORKERS_AT_ONCE", 4)
    handlers, kept = _server(tmp_path, monkeypatch, 40, _grid(), can_grow=True)
    deals = _deals_in(_text(asyncio.run(handlers["suite_progress"]({"deal": True}))))
    names = list(deals)
    submissions = []
    for name, cells in deals.items():
        # Each writer tries two more than its deal, and resubmits its first, which replaces it.
        first = len(submissions)
        for index, cell in enumerate(cells + cells[:2]):
            submissions.append(
                {
                    "name": f"{name}-{index}",
                    "instruction": "Keep a note.",
                    "coverage": {
                        **{axis: "plain" for axis in CANONICAL_AXES},
                        "task": cell[0],
                        "disposition": cell[1],
                    },
                    "persona": {"name": f"Caller {name} {index}"},
                    "deal": name,
                }
            )
        submissions.append({**submissions[first]})
    # A writer with no deal is turned away while a round is out.
    submissions.append({**submissions[0], "name": "stray", "deal": ""})

    async def all_at_once() -> list[dict[str, Any]]:
        return await asyncio.gather(
            *(handlers["submit_scenario"](dict(one)) for one in submissions)
        )

    replies = asyncio.run(all_at_once())

    held = Counter(one.name for one in kept)
    assert all(n == 1 for n in held.values())
    assert len(kept) == sum(len(cells) for cells in deals.values()) == 40
    assert "stray" not in held
    assert sum(
        1
        for one in replies
        if "is written. Report what you wrote and stop" in _text(one)
    ) == 2 * len(names)
    for name in names:
        assert sum(1 for one in kept if one.name.startswith(f"{name}-")) == len(
            deals[name]
        )


def _deals_in(said: str) -> dict[str, list[tuple[str, str]]]:
    """Each deal's cells, one entry per case, read from the deal lines the planner is handed."""
    deals: dict[str, list[tuple[str, str]]] = {}
    current = ""
    for line in said.splitlines():
        if line.startswith("Deal "):
            current = line.split()[1].rstrip(":")
            deals[current] = []
        elif current and line.startswith("  - "):
            placed, times = line[4:].rsplit(" x", 1)
            levels = dict(part.split("=") for part in placed.split(", "))
            deals[current] += [(levels["task"], levels["disposition"])] * int(times)
    return deals


def test_a_writer_brief_stays_the_same_size_at_a_thousand_cases():
    grid = _grid()

    def brief_at(size: int) -> str:
        kept = [
            Scenario(
                name=f"case-{n}",
                coverage={"task": TASKS[n % 5], "disposition": MOODS[n % 3]},
                persona={"name": f"Given{n} Family{n}"},
            )
            for n in range(size)
        ]
        [share, *_] = deal_cells(grid, kept, 1000 - size, writers_at_once=48)
        return (
            deal_brief("d1", share, ["task", "disposition"])
            + "\n\n"
            + suite_summary(kept, grid)
        )

    small, large = brief_at(10), brief_at(999)
    assert len(large) < 3000
    assert len(large) - len(small) < 400


def test_a_plan_too_thin_for_the_count_stops_at_what_it_holds_and_says_so(
    tmp_path, monkeypatch
):
    thin = _grid(tasks=["t1"])
    supports = 1 * 3 * cases_per_cell()
    handlers, kept = _server(tmp_path, monkeypatch, 50, thin, can_grow=True)

    # Thin, the plan is to be widened first: nothing stops yet.
    said = _text(asyncio.run(handlers["aim_for"]({"count": 50, "axes": thin})))
    assert "Widen the plan" in said and "supports about" not in said
    assert supported_count(tmp_path, 50) == 50

    # Only once it cannot vary further, with why, does the suite stop at what it holds.
    why = "the agent has one tool and one kind of caller"
    said = _text(
        asyncio.run(handlers["aim_for"]({"count": 50, "axes": thin, "cannot_widen": why}))
    )
    line = f"this agent supports about {supports} distinct scenarios; wrote 0 of 50"
    assert line in said and why in said
    record = json.loads(
        (tmp_path / "generic-harness" / "yield.json").read_text(encoding="utf-8")
    )
    assert record["supports"]["line"] == line and record["supports"]["cases"] == supports
    assert record["supports"]["why"] == why
    assert supported_count(tmp_path, 50) == supports

    for n in range(supports + 1):
        reply = asyncio.run(
            handlers["submit_scenario"](
                {
                    "name": f"case-{n}",
                    "instruction": "Keep a note.",
                    "coverage": {
                        **{axis: "plain" for axis in CANONICAL_AXES},
                        "task": "t1",
                        "disposition": MOODS[n % 3],
                    },
                }
            )
        )
    assert len(kept) == supports and "This is complete" in _text(reply)

    # A plan widened to hold the count clears it.
    asyncio.run(
        handlers["aim_for"]({"count": 50, "axes": {"task": ["t2", "t3", "t4", "t5"]}})
    )
    assert supported_count(tmp_path, 50) == 50


@pytest.mark.parametrize("given", ["", "0", "-3", "four", "1.5"])
def test_a_bad_setting_is_the_default_and_never_breaks_the_import(monkeypatch, given):
    monkeypatch.setenv("ALK_HARNESS_CASES_PER_CELL", given)
    monkeypatch.setenv("ALK_HARNESS_CASES_PER_WRITER", given)
    assert cases_per_cell() == 4 and cases_per_writer() == 16


def test_a_bad_setting_never_stops_the_kit_importing():
    import os
    import subprocess
    import sys

    env = {**os.environ, "ALK_HARNESS_CASES_PER_CELL": "four", "ALK_HARNESS_CASES_PER_WRITER": "0"}
    ran = subprocess.run(
        [sys.executable, "-c", "import fi.alk.harness.scenario_tools"], env=env, capture_output=True, text=True
    )
    assert ran.returncode == 0, ran.stderr[-500:]


def test_a_setting_is_read_when_used_and_held_to_its_ceiling(monkeypatch):
    monkeypatch.setenv("ALK_HARNESS_CASES_PER_CELL", "7")
    monkeypatch.setenv("ALK_HARNESS_CASES_PER_WRITER", "100000")
    assert cases_per_cell() == 7 and cases_per_writer() == 200


def _one_round(tmp_path, monkeypatch):
    """Two deals out, each having written one case: the handlers, the kept list and each deal's cells."""
    monkeypatch.setattr(tools_module, "MOST_WORKERS_AT_ONCE", 2)
    monkeypatch.setenv("ALK_HARNESS_CASES_PER_WRITER", "3")
    handlers, kept = _server(tmp_path, monkeypatch, 6, _grid(), can_grow=True)
    deals = _deals_in(_text(asyncio.run(handlers["suite_progress"]({"deal": True}))))
    assert sorted(deals) == ["d1", "d2"]
    for name, cells in deals.items():
        said = _text(asyncio.run(handlers["submit_scenario"](_case(f"{name}-0", cells[0], name))))
        assert "kept" in said, said
    return handlers, kept, deals


def _case(name: str, cell: tuple[str, str], deal: str = "", **more: Any) -> dict[str, Any]:
    return {
        "name": name,
        "instruction": "Keep a note.",
        "coverage": {**{axis: "plain" for axis in CANONICAL_AXES}, "task": cell[0], "disposition": cell[1]},
        "deal": deal,
        **more,
    }


def _key(kept: list[Scenario], name: str) -> str:
    return next(one.scenario_key for one in kept if one.name == name)


def test_a_deal_never_replaces_another_deal_s_case_by_name_or_key(tmp_path, monkeypatch):
    handlers, kept, deals = _one_round(tmp_path, monkeypatch)
    theirs = deals["d2"][0]

    by_name = _text(asyncio.run(handlers["submit_scenario"](_case("d2-0", theirs, "d1"))))
    by_key = _text(
        asyncio.run(
            handlers["submit_scenario"](
                _case("moved", deals["d1"][1], "d1", scenario_key=_key(kept, "d2-0"))
            )
        )
    )

    assert "not written under deal d1" in by_name and "not written under deal d1" in by_key
    assert {one.name for one in kept} == {"d1-0", "d2-0"}


def test_a_deal_moves_its_own_case_only_onto_its_own_cells(tmp_path, monkeypatch):
    handlers, kept, deals = _one_round(tmp_path, monkeypatch)
    off = next(cell for cell in deals["d2"] if cell not in deals["d1"])

    moved = _text(asyncio.run(handlers["submit_scenario"](_case("d1-0", off, "d1"))))
    kept_on = _text(asyncio.run(handlers["submit_scenario"](_case("d1-0", deals["d1"][1], "d1"))))

    assert "is not a cell of deal d1" in moved and "kept" in kept_on
    # Moving its own case frees the cell it left and keeps the deal's count: it still has two to write.
    for index in (0, 2):
        said = _text(asyncio.run(handlers["submit_scenario"](_case(f"d1-new-{index}", deals["d1"][index], "d1"))))
        assert "kept" in said, said
    assert sum(1 for one in kept if one.name.startswith("d1-")) == 3


def test_while_a_round_is_out_edits_and_drops_go_only_through_the_deal_that_wrote_the_case(tmp_path, monkeypatch):
    handlers, kept, deals = _one_round(tmp_path, monkeypatch)
    theirs = _key(kept, "d2-0")

    unowned_edit = _text(
        asyncio.run(handlers["edit_scenario"]({"scenario_key": theirs, "name": "renamed", "deal": "d1"}))
    )
    plain_edit = _text(asyncio.run(handlers["edit_scenario"]({"scenario_key": theirs, "name": "renamed"})))
    unowned_drop = _text(asyncio.run(handlers["drop_scenario"]({"name": "d2-0", "deal": "d1"})))
    plain_drop = _text(asyncio.run(handlers["drop_scenario"]({"name": "d2-0"})))
    everything = _text(asyncio.run(handlers["drop_scenario"]({"name": "*", "everything": True})))
    plain_resubmit = _text(asyncio.run(handlers["submit_scenario"](_case("d2-0", deals["d2"][0]))))

    assert "not written under deal d1" in unowned_edit and "not written under deal d1" in unowned_drop
    assert "A round of writers is out" in plain_edit and "A round of writers is out" in plain_drop
    assert "A round of writers is out" in plain_resubmit and "Not dropped" in everything
    assert {one.name for one in kept} == {"d1-0", "d2-0"}

    # The deal that wrote it may drop it, and that frees its place.
    owned = _text(asyncio.run(handlers["drop_scenario"]({"name": "d2-0", "deal": "d2"})))
    assert "dropped" in owned and {one.name for one in kept} == {"d1-0"}

    # Once the round ends, whoever dealt it changes the suite freely.
    asyncio.run(handlers["suite_progress"]({"end_round": True}))
    assert "dropped" in _text(asyncio.run(handlers["drop_scenario"]({"name": "d1-0"})))
