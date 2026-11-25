from __future__ import annotations

import pytest

from fi.alk.harness.scenario import Scenario, Step
from fi.alk.harness.scenario_repair import ScenarioChangeKind, certify_scenario_repair


def _scenario(
    name: str, *, instruction: str = "do it", goals: list[str] | None = None
) -> Scenario:
    return Scenario(
        name=name,
        instruction=instruction,
        sub_goals=goals or ["complete"],
        solution=[Step(tool="act")],
    )


def test_repair_can_replace_and_add_without_exposing_scenario_values() -> None:
    before = [_scenario("one")]
    after = [_scenario("one", instruction="do it safely"), _scenario("two")]
    receipt = certify_scenario_repair(
        before, after, expected_count=2, diagnostic_codes={"generated_setup_invalid"}
    )
    assert [change.kind for change in receipt.changes] == [
        ScenarioChangeKind.REPLACED,
        ScenarioChangeKind.ADDED,
    ]
    assert "do it safely" not in receipt.model_dump_json()
    assert receipt.diagnostic_codes == ("generated_setup_invalid",)


@pytest.mark.parametrize(
    "after,code",
    [
        ([], "scenario_repair_count_mismatch"),
        ([_scenario("one", goals=["other"])], "scenario_repair_weakened_checks"),
        ([_scenario("renamed")], "scenario_repair_deleted_scenarios"),
    ],
)
def test_repair_rejects_deletion_or_weakened_checks(after, code) -> None:
    with pytest.raises(ValueError, match=code):
        certify_scenario_repair(
            [_scenario("one")],
            after,
            expected_count=1,
            diagnostic_codes={"generated_setup_invalid"},
        )


def test_repair_rejects_noop() -> None:
    scenario = _scenario("one")
    with pytest.raises(ValueError, match="no_material_change"):
        certify_scenario_repair(
            [scenario],
            [scenario.model_copy(deep=True)],
            expected_count=1,
            diagnostic_codes={"generated_setup_invalid"},
        )


def test_a_branch_loses_a_pasted_kind_label_and_keeps_its_description():
    from fi.alk.harness.scenario_tools import _without_kind_label

    assert _without_kind_label(
        "two facts that disagree: caller asks if an XL is needed"
    ) == "Caller asks if an XL is needed"
    assert _without_kind_label(
        "two facts that disagree on the pickup, the caller names two squares"
    ) == "The caller names two squares"
    assert _without_kind_label(
        "a reference with no referent when the caller asks for the usual airport"
    ) == "The caller asks for the usual airport"
    assert _without_kind_label("The caller corrects the drop-off") == "The caller corrects the drop-off"


def _cell(name: str, coverage: dict | None, **fields) -> Scenario:
    return _scenario(name, **fields).model_copy(update={"coverage": coverage})


_LATE = {"intent": "refund", "situation": "past the window"}
_ON_TIME = {"intent": "refund", "situation": "inside the window"}


def test_a_held_case_may_be_replaced_by_a_new_case_in_its_own_cell() -> None:
    before = [_cell("late-refund", _LATE), _cell("on-time-refund", _ON_TIME)]
    after = [_cell("late-refund-by-email", _LATE), _cell("on-time-refund", _ON_TIME)]
    late, new = before[0].scenario_key, after[0].scenario_key

    receipt = certify_scenario_repair(before, after, expected_count=2, diagnostic_codes=set(), replaceable={late})

    assert {(one.scenario_key, one.kind) for one in receipt.changes} == {
        (late, ScenarioChangeKind.REMOVED),
        (new, ScenarioChangeKind.ADDED),
    }


@pytest.mark.parametrize(
    "after,replaceable",
    [
        # Another cell.
        ([_cell("late-refund-by-email", _ON_TIME), _cell("on-time-refund", _ON_TIME)], {"late"}),
        # A case that was not held.
        ([_cell("late-refund", _LATE), _cell("on-time-refund-again", _ON_TIME)], {"late"}),
        # A case with no declared cell is never matched to one.
        ([_cell("late-refund-by-email", None), _cell("on-time-refund", _ON_TIME)], {"late"}),
    ],
    ids=["another_cell", "not_held", "no_cell"],
)
def test_a_removal_nothing_stands_in_for_is_refused(after, replaceable) -> None:
    before = [_cell("late-refund", _LATE), _cell("on-time-refund", _ON_TIME)]
    keys = {"late": before[0].scenario_key}
    with pytest.raises(ValueError, match="scenario_repair_deleted_scenarios"):
        certify_scenario_repair(
            before, after, expected_count=2, diagnostic_codes=set(), replaceable={keys[one] for one in replaceable}
        )


def test_two_held_cases_in_one_cell_need_two_replacements() -> None:
    before = [_cell("late-a", _LATE), _cell("late-b", _LATE), _cell("on-time", _ON_TIME)]
    after = [_cell("late-c", _LATE), _cell("on-time", _ON_TIME), _cell("on-time-2", _ON_TIME)]
    with pytest.raises(ValueError, match="scenario_repair_deleted_scenarios"):
        certify_scenario_repair(
            before,
            after,
            expected_count=3,
            diagnostic_codes=set(),
            replaceable={before[0].scenario_key, before[1].scenario_key},
        )


def test_a_held_case_with_no_declared_cell_is_never_replaced() -> None:
    before = [_cell("late-refund", None), _cell("on-time-refund", _ON_TIME)]
    after = [_cell("late-refund-by-email", None), _cell("on-time-refund", _ON_TIME)]
    with pytest.raises(ValueError, match="scenario_repair_deleted_scenarios"):
        certify_scenario_repair(
            before, after, expected_count=2, diagnostic_codes=set(), replaceable={before[0].scenario_key}
        )


def test_a_replacement_that_drops_a_sub_goal_of_the_held_case_is_refused() -> None:
    before = [_cell("late-refund", _LATE, goals=["refund_refused", "policy_explained"]), _cell("on-time", _ON_TIME)]
    after = [_cell("late-refund-by-email", _LATE, goals=["policy_explained"]), _cell("on-time", _ON_TIME)]
    with pytest.raises(ValueError, match="names its sub-goals"):
        certify_scenario_repair(
            before, after, expected_count=2, diagnostic_codes=set(), replaceable={before[0].scenario_key}
        )
    # Naming them all (and more) stands in for it.
    after[0] = _cell("late-refund-by-email", _LATE, goals=["refund_refused", "policy_explained", "lookup"])
    certify_scenario_repair(before, after, expected_count=2, diagnostic_codes=set(), replaceable={before[0].scenario_key})
