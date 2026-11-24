"""Two trainers, one task, different actions, each earning the verdict it deserves.

The plan's first integration test for T28 and T32: two attempts open the same frozen task,
take different actions, observe different world states, and obtain the same verdict each
trajectory would earn in a simulation. One refunds; the other reads the policy, finds the
order ineligible, explains the refusal and writes nothing.

The world here is small on purpose -- a dict of orders behind a door-shaped executor -- because
what is under test is the composition: episode -> evidence -> shared verdict -> reward. The
same `terminal_verdict.decide` the hosted scheduler calls decides both outcomes, which is the
property that makes a reward mean what a simulation verdict means.
"""

from __future__ import annotations

from typing import Any, Mapping

from fi.alk.harness.episode_driver import EndReason, Episode, PublicTool
from fi.alk.harness.terminal_verdict import Status, decide, reward_for
from fi.alk.harness.world_door import Outcome, ToolCall

POLICY = "Refunds are allowed within 30 days of delivery."


class _Orders:
    """A seeded world: two orders, one refundable, one not. Each episode gets its own copy."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {
            "o1": {"days_since_delivery": 3, "refunded": False, "amount": 40.0},
            "o9": {"days_since_delivery": 90, "refunded": False, "amount": 15.0},
        }
        self.said: list[str] = []

    def refunded(self, order_id: str) -> bool:
        return bool(self.rows[order_id]["refunded"])


class _Door:
    """Executes against one `_Orders`, answering in the shape `WorldDoor.enter` returns.

    Stands in for the real door so this test exercises the episode seam and the verdict, not
    the door's own permission and idempotency machinery, which has its own tests.
    """

    def __init__(self, world: _Orders) -> None:
        self.world = world
        self.calls: list[ToolCall] = []

    def enter(self, call: ToolCall) -> Outcome:
        self.calls.append(call)
        name = call.action.rsplit(".", 1)[-1]
        args: Mapping[str, Any] = call.arguments or {}
        if name == "look_up_order":
            row = self.world.rows.get(str(args.get("order_id")))
            if row is None:
                return Outcome(outcome="refused", status=404, refusal_code="unknown_order")
            return Outcome(outcome="committed", status=200, result=dict(row))
        if name == "return_policy":
            return Outcome(outcome="committed", status=200, result={"policy": POLICY})
        if name == "refund":
            order_id = str(args.get("order_id"))
            row = self.world.rows.get(order_id)
            if row is None:
                return Outcome(outcome="refused", status=404, refusal_code="unknown_order")
            row["refunded"] = True
            return Outcome(outcome="committed", status=200, result={"refunded": order_id})
        if name == "say":
            self.world.said.append(str(args.get("message", "")))
            return Outcome(outcome="committed", status=200, result={"delivered": True})
        return Outcome(outcome="refused", status=400, refusal_code="unknown_tool")


class _Catalogue:
    TOOLS = (
        PublicTool("look_up_order", "Find an order", ("order_id",)),
        PublicTool("return_policy", "Read the refund policy", ()),
        PublicTool("refund", "Refund an order", ("order_id",)),
        PublicTool("say", "Speak to the customer", ("message",)),
    )

    def published(self):
        return self.TOOLS

    def world_action(self, tool: str) -> str:
        return f"orders.{tool}"

    def effect(self, tool: str) -> str:
        return "write" if tool == "refund" else "read"


class _Result:
    """A decided sub-goal, as the grading path produces them."""

    def __init__(self, name: str, held: bool | None, *, judged: bool = False) -> None:
        self.name = name
        self.held = held
        self.reason = None
        self.judged = judged
        self.subject = "task"


def _episode(world: _Orders, episode_id: str) -> tuple[Episode, _Door]:
    door = _Door(world)
    return (
        Episode(
            episode_id=episode_id,
            prompt=f"Handle the customer's refund request. Policy: {POLICY}",
            door=door,  # type: ignore[arg-type]  # door-shaped; see _Door's docstring
            catalogue=_Catalogue(),
            step_limit=10,
        ),
        door,
    )


def _grade(*, refund_expected: bool, refunded: bool, explained: bool):
    """The scenario's checks, as the hosted path would decide them for this task."""
    results = [_Result("refund matches policy", held=(refunded == refund_expected))]
    if not refund_expected:
        results.append(_Result("refusal was explained", held=explained, judged=True))
    decision = decide(results)
    return decision, reward_for(decision)


def test_a_correct_refund_earns_one() -> None:
    world = _Orders()
    episode, _ = _episode(world, "ep-refund")

    episode.step(action_id="a1", tool="look_up_order", arguments={"order_id": "o1"})
    episode.step(action_id="a2", tool="return_policy")
    refund = episode.step(action_id="a3", tool="refund", arguments={"order_id": "o1"})
    episode.say(action_id="a4", tool="say", message="That is refunded for you.")
    observation = episode.freeze(EndReason.COMPLETED)

    assert refund.public.ok is True
    assert world.refunded("o1") is True
    assert observation.end_reason is EndReason.COMPLETED

    decision, reward = _grade(refund_expected=True, refunded=True, explained=False)
    assert decision.status is Status.PASSED
    assert reward == 1.0


def test_a_correct_refusal_earns_one_without_any_write() -> None:
    """The plan's requirement: an agent may end with a correct refusal and still pass."""
    world = _Orders()
    episode, door = _episode(world, "ep-refuse")

    episode.step(action_id="b1", tool="look_up_order", arguments={"order_id": "o9"})
    episode.step(action_id="b2", tool="return_policy")
    episode.say(
        action_id="b3",
        tool="say",
        message="That order is 90 days old, outside the 30-day window, so I cannot refund it.",
    )
    episode.freeze(EndReason.COMPLETED)

    assert world.refunded("o9") is False
    assert not [call for call in door.calls if call.effect == "write"]

    decision, reward = _grade(refund_expected=False, refunded=False, explained=True)
    assert decision.status is Status.PASSED
    assert reward == 1.0


def test_refunding_an_ineligible_order_earns_zero() -> None:
    world = _Orders()
    episode, _ = _episode(world, "ep-wrong")

    episode.step(action_id="c1", tool="refund", arguments={"order_id": "o9"})
    episode.freeze(EndReason.COMPLETED)

    assert world.refunded("o9") is True
    decision, reward = _grade(refund_expected=False, refunded=True, explained=False)
    assert decision.status is Status.FAILED
    assert reward == 0.0


def test_two_trainers_on_the_same_task_do_not_share_world_state() -> None:
    """The isolation property: each episode reads back only its own effects."""
    refunding, refusing = _Orders(), _Orders()
    one, _ = _episode(refunding, "ep-a")
    two, _ = _episode(refusing, "ep-b")

    one.step(action_id="a1", tool="refund", arguments={"order_id": "o1"})
    two.step(action_id="b1", tool="look_up_order", arguments={"order_id": "o1"})

    assert refunding.refunded("o1") is True
    assert refusing.refunded("o1") is False, "one trainer's write must not reach another's world"

    seen = two.step(action_id="b2", tool="look_up_order", arguments={"order_id": "o1"})
    assert seen.public.output["refunded"] is False


def test_each_trainer_sees_only_its_own_dialogue() -> None:
    world_one, world_two = _Orders(), _Orders()
    one, _ = _episode(world_one, "ep-a")
    two, _ = _episode(world_two, "ep-b")

    one.say(action_id="a1", tool="say", message="refunding now")
    two.say(action_id="b1", tool="say", message="checking the policy")

    assert one.observe().messages == ("refunding now",)
    assert two.observe().messages == ("checking the policy",)


def test_a_judge_outage_yields_no_reward_rather_than_a_zero() -> None:
    """T32's rule, end to end: an ungraded trial must not look like a failure."""
    world = _Orders()
    episode, _ = _episode(world, "ep-outage")
    episode.step(action_id="d1", tool="look_up_order", arguments={"order_id": "o9"})
    episode.freeze(EndReason.COMPLETED)

    decision = decide([_Result("refusal was explained", held=None, judged=True)])
    assert decision.status is Status.ERRORED
    assert decision.failure_code == "judge_undecided"
    assert reward_for(decision) is None
    assert not decision.graded


def test_a_tool_that_only_reports_success_cannot_earn_reward() -> None:
    """A trainer's reward must track the world, never a tool's own acknowledgement.

    The hollow-tool trap: an acknowledgement with no effect must grade as a failure, because
    the check reads the world rather than the response.
    """
    world = _Orders()
    episode, _ = _episode(world, "ep-hollow")

    # A tool answering ok while changing nothing.
    class _Hollow(_Door):
        def enter(self, call: ToolCall) -> Outcome:
            self.calls.append(call)
            return Outcome(outcome="committed", status=200, result={"observed": True})

    episode.door = _Hollow(world)  # type: ignore[assignment]
    acknowledged = episode.step(action_id="e1", tool="refund", arguments={"order_id": "o1"})
    episode.freeze(EndReason.COMPLETED)

    assert acknowledged.public.ok is True, "the tool reported success"
    assert world.refunded("o1") is False, "yet nothing happened in the world"

    decision, reward = _grade(refund_expected=True, refunded=False, explained=False)
    assert decision.status is Status.FAILED
    assert reward == 0.0
