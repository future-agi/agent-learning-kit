"""The canvas: what a suite intends to cover, and what has been written against it.

A plan is cheap to change and a suite is not, so the cases worth pinning are the ones where a plan
looks fine and is not, and the ones where the loop could lose track of what is left. The failure
this whole structure exists to prevent is a run that reports success having written almost nothing.
"""

from __future__ import annotations

import pytest

from fi.alk.harness.blueprint import MOST_ATTEMPTS, Angle, Canvas, Theme, load
from fi.alk.harness.contract import AgentContract, ToolSpec


@pytest.fixture()
def contract():
    return AgentContract(
        agent="ride",
        modality="voice",
        tools=[ToolSpec(name="get_rides"), ToolSpec(name="cancel_ride")],
        data_schema={"rides": {}, "users": {}, "fares": {}},
    )


@pytest.fixture()
def where(tmp_path):
    return tmp_path


def canvas(*rows, target: int = 0, themes=("TH01",)) -> Canvas:
    """Rows are (id, theme, cell, angle) with optional facet and want."""
    return Canvas(
        target=target,
        themes=[Theme(id=one, name=one) for one in themes],
        angles=[
            Angle(
                id=row[0], theme=row[1], cell=row[2], angle=row[3],
                facet=row[4] if len(row) > 4 else "",
                want=row[5] if len(row) > 5 else 1,
            )
            for row in rows
        ],
    )


class TestWhatAPlanMustSayBeforeAnyoneWritesFromIt:
    def test_a_cell_nobody_has_is_reported(self):
        held = canvas(("A1", "TH01", "invent-thing", "something impossible"))
        assert "not on the grid" in " ".join(held.problems({"retrieve-ride"}))

    def test_an_angle_too_thin_to_write_from_is_reported(self):
        held = canvas(("A1", "TH01", "retrieve-ride", "ride"))
        assert "say too little" in " ".join(held.problems({"retrieve-ride"}))

    def test_an_angle_written_as_a_script_is_reported(self):
        """The failure that made a plan for a thousand impossible to emit at all."""
        held = canvas((
            "A1", "TH01", "retrieve-ride",
            "caller was charged 2.3x for a trip that started one minute before the surge "
            "window closed and the receipt shows the higher rate with no explanation",
        ))
        assert "scripts rather than angles" in " ".join(held.problems({"retrieve-ride"}))

    def test_a_theme_nobody_declared_is_reported(self):
        held = canvas(("A1", "TH99", "retrieve-ride", "booking cannot be found"))
        assert "theme that is not declared" in " ".join(held.problems({"retrieve-ride"}))

    def test_a_repeated_id_is_reported(self):
        held = canvas(
            ("A1", "TH01", "retrieve-ride", "booking cannot be found"),
            ("A1", "TH01", "cancel-ride", "fee disclosed first"),
        )
        assert "appear twice" in " ".join(held.problems({"retrieve-ride", "cancel-ride"}))

    def test_counts_are_what_say_how_many_scenarios(self):
        """Lines and scenarios are deliberately no longer the same number."""
        held = canvas(
            ("A1", "TH01", "retrieve-ride", "booking cannot be found", "data:missing", 6),
            ("A2", "TH01", "cancel-ride", "fee disclosed before consent", "rule:fee", 4),
            target=10,
        )
        assert len(held.angles) == 2
        assert held.planned == 10
        assert held.shortfall() == 0


class TestCollisionsAreAPromptNotAVerdict:
    """Building the first real canvas produced seven; six were legitimate."""

    def test_one_facet_twice_on_one_cell_is_flagged(self):
        held = canvas(
            ("A1", "TH01", "retrieve-ride", "booking missing", "data:missing"),
            ("A2", "TH01", "retrieve-ride", "nothing found for the phone", "data:missing"),
        )
        assert any("same facet" in why for _, _, why in held.collisions())

    def test_the_same_facet_on_different_cells_is_not_flagged(self):
        """Three input forms for one address are three angles, not one repeated."""
        held = canvas(
            ("A1", "TH01", "retrieve-address", "given as a landmark", "input:form"),
            ("A2", "TH01", "compare-address", "given as a street", "input:form"),
        )
        assert held.collisions() == []

    def test_collisions_never_refuse_the_plan(self):
        held = canvas(
            ("A1", "TH01", "retrieve-ride", "booking missing", "data:missing"),
            ("A2", "TH01", "retrieve-ride", "nothing found for phone", "data:missing"),
        )
        assert held.problems({"retrieve-ride"}) == []


class TestPickingTheNextWriterSWork:
    def test_no_writer_is_handed_two_angles_from_one_cell(self):
        held = canvas(
            *[(f"A{i}", "TH01", "retrieve-ride", f"case {i}", "", 4) for i in range(4)],
            *[(f"B{i}", "TH01", "cancel-ride", f"case {i}", "", 4) for i in range(4)],
        )
        taken = held.next_slice(8)
        assert len({one.cell for one in taken}) == len(taken)

    def test_an_untouched_theme_outranks_a_nearly_finished_one(self):
        """What stops a suite covering the booking path and never testing the rules."""
        held = canvas(
            ("A1", "TH01", "retrieve-ride", "almost done here", "", 10),
            ("B1", "TH02", "cancel-ride", "nobody has started this", "", 3),
            themes=("TH01", "TH02"),
        )
        held.named("A1").done = 9
        assert held.next_slice(4)[0].id == "B1"

    def test_a_claimed_angle_is_not_dealt_again(self):
        held = canvas(("A1", "TH01", "retrieve-ride", "booking missing", "", 4))
        held.claim(held.next_slice(4), "w1")
        assert held.next_slice(4) == []

    def test_a_writer_that_never_returns_does_not_park_its_angles(self):
        held = canvas(("A1", "TH01", "retrieve-ride", "booking missing", "", 4))
        held.claim(held.next_slice(4), "w1")
        assert held.reclaim() == 1
        assert [one.id for one in held.next_slice(4)] == ["A1"]


class TestFoldingAWriterSReturn:
    def test_what_counts_as_written_comes_from_the_caller_not_the_writer(self):
        """A stage once reported success having saved one scenario of fifty."""
        held = canvas(("A1", "TH01", "retrieve-ride", "booking missing", "", 5))
        held.claim(held.next_slice(5), "w1")
        held.fold("A1", done=2, short="covered two")
        assert held.named("A1").done == 2
        assert held.written == 2

    def test_a_partly_filled_angle_reopens_for_somebody_else(self):
        held = canvas(("A1", "TH01", "retrieve-ride", "booking missing", "", 5))
        held.claim(held.next_slice(5), "w1")
        assert held.fold("A1", done=2) == "open"

    def test_a_filled_angle_is_done(self):
        held = canvas(("A1", "TH01", "retrieve-ride", "booking missing", "", 2))
        held.claim(held.next_slice(2), "w1")
        assert held.fold("A1", done=2) == "done"

    def test_an_angle_nobody_can_fill_becomes_evidence_of_the_ceiling(self):
        held = canvas(("A1", "TH01", "retrieve-ride", "booking missing", "", 5))
        for _ in range(MOST_ATTEMPTS):
            held.claim([held.named("A1")], "w")
            held.fold("A1", done=1)
        assert held.named("A1").state == "blocked"
        assert "could not be filled" in held.reached()

    def test_a_writer_saying_it_cannot_be_done_is_taken_at_its_word(self):
        held = canvas(("A1", "TH01", "retrieve-ride", "booking missing", "", 5))
        held.claim([held.named("A1")], "w1")
        assert held.fold("A1", done=0, blocked_reason="no second distinct case exists") == "blocked"

    def test_the_summaries_are_kept_for_whoever_reads_it_next(self):
        held = canvas(("A1", "TH01", "retrieve-ride", "booking missing", "", 5))
        held.fold("A1", done=1, short="covered the refusal only")
        assert held.named("A1").notes == ["covered the refusal only"]

    def test_a_finished_suite_claims_no_ceiling(self):
        held = canvas(("A1", "TH01", "retrieve-ride", "booking missing", "", 2))
        held.fold("A1", done=2)
        assert held.reached() == ""


class TestItSurvivesDiskAndReplanning:
    def test_a_missing_canvas_reads_as_empty(self, where):
        assert load(where).angles == []

    def test_a_damaged_canvas_reads_as_empty_rather_than_raising(self, where):
        (where / "blueprint.json").write_text("{not json", encoding="utf-8")
        assert load(where).angles == []

    def test_progress_survives_the_round_trip(self, where):
        held = canvas(("A1", "TH01", "retrieve-ride", "booking missing", "data:missing", 5), target=5)
        held.fold("A1", done=3, short="three of five")
        held.written_to(where)
        back = load(where)
        assert back.written == 3
        assert back.named("A1").notes == ["three of five"]
        assert back.target == 5
