"""The ORS routes over real HTTP, through FastAPI's test client.

The adapter's own tests prove the protocol mapping; these prove the wiring — status codes,
headers, SSE framing and the 400/404/410 distinctions a client branches on. Together with
`test_rl_end_to_end.py` (SDK against adapter, no HTTP) this closes the loop on both sides of
the transport.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fi.alk.harness.episode_driver import Episode, PublicTool
from fi.alk.harness.ors_routes import ors_router
from fi.alk.harness.terminal_verdict import Decision, Status
from fi.alk.harness.world_door import Outcome, ToolCall


class _Door:
    def __init__(self) -> None:
        self.calls: list[ToolCall] = []

    def enter(self, call: ToolCall) -> Outcome:
        self.calls.append(call)
        return Outcome(outcome="committed", status=200, result={"refunded": True})


class _Catalogue:
    def published(self):
        return [
            PublicTool("look_up_order", "Find an order", ("order_id",)),
            PublicTool("refund", "Refund an order", ("order_id",)),
        ]

    def world_action(self, tool: str) -> str:
        return f"orders.{tool}"

    def effect(self, tool: str) -> str:
        return "write" if tool == "refund" else "read"


class _Releases:
    """A sandbox holding one environment with two tasks."""

    HANDLES = ["ORD-1041", "ORD-2298"]

    def __init__(self, decision: Decision | None = None) -> None:
        self.door = _Door()
        self.decision = decision or Decision(status=Status.PASSED)
        self.opened: list[str] = []

    def environments(self):
        return ["retail-returns"]

    def splits(self, environment: str):
        from fi.alk.harness.ors_adapter import Split, SplitType

        return [Split("train", SplitType.TRAIN), Split("test", SplitType.TEST)]

    def task_handles(self, environment: str, split: str):
        return list(self.HANDLES)

    def open_episode(self, environment, *, split, index, task):
        handle = task if task is not None else self.HANDLES[index]
        if handle not in self.HANDLES:
            raise KeyError(handle)
        self.opened.append(handle)
        return Episode(
            episode_id=f"ep-{handle}",
            prompt=f"The customer asks about {handle}.",
            door=self.door,  # type: ignore[arg-type]
            catalogue=_Catalogue(),
        )

    def grade(self, episode):
        return self.decision


def _client(releases: _Releases) -> TestClient:
    app = FastAPI()
    app.include_router(ors_router(releases))
    return TestClient(app)


def _events(text: str) -> list[tuple[str, dict]]:
    parsed = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        fields = dict(line.split(": ", 1) for line in block.strip().split("\n"))
        parsed.append((fields["event"], json.loads(fields["data"])))
    return parsed


def _open(client: TestClient, *, task: str = "ORD-1041") -> str:
    sid = client.post("/create_session").json()["sid"]
    response = client.post(
        "/create",
        json={"env_name": "retail-returns", "task_spec": task},
        headers={"X-Session-ID": sid},
    )
    assert response.status_code == 200, response.text
    return sid


# --- discovery -------------------------------------------------------------------------


def test_environments_and_typed_splits() -> None:
    client = _client(_Releases())
    assert client.get("/list_environments").json() == ["retail-returns"]
    assert client.get("/retail-returns/splits").json() == [
        {"name": "train", "type": "train"},
        {"name": "test", "type": "test"},
    ]


def test_tasks_lists_opaque_handles_for_a_split() -> None:
    client = _client(_Releases())
    body = client.post("/retail-returns/tasks", json={"split": "train"}).json()
    assert body == {"env_name": "retail-returns", "tasks": ["ORD-1041", "ORD-2298"]}


# --- lifecycle -------------------------------------------------------------------------


def test_a_session_is_minted_then_an_episode_created() -> None:
    releases = _Releases()
    client = _client(releases)
    sid = _open(client)
    assert sid and releases.opened == ["ORD-1041"]


def test_creating_twice_on_one_session_is_a_400() -> None:
    client = _client(_Releases())
    sid = _open(client)
    again = client.post(
        "/create",
        json={"env_name": "retail-returns", "task_spec": "ORD-2298"},
        headers={"X-Session-ID": sid},
    )
    assert again.status_code == 400


def test_both_or_neither_task_form_is_a_400() -> None:
    client = _client(_Releases())
    sid = client.post("/create_session").json()["sid"]
    for body in [
        {"env_name": "retail-returns"},
        {"env_name": "retail-returns", "task_spec": "ORD-1041", "split": "train", "index": 0},
    ]:
        response = client.post("/create", json=body, headers={"X-Session-ID": sid})
        assert response.status_code == 400, body


def test_an_unknown_handle_is_the_callers_error_not_a_fault() -> None:
    client = _client(_Releases())
    sid = client.post("/create_session").json()["sid"]
    response = client.post(
        "/create",
        json={"env_name": "retail-returns", "task_spec": "ORD-9999"},
        headers={"X-Session-ID": sid},
    )
    assert response.status_code == 400
    assert "unknown task" in response.json()["detail"]


def test_a_missing_header_and_an_unknown_session_differ() -> None:
    client = _client(_Releases())
    assert client.get("/retail-returns/prompt").status_code == 400
    assert (
        client.get("/retail-returns/prompt", headers={"X-Session-ID": "nope"}).status_code == 404
    )


def test_a_deleted_session_is_410_so_a_late_retry_is_not_a_bug() -> None:
    client = _client(_Releases())
    sid = _open(client)
    assert client.post("/delete", headers={"X-Session-ID": sid}).status_code == 200
    assert (
        client.get("/retail-returns/prompt", headers={"X-Session-ID": sid}).status_code == 410
    )


def test_calling_before_create_is_refused() -> None:
    client = _client(_Releases())
    sid = client.post("/create_session").json()["sid"]
    response = client.get("/retail-returns/prompt", headers={"X-Session-ID": sid})
    assert response.status_code == 400
    assert "no episode" in response.json()["detail"]


# --- reading and acting ----------------------------------------------------------------


def test_the_prompt_comes_back_as_blocks() -> None:
    client = _client(_Releases())
    sid = _open(client)
    blocks = client.get("/retail-returns/prompt", headers={"X-Session-ID": sid}).json()
    assert blocks[0]["type"] == "text"
    assert "ORD-1041" in blocks[0]["text"]


def test_task_tools_and_tools_agree_and_expose_no_routes() -> None:
    client = _client(_Releases())
    sid = _open(client)
    headers = {"X-Session-ID": sid}
    task_tools = client.get("/retail-returns/task_tools", headers=headers).json()
    assert client.get("/retail-returns/tools", headers=headers).json() == task_tools
    assert {tool["name"] for tool in task_tools} == {"look_up_order", "refund"}
    assert all("world_action" not in tool and "effect" not in tool for tool in task_tools)


def test_an_action_streams_sse_in_the_specs_order() -> None:
    client = _client(_Releases())
    sid = _open(client)
    response = client.post(
        "/retail-returns/call",
        json={"name": "refund", "input": {"order_id": "ORD-1041"}, "task_id": "t1"},
        headers={"X-Session-ID": sid},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    assert [name for name, _ in events] == ["task_id", "end"]
    assert events[1][1] == {"ok": True, "output": {"refunded": True}}


def test_resending_an_action_id_replays_rather_than_acting_again() -> None:
    releases = _Releases()
    client = _client(releases)
    sid = _open(client)
    body = {"name": "refund", "input": {"order_id": "ORD-1041"}, "task_id": "t1"}
    headers = {"X-Session-ID": sid}
    first = client.post("/retail-returns/call", json=body, headers=headers).text
    second = client.post("/retail-returns/call", json=body, headers=headers).text
    assert _events(first)[-1] == _events(second)[-1]
    assert len(releases.door.calls) == 1, "a replay must not reach the world twice"


def test_an_unpublished_tool_ends_with_ok_false() -> None:
    releases = _Releases()
    client = _client(releases)
    sid = _open(client)
    response = client.post(
        "/retail-returns/call",
        json={"name": "query", "input": {"sql": "select 1"}, "task_id": "t1"},
        headers={"X-Session-ID": sid},
    )
    events = _events(response.text)
    assert events[-1][1]["ok"] is False
    assert releases.door.calls == []


# --- finishing -------------------------------------------------------------------------


def test_delete_returns_the_reward_and_what_it_means() -> None:
    client = _client(_Releases())
    sid = _open(client)
    body = client.post("/delete", headers={"X-Session-ID": sid}).json()
    assert body["reward"] == 1.0
    assert body["futureagi_grading"]["graded"] is True


def test_an_ungraded_trial_returns_a_null_reward_over_http() -> None:
    """The contract that must survive serialization: null, never 0."""
    releases = _Releases(
        decision=Decision(
            status=Status.ERRORED, failure_code="judge_undecided", failure_detail="no decision"
        )
    )
    client = _client(releases)
    sid = _open(client)
    body = client.post("/delete", headers={"X-Session-ID": sid}).json()
    assert body["reward"] is None
    assert body["futureagi_grading"]["graded"] is False
    assert body["futureagi_grading"]["code"] == "judge_undecided"


def test_delete_session_forgets_the_id() -> None:
    client = _client(_Releases())
    sid = _open(client)
    client.post("/delete", headers={"X-Session-ID": sid})
    assert client.post("/delete_session", headers={"X-Session-ID": sid}).json() == {"ok": True}
    # Forgotten rather than remembered-as-deleted, so it now reads as unknown.
    assert (
        client.get("/retail-returns/prompt", headers={"X-Session-ID": sid}).status_code == 404
    )


@pytest.mark.parametrize("path", ["/num_tasks", "/task", "/task_range", "/ping"])
def test_the_unimplemented_conveniences_are_absent_rather_than_broken(path: str) -> None:
    """Named in the module docstring: a client gets a clean 404, not a half-working route."""
    client = _client(_Releases())
    assert client.post(f"/retail-returns{path}").status_code == 404


# --- the mount on the sandbox app ------------------------------------------------------


def _sandbox_app(releases: _Releases | None, tmp_path):
    """The real sandbox app factory, so the mount is tested and not just the router."""
    from fi.alk.harness.sandbox_server import create_app

    return create_app(root=tmp_path, releases=releases)


def test_the_trainer_surface_is_absent_unless_the_sandbox_holds_releases(tmp_path) -> None:
    """An ordinary simulation sandbox must expose no trainer routes at all."""
    app = _sandbox_app(None, tmp_path)
    with pytest.raises(Exception):
        # `url_path_for` is FastAPI's own lookup, and raises when a route name is unknown;
        # walking `app.routes` would miss an included router, which is exactly what is mounted.
        app.url_path_for("create_session")
    assert app.url_path_for("health") == "/health", "the sandbox's own routes are untouched"


def test_the_trainer_surface_appears_when_releases_are_held(tmp_path) -> None:
    app = _sandbox_app(_Releases(), tmp_path)
    assert app.url_path_for("create_session") == "/create_session"
    assert app.url_path_for("create") == "/create"
    assert app.url_path_for("delete") == "/delete"
    assert app.url_path_for("call", environment="retail-returns") == "/retail-returns/call"
    assert app.url_path_for("health") == "/health"
