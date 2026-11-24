"""TH-10052: a refused Protect check must never be reported as passed.

When the server refuses an evaluation (429 rate limit, 402 budget / plan
limit) or the request fails, the rule did not run. Protect must report it
as uncompleted, keep the refusal code and reason, and never say
"All checks passed".
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from fi.evals.protect import Protect

ALL_PASSED = "All checks passed"


@pytest.fixture
def server():
    state = {"status": 200, "body": {}, "hits": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            state["hits"] += 1
            body = json.dumps(state["body"]).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{httpd.server_port}"
    try:
        yield state
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    monkeypatch.setattr("fi.api.auth.time.sleep", lambda *_: None)


@pytest.fixture(autouse=True)
def _env(monkeypatch, server):
    # Credentials via env so this test is independent of constructor-key handling.
    monkeypatch.setenv("FI_API_KEY", "k")
    monkeypatch.setenv("FI_SECRET_KEY", "s")
    monkeypatch.setenv("FI_BASE_URL", server["url"])


def _protect(server, status, body, **kwargs):
    server["status"], server["body"] = status, body
    client = Protect()
    return client.protect(
        "Hello, have a pleasant day.",
        protect_rules=[{"metric": "toxicity"}],
        timeout=20000,
        **kwargs,
    )


def _assert_not_run(result):
    assert result["status"] != "passed"
    assert result["completed_rules"] == []
    assert result["uncompleted_rules"] == ["toxicity"]
    assert result["failed_rule"] == []
    assert ALL_PASSED not in result["reasons"]
    assert result["messages"] != ALL_PASSED


@pytest.mark.parametrize("reason", [True, False])
@pytest.mark.parametrize(
    "status, body, code, message",
    [
        (
            429,
            {"status": False, "code": "rate_limited", "message": "Rate limit exceeded"},
            "rate_limited",
            "Rate limit exceeded",
        ),
        (
            402,
            {
                "status": False,
                "code": "BUDGET_PAUSED",
                "message": "Budget paused",
                "dimension": "credits",
            },
            "BUDGET_PAUSED",
            "Budget paused",
        ),
        (
            402,
            {"status": False, "code": "FREE_TIER_LIMIT_EXCEEDED", "result": "Free tier used up"},
            "FREE_TIER_LIMIT_EXCEEDED",
            "Free tier used up",
        ),
    ],
)
def test_refused_check_is_not_run_and_keeps_code_and_reason(
    server, status, body, code, message, reason
):
    result = _protect(server, status, body, reason=reason)

    _assert_not_run(result)
    assert result["refusals"] == [
        {"rule": "toxicity", "status_code": status, "code": code, "reason": message}
    ]
    assert any(message in r for r in result["reasons"])


def test_server_error_is_not_run(server):
    result = _protect(server, 500, {"detail": "boom"}, reason=True)

    _assert_not_run(result)
    assert len(result["refusals"]) == 1
    refusal = result["refusals"][0]
    assert refusal["rule"] == "toxicity"
    assert refusal["status_code"] == 500


def test_passing_check_still_passes(server):
    body = {"result": [{"evaluations": [{"name": "toxicity", "output": "Passed"}]}]}
    result = _protect(server, 200, body, reason=True)

    assert result["status"] == "passed"
    assert result["completed_rules"] == ["toxicity"]
    assert result["uncompleted_rules"] == []
    assert result["reasons"] == [ALL_PASSED]
    assert result["refusals"] == []


def test_failing_check_still_fails(server):
    body = {
        "result": [
            {"evaluations": [{"name": "toxicity", "output": "Failed", "reason": "rude"}]}
        ]
    }
    result = _protect(server, 200, body, reason=True)

    assert result["status"] == "failed"
    assert result["completed_rules"] == ["toxicity"]
    assert result["failed_rule"] == ["toxicity"]
    assert result["reasons"] == ["rude"]
    assert result["refusals"] == []
