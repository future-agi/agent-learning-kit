"""A refusal the contract records on a record's own field is answered by the proof's world as the tool's service
answers it live: an order carrying a payment fault in the agent's own data is refused at submit, not only live."""

from __future__ import annotations

from pathlib import Path

import pytest

from fi.alk.harness import prove
from fi.alk.harness.amend import fix_tool
from fi.alk.harness.catalogue import Catalogue, SubGoal
from fi.alk.harness.contract import AgentContract, RecordRefusal, ToolSpec, validate_contract, write_contract
from fi.alk.harness.scenario import Scenario, Step
from fi.alk.harness.world.runtime import GeneratedWorld, record_refusal
from fi.alk.harness.world.snapshot import record_refusals, save

# As the returns fixture's ledger refuses (tests/fixtures/harness_agents/returns_multi_agent/ledger.py `_issue`).
PAYMENT_FAULT = {
    "status": 504,
    "table": "orders",
    "column": "order_id",
    "argument": "order_id",
    "field": "fault",
    "values": ["payment_times_out", "refund_never_returns"],
    "message": "the payment service timed out",
}
ORDERS = [
    {"order_id": "1042", "amount": 40.0, "fault": None},
    {"order_id": "1045", "amount": 35.0, "fault": "payment_times_out"},
    # A fault the refund path never reads (the fixture's `skip_refund_check`): the refund goes through.
    {"order_id": "1047", "amount": 20.0, "fault": "skip_refund_check"},
]
ISSUE = "def handle(args, db):\n    return {'refund_id': 'R-1', 'order_id': args['order_id'], 'status': 'issued'}\n"


def _world(*, handler: bool) -> GeneratedWorld:
    world = GeneratedWorld()
    world.state_object = {"orders": [dict(row) for row in ORDERS]}
    world.refusals = {"issue_refund": [PAYMENT_FAULT]}
    if handler:
        world.handlers = {"issue_refund": ISSUE}
    else:
        # A service tool nothing here runs: the proof records its step rather than executing it.
        world.runtime_tools = {"issue_refund"}
        world.endpoint_for = {}
    return world


def test_a_handler_world_refuses_a_payment_fault_order_as_the_service_does_and_runs_a_normal_one() -> None:
    world = _world(handler=True)
    try:
        faulted = world.call("issue_refund", {"order_id": "1045", "amount": 35.0})
        normal = world.call("issue_refund", {"order_id": "1042", "amount": 40.0})
        other_fault = world.call("issue_refund", {"order_id": "1047", "amount": 20.0})
    finally:
        world.close()

    assert (faulted.ok, faulted.refused, faulted.refusal_code) == (False, True, "")
    assert faulted.result == {"detail": "the payment service timed out"}
    assert (normal.ok, normal.refused) == (True, False)
    assert normal.result["status"] == "issued"
    assert (other_fault.ok, other_fault.refused) == (True, False)


def test_a_reference_step_nothing_runs_is_refused_for_a_payment_fault_order_and_never_counted_assumed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    world = _world(handler=False)
    monkeypatch.setattr(prove, "prepared", lambda scenario, root: (world, None, None))
    scenario = Scenario(
        name="refund_payment_fault",
        instruction="x",
        solution=[Step(tool="issue_refund", arguments={"order_id": "1045", "amount": 35.0})],
    )
    try:
        _, calls, refused, assumed = prove.run_steps(scenario, tmp_path, with_solution=True)
    finally:
        world.close()

    assert [(call.ok, call.refused) for call in calls] == [(False, True)]
    assert refused and "1045" in refused[0]
    assert assumed == []


def test_a_reference_step_nothing_runs_for_a_normal_order_is_still_assumed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    world = _world(handler=False)
    monkeypatch.setattr(prove, "prepared", lambda scenario, root: (world, None, None))
    scenario = Scenario(
        name="refund_normal",
        instruction="x",
        solution=[Step(tool="issue_refund", arguments={"order_id": "1042", "amount": 40.0})],
    )
    # Left over from an earlier play on this world: each run starts its own list.
    world.answered_by_record = list(scenario.solution)
    try:
        _, calls, refused, assumed = prove.run_steps(scenario, tmp_path, with_solution=True)
    finally:
        world.close()

    # Recorded not run: neither ok nor refused, and not listed as refused.
    assert [(call.ok, call.refused) for call in calls] == [(None, None)]
    assert refused == []
    assert assumed == ["issue_refund"]


def test_the_world_reads_record_refusals_from_its_contract_and_fix_tool_records_them(tmp_path: Path) -> None:
    contract = AgentContract(
        agent="desk",
        one_liner="refunds orders",
        real_use_cases=["refund an order"],
        tools=[
            ToolSpec(name="issue_refund", args=["order_id", "amount"]),
            ToolSpec(name="get_order", args=["order_id"]),
        ],
    )
    write_contract(contract, tmp_path)
    assert record_refusals(tmp_path) == {}
    # A tool with none writes no `refusals` key, so no existing contract's bytes change.
    assert "refusals" not in contract.tools[1].model_dump()

    done, said = fix_tool(
        contract, tmp_path, tool_name="issue_refund", why="the ledger answers 504", refusals=[PAYMENT_FAULT]
    )
    assert done, said

    assert record_refusals(tmp_path) == {"issue_refund": [PAYMENT_FAULT]}


def test_a_record_refusal_on_an_argument_the_tool_does_not_take_is_refused() -> None:
    contract = AgentContract(
        agent="desk",
        one_liner="refunds orders",
        real_use_cases=["refund an order"],
        tools=[ToolSpec(name="issue_refund", args=["order"], refusals=[RecordRefusal(**PAYMENT_FAULT)])],
    )

    assert "tool[issue_refund]:refusal-on-unknown-arg:order_id" in validate_contract(contract)
    # With no values there is no refusal to answer: "any value" is not a mode, so a false-valued field never fires.
    contract.tools[0].args = ["order_id"]
    contract.tools[0].refusals[0].values = []
    assert "tool[issue_refund]:refusal-needs-the-values-that-refuse" in validate_contract(contract)


def test_a_boolean_refusal_matches_only_a_boolean_and_an_empty_values_list_matches_nothing() -> None:
    world = GeneratedWorld()
    world.state_object = {
        "accounts": [{"id": "A", "closed": 1}, {"id": "B", "closed": True}, {"id": "C", "closed": False}]
    }
    closed = {"status": 409, "table": "accounts", "column": "id", "argument": "id", "field": "closed"}
    try:
        world.refusals = {"pay": [{**closed, "values": [True]}]}
        assert record_refusal(world, "pay", {"id": "A"}) is None
        assert record_refusal(world, "pay", {"id": "B"}).refusal_code == "world_rule"
        assert record_refusal(world, "pay", {"id": "C"}) is None
        world.refusals = {"pay": [{**closed, "values": []}]}
        assert record_refusal(world, "pay", {"id": "B"}) is None
    finally:
        world.close()


def test_a_table_kept_as_a_mapping_by_key_is_matched_on_its_key() -> None:
    world = GeneratedWorld()
    world.state_object = {"orders": {"1045": {"fault": "payment_times_out"}, "1042": {"fault": None}}}
    world.refusals = {"issue_refund": [PAYMENT_FAULT]}
    try:
        assert record_refusal(world, "issue_refund", {"order_id": "1045"}).refused is True
        assert record_refusal(world, "issue_refund", {"order_id": "1042"}) is None
    finally:
        world.close()


def test_a_world_running_the_agent_s_own_code_answers_by_that_code() -> None:
    world = _world(handler=True)
    world.native_runtime = True
    try:
        assert world.call("issue_refund", {"order_id": "1045", "amount": 35.0}).ok is True
    finally:
        world.close()


SUCCEEDED = (
    "def check(world, calls):\n"
    "    done = [c for c in calls if c.name == 'issue_refund' and c.ok and c.arguments.get('order_id') == '1042']\n"
    "    return None if done else 'no refund of 1042 went through'\n"
)


def _assumed_refund_world(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A world where `issue_refund` has no endpoint: its reference step is recorded, never run."""

    class Desk(GeneratedWorld):
        name = "desk"
        tools = [{"name": "issue_refund"}]
        handlers = {}

    world = Desk(":memory:")
    world.connection.executescript("CREATE TABLE refunds(id TEXT);")
    save(world, root, notes="test", sequences=[])
    prepared = prove.prepared

    def unbound(scenario, world_root):
        made, applied, ready = prepared(scenario, world_root)
        made.runtime_tools, made.endpoint_for = {"issue_refund"}, {}
        return made, applied, ready

    monkeypatch.setattr(prove, "prepared", unbound)


def _refund_case(order_id: str) -> Scenario:
    return Scenario(
        name="refund",
        instruction="You want your head torch refunded.",
        sub_goals=["refunds_1042"],
        solution=[Step(tool="issue_refund", arguments={"order_id": order_id, "amount": 40.0})],
        negative_controls=[
            {
                "name": "refunds_the_wrong_order",
                "why": "refunds another order",
                "steps": [{"tool": "issue_refund", "arguments": {"order_id": "1043", "amount": 40.0}}],
            }
        ],
    )


def _prove_refund(root: Path, case: Scenario):
    goals = Catalogue(sub_goals=[SubGoal(name="refunds_1042", what="Order 1042 is refunded.", check=SUCCEEDED)])
    contract = AgentContract(agent="desk", tools=[ToolSpec(name="issue_refund", args=["order_id", "amount"])])
    return prove.prove(case, goals, root, contract=contract, actors=[])


def test_a_success_check_on_an_assumed_step_is_inconclusive_so_the_case_is_kept_as_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _assumed_refund_world(tmp_path, monkeypatch)

    proof = _prove_refund(tmp_path, _refund_case("1042"))

    assert proof.assumed == ["issue_refund"] and proof.refused == []
    assert proof.excused == ["refunds_1042"]
    assert proof.solvable is True and proof.proved is False and proof.kept_as_written is True


def test_a_reference_that_fails_its_check_even_if_the_assumed_step_went_through_is_not_solvable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _assumed_refund_world(tmp_path, monkeypatch)

    proof = _prove_refund(tmp_path, _refund_case("1043"))

    assert proof.excused == [] and proof.solvable is False


def test_a_writer_trying_the_reference_sees_the_payment_fault_refusal_from_the_saved_world(tmp_path: Path) -> None:
    """End to end through `restore`: the refusals come from the contract beside the saved world."""
    import asyncio

    from fi.alk.harness.scenario_tools import scenario_tools

    class Desk(GeneratedWorld):
        name = "desk"
        tools = [{"name": "issue_refund"}]
        handlers = {}

    world = Desk(":memory:")
    world.connection.executescript(
        "CREATE TABLE orders(order_id TEXT, fault TEXT);"
        "INSERT INTO orders VALUES ('1042', NULL), ('1045', 'payment_times_out');"
    )
    world.runtime_tools = {"issue_refund"}
    save(world, tmp_path, notes="test", sequences=[])
    contract = AgentContract(
        agent="desk",
        one_liner="refunds orders",
        real_use_cases=["refund an order"],
        tools=[ToolSpec(name="issue_refund", args=["order_id", "amount"], refusals=[RecordRefusal(**PAYMENT_FAULT)])],
    )
    write_contract(contract, tmp_path)
    server, _ = scenario_tools(contract, tmp_path, tmp_path, wanted=10, catalogue=Catalogue())
    try_calls = next(one.handler for one in server.tools if one.name == "try_calls")

    said = str(asyncio.run(try_calls({"calls": [{"tool": "issue_refund", "arguments": {"order_id": "1045"}}]})))
    normal = str(asyncio.run(try_calls({"calls": [{"tool": "issue_refund", "arguments": {"order_id": "1042"}}]})))

    assert "issue_refund: refused" in said and "the payment service timed out" in said
    assert "issue_refund: NOT RUN" in normal


def test_the_live_proof_reads_an_assumed_step_as_inconclusive_and_never_as_refused() -> None:
    import asyncio

    from fi.alk.harness.live_proof import _prove_case

    async def fresh() -> GeneratedWorld:
        return _world(handler=False)

    scenario = _refund_case("1042")
    scenario.negative_controls[0].steps = list(scenario.solution)
    goals = Catalogue(sub_goals=[SubGoal(name="refunds_1042", what="Order 1042 is refunded.", check=SUCCEEDED)])

    live = asyncio.run(_prove_case(scenario, goals, fresh))

    assert live["assumed"] == ["issue_refund"] and live["refused"] == []
    assert live["inconclusive"] == ["refunds_1042"]
    # The control's assumed step reads as gone through, so a check it cannot break holds under it.
    assert live["controls"][0]["held"] == ["refunds_1042"]
