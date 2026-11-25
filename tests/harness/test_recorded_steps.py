"""A step of a tool that touches no world and that nothing here can run is recorded, not run: its case and controls
are proved from the calls, never shown as executed. Anything else stays assumed."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from fi.alk.harness import prove as module
from fi.alk.harness.catalogue import Catalogue, SubGoal
from fi.alk.harness.contract import ToolSpec
from fi.alk.harness.proofs import proof_entry
from fi.alk.harness.scenario import Scenario
from fi.alk.harness.world import GeneratedWorld
from fi.alk.harness.world.snapshot import save

REMEMBERS_SEATTLE = (
    "def check(world, calls):\n"
    "    told = [c for c in calls if c.name == 'memorize' and c.arguments.get('value') == 'Seattle']\n"
    "    return None if told else 'the destination was not remembered as Seattle'\n"
)
READS_THE_RESULT = (
    "def check(world, calls):\n"
    "    return None if any(c.name == 'memorize' and c.result for c in calls) else 'nothing remembered'\n"
)


def _in_process_world(root: Path, monkeypatch) -> None:
    """A world where `memorize` runs inside the agent: a runtime tool with no endpoint, so nothing here runs it."""

    class Trip(GeneratedWorld):
        name = "trip"
        tools = [{"name": "memorize"}]
        handlers = {}

    world = Trip(":memory:")
    world.connection.executescript("CREATE TABLE trips(id TEXT);")
    save(world, root, notes="test", sequences=[])
    prepared = module.prepared

    def in_process(scenario, world_root):
        made, applied, ready = prepared(scenario, world_root)
        made.runtime_tools, made.endpoint_for = {"memorize"}, {}
        return made, applied, ready

    monkeypatch.setattr(module, "prepared", in_process)


def _case(**extra) -> Scenario:
    values = dict(
        name="remember-destination",
        instruction="You are going to Seattle.",
        sub_goals=["remembers_destination"],
        solution=[{"tool": "memorize", "arguments": {"key": "destination", "value": "Seattle"}}],
        negative_controls=[
            {
                "name": "records_wrong_destination",
                "why": "remembers the wrong city",
                "steps": [{"tool": "memorize", "arguments": {"key": "destination", "value": "Portland"}}],
            }
        ],
    )
    values.update(extra)
    return Scenario(**values)


def _prove(root: Path, case: Scenario, check: str, *, world_free: bool = True):
    contract = SimpleNamespace(tools=[ToolSpec(name="memorize", world_free=world_free)], hard_constraints=[], workers=[])
    goals = Catalogue(sub_goals=[SubGoal(name="remembers_destination", what="Seattle is remembered.", check=check)])
    return module.prove(case, goals, root, contract=contract, actors=[])


def test_a_world_free_step_is_recorded_not_run_and_its_case_proves(tmp_path, monkeypatch):
    _in_process_world(tmp_path, monkeypatch)

    proof = _prove(tmp_path, _case(), REMEMBERS_SEATTLE)

    assert proof.assumed == [] and proof.recorded == ["memorize"]
    [control] = proof.controls
    assert control["ran"] is True and control["failed"] is True
    entry = proof_entry(proof, 1)
    assert entry["recorded_note"] == "memorize: recorded, not run (no world effect)"


def test_a_tool_not_declared_world_free_stays_assumed(tmp_path, monkeypatch):
    _in_process_world(tmp_path, monkeypatch)

    proof = _prove(tmp_path, _case(), REMEMBERS_SEATTLE, world_free=False)

    assert proof.assumed == ["memorize"] and proof.recorded == []
    assert "recorded_note" not in proof_entry(proof, 1)


def test_a_case_whose_check_reads_a_result_stays_assumed(tmp_path, monkeypatch):
    _in_process_world(tmp_path, monkeypatch)

    proof = _prove(tmp_path, _case(), READS_THE_RESULT)

    assert proof.assumed == ["memorize"] and proof.recorded == []


def test_a_step_that_takes_a_value_from_its_result_keeps_it_assumed():
    case = _case(
        solution=[
            {"tool": "memorize", "arguments": {"key": "destination", "value": "Seattle"}},
            {"tool": "memorize", "arguments": {"key": "copy", "value": "$call.memorize.value"}},
        ]
    )
    contract = SimpleNamespace(tools=[ToolSpec(name="memorize", world_free=True)])

    assert module.recordable_tools(case, contract, [("remembers_destination", REMEMBERS_SEATTLE)]) == frozenset()
    assert module.recordable_tools(_case(), contract, [("remembers_destination", REMEMBERS_SEATTLE)]) == {"memorize"}


def test_a_tool_mapped_to_world_tables_is_never_recorded_whatever_world_free_says():
    from fi.alk.harness.contract import AgentContract, validate_contract

    checks = [("remembers_destination", REMEMBERS_SEATTLE)]
    reads = ToolSpec(name="memorize", world_free=True, tables_read=["orders"])
    writes = ToolSpec(name="memorize", world_free=True, tables_changed=["refunds"])
    assert module.recordable_tools(_case(), SimpleNamespace(tools=[reads]), checks) == frozenset()
    assert module.recordable_tools(_case(), SimpleNamespace(tools=[writes]), checks) == frozenset()
    problems = validate_contract(AgentContract(agent="shop", tools=[reads]))
    assert "tool[memorize]:world-free-but-touches-tables:orders" in " ".join(problems)
    assert not any("world-free" in one for one in validate_contract(AgentContract(agent="shop", tools=[writes.model_copy(update={"world_free": False})])))


def test_the_live_proof_records_the_same_step_and_reports_it(tmp_path, monkeypatch):
    from fi.alk.harness import live_proof
    from fi.alk.harness.world.snapshot import restore

    _in_process_world(tmp_path, monkeypatch)
    world = restore(tmp_path)
    world.runtime_tools, world.endpoint_for = {"memorize"}, {}
    case = _case()

    calls, assumed, refused = live_proof._play(case, world, list(case.solution), frozenset({"memorize"}))

    assert assumed == [] and refused == [] and world.recorded == ["memorize"]
    assert [(one.name, one.arguments["value"], one.result) for one in calls] == [("memorize", "Seattle", None)]
    # Without it the step stays assumed, as before.
    world = restore(tmp_path)
    world.runtime_tools, world.endpoint_for = {"memorize"}, {}
    _, assumed, _ = live_proof._play(case, world, list(case.solution))
    assert assumed == ["memorize"]



@pytest.mark.parametrize(
    "credits",
    [
        "any(c.name == 'memorize' and c.ok and c.arguments.get('value') == 'Seattle' for c in calls)",
        "succeeded(calls, 'memorize', value='Seattle')",
        "answered(calls, 'memorize', value='Seattle')",
        "exactly_once(calls, 'memorize', value='Seattle')",
        "came_before(calls, 'memorize', 'book_trip')",
        "any(c.name == 'memorize' and not c.refused and c.arguments.get('value') == 'Seattle' for c in calls)",
    ],
)
def test_a_check_that_reads_whether_a_call_went_through_keeps_the_step_assumed(tmp_path, monkeypatch, credits):
    _in_process_world(tmp_path, monkeypatch)
    check = f"def check(world, calls):\n    return None if {credits} else 'not remembered'\n"

    proof = _prove(tmp_path, _case(), check)

    assert proof.assumed == ["memorize"] and proof.recorded == [] and not proof.proved


def test_a_negated_exactly_once_never_proves_a_step_that_did_not_run(tmp_path, monkeypatch):
    """`not exactly_once(...)` holds on a call recorded but never run, and fails once the call really went through:
    it reads what the call came to, so the step stays assumed, never proof."""
    from fi.alk.harness.checks import exactly_once
    from fi.alk.harness.prove import record_unrun_step

    _in_process_world(tmp_path, monkeypatch)
    check = (
        "def check(world, calls):\n"
        "    return None if not exactly_once(calls, 'memorize', value='Seattle') else 'remembered once'\n"
    )
    recorded = record_unrun_step(
        SimpleNamespace(calls=[]), SimpleNamespace(tool="memorize", arguments={"value": "Seattle"})
    )
    ran = SimpleNamespace(name="memorize", arguments={"value": "Seattle"}, ok=True, refused=False, error=None)

    proof = _prove(tmp_path, _case(), check)

    assert not exactly_once([recorded], "memorize", value="Seattle")
    assert exactly_once([ran], "memorize", value="Seattle")
    assert proof.assumed == ["memorize"] and proof.recorded == [] and not proof.proved


def test_the_proof_reads_the_outcome_helpers_from_the_one_table_of_check_helpers():
    from fi.alk.harness.checks import CHECK_HELPERS, OUTCOME_HELPERS
    from fi.alk.harness.prove import _OUTCOME_HELPERS

    assert _OUTCOME_HELPERS == OUTCOME_HELPERS and OUTCOME_HELPERS <= set(CHECK_HELPERS)
    assert {"exactly_once", "came_before", "succeeded", "answered"} <= OUTCOME_HELPERS
    assert not {"same_id", "same_value"} & OUTCOME_HELPERS


def test_a_recorded_call_never_reads_as_gone_through_or_answered():
    from fi.alk.harness.checks import answered, succeeded
    from fi.alk.harness.prove import record_unrun_step

    world = SimpleNamespace(calls=[])
    step = SimpleNamespace(tool="memorize", arguments={"key": "destination", "value": "Seattle"})

    call = record_unrun_step(world, step)

    assert call.ok is None and call.refused is None and call.refusal_code is None and call.error is None
    assert succeeded([call], "memorize") == [] and answered([call], "memorize") == []
    assert world.recorded == ["memorize"]


def test_a_recorded_step_reads_in_the_action_log_as_recorded_never_refused(tmp_path, monkeypatch):
    _in_process_world(tmp_path, monkeypatch)
    world, calls, refused, assumed = module.run_steps(
        _case(), tmp_path, with_solution=True, recordable=frozenset({"memorize"})
    )
    try:
        [row] = [one for one in world.events(actor_id="agent") if one["name"] == "memorize"]
    finally:
        world.close()

    assert row["outcome"] == "recorded" and row["refusal_code"] is None
    assert refused == [] and assumed == []
