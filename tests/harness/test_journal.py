"""The journal that makes a long fan-out survivable.

Under delegation the stage saves once at the end, so until that save the whole suite exists only
in memory. At fifty scenarios losing it costs a run; at five hundred it costs a night. These pin
the three things the journal has to get right: it keeps what was proved, it survives being killed
mid-write, and it never lets a scenario in twice.
"""

from __future__ import annotations

from pathlib import Path

from fi.alk.harness.scenario import Scenario
from fi.alk.harness.catalogue import Catalogue
from fi.alk.harness.scenariogen.store.suite import (
    JOURNAL,
    forget_journal,
    journalled,
    load_scenarios,
    record_written,
    write_scenarios,
)


def test_what_a_writer_proved_survives_the_process(tmp_path: Path) -> None:
    record_written([Scenario(name="one"), Scenario(name="two")], tmp_path)

    assert [one.name for one in journalled(tmp_path)] == ["one", "two"]


def test_a_second_writer_appends_rather_than_replacing(tmp_path: Path) -> None:
    record_written([Scenario(name="one")], tmp_path)
    record_written([Scenario(name="two")], tmp_path)

    assert [one.name for one in journalled(tmp_path)] == ["one", "two"]


def test_a_torn_final_line_costs_only_that_scenario(tmp_path: Path) -> None:
    """A killed process can leave half a line. The rest of the run must still come back."""
    record_written([Scenario(name="one"), Scenario(name="two")], tmp_path)
    path = tmp_path / JOURNAL
    path.write_text(path.read_text()[:-12], encoding="utf-8")

    assert [one.name for one in journalled(tmp_path)] == ["one"]


def test_nothing_written_leaves_no_journal(tmp_path: Path) -> None:
    record_written([], tmp_path)

    assert not (tmp_path / JOURNAL).exists()
    assert journalled(tmp_path) == []


def test_reading_a_destination_with_no_journal_is_not_an_error(tmp_path: Path) -> None:
    assert journalled(tmp_path) == []


def test_the_journal_is_dropped_once_the_suite_is_on_disk(tmp_path: Path) -> None:
    """Left behind, it would make the next run recover scenarios it already saved."""
    record_written([Scenario(name="one")], tmp_path)
    forget_journal(tmp_path)

    assert not (tmp_path / JOURNAL).exists()
    assert journalled(tmp_path) == []


def test_forgetting_a_journal_that_is_not_there_is_quiet(tmp_path: Path) -> None:
    forget_journal(tmp_path)


def test_a_scenario_journalled_twice_comes_back_once(tmp_path: Path) -> None:
    """A retried slice re-journals what it had already proved. The caller renames folder-name
    collisions rather than dropping them, so a repeat would survive as a second folder."""
    record_written([Scenario(name="one"), Scenario(name="two")], tmp_path)
    record_written([Scenario(name="one")], tmp_path)

    assert [one.name for one in journalled(tmp_path)] == ["one", "two"]


def test_a_second_save_keeps_what_an_earlier_save_put_on_disk(tmp_path: Path) -> None:
    """Saving prunes every folder it is not given, and the save that consumes the journal drops it.

    So work proved before an earlier save exists only on disk by the time the next save runs. Folding
    only the journal deleted it: six proved scenarios were lost this way on a two hundred run.
    """
    catalogue = Catalogue(sub_goals=[])
    (tmp_path / "scenarios").mkdir(parents=True, exist_ok=True)

    def saved(kept: list[Scenario]) -> None:
        held = {one.name for one in kept}
        for one in (*journalled(tmp_path), *load_scenarios(tmp_path)):
            if one.name not in held:
                held.add(one.name)
                kept.append(one)
        write_scenarios(kept, tmp_path, catalogue)
        forget_journal(tmp_path)

    record_written([Scenario(name="alpha"), Scenario(name="beta")], tmp_path)
    saved([])
    assert sorted(one.name for one in load_scenarios(tmp_path)) == ["alpha", "beta"]

    record_written([Scenario(name="gamma")], tmp_path)
    saved([])
    assert sorted(one.name for one in load_scenarios(tmp_path)) == ["alpha", "beta", "gamma"]
