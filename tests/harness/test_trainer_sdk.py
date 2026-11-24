"""The trainer SDK, driven against a scripted transport.

The rules under test are the ones a customer gets wrong at cost: treating an ungraded trial as
a zero, retrying a write that may already have landed, and leaving an episode open.
"""

from __future__ import annotations

import json
from typing import Any, Iterator, Mapping

import pytest

from fi.alk.trainer import (
    ActionAmbiguous,
    Episode,
    EpisodeResult,
    TrainerError,
    episode,
    tasks,
)


class _Transport:
    """A scripted service. Records what was sent so the client's behaviour can be asserted."""

    def __init__(self, *, reward: Any = 1.0, graded: bool = True, grading: dict | None = None) -> None:
        self.requests: list[tuple[str, str, Any]] = []
        self.streams: list[dict[str, Any]] = []
        self.reward = reward
        self.graded = graded
        self.grading = grading
        # Scripted event sequences, consumed in order; falls back to a committed action.
        self.scripted: list[list[tuple[str, Any]]] = []
        self.results: dict[str, dict[str, Any]] = {}

    def request(
        self, method: str, url: str, *, headers: Mapping[str, str], json_body: Any = None
    ) -> tuple[int, Any]:
        self.requests.append((method, url, json_body))
        if url.endswith("/create_session"):
            return 200, {"sid": "sid-1"}
        if url.endswith("/create"):
            return 200, {"sid": "sid-1"}
        if url.endswith("/delete"):
            grading = self.grading or {
                "graded": self.graded,
                "status": "passed" if self.graded else "errored",
                "code": None if self.graded else "judge_undecided",
                "detail": None if self.graded else "the judge did not decide",
            }
            return 200, {"ok": True, "reward": self.reward, "futureagi_grading": grading}
        if url.endswith("/prompt"):
            return 200, [{"type": "text", "text": "Refund if policy allows."},
                         {"type": "text", "text": "Hello?"}]
        if url.endswith("/task_tools"):
            return 200, [{"name": "refund", "input_schema": {"properties": {"order_id": {}}}}]
        if url.endswith("/tasks"):
            return 200, {"env_name": "retail-returns", "tasks": ["h1", "h2"]}
        return 404, {"detail": "not scripted"}

    def stream(
        self, method: str, url: str, *, headers: Mapping[str, str], json_body: Any = None
    ) -> Iterator[tuple[str, Any]]:
        self.streams.append(dict(json_body or {}))
        action_id = str((json_body or {}).get("task_id"))
        if self.scripted:
            yield from self.scripted.pop(0)
            return
        stored = self.results.get(action_id)
        if stored is not None:
            yield "task_id", {"task_id": action_id}
            yield "end", stored
            return
        body = {"ok": True, "output": {"refunded": True}}
        self.results[action_id] = body
        yield "task_id", {"task_id": action_id}
        yield "end", body


def _open(transport: _Transport) -> Any:
    return episode(
        "retail-returns",
        split="train",
        index=0,
        base_url="https://example.invalid",
        api_key="k",
        transport=transport,
    )


# --- the rule that matters most --------------------------------------------------------


def test_an_ungraded_trial_gives_no_reward_not_a_zero() -> None:
    """A judge outage must not look like the model doing the wrong thing."""
    transport = _Transport(reward=None, graded=False)
    with _open(transport) as open_episode:
        result = open_episode.finish()
    assert result.reward is None
    assert result.graded is False
    assert result.usable is False
    assert result.grading_code == "judge_undecided"


def test_a_graded_failure_is_a_real_zero() -> None:
    transport = _Transport(reward=0.0, graded=True, grading={"graded": True, "status": "failed"})
    with _open(transport) as open_episode:
        result = open_episode.finish()
    assert result.reward == 0.0
    assert result.graded is True
    assert result.usable is True, "a graded zero is a usable training label"


def test_a_null_reward_claiming_to_be_graded_is_still_not_usable() -> None:
    """Defend against the service contradicting itself rather than trusting it."""
    transport = _Transport(reward=None, graded=True, grading={"graded": True, "status": "passed"})
    with _open(transport) as open_episode:
        result = open_episode.finish()
    assert result.reward is None
    assert result.usable is False


# --- retries and ambiguity -------------------------------------------------------------


def test_a_dropped_stream_reconnects_on_the_same_id_rather_than_acting_again() -> None:
    """Re-sending the same action id is a reconnect; a new id would be a second refund."""
    transport = _Transport()
    transport.scripted = [
        [("task_id", {"task_id": "ignored"}), ("error", {"error": "connection reset"})],
        [("task_id", {"task_id": "ignored"}), ("end", {"ok": True, "output": {"refunded": True}})],
    ]
    with _open(transport) as open_episode:
        result = open_episode.call("refund", order_id="ORD-1041")
    assert result.ok is True
    sent = [one["task_id"] for one in transport.streams]
    assert len(sent) == 2 and sent[0] == sent[1], "the reconnect must reuse the action id"


def test_a_second_failure_is_reported_rather_than_retried_forever() -> None:
    """One reconnect, then stop: a write that may have landed is not safe to repeat."""
    transport = _Transport()
    transport.scripted = [
        [("error", {"error": "connection reset"})],
        [("error", {"error": "connection reset again"})],
    ]
    with pytest.raises(ActionAmbiguous):
        with _open(transport) as open_episode:
            open_episode.call("refund", order_id="ORD-1041")


def test_a_stream_that_ends_with_no_result_is_ambiguous() -> None:
    transport = _Transport()
    transport.scripted = [[("task_id", {"task_id": "t1"})]]
    with pytest.raises(ActionAmbiguous, match="without a result"):
        with _open(transport) as open_episode:
            open_episode.call("refund", order_id="ORD-1041")


def test_reconnect_returns_none_before_anything_was_sent() -> None:
    with _open(_Transport()) as open_episode:
        assert open_episode.reconnect() is None


# --- the episode's shape ---------------------------------------------------------------


def test_the_world_is_released_even_when_the_block_raises() -> None:
    """An open episode holds a world, so the exit path must run on failure too."""
    transport = _Transport()
    with pytest.raises(ZeroDivisionError):
        with _open(transport):
            raise ZeroDivisionError("the caller's own bug")
    assert any(url.endswith("/delete") for _, url, _ in transport.requests)


def test_finishing_twice_is_refused() -> None:
    transport = _Transport()
    with _open(transport) as open_episode:
        open_episode.finish()
        with pytest.raises(TrainerError, match="already finished"):
            open_episode.finish()


def test_acting_after_finishing_is_refused() -> None:
    transport = _Transport()
    with _open(transport) as open_episode:
        open_episode.finish()
        with pytest.raises(TrainerError, match="finished"):
            open_episode.call("refund", order_id="ORD-1041")


def test_opening_needs_exactly_one_of_index_or_task() -> None:
    transport = _Transport()
    for kwargs in [{}, {"index": 0, "task": "h1"}]:
        with pytest.raises(TrainerError, match="exactly one"):
            with episode("e", base_url="https://x.invalid", api_key="k",
                         transport=transport, **kwargs):
                pass


def test_a_refusal_is_a_result_not_an_exception() -> None:
    """A model should be able to see a refusal and respond to it, as a person would."""
    transport = _Transport()
    transport.scripted = [[("end", {"ok": False, "error": "not_permitted"})]]
    with _open(transport) as open_episode:
        result = open_episode.call("refund", order_id="ORD-2298")
    assert result.ok is False
    assert result.error == "not_permitted"
    assert result.output is None


def test_a_chunked_result_is_reassembled() -> None:
    big = {"ok": True, "output": {"rows": ["x" * 100 for _ in range(40)]}}
    encoded = json.dumps(big)
    transport = _Transport()
    transport.scripted = [
        [
            ("task_id", {"task_id": "t1"}),
            ("chunk", {"data": encoded[: len(encoded) // 2]}),
            ("chunk", {"data": encoded[len(encoded) // 2 :]}),
            ("end", {"chunked": True}),
        ]
    ]
    with _open(transport) as open_episode:
        result = open_episode.call("look_up_order", order_id="ORD-1041")
    assert result.ok is True
    assert result.output == big["output"]


def test_the_prompt_reads_as_text_including_dialogue() -> None:
    with _open(_Transport()) as open_episode:
        assert open_episode.prompt() == "Refund if policy allows.\nHello?"


def test_the_session_header_is_sent_on_every_call() -> None:
    transport = _Transport()
    with _open(transport) as open_episode:
        open_episode.prompt()
        open_episode.call("refund", order_id="ORD-1041")
    created = [body for _, url, body in transport.requests if url.endswith("/create")]
    assert created and created[0]["split"] == "train" and created[0]["index"] == 0


def test_tasks_returns_opaque_handles() -> None:
    transport = _Transport()
    handles = tasks(
        "retail-returns", split="train", base_url="https://x.invalid",
        api_key="k", transport=transport,
    )
    assert handles == ["h1", "h2"]


def test_a_gone_session_says_it_was_already_closed() -> None:
    class _Gone(_Transport):
        def request(self, method, url, *, headers, json_body=None):
            if url.endswith("/prompt"):
                return 410, {"detail": "session deleted"}
            return super().request(method, url, headers=headers, json_body=json_body)

    with _open(_Gone()) as open_episode:
        with pytest.raises(TrainerError, match="already closed"):
            open_episode.prompt()


def test_result_usable_is_the_field_to_branch_on() -> None:
    assert EpisodeResult(reward=1.0, graded=True, status="passed").usable is True
    assert EpisodeResult(reward=0.0, graded=True, status="failed").usable is True
    assert EpisodeResult(reward=None, graded=False, status="errored").usable is False
