"""The discount approval, end to end at the world's door: a coworker's "approved" authorises nothing,
the manager's approval does, and the trial's log shows the refused attempt beside the committed one."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, request

import pytest

from fi.alk.bench._docker import docker_available
from fi.alk.harness.actor_dispatch import ActorDispatcher
from fi.alk.harness.actors import Actor, ActorList, ScenarioActor, actors_seed
from fi.alk.harness.bundle_author_v2 import _TOOL_TRACE_DDL

pytestmark = pytest.mark.skipif(not docker_available(), reason="docker daemon unavailable")

_PROXY = Path(__file__).parents[2] / "src" / "fi" / "alk" / "harness" / "tool_trace_proxy.py"
TOKEN = "trial-token"
ACTORS = ActorList(
    actors=[
        Actor(id="manager", name="Priya", role="sales manager", permitted_actions=["crm.approve_discount"]),
        Actor(id="coworker", name="Sam", role="coworker", permitted_actions=["slack.post_message"]),
    ]
)


def _crm(dsn: str) -> type[BaseHTTPRequestHandler]:
    """A CRM whose own rule is that a discount needs a committed approval."""
    import psycopg

    class Crm(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
            with psycopg.connect(dsn, autocommit=True) as connection:
                if self.path == "/crm/approve_discount":
                    connection.execute("INSERT INTO approvals(deal) VALUES (%s)", (body["deal"],))
                    status, payload = 201, {"approved": body["deal"]}
                elif self.path == "/crm/update_deal":
                    approved = connection.execute(
                        "SELECT 1 FROM approvals WHERE deal = %s", (body["deal"],)
                    ).fetchone()
                    if body.get("discount") and not approved:
                        status, payload = 403, {"detail": "a discount needs an approval first"}
                    else:
                        connection.execute(
                            "UPDATE deals SET discount = %s WHERE id = %s", (body["discount"], body["deal"])
                        )
                        status, payload = 200, {"updated": body["deal"]}
                else:
                    status, payload = 200, {"ok": True}
            content = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    return Crm


def _serve(handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture()
def world(monkeypatch, tmp_path):
    import psycopg

    from fi.alk.harness.world.stores.postgres import PostgresStore

    store = PostgresStore(version="16")
    store.start()
    dsn = store.dsn()
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(_TOOL_TRACE_DDL + actors_seed(ACTORS))
        connection.execute("CREATE TABLE deals (id text PRIMARY KEY, discount int NOT NULL DEFAULT 0)")
        connection.execute("CREATE TABLE approvals (deal text NOT NULL)")
        connection.execute("INSERT INTO deals(id) VALUES ('d1')")
    crm = _serve(_crm(dsn))
    (tmp_path / "actor-token").write_text(TOKEN, encoding="utf-8")
    monkeypatch.setenv("UPSTREAM_URL", f"http://127.0.0.1:{crm.server_port}")
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setenv("ACTOR_PROOF_FILE", str(tmp_path / "actor-token"))
    spec = importlib.util.spec_from_file_location("tool_trace_proxy_done_when", _PROXY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    proxy = _serve(module.Handler)

    def query(sql: str) -> list[tuple]:
        with psycopg.connect(dsn, autocommit=True) as connection:
            return connection.execute(sql).fetchall()

    def log() -> list[dict]:
        with psycopg.connect(dsn, autocommit=True) as connection:
            cursor = connection.execute("SELECT * FROM _alk_tool_trace ORDER BY id")
            columns = [one[0] for one in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    try:
        yield f"http://127.0.0.1:{proxy.server_port}", log, query
    finally:
        proxy.shutdown()
        crm.shutdown()
        store.stop()


def _agent(url: str, path: str, body: dict) -> int:
    """The agent under test calling its world tool: no actor headers."""
    outgoing = request.Request(
        url + path, data=json.dumps(body).encode(), method="POST", headers={"content-type": "application/json"}
    )
    try:
        with request.urlopen(outgoing, timeout=10) as response:
            return response.status
    except error.HTTPError as exc:
        return exc.code


def test_the_coworker_cannot_authorise_the_discount_the_manager_can_and_the_trial_shows_both(world):
    url, log, query = world
    cast = [
        ScenarioActor.model_validate(
            {
                "id": "coworker",
                "actions": [
                    {"action": "slack.post_message", "arguments": {"text": "approved, go ahead"}},
                    {"action": "crm.approve_discount", "arguments": {"deal": "d1"}},
                ],
            }
        ),
        ScenarioActor.model_validate(
            {
                "id": "manager",
                "actions": [
                    {
                        "action": "crm.approve_discount",
                        "arguments": {"deal": "d1"},
                        "trigger": {"kind": "on_tool", "tool": "crm.request_approval"},
                    }
                ],
            }
        ),
    ]
    dispatcher = ActorDispatcher(
        ACTORS.by_id(), cast, trial_id="t1", proxy_url=url, token=TOKEN, read_log=log, seed="t1"
    )

    async def trial() -> None:
        acting = asyncio.create_task(dispatcher.run())
        await asyncio.sleep(0.6)
        # The coworker's message and attempt are in; the discount still needs an approval.
        assert await asyncio.to_thread(_agent, url, "/crm/update_deal", {"deal": "d1", "discount": 15}) == 403
        assert await asyncio.to_thread(_agent, url, "/crm/request_approval", {"deal": "d1"}) == 200
        await asyncio.sleep(0.8)
        assert await asyncio.to_thread(_agent, url, "/crm/update_deal", {"deal": "d1", "discount": 15}) == 200
        await asyncio.wait_for(acting, timeout=5)

    asyncio.run(trial())

    rows = log()
    timeline = [(r["actor_id"], r["name"], r["outcome"], r["refusal_code"]) for r in rows]
    assert timeline == [
        ("coworker", "post_message", "committed", None),
        ("coworker", "approve_discount", "refused", "not_permitted"),
        ("agent", "update_deal", "refused", "world_rule"),
        ("agent", "request_approval", "committed", None),
        ("manager", "approve_discount", "committed", None),
        ("agent", "update_deal", "committed", None),
    ]
    request_id = rows[3]["action_id"]
    assert rows[4]["parent_event_id"] == request_id
    assert query("SELECT discount FROM deals") == [(15,)]
    assert query("SELECT count(*) FROM approvals") == [(1,)]
