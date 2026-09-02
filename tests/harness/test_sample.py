"""Choosing what to write, for any count the caller asks for.

The properties worth pinning are the ones a caller would notice and a count would hide: asking
for four and getting four, asking for four and getting four *different* things, and a small suite
still containing the cases a suite is not worth running without.
"""

from __future__ import annotations

import pytest

from fi.alk.harness.axes import axes_for
from fi.alk.harness.contract import AgentContract, ToolSpec
from fi.alk.harness.grid import derive
from fi.alk.harness.sample import coverage, plan


@pytest.fixture()
def axes():
    return axes_for("voice")


@pytest.fixture()
def grid(axes):
    contract = AgentContract(
        agent="ride-agent",
        modality="voice",
        tools=[
            ToolSpec(name=one)
            for one in (
                "book_ride", "cancel_ride", "get_ride_options", "get_bookings",
                "get_payment_methods", "select_payment_method", "send_otp", "verify_otp",
                "get_saved_places", "transfer_to_human", "get_fares", "update_booking",
            )
        ],
        data_schema={
            "users": {}, "bookings": {}, "payment_methods": {}, "saved_places": {},
            "otp_codes": {}, "fares": {},
        },
    )
    return derive(contract, axes)


class TestCount:
    @pytest.mark.parametrize("wanted", [1, 2, 4, 10, 20, 50, 100, 200, 500])
    def test_asking_for_n_returns_exactly_n(self, grid, axes, wanted):
        """A caller who asked for a number needs that number, not a best effort."""
        assert len(plan(grid, axes, wanted, env={})) == wanted

    def test_every_scenario_in_a_plan_is_distinct(self, grid, axes):
        picks = plan(grid, axes, 200, env={})
        names = [pick.name for pick in picks]
        assert len(set(names)) == len(names)

    def test_zero_is_zero_and_not_an_error(self, grid, axes):
        assert plan(grid, axes, 0, env={}) == []

    def test_a_plan_is_the_same_plan_twice(self, grid, axes):
        """Two runs of one suite have to be comparable, so the choice cannot drift."""
        first = [pick.name for pick in plan(grid, axes, 40, env={})]
        second = [pick.name for pick in plan(grid, axes, 40, env={})]
        assert first == second

    def test_a_very_large_request_is_met_in_full_and_without_repeats(self, grid, axes):
        """A count is a promise. Beyond single dials it escalates to pairs, then to branches of
        the same cell, which is a different test rather than the same one again."""
        picks = plan(grid, axes, 100_000, env={})
        assert len(picks) == 100_000
        names = [pick.name for pick in picks]
        assert len(set(names)) == len(names)
        # The escalation is visible: some carry two conditions, some are later branches.
        assert any(len(pick.dials) > 1 for pick in picks)
        assert any(pick.branch for pick in picks)


class TestSmallSuitesAreStillWorthRunning:
    def test_one_scenario_is_the_agent_doing_its_job(self, grid, axes):
        only = plan(grid, axes, 1, env={})[0]
        assert only.cell.kind == "change"
        assert only.dials == {}

    def test_four_scenarios_are_four_different_kinds_of_thing(self, grid, axes):
        picks = plan(grid, axes, 4, env={})
        assert len({pick.cell.name for pick in picks}) == 4
        # Not four happy paths: at least one carries an adversarial or safety overlay.
        assert any("twist" in pick.dials for pick in picks)

    def test_ten_scenarios_carry_every_safety_overlay(self, grid, axes):
        """The twists are too rare to survive weighting and too costly to leave out."""
        picks = plan(grid, axes, 10, env={})
        twists = {pick.dials.get("twist") for pick in picks} - {None}
        assert twists == {"impersonation", "emergency", "fraud", "injection"}

    def test_twenty_scenarios_cover_every_dial_that_reaches_a_run(self, grid, axes):
        picks = plan(grid, axes, 20, env={})
        for axis in ("who", "state", "shape", "twist"):
            used = {pick.dials.get(axis) for pick in picks} - {None}
            live = {one.name for one in axes.axis(axis).settings if one.live(env={})}
            assert used == live, f"{axis} left {live - used} untested in a suite of twenty"

    def test_a_small_suite_spreads_across_operation_kinds(self, grid, axes):
        picks = plan(grid, axes, 6, env={})
        assert len({pick.cell.kind for pick in picks}) >= 2


class TestDegenerateInputs:
    def test_an_agent_with_nothing_declared_still_gets_a_plan(self, axes):
        """The worst case. Few use cases or no objects still has to produce the asked-for count."""
        grid = derive(AgentContract(agent="mystery", modality="voice"), axes)
        picks = plan(grid, axes, 10, env={})
        assert picks
        assert len({pick.name for pick in picks}) == len(picks)

    def test_an_empty_grid_plans_nothing_rather_than_crashing(self, axes):
        from fi.alk.harness.grid import Grid

        assert plan(Grid(), axes, 10, env={}) == []

    def test_settings_the_run_cannot_honour_are_never_planned(self, grid, axes):
        """Channel needs an environment variable. Without it, planning it would be a lie."""
        picks = plan(grid, axes, 200, env={})
        assert {pick.dials.get("channel") for pick in picks} == {None}

        opened = plan(grid, axes, 200, env={"ALK_BACKGROUND_NOISE": "1"})
        assert {pick.dials.get("channel") for pick in opened} - {None}


class TestCoverageReport:
    def test_it_names_what_was_left_out(self, grid, axes):
        report = coverage(grid, axes, plan(grid, axes, 4, env={}))
        assert "not covered" in report
        assert "cells with nothing on them" in report

    def test_a_full_suite_reports_no_gaps_on_the_live_axes(self, grid, axes):
        report = coverage(grid, axes, plan(grid, axes, 300, env={}))
        assert "twist: 4/4" in report
