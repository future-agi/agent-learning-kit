"""The journal that makes a long fan-out survivable.

Under delegation the stage saves once at the end, so until that save the whole suite exists only
in memory. At fifty scenarios losing it costs a run; at five hundred it costs a night. These pin
the three things the journal has to get right: it keeps what was proved, it survives being killed
mid-write, and it never lets a scenario in twice.
"""

from __future__ import annotations

from pathlib import Path

from fi.alk.harness.scenario import Scenario
from fi.alk.harness.scenario_tools import (
    JOURNAL,
    forget_journal,
    journalled,
    record_written,
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
