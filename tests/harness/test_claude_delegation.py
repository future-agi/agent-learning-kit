"""The Claude SDK backend runs its workers itself on the gateway route.

The SDK's own sub-agent does not finish on that route: a worker with no tools reports back, and a
worker that calls one runs, executes the tool, and then the delegating turn never receives a
result. The gateway answered every request it was given and the CLI stopped asking, so the stall
is inside a lifecycle nobody documents. These tests pin the replacement.
"""

import asyncio
import dataclasses

from fi.alk.harness.backends.base import (
    Call,
    ModelReply,
    Say,
    SessionSpec,
    StageDone,
    ToolReturned,
    ToolServer,
    WorkerSpec,
    tool,
)
from fi.alk.harness.backends.claude import (
    DELEGATION_HANDOFF,
    DELEGATION_SERVER,
    ClaudeSession,
    _child_of,
    _Desk,
)


@tool("add", "Add two numbers.", {"a": int, "b": int})
async def _add(args):
    return {"content": [{"type": "text", "text": f"the sum is {args['a'] + args['b']}"}]}


def _spec(**over) -> SessionSpec:
    base = dict(
        system_prompt="coordinate",
        builtins=("Delegate",),
        max_turns=12,
        model="vertex_ai/gemini-3.5-flash",
        workers={
            "adder": WorkerSpec(
                description="Adds two numbers.",
                instructions="Call add and report the total.",
                servers={"probe": ToolServer(name="probe", tools=[_add])},
                max_turns=20,
            )
        },
    )
    base.update(over)
    return SessionSpec(**base)


class _Child:
    """A child session that behaves the way a real one does, without a model behind it."""

    def __init__(self, replies):
        self._replies = replies
        self.started = False
        self.stopped = False
        self.sent: list[str] = []

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def send(self, message):
        self.sent.append(message)

    async def replies(self):
        for one in self._replies:
            yield one


def _worked(text="42", *, cost=0.25, outcome="success"):
    return [
        ModelReply(parts=[Call(id="c1", name="mcp__probe__add", arguments={"a": 21, "b": 21})]),
        ToolReturned(id="c1", text="the sum is 42"),
        ModelReply(parts=[Say(text=text)]),
        StageDone(
            outcome=outcome,
            turns=3,
            cost_usd=cost,
            tokens_in=100,
            tokens_out=20,
            tokens_cached=40,
            models={"vertex_ai/gemini-3.5-flash"},
        ),
    ]


def test_a_worker_reports_what_it_wrote_back_to_the_stage():
    spec = _spec()
    child = _Child(_worked())
    desk = _Desk(spec, lambda _: child)

    said = asyncio.run(desk._handle({"worker": "adder", "brief": "Add 21 and 21"}))

    assert not said.get("is_error"), said
    assert said["content"][0]["text"] == "42"
    assert child.started and child.stopped
    assert child.sent == ["Add 21 and 21"]


def test_every_call_a_worker_makes_carries_the_worker_that_made_it():
    """`session.py` keys its per-agent ledger on this. Empty, a stage's whole spend reads as the
    loop's own and nothing says which worker did what."""
    spec = _spec()
    desk = _Desk(spec, lambda _: _Child(_worked()))
    asyncio.run(desk._handle({"worker": "adder", "brief": "Add 21 and 21"}))

    calls = [
        part
        for reply in desk.drain()
        if isinstance(reply, ModelReply)
        for part in reply.parts
        if isinstance(part, Call)
    ]
    assert [one.name for one in calls] == ["mcp__probe__add"]
    assert [one.by for one in calls] == ["adder"]
    # Drained once and gone: the parent's stream must not see the same call twice.
    assert desk.drain() == []


def test_what_a_worker_spent_is_folded_into_the_stage_that_briefed_it():
    spec = _spec()
    desk = _Desk(spec, lambda _: _Child(_worked(cost=0.25)))
    asyncio.run(desk._handle({"worker": "adder", "brief": "Add 21 and 21"}))
    asyncio.run(desk._handle({"worker": "adder", "brief": "Add 1 and 1"}))

    assert desk.tokens_in == 200
    assert desk.tokens_out == 40
    assert desk.tokens_cached == 80
    assert desk.cost_usd == 0.5

    session = ClaudeSession(object(), desk)
    rolled = session._with_workers(
        StageDone(outcome="success", turns=2, cost_usd=1.0, tokens_in=10, tokens_out=5)
    )
    assert rolled.tokens_in == 210
    assert rolled.tokens_out == 45
    assert rolled.tokens_cached == 80
    assert rolled.cost_usd == 1.5
    assert rolled.models == {"vertex_ai/gemini-3.5-flash"}


def test_a_session_with_no_workers_is_left_exactly_as_it_was():
    session = ClaudeSession(object())
    done = StageDone(outcome="success", turns=2, cost_usd=1.0, tokens_in=10, tokens_out=5)
    assert session._with_workers(done) is done


def test_an_unknown_worker_or_an_empty_brief_is_refused_rather_than_run():
    spec = _spec()
    opened: list[SessionSpec] = []

    def open_child(child):
        opened.append(child)
        return _Child(_worked())

    desk = _Desk(spec, open_child)
    missing = asyncio.run(desk._handle({"worker": "nobody", "brief": "do a thing"}))
    assert missing.get("is_error")
    assert "adder" in missing["content"][0]["text"]

    blank = asyncio.run(desk._handle({"worker": "adder", "brief": "   "}))
    assert blank.get("is_error")
    # Neither reached a session, so neither cost anything.
    assert opened == []


def test_a_worker_that_dies_is_reported_rather_than_swallowed():
    class _Dies(_Child):
        async def send(self, message):
            raise RuntimeError("the gateway hung up")

    desk = _Desk(_spec(), lambda _: _Dies([]))
    said = asyncio.run(desk._handle({"worker": "adder", "brief": "Add 21 and 21"}))
    assert said.get("is_error")
    assert "the gateway hung up" in said["content"][0]["text"]


def test_a_worker_that_ends_badly_still_says_so():
    desk = _Desk(_spec(), lambda _: _Child(_worked(text="", outcome="max_turns")))
    said = asyncio.run(desk._handle({"worker": "adder", "brief": "Add 21 and 21"}))
    assert "max_turns" in said["content"][0]["text"]


def test_the_tool_a_stage_is_given_names_the_workers_it_can_run():
    desk = _Desk(_spec(), lambda _: _Child(_worked()))
    server = desk.server()
    assert server.name == DELEGATION_SERVER
    assert [one.name for one in server.tools] == [DELEGATION_HANDOFF]
    assert desk.tool_name == f"mcp__{DELEGATION_SERVER}__{DELEGATION_HANDOFF}"
    described = server.tools[0].description
    assert "adder" in described and "Adds two numbers." in described


def test_a_worker_falls_back_to_its_stage_and_never_hands_out_again():
    """Everything the worker did not name is the stage's, which is what makes it part of that
    stage. It gets no workers of its own: a worker that could hand out again would spend the
    stage's budget somewhere nobody is watching."""
    parent = _spec(cwd="/work", thinking=True, gated=True)
    child = _child_of(parent, parent.workers["adder"])

    assert child.system_prompt == "Call add and report the total."
    assert child.max_turns == 20
    assert child.model == parent.model
    assert child.cwd == "/work"
    assert child.thinking is True
    assert child.gated is True
    assert child.workers == {}
    assert "Delegate" not in child.builtins
    assert set(child.servers) == {"probe"}

    # A worker that names its own model keeps it; one that does not runs on the stage's.
    own = dataclasses.replace(parent.workers["adder"], model="vertex_ai/gemini-2.5-flash")
    assert _child_of(parent, own).model == "vertex_ai/gemini-2.5-flash"


def test_a_large_system_prompt_travels_as_a_file_not_as_argv() -> None:
    """One conversation opened against a live run crashed the guest with "Argument list too long"."""
    from fi.alk.harness.backends.claude import _prompt_for

    short = "stay inline"
    assert _prompt_for(short) == short
    assert _prompt_for(None) is None
    assert _prompt_for("") == ""

    # A stage prompt is the contract, the world summary and the skills, which is far past ARG_MAX.
    whole = "a stage prompt " * 4000
    handed = _prompt_for(whole)
    assert handed["type"] == "file"
    with open(handed["path"], encoding="utf-8") as written:
        assert written.read() == whole
