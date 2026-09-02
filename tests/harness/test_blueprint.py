"""The plan for a suite, and the one thing it exists to catch.

A blueprint is cheap to change and a suite is not: every duplicate that survives planning costs
a proof, a folder and a slot that a different scenario should have had. So the cases worth
pinning are the ones where a plan looks fine and is not: the same situation reworded, a plan that
quietly names a cell nobody has, and a cut that hands one writer the whole of one cell.
"""

from __future__ import annotations

import pytest

from fi.alk.harness.blueprint import Blueprint, Entry, load
from fi.alk.harness.contract import AgentContract, ToolSpec


@pytest.fixture()
def contract():
    return AgentContract(
        agent="ride", modality="voice",
        tools=[ToolSpec(name="get_rides"), ToolSpec(name="cancel_ride")],
        data_schema={"rides": {}, "users": {}, "fares": {}},
    )


@pytest.fixture()
def where(tmp_path):
    return tmp_path


def plan(*rows: tuple[str, str, str], wanted: int = 0) -> Blueprint:
    return Blueprint(
        wanted=wanted,
        entries=[Entry(name=n, cell=c, situation=s) for n, c, s in rows],
    )


class TestSayingTheSameThingTwice:
    def test_a_reworded_situation_is_caught(self):
        held = plan(
            ("a", "retrieve-ride", "caller cannot find the booking they made this morning"),
            ("b", "retrieve-ride", "the booking made this morning cannot be found by the caller"),
        )
        assert [one[:2] for one in held.duplicates()] == [("a", "b")]

    def test_genuinely_different_situations_in_one_cell_are_left_alone(self):
        held = plan(
            ("a", "retrieve-ride", "caller cannot find the booking they made this morning"),
            ("b", "retrieve-ride", "wants the fare breakdown for a trip that crossed a surge boundary"),
        )
        assert held.duplicates() == []

    def test_two_cells_may_share_a_situation(self):
        """Retrieving and cancelling both start from a caller who cannot find their booking.

        Comparing across cells would call that a duplicate and push the plan into making cells
        artificially unlike each other, which is not what variety means here.
        """
        held = plan(
            ("a", "retrieve-ride", "caller cannot find the booking they made this morning"),
            ("d", "cancel-ride", "caller cannot find the booking they made this morning"),
        )
        assert held.duplicates() == []

    def test_padding_a_situation_does_not_make_it_a_new_one(self):
        """Scored against the smaller line, so restating it at greater length still collides."""
        held = plan(
            ("a", "cancel-ride", "card was declined at checkout"),
            ("b", "cancel-ride", "the card was unfortunately declined at checkout again today"),
        )
        assert held.duplicates()


class TestWhatAPlanMustSayBeforeAnyoneWritesFromIt:
    def test_a_cell_nobody_has_is_reported(self):
        held = plan(("a", "invent-thing", "wants something the agent cannot do"))
        said = " ".join(held.problems({"retrieve-ride"}))
        assert "not on the grid" in said

    def test_a_situation_too_thin_to_write_from_is_reported(self):
        held = plan(("a", "retrieve-ride", "a ride"))
        assert "say too little" in " ".join(held.problems({"retrieve-ride"}))

    def test_repeated_names_are_reported(self):
        held = plan(
            ("a", "retrieve-ride", "caller cannot find the booking from this morning"),
            ("a", "retrieve-ride", "wants a fare breakdown across a surge boundary"),
        )
        assert "more than once" in " ".join(held.problems({"retrieve-ride"}))

    def test_an_empty_plan_is_a_problem_not_a_crash(self):
        assert Blueprint().problems({"retrieve-ride"}) == ["the blueprint is empty"]

    def test_one_duplicate_pair_reads_as_one(self):
        held = plan(
            ("a", "retrieve-ride", "caller cannot find the booking they made this morning"),
            ("b", "retrieve-ride", "the booking made this morning cannot be found by the caller"),
        )
        assert "1 pair describe" in " ".join(held.problems({"retrieve-ride"}))


class TestCuttingItUp:
    def test_a_writer_is_not_handed_one_whole_cell(self):
        """Entries arrive grouped by cell, so a contiguous cut gives one writer one cell.

        That writer then has to invent the whole of that cell's variety alone, which is the
        position the blueprint exists to remove.
        """
        held = plan(
            *[(f"r{i}", "retrieve-ride", f"situation number {i} about finding a booking") for i in range(4)],
            *[(f"c{i}", "cancel-ride", f"situation number {i} about calling off a trip") for i in range(4)],
        )
        cuts = held.slices(4)
        assert all(len({one.cell for one in cut}) > 1 for cut in cuts), (
            "at least one writer was handed a single cell"
        )

    def test_every_entry_is_dealt_exactly_once(self):
        held = plan(*[(f"s{i}", "retrieve-ride", f"situation number {i} about a booking") for i in range(7)])
        dealt = [one.name for cut in held.slices(3) for one in cut]
        assert sorted(dealt) == sorted(one.name for one in held.entries)


class TestItSurvivesABadFile:
    def test_a_missing_plan_reads_as_empty(self, tmp_path):
        assert load(tmp_path).entries == []

    def test_a_damaged_plan_reads_as_empty_rather_than_raising(self, tmp_path):
        (tmp_path / "blueprint.json").write_text("{not json", encoding="utf-8")
        assert load(tmp_path).entries == []

    def test_a_plan_survives_the_round_trip(self, tmp_path):
        held = plan(("a", "retrieve-ride", "caller cannot find this morning's booking"), wanted=1)
        held.written(tmp_path)
        back = load(tmp_path)
        assert back.wanted == 1
        assert [one.line() for one in back.entries] == [one.line() for one in held.entries]


class TestTheStagePlansBeforeItWrites:
    """A large suite gets the planning skill; a small one does not.

    The threshold is not decoration. Below it a single session writes the whole suite in one
    context and can see everything it has written, so a plan buys nothing and costs a stage.
    """

    def test_a_large_suite_is_told_to_plan_first(self, contract, where, monkeypatch):
        from fi.alk.harness import scenarios

        monkeypatch.setattr(scenarios, "world_summary", lambda _root: "(no world here)")
        stage, _ = scenarios.open_stage(contract, out=where, wanted=200)
        said = stage._spec.system_prompt
        assert "Plan all 200 scenarios first" in said
        assert "Plan the suite before writing it" in said

    def test_a_small_suite_is_not(self, contract, where, monkeypatch):
        from fi.alk.harness import scenarios

        monkeypatch.setattr(scenarios, "world_summary", lambda _root: "(no world here)")
        stage, _ = scenarios.open_stage(contract, out=where, wanted=4)
        said = stage._spec.system_prompt
        assert "Write 4 scenarios." in said
        assert "Plan the suite before writing it" not in said

    def test_an_existing_plan_is_used_rather_than_replanned(self, contract, where, monkeypatch):
        """Reopening a planned suite must not plan it again on top of itself."""
        from fi.alk.harness import scenarios
        from fi.alk.harness.blueprint import Blueprint, Entry

        Blueprint(
            wanted=200,
            entries=[
                Entry(f"s{i}", "retrieve-ride", f"situation number {i} about finding a booking")
                for i in range(200)
            ],
        ).written(where)

        monkeypatch.setattr(scenarios, "world_summary", lambda _root: "(no world here)")
        stage, _ = scenarios.open_stage(contract, out=where, wanted=200)
        said = stage._spec.system_prompt
        assert "already planned in blueprint.json" in said
        assert "Plan all 200 scenarios first" not in said


class TestThinkingIsAKnobNotADecision:
    """Off by default, and separately settable for the planner and its writers.

    The stage used to refuse thinking outright because one provider stalled with it on. That is
    a run-time choice, not a fact about the harness, and planning a suite is the work most worth
    paying for it. Nothing here turns it on; it makes turning it on possible.
    """

    def test_the_stage_does_not_think_unless_asked(self, contract, where, monkeypatch):
        from fi.alk.harness import scenarios

        monkeypatch.delenv("ALK_SCENARIO_THINKING", raising=False)
        monkeypatch.setattr(scenarios, "world_summary", lambda _root: "(no world here)")
        stage, _ = scenarios.open_stage(contract, out=where, wanted=4)
        assert stage._spec.thinking is False

    def test_the_stage_thinks_when_the_run_asks(self, contract, where, monkeypatch):
        from fi.alk.harness import scenarios

        monkeypatch.setenv("ALK_SCENARIO_THINKING", "on")
        monkeypatch.setattr(scenarios, "world_summary", lambda _root: "(no world here)")
        stage, _ = scenarios.open_stage(contract, out=where, wanted=4)
        assert stage._spec.thinking is True

    def test_a_writer_takes_its_own_setting_not_the_stage_one(self, contract, where, monkeypatch):
        """The planner and the writers are different jobs, so they get different dials."""
        from fi.alk.harness import scenarios

        monkeypatch.setenv("ALK_SCENARIO_THINKING", "on")
        monkeypatch.setenv("ALK_WRITER_EFFORT", "low")
        monkeypatch.setattr(scenarios, "world_summary", lambda _root: "(no world here)")
        stage, _ = scenarios.open_stage(contract, out=where, wanted=50)
        worker = next(iter(stage._spec.workers.values()))
        assert stage._spec.thinking is True
        assert worker.effort == "low"

    def test_a_writer_left_alone_carries_no_setting(self, contract, where, monkeypatch):
        from fi.alk.harness import scenarios

        monkeypatch.delenv("ALK_WRITER_EFFORT", raising=False)
        monkeypatch.setattr(scenarios, "world_summary", lambda _root: "(no world here)")
        stage, _ = scenarios.open_stage(contract, out=where, wanted=50)
        assert next(iter(stage._spec.workers.values())).effort == ""


class TestWritersRunToCompletionBeforeTheStageEnds:
    """A stage that does not wait for its writers loses everything they were writing.

    Left to the default these launch in the background: the call returns "you will be notified",
    the parent takes its next turn, decides it is done and exits, and the writers die with the
    process. One run dealt fifty scenarios across five writers and saved one, reporting success.
    """

    def test_every_worker_is_declared_blocking(self, contract, where, monkeypatch):
        from fi.alk.harness import scenarios
        from fi.alk.harness.backends import claude as backend

        monkeypatch.setattr(scenarios, "world_summary", lambda _root: "(no world here)")
        stage, _ = scenarios.open_stage(contract, out=where, wanted=50)
        assert stage._spec.workers, "expected writers above the delegation threshold"

        built: list[dict] = []

        def capture(**rest):
            built.append(rest)
            return object()

        monkeypatch.setattr(backend, "AgentDefinition", capture)
        monkeypatch.setattr(backend, "ClaudeSession", lambda *a, **k: object())
        backend.ClaudeBackend().create(stage._spec)

        assert built, "no worker was defined; this test would otherwise check nothing"
        assert all(one.get("background") is False for one in built), (
            "a writer was left to run in the background, so the stage can outlive it"
        )


class TestTheStageCarriesOnlyWhatItNeeds:
    """Every turn resends the system prompt, so what is in it is paid for repeatedly.

    Measured before this: 93KB, of which 7KB was the harness preamble included twice and 44KB was
    the writing skill held by a stage that was still planning.
    """

    def test_the_preamble_appears_once(self, contract, where, monkeypatch):
        from fi.alk.harness import scenarios
        from fi.alk.harness.config import HARNESS

        monkeypatch.setattr(scenarios, "world_summary", lambda _root: "(no world here)")
        opening = HARNESS.read_text(encoding="utf-8")[:120]
        for wanted in (4, 50):
            stage, _ = scenarios.open_stage(contract, out=where, wanted=wanted)
            assert stage._spec.system_prompt.count(opening) == 1

    def test_a_planning_stage_does_not_carry_the_writing_method(
        self, contract, where, monkeypatch
    ):
        from fi.alk.harness import scenarios

        monkeypatch.setattr(scenarios, "world_summary", lambda _root: "(no world here)")
        planning, _ = scenarios.open_stage(contract, out=where, wanted=50)
        writing, _ = scenarios.open_stage(contract, out=where, wanted=4)
        assert "Plan the suite before writing it" in planning._spec.system_prompt
        assert len(planning._spec.system_prompt) < len(writing._spec.system_prompt), (
            "the planner is carrying at least as much as the writer, so nothing was saved"
        )

    def test_the_writers_still_get_the_writing_method(self, contract, where, monkeypatch):
        from fi.alk.harness import scenarios

        monkeypatch.setattr(scenarios, "world_summary", lambda _root: "(no world here)")
        stage, _ = scenarios.open_stage(contract, out=where, wanted=50)
        worker = next(iter(stage._spec.workers.values()))
        assert "submit_scenario" in worker.instructions
