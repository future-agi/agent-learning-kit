"""The SDK talking to the adapter, with no HTTP in between.

Each side has its own tests, which proves each against the specification as I read it. This
proves them against *each other*: the client's transport is wired straight to the adapter's
handlers, so a mismatch in a field name, an event order or the reward contract fails here
rather than on a customer's first call.

What is still missing is the transport itself -- Platform's HTTP, auth and tenancy. That is
exactly the seam this test fakes, and the reason it cannot stand in for the pinned reference
client the plan requires before the API is frozen.
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping

from fi.alk.harness import ors_adapter
from fi.alk.harness.episode_driver import Episode, PublicTool
from fi.alk.harness.ors_adapter import SessionStore
from fi.alk.harness.terminal_verdict import Status, decide
from fi.alk.harness.world_door import Outcome, ToolCall
from fi.alk.trainer import episode as open_episode

POLICY_DAYS = 30


class _Orders:
    """A seeded world, mirroring examples/harness/retail_returns."""

    def __init__(self) -> None:
        self.rows = {
            "ORD-1041": {"order_id": "ORD-1041", "days_since_delivery": 5, "refunded": False},
            "ORD-2298": {"order_id": "ORD-2298", "days_since_delivery": 94, "refunded": False},
        }
        self.said: list[str] = []


class _Door:
    def __init__(self, world: _Orders) -> None:
        self.world = world

    def enter(self, call: ToolCall) -> Outcome:
        name = call.action.rsplit(".", 1)[-1]
        args = call.arguments or {}
        if name == "look_up_order":
            row = self.world.rows.get(str(args.get("order_id", "")).upper())
            if row is None:
                return Outcome(outcome="committed", status=200, result={"found": False})
            return Outcome(outcome="committed", status=200, result={"found": True, **row})
        if name == "return_policy":
            return Outcome(outcome="committed", status=200, result={"window_days": POLICY_DAYS})
        if name == "refund":
            row = self.world.rows.get(str(args.get("order_id", "")).upper())
            if row is None:
                return Outcome(outcome="refused", status=404, refusal_code="unknown_order")
            row["refunded"] = True
            return Outcome(outcome="committed", status=200, result={"refunded": True})
        if name == "say":
            self.world.said.append(str(args.get("message", "")))
            return Outcome(outcome="committed", status=200, result={"delivered": True})
        return Outcome(outcome="refused", status=400, refusal_code="unknown_tool")


class _Catalogue:
    TOOLS = (
        PublicTool("look_up_order", "Find an order", ("order_id",)),
        PublicTool("return_policy", "Read the policy", ()),
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
    def __init__(self, name: str, held: bool | None) -> None:
        self.name, self.held, self.reason, self.judged, self.subject = name, held, None, False, "task"


class _LoopbackTransport:
    """The SDK's transport, answered by the adapter directly. No sockets, no framework."""

    def __init__(self, world: _Orders, *, task: str) -> None:
        self.world = world
        self.task = task
        self.store = SessionStore()
        self.env = "retail-returns"

    def _episode_for(self) -> Episode:
        prompt = f"The customer asks about {self.task}. Refund it if policy allows."
        return Episode(
            episode_id="ep-1",
            prompt=prompt,
            door=_Door(self.world),  # type: ignore[arg-type]
            catalogue=_Catalogue(),
        )

    def _decision(self):
        """Grade the trial the way the hosted path would for this task."""
        row = self.world.rows[self.task]
        should_refund = row["days_since_delivery"] <= POLICY_DAYS
        return decide([_Result("refund matches policy", row["refunded"] == should_refund)])

    def request(
        self, method: str, url: str, *, headers: Mapping[str, str], json_body: Any = None
    ) -> tuple[int, Any]:
        sid = headers.get("X-Session-ID")
        try:
            if url.endswith("/create_session"):
                return 200, ors_adapter.create_session(self.store)
            if url.endswith("/create"):
                session = self.store.require(sid)
                return 200, ors_adapter.create_episode(session, self._episode_for())
            if url.endswith("/delete"):
                session = self.store.require(sid)
                return 200, ors_adapter.delete_episode(session, decision=self._decision())
            if url.endswith("/prompt"):
                session = self.store.require(sid)
                return 200, ors_adapter.prompt_blocks(
                    ors_adapter.require_episode(session).observe()
                )
            if url.endswith("/task_tools"):
                session = self.store.require(sid)
                return 200, ors_adapter.tools_response(
                    ors_adapter.require_episode(session).observe()
                )
            if url.endswith("/tasks"):
                return 200, ors_adapter.tasks_response(self.env, list(self.world.rows))
        except ors_adapter.OrsError as exc:
            return exc.status, {"detail": exc.detail}
        return 404, {"detail": "not routed"}

    def stream(
        self, method: str, url: str, *, headers: Mapping[str, str], json_body: Any = None
    ) -> Iterator[tuple[str, Any]]:
        """The adapter's SSE text, parsed back the way the real client would parse it."""
        session = self.store.require(headers.get("X-Session-ID"))
        body = json_body or {}
        raw = "".join(
            ors_adapter.call_events(
                session,
                name=str(body.get("name") or ""),
                arguments=body.get("input") or {},
                task_id=body.get("task_id"),
            )
        )
        import json as _json

        for block in raw.split("\n\n"):
            if not block.strip():
                continue
            fields = dict(line.split(": ", 1) for line in block.strip().split("\n"))
            yield fields["event"], _json.loads(fields["data"])


def _run(task: str):
    world = _Orders()
    transport = _LoopbackTransport(world, task=task)
    with open_episode(
        "retail-returns",
        task=task,
        base_url="https://loopback.invalid",
        api_key="k",
        transport=transport,
    ) as episode:
        prompt = episode.prompt()
        order = episode.call("look_up_order", order_id=task)
        policy = episode.call("return_policy")
        age = order.output["days_since_delivery"]
        window = policy.output["window_days"]
        if age > window:
            episode.say(f"{task} is {age} days old, outside the {window}-day window.")
            took = "refused"
        else:
            episode.call("refund", order_id=task)
            episode.say(f"I have refunded {task}.")
            took = "refunded"
        result = episode.finish()
    return world, prompt, took, result


def test_a_trainer_refunds_the_eligible_order_and_earns_one() -> None:
    world, prompt, took, result = _run("ORD-1041")
    assert "ORD-1041" in prompt
    assert took == "refunded"
    assert world.rows["ORD-1041"]["refunded"] is True
    assert result.graded is True
    assert result.reward == 1.0
    assert result.usable is True


def test_a_trainer_refuses_the_stale_order_and_earns_one_without_writing() -> None:
    """The requirement that makes the task real in both directions."""
    world, _, took, result = _run("ORD-2298")
    assert took == "refused"
    assert world.rows["ORD-2298"]["refunded"] is False, "a correct refusal writes nothing"
    assert world.said and "outside" in world.said[-1]
    assert result.reward == 1.0
    assert result.usable is True


def test_refunding_the_stale_order_earns_zero() -> None:
    world = _Orders()
    transport = _LoopbackTransport(world, task="ORD-2298")
    with open_episode(
        "retail-returns", task="ORD-2298", base_url="https://loopback.invalid",
        api_key="k", transport=transport,
    ) as episode:
        episode.call("refund", order_id="ORD-2298")
        result = episode.finish()
    assert world.rows["ORD-2298"]["refunded"] is True
    assert result.graded is True
    assert result.reward == 0.0
    assert result.usable is True, "a graded zero is a usable label"


def test_an_unpublished_tool_comes_back_as_a_refusal_through_the_whole_stack() -> None:
    world = _Orders()
    transport = _LoopbackTransport(world, task="ORD-1041")
    with open_episode(
        "retail-returns", task="ORD-1041", base_url="https://loopback.invalid",
        api_key="k", transport=transport,
    ) as episode:
        result = episode.call("query", sql="select 1")
        assert result.ok is False
        assert "publishes" in (result.error or "")
        episode.finish()


def test_the_published_tools_survive_the_round_trip() -> None:
    world = _Orders()
    transport = _LoopbackTransport(world, task="ORD-1041")
    with open_episode(
        "retail-returns", task="ORD-1041", base_url="https://loopback.invalid",
        api_key="k", transport=transport,
    ) as episode:
        names = {tool["name"] for tool in episode.tools()}
        assert names == {"look_up_order", "return_policy", "refund", "say"}
        episode.finish()


def test_an_ungraded_trial_crosses_the_stack_as_no_label() -> None:
    """The contract that matters: null reward in, no usable label out -- never a zero."""
    world = _Orders()
    transport = _LoopbackTransport(world, task="ORD-1041")

    def _ungraded():
        return decide([_Result("refund matches policy", None)])

    transport._decision = _ungraded  # type: ignore[method-assign]
    with open_episode(
        "retail-returns", task="ORD-1041", base_url="https://loopback.invalid",
        api_key="k", transport=transport,
    ) as episode:
        result = episode.finish()
    assert result.reward is None
    assert result.graded is False
    assert result.usable is False
    assert result.status == Status.ERRORED.value
