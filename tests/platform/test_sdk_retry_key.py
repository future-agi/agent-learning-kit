"""TH-8848: a submission retried after a lost response carries its original retry key.

The platform deduplicates on the key, so this is what stops a flaky connection from
creating or charging for the same work twice. The stand-in platform is a real HTTP
server: its first answer is lost after it has read the request, as when a proxy drops
the connection mid-response.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fi.alk.platform import Client


@pytest.fixture
def flaky_platform():
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server's naming
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            received.append(
                {"header": self.headers.get("Idempotency-Key"), "body": body}
            )
            if len(received) == 1:
                self.close_connection = True
                self.connection.shutdown(2)  # read, then drop without answering
                return
            reply = json.dumps({"id": "job-1"}).encode()
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", received
    server.shutdown()


def _client(base_url):
    return Client(base_url=base_url, api_key="key", secret_key="secret")


def test_a_lost_response_is_retried_with_the_same_header_key(flaky_platform):
    base_url, received = flaky_platform

    with _client(base_url) as sdk:
        job = sdk.submit("/simulate/api/harness-jobs/", {"name": "x"})

    assert job == {"id": "job-1"}
    keys = [request["header"] for request in received]
    assert len(keys) == 2
    assert keys[0] and keys[0] == keys[1]
