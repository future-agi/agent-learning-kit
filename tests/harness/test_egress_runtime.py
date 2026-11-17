"""How a controlled run sets up its refusing proxy: the allowed hosts it reads from `job.json`, what it does when it
cannot record a refusal, and the world's own hosts it lets through."""

from __future__ import annotations

import json
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib import request

import pytest

from fi.alk.harness.process_runtime import ProcessRuntimeError, _egress_environment, _read_egress_allowed_hosts


def _job(tmp_path: Path, metadata: object) -> Path:
    (tmp_path / "job.json").write_text(json.dumps({"job_id": "job-1", "metadata": metadata}))
    return tmp_path


@pytest.mark.parametrize(
    "written",
    [
        '{"job_id": "job-1", "metadata": ',
        "[]",
        '{"job_id": "job-1", "metadata": "hosts"}',
        '{"job_id": "job-1", "metadata": null}',
        *(
            json.dumps({"job_id": "job-1", "metadata": {"egress_allowed_hosts": hosts}})
            for hosts in (["api.example.test", None], "api.example.test", [1], {"api.example.test": True})
        ),
    ],
)
def test_a_job_json_that_cannot_say_which_hosts_are_allowed_fails_the_spawn(tmp_path: Path, written) -> None:
    (tmp_path / "job.json").write_text(written)
    with pytest.raises(ProcessRuntimeError) as raised:
        _read_egress_allowed_hosts(tmp_path)
    assert raised.value.code == "spawn_failed"


def test_allowed_hosts_absent_or_null_mean_an_uncontrolled_run_and_a_list_is_read(tmp_path: Path) -> None:
    assert _read_egress_allowed_hosts(tmp_path) is None
    assert _read_egress_allowed_hosts(_job(tmp_path, {})) is None
    (tmp_path / "job.json").write_text('{"job_id": "job-1"}')
    assert _read_egress_allowed_hosts(tmp_path) is None
    assert _read_egress_allowed_hosts(_job(tmp_path, {"egress_allowed_hosts": None})) is None
    assert _read_egress_allowed_hosts(_job(tmp_path, {"egress_allowed_hosts": []})) == ()
    listed = _job(tmp_path, {"egress_allowed_hosts": ["api.example.test", "*.cdn.example.test"]})
    assert _read_egress_allowed_hosts(listed) == ("api.example.test", "*.cdn.example.test")


class _World(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.send_response(200)
        self.send_header("content-length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


def test_a_world_without_an_action_log_warns_once_and_its_own_hosts_pass_the_proxy(monkeypatch, caplog) -> None:
    """A client that ignores `NO_PROXY` still reaches the world's own services through the proxy."""
    world = ThreadingHTTPServer(("127.0.0.1", 0), _World)
    threading.Thread(target=world.serve_forever, daemon=True).start()
    real = socket.getaddrinfo
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *a, **k: real("127.0.0.1" if host == "tools.world.test" else host, *a, **k),
    )
    context = SimpleNamespace(egress_allowed_hosts=("api.example.test",), egress_proxies={}, bundle_dir=None)
    address = f"http://tools.world.test:{world.server_port}"
    try:
        with caplog.at_level(logging.WARNING, logger="fi.alk.harness.process_runtime"):
            environments = [_egress_environment(context, index, {}, {"tools": address}) for index in (0, 1)]
        warned = [one for one in caplog.records if one.levelno == logging.WARNING]
        assert len(warned) == 1
        for environment in environments:
            assert "tools.world.test" in environment["NO_PROXY"].split(",")
            opener = request.build_opener(request.ProxyHandler({"http": environment["HTTP_PROXY"]}))
            with opener.open(f"{address}/status", timeout=5) as answer:
                assert (answer.status, answer.read()) == (200, b"ok")
    finally:
        for proxy in context.egress_proxies.values():
            proxy.close()
        world.shutdown()
