"""The world's action log as the tool proxy writes it: what came of each action, and duplicates."""

from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, request

import pytest

from fi.alk.bench._docker import docker_available
from fi.alk.harness.actors import Actor, ActorList, actors_seed
from fi.alk.harness.bundle_author_v2 import _TOOL_TRACE_DDL

pytestmark = pytest.mark.skipif(
    not docker_available(), reason="docker daemon unavailable"
)

_PROXY = (
    Path(__file__).parents[2] / "src" / "fi" / "alk" / "harness" / "tool_trace_proxy.py"
)


class _World(BaseHTTPRequestHandler):
    """A world whose answer is named by the path: /ok, /no (403), /broken (500)."""

    seen: list[str] = []

    def log_message(self, *_args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.rfile.read(int(self.headers.get("content-length") or 0))
        _World.seen.append(self.path)
        status, payload = {
            "/ok": (201, {"id": "d-1", "count": len(_World.seen)}),
            "/records/approve": (201, {"approval": "ap-1"}),
            "/no": (403, {"detail": "a change needs an approval first"}),
            "/records/hold": (403, {"detail": "nothing to hold"}),
        }.get(self.path, (500, {"detail": "boom"}))
        content = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def _serve(handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="module")
def store():
    from fi.alk.harness.world.stores.postgres import PostgresStore

    running = PostgresStore(version="16")
    running.start()
    try:
        yield running
    finally:
        running.stop()


TOKEN = "trial-token"
ACTORS = ActorList(
    actors=[
        Actor(
            id="reviewer",
            name="Robin",
            permitted_actions=["records.approve", "records.hold"],
        ),
        Actor(id="coworker", name="Sam"),
        Actor(
            id="assistant",
            name="Lee",
            permitted_actions=["records.approve"],
            on_behalf_of="reviewer",
        ),
    ]
)


@pytest.fixture()
def proxy(store, monkeypatch, tmp_path):
    """The proxy in front of a world, logging into a fresh `_alk_tool_trace`; yields (url, rows)."""
    import psycopg

    dsn = store.dsn()
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute("DROP TABLE IF EXISTS _alk_tool_trace")
        connection.execute("DROP TABLE IF EXISTS _alk_actors")
        connection.execute(_TOOL_TRACE_DDL)
        connection.execute(actors_seed(ACTORS))
    (tmp_path / "actor-token").write_text(TOKEN, encoding="utf-8")
    monkeypatch.setenv("ACTOR_PROOF_FILE", str(tmp_path / "actor-token"))
    world = _serve(_World)
    _World.seen = []
    monkeypatch.setenv("PORT", "0")
    monkeypatch.setenv("UPSTREAM_URL", f"http://127.0.0.1:{world.server_port}")
    monkeypatch.setenv("DATABASE_URL", dsn)
    spec = importlib.util.spec_from_file_location("tool_trace_proxy_under_test", _PROXY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server = _serve(module.Handler)

    def rows() -> list[dict]:
        with psycopg.connect(dsn, autocommit=True) as connection:
            cursor = connection.execute("SELECT * FROM _alk_tool_trace ORDER BY id")
            columns = [one[0] for one in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    try:
        yield f"http://127.0.0.1:{server.server_port}", rows
    finally:
        server.shutdown()
        world.shutdown()


def _post(
    url: str, path: str, body: dict, key: str = "", **extra: str
) -> tuple[int, object]:
    headers = {
        "content-type": "application/json",
        **({"Idempotency-Key": key} if key else {}),
        **extra,
    }
    outgoing = request.Request(
        url + path, data=json.dumps(body).encode(), method="POST", headers=headers
    )
    try:
        with request.urlopen(outgoing, timeout=10) as response:
            return response.status, json.loads(response.read())
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_each_action_is_logged_as_the_agent_s_with_what_the_world_made_of_it(proxy):
    url, rows = proxy
    assert _post(url, "/ok", {"item": "1"})[0] == 201
    assert _post(url, "/no", {"item": "1"}) == (
        403,
        {"detail": "a change needs an approval first"},
    )
    assert _post(url, "/broken", {"item": "1"})[0] == 500
    logged = rows()
    assert [
        (one["name"], one["outcome"], one["ok"], one["refusal_code"]) for one in logged
    ] == [
        ("ok", "committed", True, None),
        ("no", "refused", False, "world_rule"),
        ("broken", "faulted", False, None),
    ]
    assert all(
        one["actor_id"] == "agent" and one["worker_id"] is None for one in logged
    )
    assert "approval first" in logged[1]["refusal"] and logged[2]["refusal"] is None
    assert len({one["action_id"] for one in logged}) == 3


def test_an_actor_s_retried_action_reaches_the_world_once_and_is_answered_from_the_first_result(
    proxy,
):
    url, rows = proxy
    first = _post(url, "/records/approve", {"item": "1"}, key="k-1", **_as("reviewer"))
    again = _post(url, "/records/approve", {"item": "1"}, key="k-1", **_as("reviewer"))
    assert (first, again) == ((201, {"approval": "ap-1"}), (200, {"approval": "ap-1"}))
    assert _World.seen == ["/records/approve"]
    assert [(one["outcome"], one["ok"], one["idempotency_key"]) for one in rows()] == [
        ("committed", True, "k-1"),
        ("duplicate", False, "k-1"),
    ]


def test_an_actor_s_key_whose_first_try_the_world_refused_may_be_tried_again(proxy):
    url, rows = proxy
    _post(url, "/records/hold", {"item": "1"}, key="k-1", **_as("reviewer"))
    _post(url, "/records/hold", {"item": "1"}, key="k-1", **_as("reviewer"))
    assert _World.seen == ["/records/hold"] * 2
    assert [one["outcome"] for one in rows()] == ["refused", "refused"]


def test_the_agent_s_retried_request_reaches_the_world_each_time_and_is_logged_as_it_was(
    proxy,
):
    url, rows = proxy
    assert _post(url, "/ok", {"item": "1"}, key="k-1") == (
        201,
        {"id": "d-1", "count": 1},
    )
    assert _post(url, "/ok", {"item": "1"}, key="k-1") == (
        201,
        {"id": "d-1", "count": 2},
    )
    assert _World.seen == ["/ok", "/ok"]
    assert [(one["outcome"], one["ok"]) for one in rows()] == [("committed", True)] * 2


def test_an_actor_s_key_used_again_with_other_arguments_is_refused_and_never_applied(
    proxy,
):
    url, rows = proxy
    _post(url, "/records/approve", {"item": "1"}, key="k-1", **_as("reviewer"))
    status, payload = _post(
        url, "/records/approve", {"item": "2"}, key="k-1", **_as("reviewer")
    )
    assert status == 409 and "other arguments" in payload["detail"]
    assert _World.seen == ["/records/approve"]
    assert [(one["outcome"], one["refusal_code"]) for one in rows()] == [
        ("committed", None),
        ("refused", "idempotency_conflict"),
    ]


def test_a_world_seeded_before_the_new_columns_still_gets_every_action_logged(
    proxy, store
):
    import psycopg

    url, rows = proxy
    with psycopg.connect(store.dsn(), autocommit=True) as connection:
        connection.execute("DROP TABLE _alk_tool_trace")
        connection.execute(
            "CREATE TABLE _alk_tool_trace (id bigserial PRIMARY KEY, name text NOT NULL, arguments jsonb NOT NULL, "
            "result jsonb, ok boolean NOT NULL, error text, at double precision NOT NULL)"
        )
    assert _post(url, "/ok", {"item": "1"}, key="k-1")[0] == 201
    assert _post(url, "/no", {"item": "1"})[0] == 403
    # Logged without its actor it would read as the agent's, so an actor's action never reaches the world.
    status, payload = _post(url, "/records/approve", {"item": "1"}, **_as("reviewer"))
    assert status == 503 and "cannot record" in payload["detail"]
    assert _World.seen == ["/ok", "/no"]
    assert [(one["name"], one["ok"]) for one in rows()] == [("ok", True), ("no", False)]


def _as(actor: str, token: str = TOKEN, **more: str) -> dict:
    return {"X-ALK-Actor": actor, "X-ALK-Actor-Token": token, **more}


def test_a_permitted_actor_s_action_reaches_the_world_and_is_logged_under_that_actor(
    proxy,
):
    url, rows = proxy
    assert (
        _post(
            url,
            "/records/approve",
            {"item": "1"},
            **_as("reviewer", **{"X-ALK-Parent-Event": "a1"}),
        )[0]
        == 201
    )
    assert _post(url, "/records/approve", {"item": "1"}, **_as("assistant"))[0] == 201
    assert _World.seen == ["/records/approve"] * 2
    assert [
        (r["actor_id"], r["outcome"], r["parent_event_id"], r["on_behalf_of"])
        for r in rows()
    ] == [
        ("reviewer", "committed", "a1", None),
        ("assistant", "committed", None, "reviewer"),
    ]


def test_an_actor_without_the_permission_is_refused_before_the_world_and_told_only_why(
    proxy,
):
    url, rows = proxy
    status, payload = _post(url, "/records/approve", {"item": "1"}, **_as("coworker"))
    assert status == 403 and payload == {
        "detail": "records.approve is not permitted for coworker"
    }
    assert _World.seen == []
    [row] = rows()
    assert (row["actor_id"], row["outcome"], row["refusal_code"], row["ok"]) == (
        "coworker",
        "refused",
        "not_permitted",
        False,
    )


def test_naming_an_actor_without_this_trial_s_token_is_the_agent_s_attempt_and_refused(
    proxy,
):
    url, rows = proxy
    assert (
        _post(url, "/records/approve", {"item": "1"}, **_as("reviewer", token="guess"))[
            0
        ]
        == 403
    )
    assert (
        _post(url, "/records/approve", {"item": "1"}, **{"X-ALK-Actor": "reviewer"})[0]
        == 403
    )
    assert _World.seen == []
    assert [(r["actor_id"], r["refusal_code"]) for r in rows()] == [
        ("agent", "identity_conflict")
    ] * 2


def test_an_actor_the_world_does_not_list_is_refused(proxy):
    url, rows = proxy
    assert _post(url, "/records/approve", {"item": "1"}, **_as("stranger"))[0] == 403
    assert _World.seen == []
    assert [(r["actor_id"], r["refusal_code"]) for r in rows()] == [
        ("stranger", "unknown_actor")
    ]


def test_an_actor_list_the_world_cannot_read_is_the_world_s_fault_and_never_reaches_it(
    proxy, store
):
    import psycopg

    url, rows = proxy
    with psycopg.connect(store.dsn(), autocommit=True) as connection:
        connection.execute("DROP TABLE _alk_actors")
    status, payload = _post(url, "/records/approve", {"item": "1"}, **_as("reviewer"))
    assert status == 503 and payload == {
        "detail": "the world could not check this actor"
    }
    assert _World.seen == []
    assert [(r["actor_id"], r["outcome"]) for r in rows()] == [("reviewer", "faulted")]


def _faults(tmp_path_factory_dir: Path, faults: list[dict]) -> None:
    (tmp_path_factory_dir / "trial.json").write_text(json.dumps({"faults": faults}), encoding="utf-8")


def test_a_timeout_before_commit_never_reaches_the_world_and_a_retry_commits_once(proxy, tmp_path):
    url, rows = proxy
    _faults(tmp_path, [{"action": "ok", "mode": "timeout_before_commit", "on_call": 1, "delay_seconds": 0}])
    assert _post(url, "/ok", {"item": "1"})[0] == 504
    assert _World.seen == []
    assert _post(url, "/ok", {"item": "1"})[0] == 201
    assert _World.seen == ["/ok"]
    assert [(one["outcome"], one["error"] or None) for one in rows()] == [
        ("faulted", "timeout_before_commit"),
        ("committed", None),
    ]


def test_a_timeout_after_commit_commits_without_answering_so_a_retry_commits_twice(proxy, tmp_path):
    url, rows = proxy
    _faults(tmp_path, [{"action": "ok", "mode": "timeout_after_commit", "on_call": 1, "delay_seconds": 0}])
    assert _post(url, "/ok", {"item": "1"})[0] == 504
    assert _post(url, "/ok", {"item": "1"})[0] == 201
    # The world saw both: a check reading the log can fail the duplicate the agent caused (WO-1).
    assert _World.seen == ["/ok", "/ok"]
    assert [(one["outcome"], one["error"] or None) for one in rows()] == [
        ("committed", "response_lost"),
        ("committed", None),
    ]


def test_a_fault_never_applies_to_an_actor_s_action(proxy, tmp_path):
    url, rows = proxy
    _faults(tmp_path, [{"action": "records.approve", "mode": "timeout_before_commit", "delay_seconds": 0}])
    assert _post(url, "/records/approve", {"item": "1"}, key="k-1", **_as("reviewer"))[0] == 201


CHANNELS = {"c-lookup": "order_lookup", "c-refund": "refund"}
PERMISSIONS = {"worker:order_lookup": {"allow": ["ok"]}, "worker:refund": {"allow": ["records.approve"]}}


@pytest.fixture()
def worker_proxy(proxy, monkeypatch, tmp_path):
    # As the bundle ships it: the proxy with its sealed permissions beside it.
    (tmp_path / "proxy.py").write_bytes(_PROXY.read_bytes())
    (tmp_path / "permissions.json").write_text(json.dumps(PERMISSIONS), encoding="utf-8")
    (tmp_path / "channels.json").write_text(json.dumps(CHANNELS), encoding="utf-8")
    monkeypatch.setenv("WORKER_CHANNELS_FILE", str(tmp_path / "channels.json"))
    url, rows = proxy
    spec = importlib.util.spec_from_file_location("tool_trace_proxy_workers", tmp_path / "proxy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server = _serve(module.Handler)
    try:
        yield f"http://127.0.0.1:{server.server_port}", rows, tmp_path
    finally:
        server.shutdown()


def test_a_worker_s_allowed_action_reaches_the_world_through_its_channel_and_names_the_worker(worker_proxy):
    url, rows, _ = worker_proxy
    assert _post(url, "/w/c-refund/records/approve", {"deal": "1"})[0] == 201
    # The world sees the action's own path; the channel token never leaves the proxy.
    assert _World.seen == ["/records/approve"]
    [row] = rows()
    assert (row["actor_id"], row["worker_id"], row["outcome"], row["name"]) == ("agent", "refund", "committed", "approve")
    assert "c-refund" not in json.dumps(row, default=str)


def test_a_worker_s_out_of_permission_write_is_refused_before_the_world_and_kept_as_an_attempt(worker_proxy):
    url, rows, _ = worker_proxy
    status, payload = _post(url, "/w/c-lookup/records/approve", {"deal": "1"})
    assert status == 403 and payload == {"detail": "records.approve is not permitted for order_lookup"}
    assert _World.seen == []
    [row] = rows()
    assert (row["worker_id"], row["outcome"], row["refusal_code"], row["ok"]) == ("order_lookup", "refused", "not_permitted", False)


def test_a_channel_this_trial_did_not_issue_is_refused_as_an_identity_conflict(worker_proxy):
    url, rows, _ = worker_proxy
    assert _post(url, "/w/guess/records/approve", {"deal": "1"})[0] == 403
    assert _post(url, "/w//records/approve", {"deal": "1"})[0] == 403
    assert _World.seen == []
    assert [(r["actor_id"], r["worker_id"], r["refusal_code"]) for r in rows()] == [("agent", None, "identity_conflict")] * 2


def test_an_unreadable_permissions_seal_denies_rather_than_widens(worker_proxy):
    url, rows, tmp_path = worker_proxy
    (tmp_path / "permissions.json").write_text("not json", encoding="utf-8")
    assert _post(url, "/w/c-refund/records/approve", {"deal": "1"})[0] == 403
    assert _World.seen == []
    assert rows()[0]["refusal_code"] == "not_permitted"


def test_without_a_permissions_seal_a_worker_s_action_is_attributed_but_not_limited(worker_proxy):
    url, rows, tmp_path = worker_proxy
    (tmp_path / "permissions.json").unlink()
    assert _post(url, "/w/c-lookup/records/approve", {"deal": "1"})[0] == 201
    assert [(r["worker_id"], r["outcome"]) for r in rows()] == [("order_lookup", "committed")]


def test_a_route_named_differently_from_its_tool_is_logged_as_the_tool(worker_proxy):
    url, rows, tmp_path = worker_proxy
    (tmp_path / "actions.json").write_text(json.dumps({"records.approve": "grant_discount"}), encoding="utf-8")
    assert _post(url, "/w/c-refund/records/approve", {"deal": "1"})[0] == 201
    assert _post(url, "/ok", {"deal": "1"})[0] == 201
    assert [r["name"] for r in rows()] == ["grant_discount", "ok"]



def test_past_its_deadline_a_worker_s_call_is_refused_and_before_it_goes_through(worker_proxy):
    import time

    url, rows, tmp_path = worker_proxy
    # The runner writes the deadline in the trial state beside the actor token, as it writes faults.
    state = tmp_path / "trial.json"
    state.write_text(json.dumps({"faults": [], "worker_deadline_at": time.time() + 600}), encoding="utf-8")
    assert _post(url, "/w/c-refund/records/approve", {"deal": "1"})[0] == 201
    state.write_text(json.dumps({"faults": [], "worker_deadline_at": time.time() - 1}), encoding="utf-8")
    status, payload = _post(url, "/w/c-refund/records/approve", {"deal": "2"})
    assert status == 403 and payload == {"detail": "the deadline for refund has passed"}
    # The agent's own calls, outside any worker's channel, are not a worker's to time out.
    assert _post(url, "/ok", {"deal": "3"})[0] == 201
    assert [(r["worker_id"], r["outcome"], r["refusal_code"]) for r in rows()] == [
        ("refund", "committed", None),
        ("refund", "refused", "deadline_passed"),
        (None, "committed", None),
    ]