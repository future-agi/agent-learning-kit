"""The canvas: what a suite intends to cover, and what has been written against it.

A plan is cheap to change and a suite is not, so the cases worth pinning are the ones where a plan
looks fine and is not, and the ones where the loop could lose track of what is left. The failure
this whole structure exists to prevent is a run that reports success having written almost nothing.
"""

from __future__ import annotations

import pytest

from fi.alk.harness.scenariogen.plan.canvas import _WORD, MOST_ATTEMPTS, Angle, Canvas, Theme, load
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


SAID = " where the stored record cannot be matched against details supplied during the exchange"


def canvas(*rows, target: int = 0, themes=("TH01",)) -> Canvas:
    """Rows are (id, theme, cell, angle) with optional why_hard and want.

    Angles are padded to a readable length, because a bucket that reads as a label rather than a
    description is refused, and every fixture here would otherwise be testing that instead.
    """
    return Canvas(
        target=target,
        themes=[Theme(id=one, name=one) for one in themes],
        angles=[
            Angle(
                id=row[0], theme=row[1], cell=row[2],
                angle=(row[3] if len(_WORD.findall(row[3])) >= 8 else row[3] + SAID),
                why_hard=row[4] if len(row) > 4 else "",
                want=row[5] if len(row) > 5 else 1,
            )
            for row in rows
        ],
    )


class TestWhatAPlanMustSayBeforeAnyoneWritesFromIt:
    def test_a_cell_nobody_has_is_reported(self):
        held = canvas(("A1", "TH01", "invent-thing", "something impossible"))
        assert "not on the grid" in " ".join(held.problems({"retrieve-ride"}))

    def test_a_bucket_that_is_only_labelled_is_reported(self):
        """"recognized caller greeted by first name" tells a reader nothing about the test."""
        held = Canvas(
            themes=[Theme("TH01", "TH01")],
            angles=[Angle("A1", "TH01", "retrieve-ride", "greeted by name")],
        )
        said = " ".join(held.problems({"retrieve-ride"}))
        assert "labelled rather than described" in said

    def test_an_angle_written_as_a_whole_script_is_reported(self):
        """Readable is the bar; a paragraph is the finished test with its details stripped."""
        held = canvas((
            "A1", "TH01", "retrieve-ride",
            "the person asks about a charge they did not expect, and the agent has to find the "
            "record, work out which of the two similar entries they mean, explain how the amount "
            "was reached, check whether a correction is owed, and then either issue it or explain "
            "why it cannot, while keeping the whole thing inside one short exchange",
        ))
        assert "written as whole scripts" in " ".join(held.problems({"retrieve-ride"}))

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
        assert any("same why_hard" in why for _, _, why in held.collisions())

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


class TestAPlanThatIsReallyAList:
    """The first canvas a model wrote against this stage: 50 buckets for a target of 50.

    Every `want` was one, so it was a list of scenarios carrying extra fields. At a target of a
    thousand that means writing a thousand buckets, which is the wall planning exists to avoid.
    Prose warned against it twice and lost twice, so it is checked.
    """

    def test_one_bucket_per_scenario_is_refused_when_a_target_was_set(self):
        held = canvas(
            *[(f"A{i}", "TH01", "retrieve-ride", f"case number {i} of many", "", 1)
              for i in range(30)],
            target=40,
        )
        assert "not a plan" in " ".join(held.problems({"retrieve-ride"}))

    def test_buckets_that_carry_several_scenarios_pass(self):
        from fi.alk.harness.scenariogen.plan.canvas import StateAxis

        held = canvas(
            *[(f"A{i}", "TH01", "retrieve-ride", f"case number {i} of many", "", 5)
              for i in range(8)],
            target=40,
        )
        held.axes = [StateAxis("market", ["sf", "nyc", "blr", "ldn", "par"], "")]
        for one in held.angles:
            one.varies_by = ["market"]
        assert held.problems({"retrieve-ride"}) == []

    def test_a_small_suite_is_not_second_guessed(self):
        """Below the planning threshold there is no target to judge density against."""
        held = canvas(("A1", "TH01", "retrieve-ride", "booking cannot be found"))
        assert held.problems({"retrieve-ride"}) == []


class TestACountMustSayWhatItVaries:
    def test_asking_for_several_without_naming_axes_is_refused(self):
        held = canvas(("A1", "TH01", "retrieve-ride", "booking cannot be found", "", 5))
        assert "without naming the" in " ".join(held.problems({"retrieve-ride"}))

    def test_naming_the_axes_is_what_makes_a_count_stand(self):
        from fi.alk.harness.scenariogen.plan.canvas import StateAxis

        held = canvas(("A1", "TH01", "retrieve-ride", "booking cannot be found", "", 5))
        held.axes = [StateAxis("record_state", ["a", "b", "c", "d", "e"], "")]
        held.angles[0].varies_by = ["record_state"]
        assert held.problems({"retrieve-ride"}) == []

    def test_a_single_scenario_needs_no_justification(self):
        held = canvas(("A1", "TH01", "retrieve-ride", "booking cannot be found", "", 1))
        assert held.problems({"retrieve-ride"}) == []


class TestACountIsDerivedFromTheWorld:
    """`want` stops being a guess once the axes it crosses are named.

    The planner guessed 1 everywhere, and told to group would have guessed a flat number instead,
    which is padding wearing a different hat. A count has to come from somewhere checkable: the
    state axes derived from the agent's own data, and how many of their combinations survive.
    """

    def axes(self):
        from fi.alk.harness.scenariogen.plan.canvas import StateAxis

        return [
            StateAxis("s.payment", ["valid", "expired", "none"], "decides if it can charge"),
            StateAxis("s.market", ["SF", "NYC", "BLR"], "cash only in one of them"),
        ]

    def test_naming_the_axes_a_bucket_crosses_justifies_its_count(self):
        held = canvas(("A1", "TH01", "retrieve-ride", "payment state at selection", "", 9))
        held.axes = self.axes()
        held.angles[0].varies_by = ["s.payment", "s.market"]
        assert held.problems({"retrieve-ride"}) == []

    def test_an_axis_nobody_derived_is_refused(self):
        held = canvas(("A1", "TH01", "retrieve-ride", "payment state", "", 9))
        held.axes = self.axes()
        held.angles[0].varies_by = ["s.invented"]
        assert "never derived" in " ".join(held.problems({"retrieve-ride"}))

    def test_a_count_with_no_axes_is_refused(self):
        held = canvas(("A1", "TH01", "retrieve-ride", "payment state", "", 9))
        held.axes = self.axes()
        assert "without naming the" in " ".join(held.problems({"retrieve-ride"}))


class TestThePlanReportsWhatItCovers:
    """A plan can only be checked against the agent, never against its own tidiness."""

    def test_cells_with_nothing_on_them_are_named(self):
        held = canvas(("A1", "TH01", "retrieve-ride", "booking cannot be found"))
        said = held.coverage({"retrieve-ride", "cancel-ride", "diagnose-fare"}, [])
        assert "2 with nothing" in said
        assert "cancel-ride" in said

    def test_a_rule_with_no_bucket_is_the_gap_worth_shouting_about(self):
        held = canvas(("A1", "TH01", "cancel-ride", "cancellation fee disclosed", "rule:fee"))
        said = held.coverage(
            {"cancel-ride"},
            ["Disclose any cancellation fee before cancelling", "Never invent a fare or ETA"],
        )
        assert "1 of 2 rules have a bucket" in said
        assert "Never invent a fare" in said

    def test_facet_kinds_are_counted_so_a_lopsided_plan_shows(self):
        held = canvas(
            ("A1", "TH01", "retrieve-ride", "one", "rule:a"),
            ("A2", "TH01", "cancel-ride", "two", "rule:b"),
            ("A3", "TH01", "diagnose-fare", "three", "data:c"),
        )
        assert "2 rule, 1 data" in held.coverage({"retrieve-ride", "cancel-ride", "diagnose-fare"}, [])


class TestCoverageIsStatedAgainstWhatIsCheckable:
    """A plan can claim anything about itself; these are the claims that can be checked.

    The outcome axis replaced happy/edge/adversarial/failing, which was taken from a conversation
    rather than derived and overlapped badly: an injection attempt is adversarial *and* a path
    bound to fail, and "edge" is an intensity rather than a kind. Two planners would label one
    bucket differently, which makes the count meaningless. Outcome and cause are separate fields
    now, and each is mutually exclusive within itself.
    """

    def plan(self):
        return canvas(
            ("A1", "TH01", "create-ride", "books cleanly", "rule:readback", 3),
            ("A2", "TH01", "cancel-ride", "book_ride asked for too early", "precondition:book_ride", 2),
        )

    def test_an_outcome_the_agent_is_never_asked_for_is_named(self):
        held = self.plan()
        held.angles[0].expects = "succeed"
        held.angles[1].expects = "succeed"
        said = held.coverage({"create-ride", "cancel-ride"}, [], [])
        assert "nothing the agent should refuse, ask, escalate" in said

    def test_outcomes_are_counted_in_scenarios_not_buckets(self):
        held = self.plan()
        held.angles[0].expects = "succeed"
        held.angles[1].expects = "refuse"
        said = held.coverage({"create-ride", "cancel-ride"}, [], [])
        assert "3 succeed" in said and "2 refuse" in said

    def test_an_outcome_nobody_recognises_is_refused(self):
        held = self.plan()
        held.angles[0].expects = "vibes"
        assert "not one of" in " ".join(held.problems({"create-ride", "cancel-ride"}))

    def test_cause_and_outcome_are_recorded_separately(self):
        """An injection expects a refusal AND carries an injection overlay. Not a choice."""
        from fi.alk.harness.scenariogen.plan.canvas import StateAxis

        held = self.plan()
        held.axes = [StateAxis("region", ["a", "b", "c"], "")]
        for one in held.angles:
            one.varies_by = ["region"]
        held.angles[0].expects = "refuse"
        held.angles[0].overlay = "injection"
        held.angles[1].expects = "succeed"
        assert held.problems({"create-ride", "cancel-ride"}) == []
        said = held.coverage({"create-ride", "cancel-ride"}, [], [])
        assert "3 refuse" in said
        assert "3 carry an adversarial overlay" in said

    def test_an_overlay_nobody_recognises_is_refused(self):
        held = self.plan()
        held.angles[0].overlay = "spooky"
        assert "not one of" in " ".join(held.problems({"create-ride", "cancel-ride"}))

    def test_a_precondition_gated_tool_with_no_bucket_is_named(self):
        held = self.plan()
        said = held.coverage(
            {"create-ride", "cancel-ride"}, [], ["book_ride", "verify_otp", "cancel_ride"]
        )
        assert "1 of 3 tools with preconditions" in said
        assert "verify_otp" in said


class TestAWriterIsToldWhatMustDiffer:
    """A bucket of five that does not say what varies is five chances to write one test.

    The plan deliberately never names the five scenarios; the writer chooses them with the source
    open. So the one thing the plan owes the writer is the dimension they must differ along, and
    for a while that was recorded on the bucket and then left out of the line writers actually
    see.
    """

    def test_the_dimension_reaches_the_writer(self):
        held = canvas(("A1", "TH01", "create-ride", "payment cannot be used", "data:payment", 8))
        held.angles[0].varies_by = ["payment_state", "market"]
        line = held.angles[0].line()
        assert "x8" in line
        assert "the 8 differ by: payment_state, market" in line

    def test_a_single_scenario_needs_no_dimension(self):
        held = canvas(("A1", "TH01", "create-ride", "guest has no saved places", "rule:guest", 1))
        assert "differ by" not in held.angles[0].line()

    def test_what_the_agent_should_do_reaches_the_writer_too(self):
        held = canvas(("A1", "TH01", "create-ride", "injection in the address", "rule:injection", 1))
        held.angles[0].expects = "refuse"
        held.angles[0].overlay = "injection"
        line = held.angles[0].line()
        assert "expects refuse" in line and "overlay injection" in line


class TestACountCannotExceedWhatItsAxesAllow:
    """A bucket cannot hold more scenarios than its axes can tell apart.

    The first real plan had 19 of 167 multi-scenario buckets failing this, one asking for eight
    scenarios from a single axis with three levels. Their stated reasons gave the game away: they
    listed data values rather than states. Six riders each paying with their own valid card is one
    test run six times, because the agent does the same thing every time. Checking that a reason
    exists was never enough; the arithmetic has to hold.
    """

    def axes(self):
        from fi.alk.harness.scenariogen.plan.canvas import StateAxis

        return [
            StateAxis("payment_state", ["valid", "expired", "none"], ""),
            StateAxis("market", ["sf", "nyc", "blr"], ""),
        ]

    def test_a_count_beyond_its_axes_is_refused(self):
        held = canvas(("A1", "TH01", "retrieve-ride", "cards on file", "data:cards", 8))
        held.axes = self.axes()
        held.angles[0].varies_by = ["payment_state"]
        said = " ".join(held.problems({"retrieve-ride"}))
        assert "more scenarios than the axes they name can tell apart" in said
        assert "A1 wants 8 from 3" in said

    def test_crossing_two_axes_makes_room_for_more(self):
        held = canvas(("A1", "TH01", "retrieve-ride", "cards by market", "data:cards", 8))
        held.axes = self.axes()
        held.angles[0].varies_by = ["payment_state", "market"]
        assert held.problems({"retrieve-ride"}) == []

    def test_asking_for_fewer_than_the_axes_allow_is_fine(self):
        """Masking only ever removes combinations, so under is expected and over is the fault."""
        held = canvas(("A1", "TH01", "retrieve-ride", "cards on file", "data:cards", 2))
        held.axes = self.axes()
        held.angles[0].varies_by = ["payment_state"]
        assert held.problems({"retrieve-ride"}) == []

    def test_every_multi_scenario_bucket_must_name_its_axes(self):
        """There is no prose escape hatch any more: the reason has to be checkable."""
        held = canvas(
            *[(f"A{i}", "TH01", "retrieve-ride", f"case number {i}", "data:x", 3)
              for i in range(8)],
        )
        held.axes = self.axes()
        said = " ".join(held.problems({"retrieve-ride"}))
        assert "without naming the" in said


class TestAnAxisOfNamesIsNotAnAxis:
    """The failure that survives every other check, and the one that cost a whole plan.

    Asked to justify a count, a planner that cannot find a real dimension reaches for the entities
    themselves and declares those an axis. The arithmetic then holds perfectly - eight users
    really are eight levels - but the agent behaves identically for all eight, so the suite runs
    one test eight times and reports eight. On a real plan this accounted for 265 of 406
    scenarios, and every count passed every other check.

    It is caught by asking the world instead of the words: a column distinct in every row names
    those rows, a column whose values repeat describes their state.
    """

    def labels(self):
        return {
            "dana": "users.first_name",
            "marcus": "users.first_name",
            "maya": "users.first_name",
            "noor": "users.first_name",
        }

    def plan_with(self, axis_name, levels, want=4):
        from fi.alk.harness.scenariogen.plan.canvas import StateAxis

        held = canvas(("A1", "TH01", "retrieve-ride", "greeted by name", "data:name", want))
        held.axes = [StateAxis(axis_name, levels, "")]
        held.angles[0].varies_by = [axis_name]
        return held

    def test_an_axis_built_from_row_names_is_refused(self):
        held = self.plan_with("recognized_user", ["dana", "marcus", "maya", "noor"])
        said = " ".join(held.problems({"retrieve-ride"}, self.labels()))
        assert "lists of names rather than states" in said
        assert "users.first_name" in said

    def test_it_says_how_much_of_the_plan_rests_on_it(self):
        """A reviewer needs the blast radius, not just the fault."""
        held = self.plan_with("recognized_user", ["dana", "marcus", "maya", "noor"], want=4)
        assert "4 scenarios rest on it" in " ".join(held.problems({"retrieve-ride"}, self.labels()))

    def test_an_axis_of_real_states_passes(self):
        held = self.plan_with("account_status", ["active", "suspended", "payment_hold", "banned"])
        assert held.problems({"retrieve-ride"}, self.labels()) == []

    def test_without_the_world_the_check_simply_does_not_run(self):
        """It degrades to the older checks rather than refusing everything it cannot verify."""
        held = self.plan_with("recognized_user", ["dana", "marcus", "maya", "noor"])
        assert held.problems({"retrieve-ride"}, {}) == []

    def test_one_stray_name_among_states_is_not_enough_to_condemn_an_axis(self):
        held = self.plan_with("status", ["active", "suspended", "dana"], want=3)
        assert held.problems({"retrieve-ride"}, self.labels()) == []


class TestDepthIsNotASubstituteForBreadth:
    """A large suite that touches a fraction of the grid has left most of the agent alone.

    Measured on a real 200-scenario plan: 21 of 63 cells, with a third of the suite sitting on
    four of them. Every count was justified and every axis was real; it was simply absent from
    two thirds of the agent. Depth is worth having, and it is not coverage.
    """

    def wide_grid(self):
        return {f"cell-{i}" for i in range(20)}

    def test_a_large_plan_on_a_few_cells_is_refused(self):
        """Judged once the plan is whole: instalments of a themed recording are left alone."""
        held = canvas(
            *[(f"A{i}", "TH01", "cell-0" if i < 3 else f"cell-{i}", f"case number {i}",
               "data:x", 40)
              for i in range(5)],
            target=200,
        )
        said = " ".join(held.problems(self.wide_grid()))
        assert "touches 3 of 20 cells" in said

    def test_a_first_instalment_is_not_judged_as_the_whole_plan(self):
        """The skill records one theme at a time; a first theme covers few cells by nature, and
        refusing it orders the model to break the instalment discipline."""
        held = canvas(
            *[(f"A{i}", "TH01", "cell-0" if i < 3 else f"cell-{i}", f"case number {i}", "data:x")
              for i in range(5)],
            target=200,
        )
        said = " ".join(held.problems(self.wide_grid()))
        assert "touches" not in said

    def test_a_plan_spread_across_the_grid_passes(self):
        held = canvas(
            *[(f"A{i}", "TH01", f"cell-{i}", f"case number {i}", "data:x") for i in range(12)],
            target=200,
        )
        assert held.problems(self.wide_grid()) == []

    def test_a_small_suite_is_not_asked_to_cover_everything(self):
        """Twenty scenarios cannot touch sixty cells, and pretending otherwise helps nobody."""
        held = canvas(
            *[(f"A{i}", "TH01", f"cell-{i}", f"case number {i}", "data:x") for i in range(3)],
            target=20,
        )
        assert held.problems(self.wide_grid()) == []


class TestThePlannerMayProbeFreely:
    """The probe guard belongs to writers, and it was stopping the planner from planning.

    A writer that probes the agent repeatedly without submitting anything is stalling, and the
    guard says so: after twelve while it is still learning the world, then after four between
    scenarios. A planner has nothing to submit yet: reading and probing the agent
    *is* its work at that point. Measured before the fix, a planning run spent twenty-five minutes
    refused on every probe it attempted.
    """

    def probe_of(self, stage):
        return next(
            one
            for server in stage._spec.servers.values()
            for one in server.tools
            if one.name == "try_calls"
        )

    def probes(self, stage, monkeypatch, times=6):
        """Probe repeatedly and collect whatever came back.

        The guard runs before the world is touched, so a probe it refuses returns a message while
        one it allows dies reaching for a world this test does not have. Only the refusals matter
        here, which is exactly what is under test.
        """
        import asyncio

        from fi.alk.harness.scenariogen.write import tools as scenario_tools

        def no_world(*_args, **_rest):
            raise RuntimeError("no world in this test")

        monkeypatch.setattr(scenario_tools, "restore", no_world)
        probe = self.probe_of(stage)
        said = []
        for _ in range(times):
            try:
                said.append(str(asyncio.run(probe.handler({"calls": []}))))
            except RuntimeError:
                said.append("(reached the world)")
        return said

    def test_a_planning_stage_is_not_pushed_to_submit(self, contract, where, monkeypatch):
        from fi.alk.harness.scenariogen.write import stage as scenarios

        monkeypatch.setattr(scenarios, "world_summary", lambda _root: "(no world here)")
        stage, _ = scenarios.open_stage(contract, out=where, wanted=200)
        said = self.probes(stage, monkeypatch, times=16)
        assert not any("throwaway probes have run" in one for one in said)

    def test_a_writing_stage_still_is(self, contract, where, monkeypatch):
        from fi.alk.harness.scenariogen.write import stage as scenarios

        monkeypatch.setattr(scenarios, "world_summary", lambda _root: "(no world here)")
        stage, _ = scenarios.open_stage(contract, out=where, wanted=4)
        # Past the first-look allowance, which is wider than the between-scenarios one because a
        # writer cannot ground an instruction in a world it has not been allowed to look at.
        said = self.probes(stage, monkeypatch, times=16)
        assert any("throwaway probes have run" in one for one in said)


class TestOrderingIsPlannedForRatherThanMentioned:
    """A tool that refuses until something else has happened is where an agent breaks, and the
    grid cannot show the hole: a cell names an object and says nothing about order. Two whole
    plans were reported as naming none of them and neither planner acted on it, so it is refused.
    """

    def big(self, why: str = "rule:something"):
        # want=17 apiece so the plan is whole (planned >= target): whole-plan refusals wait for
        # a whole plan, and an instalment names no gated tool without being at fault.
        return canvas(
            *[
                (f"A{n}", "TH01", "retrieve-ride", f"case number {n} that goes wrong somehow",
                 why, 17)
                for n in range(12)
            ],
            target=200,
        )

    def test_a_large_plan_naming_none_of_them_is_refused(self):
        found = " ".join(self.big().problems({"retrieve-ride"}, None, ["book_ride", "verify_otp"]))
        assert "none of the 2 tools" in found
        assert "book_ride" in found

    def test_naming_one_in_why_hard_satisfies_it(self):
        held = self.big(why="precondition:book_ride")
        found = " ".join(held.problems({"retrieve-ride"}, None, ["book_ride", "verify_otp"]))
        assert "none of the" not in found

    def test_a_contract_with_no_gated_tools_is_not_asked_for_one(self):
        assert "none of the" not in " ".join(self.big().problems({"retrieve-ride"}, None, []))

    def test_a_small_plan_is_left_alone(self):
        held = canvas(("A1", "TH01", "retrieve-ride", "one case that goes wrong"), target=10)
        found = " ".join(held.problems({"retrieve-ride"}, None, ["book_ride"]))
        assert "none of the" not in found


class TestProgressNeverMovesBackwards:
    """A bucket filled over two rounds folds each round's own names, and the second writer's
    two must not erase the first writer's three. Assigning absolutely marked finished buckets
    part-done, burned an attempt per round, and blocked buckets that were being filled."""

    def test_two_rounds_add_up(self):
        held = canvas(("A1", "TH01", "retrieve-ride", "booking missing", "", 5))
        held.credit("A1", ["one", "two", "three"])
        held.fold("A1", done=len(held.named("A1").credited))
        held.credit("A1", ["four", "five"])
        held.fold("A1", done=len(held.named("A1").credited))
        assert held.named("A1").done == 5
        assert held.named("A1").state == "done"

    def test_a_name_fills_one_bucket_only(self):
        held = canvas(
            ("A1", "TH01", "retrieve-ride", "booking missing", "", 2),
            ("B1", "TH01", "cancel-ride", "already cancelled", "", 2),
        )
        assert held.credit("A1", ["shared-name"]) == 1
        assert held.credit("B1", ["shared-name"]) == 0

    def test_a_fold_with_less_than_the_ledger_keeps_the_ledger(self):
        held = canvas(("A1", "TH01", "retrieve-ride", "booking missing", "", 5))
        held.credit("A1", ["one", "two", "three"])
        held.fold("A1", done=0, short="writer died")
        assert held.named("A1").done == 3


class TestSeveralWritersCanRunAtOnce:
    """Scale comes from parallel writers, and it is only safe because a claim takes its angles
    out of the pool. Two writers must never be handed the same scenario."""

    def some(self, n: int = 8):
        return canvas(
            *[(f"A{i}", "TH01", f"cell-{i}", f"case number {i} that goes wrong", "", 2)
              for i in range(n)],
            target=200,
        )

    def test_a_second_claim_returns_different_work(self):
        held = self.some()
        first = held.next_slice(4)
        held.claim(first, "writer_1")
        second = held.next_slice(4)
        held.claim(second, "writer_2")

        assert first and second
        assert not ({one.id for one in first} & {one.id for one in second})

    def test_each_writer_is_recorded_as_holding_its_own(self):
        held = self.some()
        held.claim(held.next_slice(4), "writer_1")
        held.claim(held.next_slice(4), "writer_2")

        holders = {one.claimed_by for one in held.angles if one.state == "claimed"}
        assert holders == {"writer_1", "writer_2"}

    def test_claiming_until_dry_never_repeats_an_angle(self):
        held = self.some(6)
        seen: list[str] = []
        for n in range(10):
            taken = held.next_slice(2)
            if not taken:
                break
            held.claim(taken, f"writer_{n}")
            seen += [one.id for one in taken]

        assert len(seen) == len(set(seen)) == 6


class TestTheSpreadIsDealtNotRequested:
    """Writers are blind to each other, so each independently picks the safest value and the
    suite converges on it. Measured: two locations across forty-one callers where the platform
    offered five, the same collapse accents had before they were dealt."""

    def test_each_slice_starts_from_a_different_place(self):
        from fi.alk.harness.scenariogen.write.stage import callers_for

        first, second = callers_for(0, 4), callers_for(1, 4)
        assert first and second
        assert first != second

    def test_locations_are_dealt_as_well_as_accents(self):
        from fi.alk.harness.scenariogen.model.persona import offered
        from fi.alk.harness.scenariogen.write.stage import callers_for

        places = offered("location")
        if not places:
            return
        said = callers_for(0, 6)
        assert sum(1 for one in places if one in said) >= 2, said


def test_the_credit_ledger_survives_a_save_and_load(tmp_path):
    """Without this the ledger evaporates on every restart, and the two-round fold it exists for
    is exactly the case that spans one: a writer dies, the run is restarted, and the bucket it
    part-filled has to remember what it already holds."""
    from fi.alk.harness.scenariogen.plan.canvas import load

    held = canvas(("A1", "TH01", "retrieve-ride", "booking missing", "", 5))
    held.credit("A1", ["one", "two"])
    held.fold("A1", done=2)
    held.written_to(tmp_path)

    back = load(tmp_path)
    assert back.named("A1").credited == ["one", "two"]
    assert back.named("A1").done == 2
    # And a name already credited is not credited a second time after the round trip.
    assert back.credit("A1", ["one", "three"]) == 3


def test_a_writer_gets_enough_turns_for_the_slice_it_can_be_handed():
    """A flat sixty gave 3.8 turns per scenario including the reading a writer does before it
    writes anything, so slices came back part-filled and the next writer paid that reading cost
    again to finish somebody else's work."""
    from fi.alk.harness.scenariogen.plan.canvas import SLICE_SCENARIOS
    from fi.alk.harness.scenariogen.write.stage import TURNS_EACH, WRITER_TURNS

    biggest = SLICE_SCENARIOS * 2  # what claim_slice clamps to
    assert WRITER_TURNS >= biggest * TURNS_EACH, "not even the writing fits"
    assert WRITER_TURNS >= biggest * TURNS_EACH + 20, "no room to read the agent first"


class TestContinuingAnUnfinishedSuite:
    """A suite short of its target is being continued, not edited. Told only that scenarios
    exist and to say what it wants changed, a stage reads a large number, finds nothing to
    change and stops: one attempt oriented itself and exited inside a minute with 354 left."""

    def contract(self):
        from fi.alk.harness.contract import AgentContract, ToolSpec

        return AgentContract(
            agent="ride-agent", modality="voice",
            tools=[ToolSpec(name="book_ride")], data_schema={"rides": {}},
        )

    def test_it_says_how_many_are_outstanding(self):
        from fi.alk.harness.scenariogen.write.stage import opening

        said = opening(self.contract(), 500, 146)
        assert "354" in said and "still to write" in said

    def test_it_names_finishing_as_the_work(self):
        from fi.alk.harness.scenariogen.write.stage import opening

        said = opening(self.contract(), 500, 146).lower()
        assert "claim_slice" in said
        assert "nothing is open" in said

    def test_a_finished_suite_is_offered_for_editing_instead(self):
        from fi.alk.harness.scenariogen.write.stage import opening

        said = opening(self.contract(), 500, 500)
        assert "still to write" not in said
        assert "changed" in said

    def test_a_fresh_suite_is_unaffected(self):
        from fi.alk.harness.scenariogen.write.stage import opening

        assert "show_grid" in opening(self.contract(), 500, 0)
