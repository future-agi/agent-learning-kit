"""TH-10048: Protect must honour keys / base URL passed in code.

Keys passed to ``Protect(...)`` are used first; the FI_* environment
variables are only a fallback (same precedence as ``Evaluator``).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from fi.evals.evaluator import Evaluator
from fi.evals.protect import Protect
from fi.utils.errors import InvalidAuthError

_ENV = ("FI_API_KEY", "FI_SECRET_KEY", "FI_BASE_URL")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def fake_server():
    seen: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            seen.append(
                {
                    "path": self.path,
                    "api_key": self.headers.get("X-Api-Key"),
                    "secret_key": self.headers.get("X-Secret-Key"),
                }
            )
            body = json.dumps({"result": [{"evaluations": []}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()
        server.server_close()


def test_protect_accepts_keys_passed_in_code_without_env():
    protect = Protect(fi_api_key="code-api", fi_secret_key="code-secret")
    assert protect.evaluator._fi_api_key == "code-api"
    assert protect.evaluator._fi_secret_key == "code-secret"


def test_protect_accepts_keys_alongside_explicit_evaluator_without_env():
    evaluator = Evaluator(fi_api_key="code-api", fi_secret_key="code-secret")
    protect = Protect(
        fi_api_key="code-api", fi_secret_key="code-secret", evaluator=evaluator
    )
    assert protect.evaluator is evaluator


def test_protect_prefers_constructor_keys_and_base_url_over_env(
    monkeypatch, fake_server
):
    base_url, seen = fake_server
    monkeypatch.setenv("FI_API_KEY", "env-api")
    monkeypatch.setenv("FI_SECRET_KEY", "env-secret")
    monkeypatch.setenv("FI_BASE_URL", "http://127.0.0.1:9")  # nothing listens

    protect = Protect(
        fi_api_key="code-api", fi_secret_key="code-secret", fi_base_url=base_url
    )
    protect.protect(
        "hello there",
        protect_rules=[{"metric": "toxicity"}],
        timeout=10000,
    )

    assert seen, "request never reached the base URL passed in code"
    assert {(r["api_key"], r["secret_key"]) for r in seen} == {
        ("code-api", "code-secret")
    }


def test_protect_request_uses_keys_passed_in_code_without_env(fake_server):
    base_url, seen = fake_server
    protect = Protect(
        fi_api_key="code-api", fi_secret_key="code-secret", fi_base_url=base_url
    )
    protect.protect(
        "hello there",
        protect_rules=[{"metric": "toxicity"}],
        timeout=10000,
    )
    assert seen and all(r["api_key"] == "code-api" for r in seen)


def test_protect_falls_back_to_env_keys(monkeypatch):
    monkeypatch.setenv("FI_API_KEY", "env-api")
    monkeypatch.setenv("FI_SECRET_KEY", "env-secret")
    protect = Protect()
    assert protect.evaluator._fi_api_key == "env-api"
    assert protect.evaluator._fi_secret_key == "env-secret"


def test_protect_without_any_keys_still_raises():
    with pytest.raises(InvalidAuthError):
        Protect()
