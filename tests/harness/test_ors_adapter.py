"""The ORS mapping, including the places the standard leaves undefined.

Written from the published specification, so these tests are also the checklist to run against
the pinned reference client: where a test asserts something the spec does not state, it says so.
"""

from __future__ import annotations

import json

import pytest

from fi.alk.harness.episode_driver import Episode, PublicTool
from fi.alk.harness.ors_adapter import (
    CHUNK_BYTES,
    GRADING_FIELD,
    OrsError,
    SessionStore,
    Split,
    SplitType,
    call_events,
    create_episode,
    create_session,
    delete_episode,
    grading_status,
    health,
    prompt_blocks,
    splits_response,
    task_range,
    tasks_response,
    terminal_output,
    tools_response,
)
from fi.alk.harness.terminal_verdict import Decision, Status
from fi.alk.harness.world_door import Outcome, ToolCall


class _Catalogue:
    def published(self):
        return [
            PublicTool("look_up_order", "Find an order", ("order_id",)),
            PublicTool("refund", "Refund an order", ("order_id",)),
            PublicTool("say", "Speak to the customer", ("message",)),
        ]

    def world_action(self, tool: str) -> str:
        return f"orders.{tool}"

    def effect(self, tool: str) -> str:
        return "write" if tool == "refund" else "read"


class _Door:
    def __init__(self, outcome: Outcome | None = None) -> None:
        self.calls: list[ToolCall] = []
        self.outcome = outcome or Outcome(outcome="committed", status=200, result={"ok": True})

    def enter(self, call: ToolCall) -> Outcome:
        self.calls.append(call)
        return self.outcome


def _episode(door: _Door | None = None) -> Episode:
    return Episode(
        episode_id="ep-1",
        prompt="Refund the customer if policy allows.",
        door=door or _Door(),  # type: ignore[arg-type]
        catalogue=_Catalogue(),
    )


def _events(stream) -> list[tuple[str, dict]]:
    """SSE text parsed back into (event name, data) pairs."""
    parsed: list[tuple[str, dict]] = []
    for block in "".join(stream).split("\n\n"):
        if not block.strip():
            continue
        lines = dict(line.split(": ", 1) for line in block.strip().split("\n"))
        parsed.append((lines["event"], json.loads(lines["data"])))
    return parsed


# --- discovery -------------------------------------------------------------------------


def test_health_and_environments() -> None:
    assert health() == {"status": "ok"}


def test_splits_carry_their_type_and_default_to_validation() -> None:
    body = splits_response([Split("train", SplitType.TRAIN), Split("holdout")])
    assert body == [
        {"name": "train", "type": "train"},
        {"name": "holdout", "type": "validation"},
    ]


def test_tasks_are_opaque_handles_only() -> None:
    """A caller must never hold anything that could substitute for the real task."""
    body = tasks_response("retail-returns", ["h1", "h2"])
    assert body == {"env_name": "retail-returns", "tasks": ["h1", "h2"]}


def test_task_range_uses_python_slice_semantics() -> None:
    handles = ["a", "b", "c", "d"]
    assert task_range(handles, 1, 3) == ["b", "c"]
    assert task_range(handles, None, 2) == ["a", "b"]
    assert task_range(handles, 2, None) == ["c", "d"]


def test_the_prompt_is_blocks_and_includes_dialogue_so_far() -> None:
    episode = _episode()
    episode.say(action_id="s1", tool="say", message="Let me check that order.")
    blocks = prompt_blocks(episode.observe())
    assert blocks[0] == {"type": "text", "text": "Refund the customer if policy allows."}
    # Dialogue the participant has heard follows the task, so a reconnecting client can
    # rebuild the conversation from the prompt alone.
    assert blocks[1] == {"type": "text", "text": "Let me check that order."}


def test_published_tools_expose_names_only_no_routes() -> None:
    body = tools_response(_episode().observe())
    assert [tool["name"] for tool in body] == ["look_up_order", "refund", "say"]
    assert set(body[0]["input_schema"]["properties"]) == {"order_id"}
    assert "effect" not in body[0] and "world_action" not in body[0]


# --- session lifecycle -----------------------------------------------------------------


def test_a_session_is_minted_empty_then_instantiated() -> None:
    """The spec's split: /create_session only generates an id."""
    store = SessionStore()
    sid = create_session(store)["sid"]
    session = store.require(sid)
    assert session.episode is None
    create_episode(session, _episode())
    assert session.episode is not None


def test_one_episode_per_session_is_the_specs_own_shape() -> None:
    """Creating twice on one id is the spec's 400 "Session already exists"."""
    store = SessionStore()
    session = store.require(create_session(store)["sid"])
    create_episode(session, _episode())
    with pytest.raises(OrsError) as raised:
        create_episode(session, _episode())
    assert raised.value.status == 400


def test_a_missing_header_unknown_session_and_deleted_session_differ() -> None:
    """410 rather than 404 after deletion: a late retry is not the same as a bug."""
    store = SessionStore()
    with pytest.raises(OrsError) as missing:
        store.require(None)
    assert missing.value.status == 400

    with pytest.raises(OrsError) as unknown:
        store.require("nope")
    assert unknown.value.status == 404

    session = store.require(create_session(store)["sid"])
    create_episode(session, _episode())
    delete_episode(session)
    with pytest.raises(OrsError) as gone:
        store.require(session.sid)
    assert gone.value.status == 410


def test_calling_before_create_is_refused() -> None:
    store = SessionStore()
    session = store.require(create_session(store)["sid"])
    events = _events(call_events(session, name="refund", arguments={"order_id": "o1"}))
    assert events[0][0] == "task_id"
    assert events[-1] == ("error", events[-1][1]) and "no episode" in events[-1][1]["error"]


# --- actions ---------------------------------------------------------------------------


def test_the_event_order_is_task_id_then_end() -> None:
    store = SessionStore()
    session = store.require(create_session(store)["sid"])
    create_episode(session, _episode())
    events = _events(
        call_events(session, name="refund", arguments={"order_id": "o1"}, task_id="t1")
    )
    assert [name for name, _ in events] == ["task_id", "end"]
    assert events[0][1] == {"task_id": "t1"}
    assert events[1][1] == {"ok": True, "output": {"ok": True}}


def test_a_reconnect_replays_rather_than_acting_twice() -> None:
    """The spec's reconnection rule, and the reason a lost response is safe."""
    door = _Door()
    store = SessionStore()
    session = store.require(create_session(store)["sid"])
    create_episode(session, _episode(door))

    first = _events(call_events(session, name="refund", arguments={"order_id": "o1"}, task_id="t1"))
    again = _events(call_events(session, name="refund", arguments={"order_id": "o1"}, task_id="t1"))

    assert first[-1][1] == again[-1][1], "the replay must return the original result"
    assert len(door.calls) == 1, "a reconnect must not reach the world a second time"


def test_an_unpublished_tool_ends_with_ok_false_not_a_crash() -> None:
    store = SessionStore()
    session = store.require(create_session(store)["sid"])
    create_episode(session, _episode())
    events = _events(call_events(session, name="query", arguments={}, task_id="t1"))
    assert events[-1][0] == "end"
    assert events[-1][1]["ok"] is False


def test_a_refused_action_reports_a_code_and_no_output() -> None:
    door = _Door(Outcome(outcome="refused", status=403, refusal_code="not_permitted"))
    store = SessionStore()
    session = store.require(create_session(store)["sid"])
    create_episode(session, _episode(door))
    events = _events(call_events(session, name="refund", arguments={"order_id": "o1"}, task_id="t1"))
    assert events[-1][1] == {"ok": False, "error": "not_permitted"}


def test_a_large_payload_is_chunked_before_end() -> None:
    big = {"rows": ["x" * 200 for _ in range(60)]}
    door = _Door(Outcome(outcome="committed", status=200, result=big))
    store = SessionStore()
    session = store.require(create_session(store)["sid"])
    create_episode(session, _episode(door))
    events = _events(call_events(session, name="look_up_order", arguments={"order_id": "o1"}, task_id="t1"))
    names = [name for name, _ in events]
    assert names[0] == "task_id" and names[-1] == "end"
    assert "chunk" in names
    rebuilt = json.loads("".join(data["data"] for name, data in events if name == "chunk"))
    assert rebuilt["output"] == big
    assert len(json.dumps(big).encode()) > CHUNK_BYTES


# --- reward ----------------------------------------------------------------------------


def test_a_graded_pass_reports_reward_one_and_graded_true() -> None:
    body = terminal_output(Decision(status=Status.PASSED))
    assert body["reward"] == 1.0
    assert body["finished"] is True
    assert body[GRADING_FIELD]["graded"] is True


def test_an_ungraded_trial_reports_null_reward_and_says_why() -> None:
    """The gap ORS leaves: null is undefined, so our field carries the meaning."""
    decision = Decision(
        status=Status.ERRORED, failure_code="judge_undecided", failure_detail="no decision"
    )
    body = terminal_output(decision)
    assert body["reward"] is None, "never 0 for an ungraded trial"
    assert body[GRADING_FIELD] == {
        "graded": False,
        "status": "errored",
        "code": "judge_undecided",
        "detail": "no decision",
    }


def test_grading_status_is_what_a_trainer_branches_on() -> None:
    assert grading_status(Decision(status=Status.FAILED))["graded"] is True
    assert grading_status(Decision(status=Status.ERRORED))["graded"] is False


def test_delete_preserves_the_grade_it_is_given() -> None:
    store = SessionStore()
    session = store.require(create_session(store)["sid"])
    create_episode(session, _episode())
    body = delete_episode(session, decision=Decision(status=Status.PASSED))
    assert body["reward"] == 1.0
    assert body[GRADING_FIELD]["graded"] is True
