"""Copying a proved scenario across the callers it stays true for.

The property that matters is that a copy is still the scenario that was proved. Everything the
three gates checked has to survive the copy untouched, or the copies are unproved scenarios
wearing a proved one's name, which is exactly the failure the gates exist to prevent.
"""

from __future__ import annotations

import pytest

from fi.alk.harness.scenariogen.plan.axes import axes_for
from fi.alk.harness.scenariogen.quality.expand import CONDITION, axes_to_vary, expand, expand_all, summarise
from fi.alk.harness.scenariogen.model.scenario import Persona, Scenario, Step


@pytest.fixture()
def axes():
    return axes_for("voice")


@pytest.fixture()
def proved():
    return Scenario(
        name="cancel-ride__baseline",
        use_case="Cancel a ride that has not started",
        branch="the ordinary path",
        tests="the ride is cancelled and the fee is explained",
        instruction="Get your ride cancelled before the driver arrives.",
        setup_code="def setup(world):\n    world.rows('bookings')[0]['status'] = 'confirmed'\n",
        ready_code="def ready(world):\n    return True\n",
        solution=[Step(tool="cancel_ride", arguments={"booking_id": "B-1"})],
        sub_goals=["cancelled_the_right_ride"],
        persona=Persona(name="Dana", personality="Friendly and cooperative", accent="American"),
    )


class TestWhatSurvivesACopy:
    def test_everything_the_gates_proved_is_carried_untouched(self, proved, axes):
        """A copy reuses the proved environment. If any of this drifts, it is not proved."""
        for copy in expand(proved, axes, env={}):
            assert copy.setup_code == proved.setup_code
            assert copy.ready_code == proved.ready_code
            assert copy.sub_goals == proved.sub_goals
            assert [step.tool for step in copy.solution] == [step.tool for step in proved.solution]
            assert copy.use_case == proved.use_case

    def test_each_copy_gets_its_own_identity(self, proved, axes):
        copies = expand(proved, axes, env={})
        names = [copy.name for copy in copies]
        assert len(set(names)) == len(names)
        assert proved.name not in names
        # A derived key carried over from the parent would collide with it wherever results land.
        assert all(not copy.scenario_key and not copy.scenario_id for copy in copies)

    def test_the_caller_is_what_changed(self, proved, axes):
        copies = {copy.name.rsplit("__", 1)[1]: copy for copy in expand(proved, axes, env={})}
        assert "__baseline__" not in " ".join(copies), "the baseline marker should be replaced, not stacked"
        assert copies["senior"].persona.age_group == "60+"
        assert copies["rushed"].persona.personality == "Impatient and direct"
        # And the guidance reaches the simulator, which renders persona metadata into its prompt.
        assert CONDITION in copies["evasive"].persona.metadata

    def test_a_copy_is_not_itself_expandable(self, proved, axes):
        """Expanding an expansion moves two dials at once and loses attribution."""
        for copy in expand(proved, axes, env={}):
            assert copy.varies == []
            assert expand(copy, axes, env={}) != []  # it still *could* be, so the guard is varies
            assert copy.varies == []


class TestWhichAxes:
    def test_by_default_every_axis_that_leaves_the_world_alone(self, proved, axes):
        varied = {axis.name for axis in axes_to_vary(proved, axes, env={})}
        assert varied == {"who", "state"}

    def test_naming_axes_withholds_the_rest(self, proved, axes):
        proved.varies = ["who"]
        assert {axis.name for axis in axes_to_vary(proved, axes, env={})} == {"who"}
        assert {copy.name.rsplit("__", 1)[1] for copy in expand(proved, axes, env={})} == {
            "senior", "second-language", "on-someone-behalf", "unverified",
        }

    def test_asking_for_a_world_changing_axis_is_refused(self, proved, axes):
        """Copying across a twist would make the copy's setup a lie about its own world."""
        proved.varies = ["twist", "who"]
        assert {axis.name for axis in axes_to_vary(proved, axes, env={})} == {"who"}

    def test_an_axis_that_does_not_exist_is_ignored_not_fatal(self, proved, axes):
        proved.varies = ["who", "weather"]
        assert {axis.name for axis in axes_to_vary(proved, axes, env={})} == {"who"}

    def test_settings_the_environment_cannot_honour_are_not_copied(self, proved, axes):
        closed = {copy.name for copy in expand(proved, axes, env={})}
        opened = {copy.name for copy in expand(proved, axes, env={"ALK_BACKGROUND_NOISE": "1"})}
        assert len(opened) > len(closed)
        assert not any("dropping" in name or "interrupted" in name for name in opened)


class TestSuite:
    def test_a_suite_expands_evenly_rather_than_front_to_back(self, proved, axes):
        """A cap taken from the front expands one scenario twelve ways and the rest not at all."""
        suite = [proved.model_copy(deep=True, update={"name": f"scenario-{i}"}) for i in range(4)]
        capped = expand_all(suite, axes, env={}, wanted=8)
        assert len(capped) == 8
        gained = [
            sum(1 for one in capped if one.name.startswith(f"{origin.name}__")) for origin in suite
        ]
        assert max(gained) - min(gained) <= 1

    def test_without_a_cap_everything_expands(self, proved, axes):
        suite = [proved.model_copy(deep=True, update={"name": f"scenario-{i}"}) for i in range(3)]
        full = expand_all(suite, axes, env={})
        per = len(expand(proved, axes, env={}))
        assert len(full) == 3 + 3 * per

    def test_an_empty_suite_expands_to_nothing(self, axes):
        assert expand_all([], axes, env={}) == []

    def test_the_summary_says_what_it_cost(self, proved, axes):
        suite = [proved]
        grown = expand_all(suite, axes, env={})
        said = summarise(1, grown, axes, env={})
        assert "no model call" in said
        assert str(len(grown)) in said
