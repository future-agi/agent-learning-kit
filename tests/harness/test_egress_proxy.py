"""The refusing proxy in front of the agent in a controlled run: allowed hosts pass, others are refused and logged."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error, request

import pytest

from fi.alk.harness.bundle_author_v2 import _TOOL_TRACE_DDL
from fi.alk.harness.call_runner import trial_state_changes
from fi.alk.harness.egress_proxy import EgressProxy, host_allowed, no_proxy
from fi.alk.harness.world_door import MemoryLog, PostgresLog


class _Upstream(BaseHTTPRequestHandler):
    seen: list[str] = []

    def log_message(self, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        _Upstream.seen.append(self.path)
        content = b'{"ok": true}'
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


@pytest.fixture()
def upstream():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Upstream.seen = []
    try:
        yield server
    finally:
        server.shutdown()


def _through(proxy: EgressProxy, url: str) -> tuple[int, bytes]:
    opener = request.build_opener(request.ProxyHandler({"http": proxy.url}))
    try:
        with opener.open(url, timeout=5) as answer:
            return answer.status, answer.read()
    except error.HTTPError as exc:
        return exc.code, exc.read()


def _connect(proxy: EgressProxy, target: str) -> bytes:
    port = int(proxy.url.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        connection.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        return connection.recv(4096)


def test_no_proxy_lists_loopback_world_hosts_and_allowed_domains_with_wildcards_as_suffixes() -> None:
    entries = no_proxy(["api.openai.com", "*.hf.co"], ["world-db.internal"]).split(",")
    assert entries == ["localhost", "127.0.0.1", "::1", "world-db.internal", "api.openai.com", ".hf.co"]
    assert host_allowed("cdn-lfs.hf.co", ["*.hf.co"])
    assert host_allowed("API.openai.com", ["api.openai.com"])
    assert not host_allowed("refunds.example.com", ["api.openai.com", "*.hf.co"])
    assert not host_allowed("evil-openai.com", ["openai.com"])


def test_an_allowed_host_passes_untouched_and_nothing_is_logged(upstream) -> None:
    log = MemoryLog()
    proxy = EgressProxy(["127.0.0.1"], log)
    try:
        status, body = _through(proxy, f"http://127.0.0.1:{upstream.server_port}/orders?id=1")
    finally:
        proxy.close()
    assert (status, json.loads(body)) == (200, {"ok": True})
    assert _Upstream.seen == ["/orders?id=1"]
    assert log.rows() == []


def test_a_host_outside_the_world_is_refused_with_403_and_logged_as_the_agent_s_refused_attempt(upstream) -> None:
    log = MemoryLog()
    proxy = EgressProxy(["api.openai.com"], log, {"refunds.example.com": "issue_refund"})
    try:
        status, body = _through(proxy, "http://refunds.example.com/v1/refunds")
    finally:
        proxy.close()
    assert status == 403
    assert body == b"refunds.example.com is outside the test world"
    [row] = log.rows()
    assert row["outcome"] == "refused" and row["ok"] is False
    assert row["refusal_code"] == "egress_blocked"
    assert row["actor_id"] == "agent"
    assert row["arguments"] == {"host": "refunds.example.com", "method": "GET", "tool": "issue_refund"}
    assert row["refusal"] == "refunds.example.com is outside the test world"


def test_an_https_connect_is_refused_and_logged_by_host() -> None:
    log = MemoryLog()
    proxy = EgressProxy(["api.openai.com"], log)
    try:
        answer = _connect(proxy, "payments.example.com:443")
    finally:
        proxy.close()
    assert answer.startswith(b"HTTP/1.0 403") or answer.startswith(b"HTTP/1.1 403")
    [row] = log.rows()
    assert (row["outcome"], row["refusal_code"]) == ("refused", "egress_blocked")
    assert row["arguments"] == {"host": "payments.example.com", "method": "CONNECT"}


def test_an_allowed_https_connect_is_tunnelled(upstream) -> None:
    log = MemoryLog()
    proxy = EgressProxy(["127.0.0.1"], log)
    try:
        answer = _connect(proxy, f"127.0.0.1:{upstream.server_port}")
    finally:
        proxy.close()
    assert b" 200 " in answer.split(b"\r\n", 1)[0]
    assert log.rows() == []


@pytest.fixture()
def world_log():
    """A fresh `_alk_tool_trace` in its own schema of the test database; yields (dsn, rows)."""
    dsn = os.environ.get("ALK_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set ALK_TEST_POSTGRES_DSN to run the controlled-run egress test")
    import psycopg

    schema = f"egress_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(f"CREATE SCHEMA {schema}")
        connection.execute(f"SET search_path TO {schema}")
        connection.execute(_TOOL_TRACE_DDL)

    def rows() -> list[dict]:
        with psycopg.connect(dsn, autocommit=True) as connection:
            query = f"SELECT * FROM {schema}._alk_tool_trace ORDER BY at"  # noqa: S608 - the test's own schema
            cursor = connection.execute(query)
            columns = [one[0] for one in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    try:
        yield f"{dsn}?options=-csearch_path%3D{schema}", rows
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA {schema} CASCADE")


_AGENT = """
import os, urllib.request, urllib.error
for url in (os.environ["WORLD_URL"], "http://refunds.example.com/v1/refunds", "https://refunds.example.com/v1/refunds"):
    try:
        with urllib.request.urlopen(url, timeout=5) as answer:
            print(url, answer.status)
    except urllib.error.HTTPError as exc:
        print(url, exc.code)
    except OSError as exc:
        print(url, "refused", type(exc).__name__)
"""


def test_a_test_agent_s_call_outside_the_world_is_blocked_logged_and_counted_refused(world_log, upstream) -> None:
    """An agent process given the controlled run's proxy variables: its world call goes straight to the world, its
    refund call to a host outside it is refused over HTTP and HTTPS, and World changes list both as blocked hosts,
    never as attempts or committed changes."""
    dsn, rows = world_log
    proxy = EgressProxy(["api.openai.com"], PostgresLog(dsn))
    world_url = f"http://127.0.0.1:{upstream.server_port}/orders"
    try:
        done = subprocess.run(
            [sys.executable, "-c", _AGENT],
            env={"PATH": os.environ["PATH"], "WORLD_URL": world_url, **proxy.environment()},
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    finally:
        proxy.close()
    said = done.stdout.splitlines()
    assert said[0] == f"{world_url} 200"
    assert said[1] == "http://refunds.example.com/v1/refunds 403"
    assert said[2].startswith("https://refunds.example.com/v1/refunds refused")
    # The world answered its own call; the refund host never got one.
    assert _Upstream.seen == ["/orders"]
    logged = rows()
    assert [(row["outcome"], row["refusal_code"]) for row in logged] == [("refused", "egress_blocked")] * 2
    [agent] = trial_state_changes(logged, {})
    assert (agent["attempted"], agent["refused"], agent["committed"]) == (0, 0, 0)
    assert agent["no_change"] is True
    assert agent["blocked"] == [
        {"host": "refunds.example.com", "method": "GET", "count": 1},
        {"host": "refunds.example.com", "method": "CONNECT", "count": 1},
    ]
