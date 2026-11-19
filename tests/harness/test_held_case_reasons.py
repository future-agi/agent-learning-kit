"""What a submit proof tells a writer about the two holds that cost a large build most: a case whose own fault fails
its reference, and a negative control no check fails. And the check helpers that tell a lookup answered from one that
went through."""

from __future__ import annotations

from pathlib import Path

from fi.alk.harness.actors import Fault
from fi.alk.harness.checks import Outcome, answered, check_namespace, succeeded
from fi.alk.harness.controls import writes_beyond
from fi.alk.harness.prove import Proof, run_steps
from fi.alk.harness.scenario import Scenario
from fi.alk.harness.world import GeneratedWorld
from fi.alk.harness.world.runtime import Call
from fi.alk.harness.world.snapshot import save


def _refunds(root: Path) -> None:
    class Shop(GeneratedWorld):
        name = "shop"
        tools = [{"name": "issue_refund"}, {"name": "get_order"}]
        handlers = {
            "issue_refund": "def handle(args, db):\n    return {'refunded': args['order_id']}\n",
            "get_order": "def handle(args, db):\n    return {'id': args['order_id']}\n",
        }

    world = Shop(":memory:")
    world.connection.executescript("CREATE TABLE refund(id TEXT);")
    save(world, root, notes="test", sequences=[])


def _refund_case(**fields) -> Scenario:
    return Scenario(
        name="refund-with-a-fault",
        instruction="You want your refund.",
        solution=[
            {"tool": "get_order", "arguments": {"order_id": "1045"}},
            {"tool": "issue_refund", "arguments": {"order_id": "1045", "amount": 35}},
        ],
        **fields,
    )


def test_a_case_s_own_fault_fails_its_reference_step_as_the_run_does(tmp_path):
    _refunds(tmp_path)
    fault = Fault(action="issue_refund", mode="error", message="payment service timed out")

    world, calls, refused, _ = run_steps(_refund_case(faults=[fault]), tmp_path, with_solution=True)
    try:
        refund = next(call for call in calls if call.name == "issue_refund")
        assert not refund.ok and refund.error == "payment service timed out"
        assert world.faulted == ["issue_refund({'order_id': '1045', 'amount': 35}): error"]
        assert any("payment service timed out" in one for one in refused)
        # The lookup the fault does not name still goes through.
        assert next(call for call in calls if call.name == "get_order").ok
    finally:
        world.close()

    # A fault on a later call of the action leaves the first one alone.
    later = Fault(action="issue_refund", mode="unavailable", on_call=2)
    world, calls, refused, _ = run_steps(_refund_case(faults=[later]), tmp_path, with_solution=True)
    try:
        assert all(call.ok for call in calls) and not refused and world.faulted == []
    finally:
        world.close()


def test_a_reference_its_own_fault_fails_is_refused_with_the_failure_path_to_write():
    proof = Proof(
        ready=True,
        solvable=False,
        with_solution=[Outcome("issues_full_refund", False, "not done: issue_refund was not successfully called")],
        refused=["issue_refund({'order_id': '1045'}): payment service timed out"],
        faulted=["issue_refund({'order_id': '1045'}): error"],
    )

    said = proof.why()

    assert "This scenario's own faults fail these steps" in said
    assert "tests the failure path, never the change succeeding" in said
    assert "reports the failure honestly" in said


def test_a_lookup_that_finds_nothing_is_answered_but_never_succeeded():
    found = Call(name="get_order", arguments={"order_id": "1045"}, result={"id": "1045"})
    missing = Call(
        name="get_order", arguments={"order_id": "6619"}, ok=False, refused=True, refusal_code="world_rule",
        error="no order 6619",
    )
    crashed = Call(name="get_order", arguments={"order_id": "7"}, ok=False, error="KeyError: 'orders'")
    faulted = Call(name="get_order", arguments={"order_id": "8"}, ok=False, error="unavailable")
    calls = [found, missing, crashed, faulted]

    assert answered(calls, "get_order", order_id="6619") == [missing]
    assert answered(calls, "get_order") == [found, missing]
    assert succeeded(calls, "get_order", order_id="6619") == []
    assert succeeded(calls, "get_order") == [found]
    # Every check is given both.
    assert {"answered", "succeeded"} <= set(check_namespace())


class _Logged:
    """A world whose action log holds the agent's writes, as a proof's or a trial's does."""

    def __init__(self, *writes: dict) -> None:
        self.rows = [
            {"name": action, "action": action, "arguments": arguments, "ok": True, "outcome": "committed",
             "action_id": f"a{index}", "actor_id": "agent"}
            for index, (action, arguments) in enumerate((one["action"], one["arguments"]) for one in writes)
        ]

    def events(self, actor_id=None, outcome=None):
        return [row for row in self.rows if actor_id in (None, row["actor_id"])]


def test_a_passing_control_names_the_record_it_changed_beyond_the_reference():
    reference = [{"tool": "issue_refund", "arguments": {"order_id": "1045"}}]
    once = {"action": "issue_refund", "arguments": {"order_id": "1045", "amount": 35}}

    assert writes_beyond(_Logged(once), reference) == []
    assert writes_beyond(_Logged(once, once), reference) == ["issue_refund(amount=35, order_id='1045')"]
    other = {"action": "issue_refund", "arguments": {"order_id": "2001", "amount": 10}}
    assert writes_beyond(_Logged(other), reference) == ["issue_refund(amount=10, order_id='2001')"]


def test_the_refusal_says_which_control_passed_and_which_record_a_check_must_read():
    proof = Proof(
        ready=True,
        solvable=True,
        vacuous=False,
        with_solution=[Outcome("history_read", True)],
        with_nothing=[Outcome("history_read", False, "not done")],
        controls=[
            {
                "name": "duplicate_refund_issued_on_history_check",
                "why": "refunds again while only reading the history",
                "ran": True,
                "failed": False,
                "held": ["history_read"],
                "assumed": [],
                "changed": ["issue_refund(amount=35, order_id='1045')"],
            }
        ],
    )

    said = proof.why()

    assert "duplicate_refund_issued_on_history_check" in said
    assert "it committed issue_refund(amount=35, order_id='1045'), which the reference never does" in said
    assert "committed_writes(world)" in said


def test_a_control_played_on_a_proof_world_names_its_extra_write(tmp_path):
    from fi.alk.harness.checks import reference_of
    from fi.alk.harness.controls import play_controls

    _refunds(tmp_path)
    refund = {"tool": "issue_refund", "arguments": {"order_id": "1045", "amount": 35}}
    case = _refund_case(
        negative_controls=[{"name": "refunds_twice", "why": "refunds the order again", "steps": [refund, refund]}]
    )
    never_fails = "def check(world, calls):\n    return None\n"

    [played] = play_controls(case, tmp_path, [("anything", never_fails)], reference_of(case.solution), run=run_steps)

    assert played["failed"] is False
    assert played["changed"] == ["issue_refund(amount=35, order_id='1045')"]
