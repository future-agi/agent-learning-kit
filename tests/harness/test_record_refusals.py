"""A refusal the contract records on a record's own field is answered by the proof's world as the tool's service
answers it live: an order carrying a payment fault in the agent's own data is refused at submit, not only live."""

from __future__ import annotations

from pathlib import Path

import pytest

from fi.alk.harness import prove
from fi.alk.harness.amend import fix_tool
from fi.alk.harness.contract import AgentContract, RecordRefusal, ToolSpec, validate_contract, write_contract
from fi.alk.harness.scenario import Scenario, Step
from fi.alk.harness.world.runtime import GeneratedWorld
from fi.alk.harness.world.snapshot import record_refusals

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
    try:
        _, calls, refused, assumed = prove.run_steps(scenario, tmp_path, with_solution=True)
    finally:
        world.close()

    assert [call.ok for call in calls] == [True]
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
