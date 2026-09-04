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


def test_a_setup_that_only_adjusts_borrowed_records_is_refused():
    """Measured on real suites: refuses 4 of 50 in the reference suite, 30 of 86 in the thin one."""
    from fi.alk.harness.scenario import self_sufficiency_problems

    borrowed = Scenario(
        name="expired_card_on_file",
        instruction="Get the booking paid for.",
        sub_goals=["fee_removed"],
        setup_code=(
            "def setup(world):\n"
            "    world.change('payment_methods', 'pm_existing', {'last4': '6172'}, by='id')\n"
        ),
    )
    assert self_sufficiency_problems(borrowed)

    owns_it = Scenario(
        name="expired_card_on_file",
        instruction="Get the booking paid for.",
        sub_goals=["fee_removed"],
        setup_code=(
            "def setup(world):\n"
            "    world.put('payment_methods', {'id': 'pm_own', 'last4': '6172'})\n"
            "    world.change('payment_methods', 'pm_own', {'expired': 1}, by='id')\n"
        ),
    )
    assert self_sufficiency_problems(owns_it) == []

    # The documented no-seam case: nothing to change, so nothing is claimed.
    assert (
        self_sufficiency_problems(
            Scenario(name="reads_only", instruction="Ask for the status.", sub_goals=["x"])
        )
        == []
    )


def test_a_writer_that_dies_after_proving_still_leaves_its_work(tmp_path):
    """A delegated writer cannot write folders, so the journal is the only record it leaves."""
    from fi.alk.harness.scenario_tools import journal_scenario, journalled

    one = Scenario(
        name="cancel_after_fee_quoted",
        instruction="Get the ride cancelled.",
        sub_goals=["ride_cancelled"],
    )
    journal_scenario(one, tmp_path)
    # A retried slice journals the same name again, and the later line wins rather than duplicating.
    journal_scenario(one.model_copy(update={"instruction": "Cancel it, and ask what it costs."}), tmp_path)
    back = journalled(tmp_path)
    assert [x.name for x in back] == ["cancel_after_fee_quoted"]
    assert back[0].instruction.startswith("Cancel it")
    assert journalled(tmp_path / "nothing-here") == []


def test_a_declared_check_that_never_reached_the_folder_is_not_read_as_judged(tmp_path):
    """Absence of a check file means judged, which is wrong when the catalogue settles it in code."""
    import json

    import pytest

    from fi.alk.harness.scenario_source import ScenarioDocumentInvalid, load_scenarios

    bundle = tmp_path
    (bundle / "sub_goals.json").write_text(
        json.dumps(
            {
                "sub_goals": [
                    {"name": "fee_removed", "what": "the fee is gone",
                     "check": "def check(world, calls):\n    return None\n"},
                    {"name": "explained_kindly", "what": "tone", "judged": "read the transcript"},
                ]
            }
        )
    )
    folder = bundle / "scenarios" / "refund_after_shipping"
    folder.mkdir(parents=True)
    (folder / "scenario.json").write_text(
        json.dumps({"scenario_key": "refund", "scenario_id": "",
                    "sub_goals": ["fee_removed", "explained_kindly"],
                    "name": "refund_after_shipping", "instruction": "Get the fee taken off."})
    )
    (folder / "setup.py").write_text("def setup(world):\n    return None\n")
    (folder / "ready.py").write_text("def ready(world):\n    return None\n")

    with pytest.raises(ScenarioDocumentInvalid) as refused:
        load_scenarios(bundle)
    assert "fee_removed" in str(refused.value)

    # With the check materialised, the judged one beside it is still judged.
    checks = folder / "checks"
    checks.mkdir()
    (checks / "fee_removed.py").write_text("def check(world, calls):\n    return None\n")
    loaded = load_scenarios(bundle)
    assert [one.scenario_key for one in loaded] == ["refund"]
    assert loaded[0].presented["situation"] == "Get the fee taken off."


def test_a_slice_writer_stops_at_the_size_it_was_given(tmp_path):
    """Its turn budget is far larger than its slice, and left alone it keeps writing."""
    import asyncio

    from fi.alk.harness.contract import AgentContract
    from fi.alk.harness.scenario_tools import scenario_tools

    contract = AgentContract(agent="cart", real_use_cases=["add an item"])
    server, kept = scenario_tools(contract, tmp_path, tmp_path, wanted=1, can_save=False)
    submit = next(spec for spec in server.tools if spec.name == "submit_scenario")
    kept.append(Scenario(name="already_here", instruction="one", sub_goals=["x"]))

    said = asyncio.run(submit.handler({"name": "a_second_one", "instruction": "two"}))
    assert said.get("is_error")
    assert "This slice is complete" in said["content"][0]["text"]

    # Replacing one of its own is still allowed, which is how a refused scenario gets fixed. It gets
    # past the cap and into validation, which here has no world to validate against.
    import pytest

    with pytest.raises(FileNotFoundError):
        asyncio.run(submit.handler({"name": "already_here", "instruction": "one, fixed"}))
