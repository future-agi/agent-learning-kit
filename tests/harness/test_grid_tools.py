"""The tools a stage uses to see the grid, correct it, and change a suite that already exists.

A suite is not written once. Somebody reads what came back and says "add twenty adversarial
ones", "drop the weak ones", "these are too easy". That is a conversation about a suite on disk,
so what matters here is that these tools read the saved suite rather than whatever this session
happens to remember, and that correcting the grid actually changes what gets planned next.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from fi.alk.harness.contract import AgentContract, ToolSpec
from fi.alk.harness.grid_tools import grid_tools
from fi.alk.harness.scenario import Persona, Scenario
from fi.alk.harness.scenario_tools import write_scenarios


def call(server, name: str, args: dict | None = None) -> str:
    for spec in server.tools:
        if spec.name == name:
            result = asyncio.run(spec.handler(args or {}))
            return result["content"][0]["text"]
    raise AssertionError(f"no tool named {name}")


def failed(server, name: str, args: dict | None = None) -> bool:
    for spec in server.tools:
        if spec.name == name:
            return bool(asyncio.run(spec.handler(args or {})).get("is_error"))
    raise AssertionError(f"no tool named {name}")


@pytest.fixture()
def contract():
    return AgentContract(
        agent="ride-agent",
        modality="voice",
        tools=[
            ToolSpec(name=one)
            for one in ("book_ride", "cancel_ride", "get_fares", "send_otp", "verify_otp")
        ],
        data_schema={"rides": {}, "fares": {}, "otp_codes": {}, "users": {}},
    )


@pytest.fixture()
def where(tmp_path: Path):
    return tmp_path / "session"


class TestSeeingAndCorrectingTheGrid:
    def test_the_grid_is_shown_with_its_arithmetic(self, contract, where):
        server, _ = grid_tools(contract, where)
        said = call(server, "show_grid")
        assert "objects x" in said and "valid" in said

    def test_correcting_the_objects_replans_everything(self, contract, where):
        """The contract is a summary. The stage reads the source and can see what it missed."""
        server, state = grid_tools(contract, where)
        before = len(state.grid.cells)
        said = call(
            server,
            "set_objects",
            {"objects": ["ride", "fare", "otp_code", "user", "driver", "receipt"], "why": "two were missing"},
        )
        assert "Grid rebuilt" in said
        assert len(state.grid.cells) != before or "driver" in state.grid.objects
        assert "driver" in state.grid.objects

    def test_a_correction_sticks_for_later_planning(self, contract, where):
        """And an asserted object keeps its cells even where no tool name matches it.

        Pruning on tool-name matching would make a correction shrink the grid, which is the
        opposite of what correcting it is for: the corrector read the source, this did not.
        """
        server, _ = grid_tools(contract, where)
        call(server, "set_objects", {"objects": ["invoice"]})
        assert "invoice" in call(server, "plan_suite", {"count": 5})

    def test_an_empty_correction_is_refused(self, contract, where):
        server, _ = grid_tools(contract, where)
        assert failed(server, "set_objects", {"objects": []})


class TestPlanning:
    @pytest.mark.parametrize("count", [1, 7, 40])
    def test_a_plan_names_one_coordinate_per_scenario(self, contract, where, count):
        server, _ = grid_tools(contract, where)
        said = call(server, "plan_suite", {"count": count})
        assert said.startswith(f"{count} scenarios planned:")
        assert "because:" in said

    def test_a_nonsense_count_is_refused_rather_than_guessed(self, contract, where):
        server, _ = grid_tools(contract, where)
        assert failed(server, "plan_suite", {"count": 0})
        assert failed(server, "plan_suite", {"count": "lots"})


class TestChangingASuiteThatAlreadyExists:
    def saved(self, where: Path, names: list[str]) -> None:
        write_scenarios(
            [
                Scenario(
                    name=name,
                    use_case="Cancel a ride",
                    branch="the ordinary path",
                    tests="it is cancelled",
                    persona=Persona(name="Dana"),
                )
                for name in names
            ],
            where,
        )

    def test_nothing_saved_reads_as_nothing_rather_than_an_error(self, contract, where):
        server, _ = grid_tools(contract, where)
        assert "No scenarios" in call(server, "list_scenarios")
        assert "nothing is covered" in call(server, "show_coverage").lower()

    def test_the_saved_suite_is_what_gets_listed(self, contract, where):
        self.saved(where, ["cancel-ride__baseline", "diagnose-fare__evasive"])
        server, _ = grid_tools(contract, where)
        said = call(server, "list_scenarios")
        assert "2 saved" in said
        assert "cancel-ride__baseline" in said and "diagnose-fare__evasive" in said

    def test_coverage_is_recovered_from_names_on_disk(self, contract, where):
        """A suite written last week has to report the same way as one written a minute ago."""
        self.saved(where, ["cancel-ride__baseline", "diagnose-fare__evasive"])
        server, _ = grid_tools(contract, where)
        said = call(server, "show_coverage")
        assert "covering 2 of" in said
        assert "state: 1/" in said
        assert "cells with nothing on them" in said

    def test_a_scenario_named_off_the_grid_is_called_out_not_counted(self, contract, where):
        self.saved(where, ["scenario_1", "edge_case_a"])
        server, _ = grid_tools(contract, where)
        said = call(server, "show_coverage")
        assert "covering 0 of" in said
        assert "does not match a cell" in said

    def test_expanding_copies_the_suite_across_callers_and_saves(self, contract, where):
        self.saved(where, ["cancel-ride__baseline"])
        server, _ = grid_tools(contract, where)
        said = call(server, "expand_suite")
        assert "no model call" in said
        from fi.alk.harness.scenario_tools import load_scenarios

        grown = load_scenarios(where)
        assert len(grown) > 1
        assert any(one.name.startswith("cancel-ride__") and one.name != "cancel-ride__baseline" for one in grown)

    def test_expanding_respects_a_total(self, contract, where):
        self.saved(where, ["cancel-ride__baseline", "diagnose-fare__baseline"])
        server, _ = grid_tools(contract, where)
        call(server, "expand_suite", {"total": 6})
        from fi.alk.harness.scenario_tools import load_scenarios

        assert len(load_scenarios(where)) == 6

    def test_expanding_nothing_is_refused_with_a_reason(self, contract, where):
        server, _ = grid_tools(contract, where)
        assert failed(server, "expand_suite")


class TestUngatedStageStillTalksToTheOperator:
    """The scenarios stage runs ungated so it can read the agent's own repository.

    Ungated must not mean unattended. In a conversation there is a person on the other side, and
    a stage that can no longer ask them anything has lost the reason it stays open.
    """

    def test_an_ungated_spec_routes_the_question_and_allows_the_rest(self):
        import asyncio

        from fi.alk.harness.backends.claude import _ask_only

        seen: list[str] = []

        async def ask(name, payload, context):
            seen.append(name)
            return "answered"

        gate = _ask_only(ask)
        assert asyncio.run(gate("AskUserQuestion", {}, None)) == "answered"
        assert seen == ["AskUserQuestion"]
        # Everything else is permitted rather than routed, which is what ungated means.
        allowed = asyncio.run(gate("Bash", {"command": "ls"}, None))
        assert type(allowed).__name__ == "PermissionResultAllow"
        assert seen == ["AskUserQuestion"]

    def test_the_scenarios_stage_is_ungated_and_carries_the_host_tools(
        self, contract, tmp_path, monkeypatch
    ):
        from fi.alk.harness import scenarios

        # The stage reads the built world to ground its prompt, which is not what is under test.
        monkeypatch.setattr(scenarios, "world_summary", lambda _root: "(no world here)")
        stage, _ = scenarios.open_stage(contract, out=tmp_path / "s", wanted=5)
        spec = stage._spec
        assert spec.gated is False
        assert "Bash" in spec.builtins and "Read" in spec.builtins
        # And it has both tool servers: writing scenarios, and seeing the grid.
        assert {"scenarios", "grid"} <= set(spec.servers)


class TestAScenarioMustMeanWhatItsNameClaims:
    """A scenario named for an adversarial condition is counted as covering it.

    So the name is a claim about the world, not a label. An impersonation test where the caller
    really is the account holder is an ordinary call wearing a dangerous name, and it is worse
    than having no such test: the coverage report then says the case is handled. This was found
    on a real run, where a scenario named for impersonation passed all three gates while its
    branch read "books a ride, then cancels after confirming" and its setup was empty.
    """

    def refused(self, name: str, setup: str = "") -> list[str]:
        from fi.alk.harness.scenario import Scenario
        from fi.alk.harness.scenario_tools import unbacked_condition_problems

        return unbacked_condition_problems(Scenario(name=name, setup_code=setup))

    def test_claiming_a_world_backed_condition_without_seeding_it_is_refused(self):
        said = self.refused("cancel-ride__impersonation")
        assert said and "nothing in the world makes that true" in said[0]
        # And the reason comes from the axis file, so it says what to seed rather than just no.
        assert "not who they claim to be" in said[0]

    def test_seeding_it_settles_the_objection(self):
        assert self.refused("cancel-ride__impersonation", "def setup(world):\n    pass\n") == []

    def test_the_free_caller_dials_are_untouched(self):
        """A rushed or second-language caller needs no world change, so requiring one is wrong."""
        for name in ("cancel-ride__rushed", "cancel-ride__second-language", "cancel-ride__senior"):
            assert self.refused(name) == []

    def test_a_baseline_scenario_needs_no_setup(self):
        assert self.refused("cancel-ride__baseline") == []

    def test_every_world_backed_setting_is_held_to_it(self):
        for setting in ("impersonation", "emergency", "fraud"):
            assert self.refused(f"execute-payment__{setting}"), setting

    def test_a_setting_name_inside_another_word_does_not_trigger_it(self):
        """`second-language` must never read as some other axis value by substring."""
        assert self.refused("explain-fare__second-language") == []
