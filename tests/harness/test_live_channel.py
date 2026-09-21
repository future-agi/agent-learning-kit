"""The live channel: a person can talk to a stage while it is running, not after it."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from fi.alk.harness.backends import ModelReply, Say, SessionSpec, StageDone
from fi.alk.harness.live import Channel
from fi.alk.harness.session import Stage


class _Session:
    """A session that records what it was sent and answers each message once."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, message: str) -> None:
        self.sent.append(message)

    def replies(self):
        async def stream():
            yield ModelReply(parts=[Say(text="done")])
            yield StageDone(outcome="success", turns=1, cost_usd=0.0, models={"m"})

        return stream()


class _Backend:
    name = "stub"
    default_model = "m"

    def __init__(self, session: _Session) -> None:
        self._session = session

    def create(self, spec: SessionSpec) -> _Session:
        return self._session

    def can_drive(self, model: str) -> bool:
        return True


async def _run(stage: Stage, message: str) -> None:
    async with stage:
        async for _event in stage.stream(message):
            pass


def _stage(tmp_path, monkeypatch, *, live: bool) -> tuple[Stage, _Session]:
    if live:
        monkeypatch.setenv("ALK_HARNESS_CHAT_DIR", str(tmp_path))
    else:
        monkeypatch.delenv("ALK_HARNESS_CHAT_DIR", raising=False)
    session = _Session()
    return Stage(SessionSpec(system_prompt="x"), name="write-scenarios", backend=_Backend(session)), session


def test_a_message_sent_mid_stage_reaches_the_very_next_turn(tmp_path, monkeypatch):
    """The point of the whole change: it used to wait for the stage to end, and a stage is
    twenty minutes."""
    stage, session = _stage(tmp_path, monkeypatch, live=True)
    (tmp_path / "inbox.jsonl").write_text(
        json.dumps({"text": "drop the scenario about refunds"}) + "\n", encoding="utf-8"
    )

    asyncio.run(_run(stage, "carry on"))

    assert "drop the scenario about refunds" in session.sent[0]
    assert "carry on" in session.sent[0]


def test_the_same_message_is_never_delivered_twice(tmp_path, monkeypatch):
    stage, session = _stage(tmp_path, monkeypatch, live=True)
    (tmp_path / "inbox.jsonl").write_text(
        json.dumps({"text": "only once"}) + "\n", encoding="utf-8"
    )

    asyncio.run(_run(stage, "first"))
    asyncio.run(_run(stage, "second"))

    assert "only once" in session.sent[0]
    assert "only once" not in session.sent[1]


def test_every_event_is_written_out_as_it_happens(tmp_path, monkeypatch):
    """Tailed while the stage runs, so it cannot be written only at the end."""
    stage, _session = _stage(tmp_path, monkeypatch, live=True)

    asyncio.run(_run(stage, "go"))

    written = [
        json.loads(line)
        for line in (tmp_path / "outbox.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [one["kind"] for one in written] == ["text", "done"]
    assert written[0]["text"] == "done"
    assert written[0]["detail"]["stage"] == "write-scenarios"


def test_a_half_written_line_is_skipped_rather_than_taking_the_stage_down(
    tmp_path, monkeypatch
):
    """Another process is appending to this file; a torn line must not end the run."""
    stage, session = _stage(tmp_path, monkeypatch, live=True)
    (tmp_path / "inbox.jsonl").write_text(
        '{"text": "good"}\n{"text": "torn\n', encoding="utf-8"
    )

    asyncio.run(_run(stage, "go"))

    assert "good" in session.sent[0]


def test_a_run_with_no_channel_is_untouched(tmp_path, monkeypatch):
    """Every unattended run takes this path, so it has to cost exactly nothing."""
    stage, session = _stage(tmp_path, monkeypatch, live=False)

    asyncio.run(_run(stage, "go"))

    assert session.sent == ["go"]
    assert not stage.channel.live
    assert list(tmp_path.iterdir()) == []


def test_a_channel_with_nowhere_to_write_swallows_it(tmp_path):
    """Losing the transcript must never lose the run."""
    channel = Channel(tmp_path / "nested" / "under" / "a" / "file")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "under").write_text("not a directory", encoding="utf-8")

    channel.record("text", "this cannot be written")


def test_a_message_reaches_a_stage_that_never_gets_a_second_message(tmp_path, monkeypatch):
    """The case the first design missed. A scenario stage is sent one opening message and then
    runs for twenty minutes driving tools, so an inbox checked per sent message is checked once
    and never again. Tool results are what it reads constantly, so the handover rides on those."""
    from fi.alk.harness.backends import ToolServer, tool
    from fi.alk.harness.live import Channel, carrying

    async def handler(arguments):
        return {"content": [{"type": "text", "text": "9 rows"}]}

    spec = tool("inspect_world", "look", {"table": str})(handler)
    monkeypatch.setenv("ALK_HARNESS_CHAT_DIR", str(tmp_path))
    channel = Channel()
    servers = carrying(channel, {"scenarios": ToolServer(name="scenarios", tools=[spec])})
    carried = servers["scenarios"].tools[0].handler

    before = asyncio.run(carried({"table": "orders"}))
    assert [one["text"] for one in before["content"]] == ["9 rows"]

    (tmp_path / "inbox.jsonl").write_text(
        json.dumps({"text": "how many have you written?"}) + "\n", encoding="utf-8"
    )
    after = asyncio.run(carried({"table": "orders"}))

    assert after["content"][0]["text"] == "9 rows"
    assert "how many have you written?" in after["content"][-1]["text"]

    # It rides in again, because a model part way through a burst of calls reads one result and
    # keeps going: handing it over once is handing it over to nobody.
    again = asyncio.run(carried({"table": "orders"}))
    assert "how many have you written?" in again["content"][-1]["text"]

    # Until the stage actually says something, which is the proof it read it.
    channel.record("text", "I have kept 3 so far.")
    after_speaking = asyncio.run(carried({"table": "orders"}))
    assert len(after_speaking["content"]) == 1


def test_a_worker_never_carries_a_message_meant_for_the_stage(tmp_path, monkeypatch):
    """A writer told to stop and answer a question addressed to the stage that briefed it would
    answer for everybody, and would eat the message on the way."""
    from fi.alk.harness.backends import SessionSpec, ToolServer, WorkerSpec, tool
    from fi.alk.harness.live import Channel

    async def handler(arguments):
        return {"content": [{"type": "text", "text": "written"}]}

    worker_tool = tool("submit_scenario", "save", {"name": str})(handler)
    worker_server = ToolServer(name="writing", tools=[worker_tool])
    monkeypatch.setenv("ALK_HARNESS_CHAT_DIR", str(tmp_path))
    spec = SessionSpec(system_prompt="x", servers={"scenarios": ToolServer(name="scenarios")})
    spec.workers = {"writer": WorkerSpec(description="w", instructions="w", servers={"writing": worker_server})}

    stage = Stage(spec, name="write-scenarios", backend=_Backend(_Session()))
    asyncio.run(_run(stage, "go"))

    assert spec.workers["writer"].servers["writing"].tools[0].handler is handler


def test_a_stage_that_cannot_call_its_workers_is_refused_at_once():
    """Two hosted attempts, an hour each, ended with a stage that could only read: it wrote the
    suite out as prose, said the tools were not connected, and reported success with nothing
    saved. A stage with workers and no way to call them is not a run, it is an outage."""
    import pytest

    from fi.alk.harness.backends import SessionSpec, ToolServer, WorkerSpec
    from fi.alk.harness.backends.claude import _can_reach_its_workers

    spec = SessionSpec(system_prompt="x", servers={"scenarios": ToolServer(name="scenarios")})
    spec.workers = {"writer": WorkerSpec(description="w", instructions="w")}

    # The SDK's own sub-agent tools. There is no second delegation implementation to reach for.
    _can_reach_its_workers(spec, ["Agent", "Task"])
    _can_reach_its_workers(SessionSpec(system_prompt="x"), ["mcp__scenarios__inspect_world"])

    with pytest.raises(ValueError) as refused:
        _can_reach_its_workers(spec, ["mcp__scenarios__inspect_world", "mcp__scenarios__suite_progress"])
    assert "no way to call them" in str(refused.value)
    assert "writer" in str(refused.value)


def test_a_stage_that_never_speaks_is_not_nagged_for_ever(tmp_path, monkeypatch):
    """Repeating is for surviving a burst of tool calls, not for wearing the model down."""
    from fi.alk.harness.backends import ToolServer, tool
    from fi.alk.harness.live import HANDOVERS, Channel, carrying

    async def handler(arguments):
        return {"content": [{"type": "text", "text": "9 rows"}]}

    spec = tool("inspect_world", "look", {"table": str})(handler)
    monkeypatch.setenv("ALK_HARNESS_CHAT_DIR", str(tmp_path))
    channel = Channel()
    servers = carrying(channel, {"scenarios": ToolServer(name="scenarios", tools=[spec])})
    carried = servers["scenarios"].tools[0].handler
    (tmp_path / "inbox.jsonl").write_text(
        json.dumps({"text": "answer me"}) + "\n", encoding="utf-8"
    )

    carried_counts = [
        len(asyncio.run(carried({"table": "orders"}))["content"])
        for _ in range(HANDOVERS + 2)
    ]

    assert carried_counts[:HANDOVERS] == [2] * HANDOVERS
    assert carried_counts[HANDOVERS:] == [1, 1]


def test_a_chat_caller_is_not_made_to_have_an_accent():
    """A chat suite came back with a caller in the United Kingdom, named Mei-Ling Zhou, given an
    Indian accent, because the field was required and nothing in the situation said what to put
    there. Off a call there is no voice to pick, so the field is not asked for."""
    from fi.alk.harness.scenario import Persona

    typed = Persona(
        name="Mei-Ling Zhou",
        personality="Analytical",
        communication_style="Technical",
        initial_message="what is the status of ord_2",
        languages=["English"],
        keywords=["order_lookup"],
    )

    assert typed.missing_profile_fields(spoken=False) == []
    spoken = typed.missing_profile_fields()
    assert "accent" in spoken and "gender" in spoken and "age_group" in spoken


def test_redefining_a_sub_goal_reports_the_scenarios_it_breaks(tmp_path, monkeypatch):
    """Found on a real suite: three of sixty shipped graded by a check nothing had ever run
    against them. `refresh_check` rewrites the check file in every scenario naming the sub-goal,
    and a scenario proved against one definition kept its proof while inheriting another."""
    from fi.alk.harness.catalogue import Catalogue, SubGoal
    from fi.alk.harness.folder import refresh_check
    from fi.alk.harness.scenario_tools import _no_longer_hold

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from test_harness import _built_environment, _delta

    holds = SubGoal(
        name="item-added",
        what="the item reached the cart",
        check=(
            "def check(world, calls):\n"
            "    rows = world.state()['cart']\n"
            "    if len(rows) != 1: return '%d rows, expected 1' % len(rows)\n"
            "    return None\n"
        ),
    )
    impossible = SubGoal(
        name="item-added",
        what="the item reached the cart and the agent handed off",
        check=(
            "def check(world, calls):\n"
            "    if not any(c.name == 'transfer_to_human' and c.ok for c in calls):\n"
            "        return 'the agent never handed off'\n"
            "    return None\n"
        ),
    )

    root, _contract, _catalogue = _built_environment(tmp_path)
    kept: list = []
    from fi.alk.harness.scenario_tools import accept_scenario

    catalogue = Catalogue(sub_goals=[holds, _right_item()])
    accepted = accept_scenario(
        _delta(sub_goals=["item-added"]), world_root=root, catalogue=catalogue, kept=kept
    )
    assert not accepted.get("is_error"), accepted

    # The sub-goal is redefined to something this scenario's own solution cannot satisfy.
    catalogue.sub_goals = [impossible, _right_item()]
    restated = refresh_check(root, impossible)
    assert restated == ["adds-a-big-mac"]

    broken = _no_longer_hold(root, catalogue, restated, root)

    assert [name for name, _why in broken] == ["adds-a-big-mac"]
    assert "reference solution" in broken[0][1]


def _right_item():
    from fi.alk.harness.catalogue import SubGoal

    return SubGoal(
        name="right-item",
        what="the call carried the item that was asked for",
        check=(
            "def check(world, calls):\n"
            "    if not any(c.name == 'add' for c in calls): return 'no add'\n"
            "    return None\n"
        ),
    )


def test_a_question_waits_for_its_answer_and_gets_it(tmp_path, monkeypatch):
    """The model asked because it cannot sensibly go on, so it waits rather than guessing."""
    import threading

    from fi.alk.harness.live import Channel

    monkeypatch.setenv("ALK_HARNESS_CHAT_DIR", str(tmp_path))
    channel = Channel()

    def answer_shortly():
        for _ in range(100):
            if (tmp_path / "outbox.jsonl").is_file():
                asked = [
                    json.loads(line)
                    for line in (tmp_path / "outbox.jsonl").read_text().splitlines()
                ]
                ask = next((one for one in asked if one["kind"] == "ask"), None)
                if ask:
                    with (tmp_path / "inbox.jsonl").open("a", encoding="utf-8") as stream:
                        stream.write(
                            json.dumps(
                                {"answer_to": ask["detail"]["ask_id"], "pick": "voice"}
                            )
                            + "\n"
                        )
                    return
            time.sleep(0.05)

    threading.Thread(target=answer_shortly, daemon=True).start()
    got = channel.ask(
        {"prompt": "Which modality is being tested?", "options": ["voice", "chat"]},
        timeout=10,
    )

    assert got["answered"] is True
    assert got["pick"] == "voice"


def test_a_question_nobody_answers_gives_up_rather_than_holding_the_sandbox(tmp_path, monkeypatch):
    from fi.alk.harness.live import Channel

    monkeypatch.setenv("ALK_HARNESS_CHAT_DIR", str(tmp_path))

    got = Channel().ask({"prompt": "anyone there?"}, timeout=0.2)

    assert got["answered"] is False
    assert "nobody answered" in got["reason"]


def test_an_answer_is_not_delivered_again_as_a_fresh_message(tmp_path, monkeypatch):
    """Both travel in the inbox. An answer read as a message would hand the model its own reply."""
    from fi.alk.harness.live import Channel

    monkeypatch.setenv("ALK_HARNESS_CHAT_DIR", str(tmp_path))
    (tmp_path / "inbox.jsonl").write_text(
        json.dumps({"text": "a real message"}) + "\n"
        + json.dumps({"answer_to": "abc", "pick": "voice", "text": "voice"}) + "\n",
        encoding="utf-8",
    )

    assert Channel().waiting() == ["a real message"]


def test_the_outcome_a_scenario_exists_for_must_be_asserted(tmp_path):
    """Six of sixty on a real suite called get_booking_status last, said in their tests line that
    they checked the status, and named only booking sub-goals. An agent that booked and never
    looked it up passed all six. The chat found it; this catches it."""
    from fi.alk.harness.folder import unasserted_behaviour
    from fi.alk.harness.scenario import Scenario

    scenario = Scenario(
        name="books-then-checks",
        instruction="Book it, then tell me the status.",
        tests="whether the agent checks the booking status after making it",
        solution=[{"tool": "book_ride", "arguments": {}}, {"tool": "get_booking_status", "arguments": {}}],
        sub_goals=["booked"],
    )
    checks = tmp_path / "scenarios" / "books-then-checks" / "checks"
    checks.mkdir(parents=True)
    (checks / "booked.py").write_text(
        "def check(world, calls):\n"
        "    return None if any(c.name == 'book_ride' and c.ok for c in calls) else 'no booking'\n",
        encoding="utf-8",
    )

    said = unasserted_behaviour([scenario], tmp_path)

    assert len(said) == 1
    assert "get_booking_status" in said[0]
    assert "stops short of it" in said[0]


def test_a_confirming_read_at_the_end_is_not_remarked_on(tmp_path):
    """The second cry-wolf: flagging every unasserted last call reported 13 of 30, because a
    solution that ends `view_cart` to confirm what it just did is not a scenario about viewing the
    cart. What separates the real fault is the scenario claiming to test that thing."""
    from fi.alk.harness.folder import unasserted_behaviour
    from fi.alk.harness.scenario import Scenario

    scenario = Scenario(
        name="add-a-big-mac",
        instruction="Add a Big Mac.",
        tests="whether the agent puts the right item in the cart",
        solution=[{"tool": "add_item", "arguments": {}}, {"tool": "view_cart", "arguments": {}}],
        sub_goals=["added"],
    )
    checks = tmp_path / "scenarios" / "add-a-big-mac" / "checks"
    checks.mkdir(parents=True)
    (checks / "added.py").write_text(
        "def check(world, calls):\n"
        "    return None if any(c.name == 'add_item' and c.ok for c in calls) else 'nothing added'\n",
        encoding="utf-8",
    )

    assert unasserted_behaviour([scenario], tmp_path) == []


def test_a_lookup_the_solution_merely_passes_through_is_not_remarked_on(tmp_path):
    """Flagging every unasserted tool reported 53 of 60, which is the cry-wolf failure this file
    already learned once. Only the last call counts."""
    from fi.alk.harness.folder import unasserted_behaviour
    from fi.alk.harness.scenario import Scenario

    scenario = Scenario(
        name="books-a-ride",
        instruction="Book it.",
        tests="whether the agent books what was asked for",
        solution=[
            {"tool": "get_payment_methods", "arguments": {}},
            {"tool": "book_ride", "arguments": {}},
        ],
        sub_goals=["booked"],
    )
    checks = tmp_path / "scenarios" / "books-a-ride" / "checks"
    checks.mkdir(parents=True)
    (checks / "booked.py").write_text(
        "def check(world, calls):\n"
        "    return None if any(c.name == 'book_ride' and c.ok for c in calls) else 'no booking'\n",
        encoding="utf-8",
    )

    assert unasserted_behaviour([scenario], tmp_path) == []


def test_an_instruction_may_not_tell_the_caller_what_the_agent_must_do():
    """Seven of sixty on a real suite said things like "The agent must enforce the minor safety
    policy". The caller is not the examiner: handed the answer, they lead the agent to it."""
    from fi.alk.harness.catalogue import Catalogue, SubGoal
    from fi.alk.harness.scenario import Scenario, validate_scenario

    catalogue = Catalogue(
        sub_goals=[SubGoal(name="refused", what="refused", judged="from the transcript")]
    )

    def problems_for(instruction: str) -> list:
        scenario = Scenario(
            name="solo-minor",
            instruction=instruction,
            tests="whether the agent refuses a solo minor and hands off",
            solution=[{"tool": "transfer_to_human", "arguments": {}}],
            sub_goals=["refused"],
        )
        return validate_scenario(scenario, catalogue, {}, "")

    directed = problems_for(
        "You are 13 and travelling alone. The agent must enforce the minor safety policy."
    )
    assert any("tells the person what the agent must do" in one for one in directed)

    # What the caller is told to expect is theirs to know, and stays allowed.
    anticipated = problems_for(
        "You are 13 and travelling alone. The agent will ask your age; say thirteen."
    )
    assert not any("tells the person what the agent must do" in one for one in anticipated)


def test_a_scenario_must_place_on_every_axis_the_plan_deals():
    """A suite of fifteen showed three scenarios in its overlay rows because twelve omitted the
    axis rather than setting its ordinary level. The grid then does not add up to the suite, which
    is exactly the screen that cannot be shown to anyone."""
    from fi.alk.harness.scenario_tools import _off_the_grid

    grid = {"task": ["book_ride", "cancel_ride"], "overlay": ["none", "prompt_injection"]}

    assert _off_the_grid({"task": "book_ride", "overlay": "none"}, grid) == ""

    missing = _off_the_grid({"task": "book_ride"}, grid)
    assert "says nothing about overlay" in missing
    assert "stops adding up to the suite" in missing

    # No grid declared means no opinion, exactly as before.
    assert _off_the_grid({"task": "book_ride"}, None) == ""
