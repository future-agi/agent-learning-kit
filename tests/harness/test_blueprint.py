"""The plan for a suite, and the one thing it exists to catch.

A blueprint is cheap to change and a suite is not: every duplicate that survives planning costs
a proof, a folder and a slot that a different scenario should have had. So the cases worth
pinning are the ones where a plan looks fine and is not: the same situation reworded, a plan that
quietly names a cell nobody has, and a cut that hands one writer the whole of one cell.
"""

from __future__ import annotations

from fi.alk.harness.blueprint import Blueprint, Entry, load


def plan(*rows: tuple[str, str, str], wanted: int = 0) -> Blueprint:
    return Blueprint(
        wanted=wanted,
        entries=[Entry(name=n, cell=c, situation=s) for n, c, s in rows],
    )


class TestSayingTheSameThingTwice:
    def test_a_reworded_situation_is_caught(self):
        held = plan(
            ("a", "retrieve-ride", "caller cannot find the booking they made this morning"),
            ("b", "retrieve-ride", "the booking made this morning cannot be found by the caller"),
        )
        assert [one[:2] for one in held.duplicates()] == [("a", "b")]

    def test_genuinely_different_situations_in_one_cell_are_left_alone(self):
        held = plan(
            ("a", "retrieve-ride", "caller cannot find the booking they made this morning"),
            ("b", "retrieve-ride", "wants the fare breakdown for a trip that crossed a surge boundary"),
        )
        assert held.duplicates() == []

    def test_two_cells_may_share_a_situation(self):
        """Retrieving and cancelling both start from a caller who cannot find their booking.

        Comparing across cells would call that a duplicate and push the plan into making cells
        artificially unlike each other, which is not what variety means here.
        """
        held = plan(
            ("a", "retrieve-ride", "caller cannot find the booking they made this morning"),
            ("d", "cancel-ride", "caller cannot find the booking they made this morning"),
        )
        assert held.duplicates() == []

    def test_padding_a_situation_does_not_make_it_a_new_one(self):
        """Scored against the smaller line, so restating it at greater length still collides."""
        held = plan(
            ("a", "cancel-ride", "card was declined at checkout"),
            ("b", "cancel-ride", "the card was unfortunately declined at checkout again today"),
        )
        assert held.duplicates()


class TestWhatAPlanMustSayBeforeAnyoneWritesFromIt:
    def test_a_cell_nobody_has_is_reported(self):
        held = plan(("a", "invent-thing", "wants something the agent cannot do"))
        said = " ".join(held.problems({"retrieve-ride"}))
        assert "not on the grid" in said

    def test_a_situation_too_thin_to_write_from_is_reported(self):
        held = plan(("a", "retrieve-ride", "a ride"))
        assert "say too little" in " ".join(held.problems({"retrieve-ride"}))

    def test_repeated_names_are_reported(self):
        held = plan(
            ("a", "retrieve-ride", "caller cannot find the booking from this morning"),
            ("a", "retrieve-ride", "wants a fare breakdown across a surge boundary"),
        )
        assert "more than once" in " ".join(held.problems({"retrieve-ride"}))

    def test_an_empty_plan_is_a_problem_not_a_crash(self):
        assert Blueprint().problems({"retrieve-ride"}) == ["the blueprint is empty"]

    def test_one_duplicate_pair_reads_as_one(self):
        held = plan(
            ("a", "retrieve-ride", "caller cannot find the booking they made this morning"),
            ("b", "retrieve-ride", "the booking made this morning cannot be found by the caller"),
        )
        assert "1 pair describe" in " ".join(held.problems({"retrieve-ride"}))


class TestCuttingItUp:
    def test_a_writer_is_not_handed_one_whole_cell(self):
        """Entries arrive grouped by cell, so a contiguous cut gives one writer one cell.

        That writer then has to invent the whole of that cell's variety alone, which is the
        position the blueprint exists to remove.
        """
        held = plan(
            *[(f"r{i}", "retrieve-ride", f"situation number {i} about finding a booking") for i in range(4)],
            *[(f"c{i}", "cancel-ride", f"situation number {i} about calling off a trip") for i in range(4)],
        )
        cuts = held.slices(4)
        assert all(len({one.cell for one in cut}) > 1 for cut in cuts), (
            "at least one writer was handed a single cell"
        )

    def test_every_entry_is_dealt_exactly_once(self):
        held = plan(*[(f"s{i}", "retrieve-ride", f"situation number {i} about a booking") for i in range(7)])
        dealt = [one.name for cut in held.slices(3) for one in cut]
        assert sorted(dealt) == sorted(one.name for one in held.entries)


class TestItSurvivesABadFile:
    def test_a_missing_plan_reads_as_empty(self, tmp_path):
        assert load(tmp_path).entries == []

    def test_a_damaged_plan_reads_as_empty_rather_than_raising(self, tmp_path):
        (tmp_path / "blueprint.json").write_text("{not json", encoding="utf-8")
        assert load(tmp_path).entries == []

    def test_a_plan_survives_the_round_trip(self, tmp_path):
        held = plan(("a", "retrieve-ride", "caller cannot find this morning's booking"), wanted=1)
        held.written(tmp_path)
        back = load(tmp_path)
        assert back.wanted == 1
        assert [one.line() for one in back.entries] == [one.line() for one in held.entries]
