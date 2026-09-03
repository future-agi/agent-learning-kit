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
from fi.alk.harness.scenariogen.suite import write_scenarios


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
        assert f"A suggested {count}." in said
        assert "because:" in said

    def test_the_plan_presents_itself_as_a_suggestion(self, contract, where):
        """Choosing what to test is the model's call. This is arithmetic over the grid and
        knows nothing about which of the agent's operations are dangerous in practice."""
        server, _ = grid_tools(contract, where)
        said = call(server, "plan_suite", {"count": 5})
        assert "Yours to change" in said

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
        from fi.alk.harness.scenariogen.suite import load_scenarios

        grown = load_scenarios(where)
        assert len(grown) > 1
        assert any(one.name.startswith("cancel-ride__") and one.name != "cancel-ride__baseline" for one in grown)

    def test_expanding_respects_a_total(self, contract, where):
        self.saved(where, ["cancel-ride__baseline", "diagnose-fare__baseline"])
        server, _ = grid_tools(contract, where)
        call(server, "expand_suite", {"total": 6})
        from fi.alk.harness.scenariogen.suite import load_scenarios

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

    def refused(self, name: str, setup: str = "", ready: str = "") -> list[str]:
        from fi.alk.harness.scenario import Scenario
        from fi.alk.harness.scenario_tools import unbacked_condition_problems

        return unbacked_condition_problems(
            Scenario(name=name, setup_code=setup, ready_code=ready)
        )

    def test_claiming_a_world_backed_condition_without_grounding_it_is_refused(self):
        said = self.refused("cancel-ride__impersonation")
        assert said and "nothing ties it to the world" in said[0]
        # And the reason comes from the axis file, so it says what to ground rather than just no.
        assert "not who they claim to be" in said[0]

    def test_asserting_the_condition_grounds_it_just_as_well_as_seeding_it(self):
        """Found on a real run: the base world already held a suspended account.

        A scenario that finds the condition in the agent's own starting data and asserts it in
        ready_code is better grounded than one that writes its own, not worse. Demanding
        setup_code specifically refused correct scenarios.
        """
        asserts = (
            'def ready(world):\n'
            '    u = next(x for x in world.state()["users"] if x["status"] == "suspended")\n'
            '    return None if u else "no suspended user"\n'
        )
        assert self.refused("execute-payment__fraud", ready=asserts) == []

    def test_seeding_it_settles_the_objection(self):
        seeded = 'def setup(world):\n    world.rows("users")[0]["phone"] = "+15550000"\n'
        assert self.refused("cancel-ride__impersonation", seeded) == []

    def test_the_free_caller_dials_are_untouched(self):
        """A rushed or second-language caller needs no world change, so requiring one is wrong."""
        for name in ("cancel-ride__rushed", "cancel-ride__second-language", "cancel-ride__senior"):
            assert self.refused(name) == []

    def test_a_baseline_scenario_needs_no_setup(self):
        assert self.refused("cancel-ride__baseline") == []

    def test_every_world_backed_setting_is_held_to_it(self):
        for setting in ("impersonation", "fraud"):
            assert self.refused(f"execute-payment__{setting}"), setting

    def test_a_prompt_side_twist_needs_no_world_at_all(self):
        """An emergency is reported by the caller, not written in a table, and neither is an
        injection. Holding those to a world condition would demand fiction."""
        for setting in ("emergency", "injection"):
            assert self.refused(f"handoff-caller__{setting}") == [], setting

    def test_a_setting_name_inside_another_word_does_not_trigger_it(self):
        """`second-language` must never read as some other axis value by substring."""
        assert self.refused("explain-fare__second-language") == []

    def test_placeholder_setup_does_not_satisfy_the_claim(self):
        """The folder writer puts a docstring-only setup.py beside every scenario.

        A scenario read back from disk therefore carries it, and treating that as seeding would
        let the placeholder satisfy the very check it fails to satisfy.
        """
        stub = 'def setup(world):\n    """This scenario runs on the base world unchanged."""\n'
        assert self.refused("cancel-ride__impersonation", stub)
        assert self.refused("cancel-ride__impersonation", "def setup(world):\n    pass\n")
        real = 'def setup(world):\n    world.rows("users")[0]["phone"] = "+15550000"\n'
        assert self.refused("cancel-ride__impersonation", real) == []


class TestAFanOutCanActuallySave:
    """A delegated run accepted 50 scenarios and wrote none.

    Each writer got its own tool server with its own `kept` list, the stage saved from a
    different list, and `save_scenarios` reported "Saved 0 scenarios" after fifty had passed all
    three gates. Nothing below the delegation threshold exercised this, because there a single
    session both accepts and saves. The property that matters is identity: every server the stage
    builds must append into the very list the stage saves from.
    """

    def test_share_hands_back_the_same_list_not_a_copy(self, contract, where):
        from fi.alk.harness.scenario_tools import scenario_tools

        mine: list = []
        _, kept = scenario_tools(contract, where, where, wanted=0, share=mine)
        assert kept is mine, "share must not copy, or the caller cannot see what was accepted"

    def test_start_from_still_copies(self, contract, where):
        """The other case is unchanged: a writer seeded from disk must not alias it."""
        from fi.alk.harness.scenario_tools import scenario_tools

        seed: list = []
        _, kept = scenario_tools(contract, where, where, wanted=0, start_from=seed)
        assert kept is not seed

    def test_the_stage_and_its_writers_share_one_list(self, contract, where, monkeypatch):
        from fi.alk.harness import scenarios
        from fi.alk.harness.scenario_tools import scenario_tools

        monkeypatch.setattr(scenarios, "world_summary", lambda _root: "(no world here)")
        seen: list = []
        real = scenario_tools

        def spy(*args, **rest):
            server, kept = real(*args, **rest)
            seen.append(kept)
            return server, kept

        monkeypatch.setattr(scenarios, "scenario_tools", spy)
        # Above the delegation threshold, so writers are declared.
        scenarios.open_stage(contract, out=where, wanted=50)
        assert len(seen) >= 2, "expected a server for the stage and one for its writers"
        assert all(one is seen[0] for one in seen), "each server built its own list; a save would lose the rest"

    def test_a_writer_cannot_drop_what_it_did_not_write(self, contract, where):
        """Dropping rewrites the index and deletes folders, so it belongs to the saving session.

        With one shared list a writer holding `drop_scenario` could clear a sibling's proved work
        out from under the stage, and `drop_scenario('*')` would empty the suite on disk mid-run.
        """
        from fi.alk.harness.scenario_tools import scenario_tools

        writer, _ = scenario_tools(contract, where, where, wanted=0, can_save=False, share=[])
        offered = {spec.name for spec in writer.tools}
        assert "drop_scenario" not in offered
        assert "save_scenarios" not in offered
        assert "submit_scenario" in offered, "a writer must still be able to contribute"

    def test_a_saving_session_keeps_it(self, contract, where):
        from fi.alk.harness.scenario_tools import scenario_tools

        stage, _ = scenario_tools(contract, where, where, wanted=10)
        assert "drop_scenario" in {spec.name for spec in stage.tools}

    def test_what_a_writer_accepts_is_what_the_stage_saves(self, contract, where, monkeypatch):
        """The whole failure, end to end: accept through a writer, save through the stage.

        The gates are not the subject here and are exercised elsewhere; what broke was everything
        after them, so this puts a proved scenario into the writers' list the way an acceptance
        does and asks the stage to save.
        """
        from fi.alk.harness import scenarios as stage_module
        from fi.alk.harness.scenario_tools import scenario_tools

        monkeypatch.setattr(stage_module, "world_summary", lambda _root: "(no world here)")
        shared: list = []
        stage, kept = scenario_tools(contract, where, where, wanted=1, share=shared)
        writers = stage_module.writer_workers(contract, where, share=shared)
        writer = writers[stage_module.WRITER].servers[stage_module.SCENARIO_SERVER]

        # What accept_scenario does once all three gates pass: the writer's list gets it.
        writers_list = next(iter(writers.values())).servers[stage_module.SCENARIO_SERVER]
        assert writers_list is writer
        shared.append(Scenario(name="only-one", setup_code="", ready_code=""))

        save = next(spec for spec in stage.tools if spec.name == "save_scenarios")
        text = str(asyncio.run(save.handler({})))
        assert "Saved 1 scenario" in text, f"the stage saved nothing a writer produced: {text}"
        assert (where / "scenarios" / "only-one").is_dir()
        assert kept is shared


class TestTheCanvasLoopEndToEnd:
    """Plan, claim, write, fold, and again. The loop the stage is now built around.

    Exercised through the real tools rather than the objects underneath, because every defect
    this run has produced lived in the wiring: a list that was copied instead of shared, writers
    that were never waited for, a reader that rewrote the world. The model is not in this test;
    everything it would call is.
    """

    def canvas_of(self, server, cells):
        return call(
            server,
            "record_canvas",
            {
                "target": 6,
                "axes": [{"name": "record_state", "levels": ["a", "b", "c", "d"]}],
                "themes": [{"id": "TH01", "name": "Spine"}, {"id": "TH02", "name": "Rules"}],
                "angles": [
                    {"id": "TH01-01", "theme": "TH01", "cell": cells[0],
                     "angle": "a stored record cannot be matched against the identifying details somebody supplied during the exchange",
                     "why_hard": "data:missing", "expects": "ask", "want": 3,
                     "varies_by": ["record_state"]},
                    {"id": "TH02-01", "theme": "TH02", "cell": cells[1],
                     "angle": "a cost must be disclosed clearly and explicitly agreed before the irreversible step proceeds",
                     "why_hard": "rule:fee", "expects": "ask", "want": 3,
                     "varies_by": ["record_state"]},
                ],
            },
        )

    def test_a_plan_is_recorded_and_read_back(self, contract, where):
        server, state = grid_tools(contract, where)
        cells = sorted({one.name for one in state.grid.cells})[:2]
        said = self.canvas_of(server, cells)
        assert "6 scenarios planned:" in said
        assert "2 buckets over" in said
        assert (where / "blueprint.json").exists()
        assert "0 written of 6 planned" in call(server, "show_canvas")

    def test_a_slice_claims_its_angles_so_nothing_is_written_twice(self, contract, where):
        server, state = grid_tools(contract, where)
        cells = sorted({one.name for one in state.grid.cells})[:2]
        self.canvas_of(server, cells)
        first = call(server, "claim_slice", {"scenarios": 6, "writer": "w1"})
        assert "TH01-01" in first and "TH02-01" in first
        assert "Nothing is open" in call(server, "claim_slice", {"writer": "w2"})

    def test_what_a_writer_claims_is_checked_against_disk(self, contract, where):
        """The writer says three; the disk says none; the disk wins and the gap is named."""
        server, state = grid_tools(contract, where)
        cells = sorted({one.name for one in state.grid.cells})[:2]
        self.canvas_of(server, cells)
        call(server, "claim_slice", {"writer": "w1"})
        said = call(
            server,
            "fold_return",
            {"returns": [{"angle_id": "TH01-01", "wrote": 3, "short": "covered all three"}]},
        )
        assert "0/3 on disk" in said
        assert "writer said 3" in said and "does not match" in said

    def test_a_part_filled_angle_comes_back_for_somebody_else(self, contract, where):
        server, state = grid_tools(contract, where)
        cells = sorted({one.name for one in state.grid.cells})[:2]
        self.canvas_of(server, cells)
        call(server, "claim_slice", {"writer": "w1"})
        call(server, "fold_return", {"returns": [{"angle_id": "TH01-01", "wrote": 0}]})
        assert "TH01-01" in call(server, "claim_slice", {"writer": "w2"})

    def test_an_angle_a_writer_says_is_impossible_is_not_dealt_again(self, contract, where):
        server, state = grid_tools(contract, where)
        cells = sorted({one.name for one in state.grid.cells})[:2]
        self.canvas_of(server, cells)
        call(server, "claim_slice", {"writer": "w1"})
        call(
            server,
            "fold_return",
            {"returns": [{"angle_id": "TH01-01", "blocked_reason": "no second case exists"}]},
        )
        assert "TH01-01" not in call(server, "claim_slice", {"writer": "w2"})

    def test_replanning_keeps_what_writers_already_did(self, contract, where):
        """Re-recording is how a plan is corrected mid-run; it must not erase the ledger."""
        server, state = grid_tools(contract, where)
        cells = sorted({one.name for one in state.grid.cells})[:2]
        self.canvas_of(server, cells)
        state.canvas.named("TH01-01").done = 2
        self.canvas_of(server, cells)
        assert state.canvas.named("TH01-01").done == 2

    def test_a_writer_can_open_buckets_nobody_planned(self, contract, where):
        """The plan is a starting partition, not an exhaustive list.

        A writer works inside one bucket with the source open, which is the only place a case
        the planner could not see from outside gets noticed. With nowhere to put it, the writer
        drops it or crams it into the bucket it was given, and the canvas goes on claiming a
        completeness it never had.
        """
        server, state = grid_tools(contract, where)
        cells = sorted({one.name for one in state.grid.cells})[:2]
        self.canvas_of(server, cells)
        before = state.canvas.planned
        said = call(
            server,
            "fold_return",
            {
                "returns": [{"angle_id": "TH01-01", "wrote": 0, "short": "found more here"}],
                "found": [
                    {"theme": "TH01", "cell": cells[0], "angle": "a published rate changes between the quoted figure and the confirmation step itself",
                     "why_hard": "rule:surge", "want": 2}
                ],
            },
        )
        assert "1 buckets opened that nobody planned" in said
        assert state.canvas.planned == before + 2

    def test_a_found_bucket_gets_dealt_like_any_other(self, contract, where):
        server, state = grid_tools(contract, where)
        cells = sorted({one.name for one in state.grid.cells})[:2]
        self.canvas_of(server, cells)
        call(server, "claim_slice", {"writer": "w1"})
        call(
            server,
            "fold_return",
            {
                "returns": [
                    {"angle_id": "TH01-01", "blocked_reason": "done here"},
                    {"angle_id": "TH02-01", "blocked_reason": "done here"},
                ],
                "found": [
                    {"theme": "TH02", "cell": cells[1], "angle": "the assigned resource has already arrived by the moment the cancellation request reaches",
                     "why_hard": "state:arrived", "want": 3}
                ],
            },
        )
        assert "TH02-F01" in call(server, "claim_slice", {"writer": "w2"})

    def test_writer_ids_cannot_collide_with_planned_ones(self, contract, where):
        server, state = grid_tools(contract, where)
        cells = sorted({one.name for one in state.grid.cells})[:2]
        self.canvas_of(server, cells)
        for _ in range(3):
            call(
                server,
                "fold_return",
                {
                    "returns": [],
                    "found": [{"theme": "TH01", "cell": cells[0], "angle": "a further difficult case discovered while reading the source, never planned for originally"}],
                },
            )
        ids = [one.id for one in state.canvas.angles]
        assert len(ids) == len(set(ids))

    def test_a_writer_cannot_reach_the_canvas_so_the_stage_must_transcribe(
        self, contract, where, monkeypatch
    ):
        """The writer has no canvas tools, so telling it to call one is telling it nothing.

        Discoveries travel as text in the writer's reply and the stage puts them into the canvas.
        This pins the split, because the skill on the writer's side once told it to call a tool
        that was never on its server.
        """
        from fi.alk.harness import scenarios

        monkeypatch.setattr(scenarios, "world_summary", lambda _root: "(no world here)")
        stage, _ = scenarios.open_stage(contract, out=where, wanted=50)
        writer = next(iter(stage._spec.workers.values()))
        writer_tools = {one.name for server in writer.servers.values() for one in server.tools}
        stage_tools = {one.name for server in stage._spec.servers.values() for one in server.tools}

        assert "submit_scenario" in writer_tools
        for canvas_tool in ("fold_return", "claim_slice", "record_canvas", "show_canvas"):
            assert canvas_tool not in writer_tools, f"{canvas_tool} is not the writer's to call"
            assert canvas_tool in stage_tools
        # And the writer is told to report them instead, since it cannot record them.
        assert "report" in writer.instructions.lower()
        assert "did not ask for" in writer.instructions

    def test_a_plan_can_be_built_up_a_theme_at_a_time(self, contract, where):
        """A canvas for a large suite is too much to emit in one response.

        A model that tries either runs long or truncates, and either way the whole plan is lost.
        So recording adds rather than replaces, and the earlier instalments have to survive.
        """
        server, state = grid_tools(contract, where)
        cells = sorted({one.name for one in state.grid.cells})[:3]
        call(server, "record_canvas", {
            "target": 12,
            "themes": [{"id": "TH01", "name": "First"}],
            "axes": [{"name": "s.market", "levels": ["a", "b", "c", "d"]}],
            "angles": [{"id": "TH01-01", "theme": "TH01", "cell": cells[0],
                        "angle": "a stored record is missing the particular field that the following step depends upon entirely", "why_hard": "data:x", "expects": "ask", "want": 2,
                        "varies_by": ["s.market"]}],
        })
        said = call(server, "record_canvas", {
            "themes": [{"id": "TH02", "name": "Second"}],
            "angles": [{"id": "TH02-01", "theme": "TH02", "cell": cells[1],
                        "angle": "two stored records resemble each other closely enough that choosing wrongly between them matters", "why_hard": "ambiguity:x", "expects": "ask", "want": 3,
                        "varies_by": ["s.market"]}],
        })
        assert "2 buckets" in said
        assert {one.id for one in state.canvas.angles} == {"TH01-01", "TH02-01"}
        assert {one.id for one in state.canvas.themes} == {"TH01", "TH02"}
        assert [one.name for one in state.canvas.axes] == ["s.market"]

    def test_replacing_is_possible_but_has_to_be_asked_for(self, contract, where):
        server, state = grid_tools(contract, where)
        cells = sorted({one.name for one in state.grid.cells})[:3]
        call(server, "record_canvas", {
            "target": 12,
            "themes": [{"id": "TH01", "name": "First"}],
            "angles": [{"id": "TH01-01", "theme": "TH01", "cell": cells[0],
                        "angle": "a stored record is missing the particular field that the following step depends upon entirely", "why_hard": "data:x", "expects": "ask"}],
        })
        call(server, "record_canvas", {
            "replace": True,
            "themes": [{"id": "TH02", "name": "Second"}],
            "angles": [{"id": "TH02-01", "theme": "TH02", "cell": cells[1],
                        "angle": "an entirely separate plan covering unrelated stored records and their awkward states", "why_hard": "data:y", "expects": "ask"}],
        })
        assert {one.id for one in state.canvas.angles} == {"TH02-01"}

    def test_an_instalment_keeps_progress_already_made(self, contract, where):
        server, state = grid_tools(contract, where)
        cells = sorted({one.name for one in state.grid.cells})[:3]
        call(server, "record_canvas", {
            "target": 12,
            "axes": [{"name": "s.market", "levels": ["a", "b", "c", "d"]}],
            "themes": [{"id": "TH01", "name": "First"}],
            "angles": [{"id": "TH01-01", "theme": "TH01", "cell": cells[0],
                        "angle": "a stored record is missing the particular field that the following step depends upon entirely", "why_hard": "data:x", "expects": "ask", "want": 3,
                        "varies_by": ["s.market"]}],
        })
        state.canvas.named("TH01-01").done = 2
        call(server, "record_canvas", {
            "themes": [{"id": "TH02", "name": "Second"}],
            "angles": [{"id": "TH02-01", "theme": "TH02", "cell": cells[1],
                        "angle": "two stored records resemble each other closely enough that choosing wrongly between them matters", "why_hard": "ambiguity:x", "expects": "ask"}],
        })
        assert state.canvas.named("TH01-01").done == 2

    def test_progress_is_counted_by_checking_named_scenarios_against_disk(
        self, contract, where
    ):
        """The writer says what it wrote; each name is only counted if it is really there.

        The earlier approach looked for the bucket id inside a free-text field that nothing tells
        writers to fill. It would have matched nothing, so every bucket would have looked unfilled
        while its scenarios sat on disk, and a whole run would have ended reporting everything
        blocked.
        """
        from fi.alk.harness.scenario import Scenario
        from fi.alk.harness.scenariogen.suite import write_scenarios

        server, state = grid_tools(contract, where)
        cells = sorted({one.name for one in state.grid.cells})[:2]
        self.canvas_of(server, cells)
        write_scenarios(
            [Scenario(name="really-here-1"), Scenario(name="really-here-2")], where, None
        )
        said = call(
            server,
            "fold_return",
            {
                "returns": [
                    {
                        "angle_id": "TH01-01",
                        "wrote": 3,
                        "names": ["really-here-1", "really-here-2", "never-written"],
                    }
                ]
            },
        )
        assert "2/3 on disk" in said
        assert "1 named but not on disk: never-written" in said
        assert state.canvas.named("TH01-01").done == 2


def test_the_stage_s_count_wins_over_the_target_the_model_types(contract, where, tmp_path):
    """Every whole-plan refusal guards on target, so a lowballed or omitted one disarmed all of
    them. The stage knows what was asked for; the model does not get to lower it."""
    import asyncio

    from fi.alk.harness.blueprint import load as load_canvas
    from fi.alk.harness.grid_tools import grid_tools

    server, _state = grid_tools(contract, tmp_path, wanted=500)
    record = next(one for one in server.tools if one.name == "record_canvas")
    asyncio.run(
        record.handler(
            {
                "target": 20,
                "themes": [{"id": "TH01", "name": "T", "why": "w"}],
                "buckets": [
                    {
                        "id": "A1",
                        "theme": "TH01",
                        "cell": "retrieve-ride",
                        "angle": (
                            "someone returning is matched against two stored records that look "
                            "alike and the wrong one is chosen first"
                        ),
                        "why_hard": "data:two-records",
                        "want": 1,
                    }
                ],
            }
        )
    )

    assert load_canvas(tmp_path).target == 500


def test_folding_credits_journalled_scenarios_not_only_folders(contract, tmp_path):
    """A delegated writer cannot write folders - saving would delete its siblings' work - so it
    journals instead. Checking folders alone found nothing, credited nothing, and blocked
    buckets whose scenarios existed all along."""
    import asyncio

    from fi.alk.harness.grid_tools import grid_tools
    from fi.alk.harness.scenario import Scenario
    from fi.alk.harness.scenariogen.suite import record_written

    server, state = grid_tools(contract, tmp_path, wanted=200)
    record = next(one for one in server.tools if one.name == "record_canvas")
    asyncio.run(
        record.handler(
            {
                "themes": [{"id": "TH01", "name": "T", "why": "w"}],
                "buckets": [
                    {
                        "id": "A1",
                        "theme": "TH01",
                        "cell": "retrieve-ride",
                        "angle": (
                            "someone returning is matched against two stored records that look "
                            "alike and the wrong one is chosen first"
                        ),
                        "why_hard": "data:two-records",
                        "want": 2,
                        "varies_by": ["record_state"],
                    }
                ],
                "axes": [
                    {
                        "name": "record_state",
                        "levels": ["one match", "two that look alike"],
                        "why": "which record is chosen changes the answer",
                    }
                ],
            }
        )
    )
    # Proved and journalled, never written as a folder.
    record_written(
        [Scenario(name="retrieve-ride__one"), Scenario(name="retrieve-ride__two")], tmp_path
    )

    fold = next(one for one in server.tools if one.name == "fold_return")
    said = asyncio.run(
        fold.handler(
            {
                "returns": [
                    {
                        "angle_id": "A1",
                        "wrote": 2,
                        "names": ["retrieve-ride__one", "retrieve-ride__two"],
                        "short": "covered both",
                    }
                ]
            }
        )
    )

    assert not said.get("is_error"), said
    assert state.canvas.named("A1").done == 2
    assert state.canvas.named("A1").state == "done"


def test_folding_recovers_work_when_the_reported_names_are_wrong(contract, tmp_path):
    """The writer names its own scenarios and the report passes through another model, so names
    come back invented. Measured: every fold reported names that were nowhere on disk."""
    import asyncio

    from fi.alk.harness.grid_tools import grid_tools
    from fi.alk.harness.scenario import Scenario
    from fi.alk.harness.scenariogen.suite import record_written

    server, state = grid_tools(contract, tmp_path, wanted=200)
    record = next(one for one in server.tools if one.name == "record_canvas")
    asyncio.run(
        record.handler(
            {
                "themes": [{"id": "TH01", "name": "T", "why": "w"}],
                "buckets": [
                    {
                        "id": "A1",
                        "theme": "TH01",
                        "cell": "retrieve-ride",
                        "angle": (
                            "someone returning is matched against two stored records that look "
                            "alike and the wrong one is chosen first"
                        ),
                        "why_hard": "data:two-records",
                        "want": 2,
                        "varies_by": ["record_state"],
                    }
                ],
                "axes": [
                    {
                        "name": "record_state",
                        "levels": ["one match", "two that look alike"],
                        "why": "which record is chosen changes the answer",
                    }
                ],
            }
        )
    )
    record_written(
        [Scenario(name="retrieve-ride__real-one"), Scenario(name="retrieve-ride__real-two")],
        tmp_path,
    )

    fold = next(one for one in server.tools if one.name == "fold_return")
    said = asyncio.run(
        fold.handler(
            {"returns": [{"angle_id": "A1", "wrote": 2, "names": ["retrieve-ride__invented"]}]}
        )
    )

    # The work is credited from the cell, and the disagreement is still reported.
    assert state.canvas.named("A1").done == 2
    assert "not on disk" in said["content"][0]["text"]


def test_a_stage_with_writers_cannot_submit_scenarios_itself(contract, tmp_path):
    """Offered both, the model does the work itself: measured on a 200 run, the stage made 59 of
    the submissions and dispatched four writers, then spent its turns proving instead of dealing.
    The same argument already withholds generate_suite."""
    from fi.alk.harness.scenario_tools import scenario_tools

    delegating, _ = scenario_tools(contract, tmp_path, tmp_path, wanted=200, delegates=True)
    alone, _ = scenario_tools(contract, tmp_path, tmp_path, wanted=200, delegates=False)

    assert "submit_scenario" not in {one.name for one in delegating.tools}
    assert "submit_scenario" in {one.name for one in alone.tools}
    # It still folds, saves and reads the world; only writing is taken away.
    assert {"save_scenarios", "inspect_world", "try_calls"} <= {
        one.name for one in delegating.tools
    }


def test_a_small_ask_keeps_its_own_pen(contract, tmp_path, monkeypatch):
    """Withholding writing must not reach a stage that has nobody to delegate to. Asking for five
    or ten scenarios declares no writers, so the stage writes them itself as it always did."""
    from fi.alk.harness import scenarios as stage_module

    # A worker's prompt embeds the seeded world; this test is about the tool list beside it.
    monkeypatch.setattr(stage_module, "world_summary", lambda _where: "a world")

    for wanted, expected in ((5, True), (10, True), (19, True), (20, False), (200, False)):
        workers = (
            stage_module.writer_workers(contract, tmp_path)
            if wanted >= stage_module.FEWEST_WORTH_DELEGATING
            else {}
        )
        server, _ = stage_module.scenario_tools(
            contract, tmp_path, tmp_path, wanted=wanted, delegates=bool(workers)
        )
        has_pen = "submit_scenario" in {one.name for one in server.tools}
        assert has_pen is expected, f"wanted={wanted} should write itself: {expected}"
