"""TH-10041: a batch refused partway must keep its finished results and not be resent.

The backend (future-agi/future-agi#3798) answers a batch that hits a usage limit
partway with 429 (rate limit) or 402 (usage limit) and a body carrying
``completed_results`` (items that already ran and may be charged) and
``failed_index`` (the first item that did not run). These tests drive the real
``Evaluator.evaluate`` through ``requests`` against a local fake server.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fi.evals.evaluator import Evaluator

EVAL_NAME = "synthetic_eval"
EVAL_ROUTE = "/sdk/api/v1/new-eval/"


def _completed(index: int) -> dict:
    return {
        "name": EVAL_NAME,
        "reason": f"finished item {index}",
        "runtime": 10 + index,
        "output": "Passed",
        "output_type": "Pass/Fail",
        "eval_id": f"eval-{index}",
    }


def _denial(status: int, completed: int) -> tuple[int, dict]:
    code = "rate_limited" if status == 429 else "FREE_TIER_LIMIT"
    message = (
        "Evaluation rate limit reached."
        if status == 429
        else "Free tier limit reached."
    )
    message += " Retry only items at or after failed_index."
    return status, {
        "status": False,
        "type": "rate_limit_error" if status == 429 else "billing_error",
        "code": code,
        "detail": message,
        "message": message,
        "error": message,
        "result": message,
        "completed_results": [_completed(i) for i in range(completed)],
        "failed_index": completed,
    }


class _FakeBackend:
    """Serves a scripted reply per POST to the eval route; repeats the last one."""

    def __init__(self, replies: list[tuple[int, dict]]) -> None:
        self.replies = replies
        self.posts: list[dict] = []
        backend = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path != EVAL_ROUTE:
                    self._send(404, {"status": False, "message": "not found"})
                    return
                backend.posts.append(body)
                index = min(len(backend.posts), len(backend.replies)) - 1
                self._send(*backend.replies[index])

            def _send(self, status: int, payload: dict) -> None:
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):  # keep test output quiet
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()


def _evaluate(backend: _FakeBackend, texts: list[str]):
    evaluator = Evaluator(
        fi_api_key="synthetic-api-key",
        fi_secret_key="synthetic-secret-key",
        fi_base_url=backend.url,
        timeout=10,
    )
    return evaluator.evaluate(
        eval_templates=EVAL_NAME,
        inputs={"text": texts},
        skip_input_mapping=True,
    )


@pytest.mark.parametrize("status", [429, 402])
def test_partial_batch_denial_keeps_finished_results_and_is_not_resent(status):
    # First reply: 2 of 3 items ran. Any resend is refused at item 0 (as in the ticket).
    replies = [_denial(status, completed=2), _denial(status, completed=0)]
    with _FakeBackend(replies) as backend:
        result = _evaluate(backend, ["a", "b", "c"])

    assert len(backend.posts) == 1, "a partially-executed batch must not be resent"
    assert len(result.eval_results) == 3

    finished = result.eval_results[:2]
    assert [r.output for r in finished] == ["Passed", "Passed"]
    assert [r.reason for r in finished] == ["finished item 0", "finished item 1"]
    assert [r.eval_id for r in finished] == ["eval-0", "eval-1"]
    assert [r.runtime for r in finished] == [10, 11]

    refused = result.eval_results[2]
    assert refused.name == EVAL_NAME
    assert refused.output is None
    assert "Retry only items at or after failed_index" in refused.reason


def test_batch_denied_at_first_item_marks_every_item_refused_without_resending():
    with _FakeBackend([_denial(429, completed=0)]) as backend:
        result = _evaluate(backend, ["a", "b", "c"])

    assert len(backend.posts) == 1
    assert len(result.eval_results) == 3
    assert all(r.output is None for r in result.eval_results)
    assert all("rate limit" in r.reason for r in result.eval_results)


def test_server_error_is_still_retried():
    # Ordinary failures keep the existing retry behaviour.
    with _FakeBackend([(500, {"status": False, "message": "boom"})]) as backend:
        result = _evaluate(backend, ["a"])

    assert len(backend.posts) == 3
    assert len(result.eval_results) == 1
    assert result.eval_results[0].output is None


def test_rate_limit_without_batch_progress_keeps_existing_behaviour():
    # A 429 that carries no completed_results has nothing to lose by resending.
    reply = (429, {"status": False, "code": "rate_limited", "message": "slow down"})
    with _FakeBackend([reply]) as backend:
        result = _evaluate(backend, ["a"])

    assert len(backend.posts) == 3
    assert len(result.eval_results) == 1
    assert result.eval_results[0].output is None
