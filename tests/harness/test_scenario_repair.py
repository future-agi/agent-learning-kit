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
