"""The refusing proxy relays an allowed plain-HTTP request whole and only as the URL names it, on a host name that is
not loopback: the name is mapped to a local upstream inside each test."""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from urllib import request

import pytest

from fi.alk.harness.egress_proxy import EgressProxy
from fi.alk.harness.world_door import MemoryLog

NAME = "service.example.test"


class _Upstream:
    """A local HTTP host that records each request's head and raw body, then answers with the body it read."""

    def __init__(self) -> None:
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.seen: list[tuple[str, dict[str, str], bytes]] = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            client.settimeout(3)
            with client, client.makefile("rb") as reader:
                try:
                    line = reader.readline().decode("latin-1").strip()
                    headers: dict[str, str] = {}
                    while (header := reader.readline()) not in (b"\r\n", b""):
                        key, _, value = header.decode("latin-1").partition(":")
                        headers[key.strip().lower()] = value.strip()
                    if headers.get("transfer-encoding") == "chunked":
                        body = b""
                        while size := int((chunk := reader.readline()).split(b";")[0], 16):
                            body += chunk + reader.read(size + 2)
                        body += chunk
                        while (trailer := reader.readline()) not in (b"\r\n", b""):
                            body += trailer
                        body += trailer
                    else:
                        body = reader.read(int(headers.get("content-length") or 0))
                except (OSError, ValueError):
                    line, headers, body = "", {}, b""
                self.seen.append((line, headers, body))
                answer = body[:64] or b"none"
                with contextlib.suppress(OSError):
                    client.sendall(b"HTTP/1.1 200 OK\r\ncontent-length: %d\r\n\r\n%s" % (len(answer), answer))

    def close(self) -> None:
        self.server.close()


@pytest.fixture()
def upstream(monkeypatch):
    """A local upstream reached as `NAME`: the resolver maps the name to it for this test only."""
    real = socket.getaddrinfo

    def resolve(host, *args, **kwargs):
        return real("127.0.0.1" if host == NAME else host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    server = _Upstream()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture()
def proxy():
    log = MemoryLog()
    one = EgressProxy([NAME], log)
    try:
        yield one, log
    finally:
        one.close()


def _send(proxy: EgressProxy, raw: bytes, wait: float = 5) -> bytes:
    """Send raw bytes to the proxy and read its whole answer."""
    with socket.create_connection(("127.0.0.1", proxy.server.server_address[1]), timeout=wait) as connection:
        connection.sendall(raw)
        answer = b""
        while piece := connection.recv(65536):
            answer += piece
    return answer


def test_an_allowed_named_host_gets_the_body_and_content_headers_and_the_client_its_answer(upstream, proxy) -> None:
    one, log = proxy
    opener = request.build_opener(request.ProxyHandler({"http": one.url}))
    sent = request.Request(
        f"http://{NAME}:{upstream.port}/items?page=2",
        data=b'{"name": "first"}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(sent, timeout=5) as answer:
        assert (answer.status, answer.read()) == (200, b'{"name": "first"}')
    [(line, headers, body)] = upstream.seen
    assert line == "POST /items?page=2 HTTP/1.1"
    assert body == b'{"name": "first"}'
    assert headers["content-type"] == "application/json"
    assert headers["content-length"] == "17"
    assert headers["host"] == f"{NAME}:{upstream.port}"
    assert log.rows() == []


def test_a_chunked_body_is_relayed_whole_however_it_arrives(upstream, proxy) -> None:
    one, _ = proxy
    large = b"x" * 150_000
    chunks = b"5;note=first\r\nhello\r\n%x\r\n%s\r\n0\r\nx-checksum: 1\r\n\r\n" % (len(large), large)
    head = f"POST http://{NAME}:{upstream.port}/upload HTTP/1.1\r\nHost: {NAME}\r\nTransfer-Encoding: chunked\r\n\r\n"
    answer = _send(one, head.encode() + chunks)
    assert answer.startswith(b"HTTP/1.1 200 OK")
    [(line, headers, body)] = upstream.seen
    assert line == "POST /upload HTTP/1.1"
    assert headers["transfer-encoding"] == "chunked"
    assert body == chunks


@pytest.mark.parametrize(
    "framing",
    [
        "Content-Length: -1",
        "Content-Length: abc",
        "Content-Length: 5\r\nContent-Length: 6",
        "Content-Length: 5\r\nTransfer-Encoding: chunked",
        "Transfer-Encoding: gzip",
    ],
)
def test_a_body_whose_end_cannot_be_told_is_answered_400_and_never_sent_on(upstream, proxy, framing) -> None:
    one, log = proxy
    started = time.monotonic()
    answer = _send(one, f"POST http://{NAME}:{upstream.port}/x HTTP/1.1\r\n{framing}\r\n\r\nhello".encode())
    assert answer.split(b"\r\n", 1)[0].split(b" ")[1] == b"400"
    assert time.monotonic() - started < 3
    assert upstream.seen == []
    assert log.rows() == []


def test_a_malformed_chunk_is_answered_400(upstream, proxy) -> None:
    one, _ = proxy
    head = f"POST http://{NAME}:{upstream.port}/x HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
    answer = _send(one, head.encode() + b"zz\r\nhello\r\n0\r\n\r\n")
    assert answer.split(b"\r\n", 1)[0].split(b" ")[1] == b"400"


def test_host_comes_from_the_url_and_the_hop_to_the_proxy_stays_behind(upstream, proxy) -> None:
    one, _ = proxy
    raw = (
        f"POST http://{NAME}:{upstream.port}/x HTTP/1.1\r\n"
        "Host: other.example.net\r\n"
        "Proxy-Authorization: Basic dXNlcjpwYXNz\r\n"
        "Proxy-Authenticate: Basic\r\n"
        "Proxy-Connection: keep-alive\r\n"
        "Keep-Alive: timeout=5\r\n"
        "TE: trailers\r\n"
        "Trailer: x-checksum\r\n"
        "Upgrade: websocket\r\n"
        "Connection: X-Hop, Content-Length\r\n"
        "X-Hop: 1\r\n"
        "X-Kept: yes\r\n"
        "Content-Length: 5\r\n\r\nhello"
    )
    assert _send(one, raw.encode()).startswith(b"HTTP/1.1 200 OK")
    [(_, headers, body)] = upstream.seen
    assert headers == {
        "host": f"{NAME}:{upstream.port}",
        "x-kept": "yes",
        "content-length": "5",
        "connection": "close",
    }
    assert body == b"hello"


def test_an_allowed_named_host_is_tunnelled_over_connect(upstream, proxy) -> None:
    one, log = proxy
    with socket.create_connection(("127.0.0.1", one.server.server_address[1]), timeout=5) as connection:
        connection.sendall(f"CONNECT {NAME}:{upstream.port} HTTP/1.1\r\nHost: {NAME}\r\n\r\n".encode())
        established = connection.recv(4096)
        assert b" 200 " in established.split(b"\r\n", 1)[0]
        connection.sendall(b"POST /inside HTTP/1.1\r\nContent-Length: 4\r\n\r\nping")
        answer = b""
        while piece := connection.recv(65536):
            answer += piece
    assert answer.endswith(b"ping")
    assert upstream.seen == [("POST /inside HTTP/1.1", {"content-length": "4"}, b"ping")]
    assert log.rows() == []


def test_bytes_sent_right_behind_a_connect_reach_the_host(upstream, proxy) -> None:
    """A client that writes its first bytes (a TLS hello) without waiting for the 200 loses none of them."""
    one, _ = proxy
    inside = b"POST /inside HTTP/1.1\r\nContent-Length: 4\r\n\r\nping"
    raw = f"CONNECT {NAME}:{upstream.port} HTTP/1.1\r\nHost: {NAME}\r\n\r\n".encode() + inside
    answer = _send(one, raw)
    assert answer.endswith(b"ping")
    assert upstream.seen == [("POST /inside HTTP/1.1", {"content-length": "4"}, b"ping")]


def test_an_idle_tunnel_is_closed_after_the_tunnel_idle_time(upstream, monkeypatch) -> None:
    monkeypatch.setenv("ALK_EGRESS_TUNNEL_IDLE_SECONDS", "0.5")
    one = EgressProxy([NAME])
    try:
        with socket.create_connection(("127.0.0.1", one.server.server_address[1]), timeout=5) as connection:
            connection.sendall(f"CONNECT {NAME}:{upstream.port} HTTP/1.1\r\n\r\n".encode())
            assert b" 200 " in connection.recv(4096).split(b"\r\n", 1)[0]
            started = time.monotonic()
            assert connection.recv(1) == b""
            assert time.monotonic() - started < 3
    finally:
        one.close()


@pytest.mark.parametrize("alias", ["127.0.0.1", "localhost"])
def test_the_proxy_never_relays_to_itself(proxy, alias) -> None:
    one, log = proxy
    port = one.server.server_address[1]
    tunnel = _send(one, f"CONNECT {alias}:{port} HTTP/1.1\r\n\r\n".encode())
    relay = _send(one, f"GET http://{alias}:{port}/ HTTP/1.1\r\n\r\n".encode())
    for answer in (tunnel, relay):
        assert answer.split(b"\r\n", 1)[0].split(b" ")[1] == b"400"
    assert log.rows() == []


def test_an_origin_form_request_is_400_and_logs_nothing(proxy) -> None:
    one, log = proxy
    answer = _send(one, b"GET /x HTTP/1.1\r\nHost: whatever\r\n\r\n")
    assert answer.split(b"\r\n", 1)[0].split(b" ")[1] == b"400"
    assert log.rows() == []


def test_a_silent_client_is_dropped_and_connections_past_the_bound_are_closed(monkeypatch) -> None:
    monkeypatch.setenv("ALK_EGRESS_CLIENT_TIMEOUT_SECONDS", "0.5")
    monkeypatch.setenv("ALK_EGRESS_MAX_CLIENTS", "1")
    one = EgressProxy([NAME])
    address = ("127.0.0.1", one.server.server_address[1])
    try:
        with socket.create_connection(address, timeout=5) as silent:
            silent.sendall(b"GET http://")
            time.sleep(0.1)
            # A complete request past the bound is closed unanswered, where a served one would get its 400.
            with socket.create_connection(address, timeout=5) as extra:
                try:
                    extra.sendall(b"GET /x HTTP/1.1\r\n\r\n")
                    assert extra.recv(1) == b""
                except ConnectionResetError:
                    pass
            started = time.monotonic()
            assert silent.recv(1) == b""
            assert time.monotonic() - started < 3
        # The slot is free again once the silent client is gone.
        time.sleep(0.1)
        assert _send(one, b"GET /x HTTP/1.1\r\n\r\n").split(b" ")[1] == b"400"
    finally:
        one.close()


@pytest.mark.parametrize(
    "name", ["ALK_EGRESS_MAX_CLIENTS", "ALK_EGRESS_CLIENT_TIMEOUT_SECONDS", "ALK_EGRESS_TUNNEL_IDLE_SECONDS"]
)
@pytest.mark.parametrize("configured", ["0", "-3", "many", "inf"])
def test_an_unusable_override_fails_naming_the_variable(monkeypatch, name, configured) -> None:
    monkeypatch.setenv(name, configured)
    with pytest.raises(ValueError, match=name):
        EgressProxy([NAME]).close()
