"""What a result reports when the catalogue cannot settle what the scenario named."""

from __future__ import annotations

from fi.alk.harness.catalogue import Catalogue, SubGoal
from fi.alk.harness.run.grade import Result, ungraded_sub_goals
from fi.alk.harness.scenario import Scenario


def _scenario() -> Scenario:
    return Scenario(
        name="refund_after_shipping",
        instruction="Get the fee taken off.",
        sub_goals=["fee_removed", "order_checked"],
    )


def test_a_result_that_checked_nothing_has_not_passed():
    assert Result(scenario="refund_after_shipping").passed is False


def test_a_scenario_still_passes_on_its_checkpoints():
    from fi.alk.harness.run.grade import Checkpoint

    result = Result(
        scenario="refund_after_shipping",
        checkpoints=[Checkpoint(name="fee_removed", kind="code", passed=True)],
    )
    assert result.passed is True


def test_a_sub_goal_missing_from_the_catalogue_is_reported():
    catalogue = Catalogue(
        sub_goals=[SubGoal(name="fee_removed", what="the fee is gone", check="def check(world, calls):\n    return None")]
    )
    assert ungraded_sub_goals(_scenario(), catalogue) == ["order_checked"]


def test_nothing_is_reported_when_the_catalogue_holds_them_all():
    catalogue = Catalogue(
        sub_goals=[
            SubGoal(name="fee_removed", what="the fee is gone", check="def check(world, calls):\n    return None"),
            SubGoal(name="order_checked", what="the order was read", judged="did it read the order"),
        ]
    )
    assert ungraded_sub_goals(_scenario(), catalogue) == []
