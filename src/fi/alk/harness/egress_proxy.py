"""The proxy in front of the agent under test in a controlled run.

The agent's process gets `HTTP_PROXY` and `HTTPS_PROXY` pointing here and `NO_PROXY` naming every destination the run
allows, so allowed traffic goes straight out and never reaches this proxy. Whatever does reach it is a call to a host
outside the test world: plain HTTP is answered 403 and an HTTPS `CONNECT` is refused, and each one is written to the
world's action log as the agent's refused attempt (`egress_blocked`). A request for an allowed host from a client that
ignores `NO_PROXY` is passed through untouched.
"""

from __future__ import annotations

import http.client
import select
import socket
import threading
from collections.abc import Iterable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from .world_door import AGENT, ActionLog, Executed, ToolCall, WorldDoor

REFUSAL_CODE = "egress_blocked"
LOOPBACK = ("localhost", "127.0.0.1", "::1")
_TUNNEL_SECONDS = 300.0


def no_proxy_entry(domain: str) -> str:
    """One allowed destination as `NO_PROXY` reads it: `*.hf.co` is `.hf.co`."""
    entry = domain.strip().lower()
    return entry[1:] if entry.startswith("*.") else entry


def no_proxy(allowed: Iterable[str], hosts: Iterable[str] = ()) -> str:
    """`NO_PROXY` for a controlled run: loopback, the environment's own hosts and every allowed domain."""
    entries = [*LOOPBACK, *(host.strip().lower() for host in hosts), *(no_proxy_entry(one) for one in allowed)]
    return ",".join(dict.fromkeys(one for one in entries if one))


def host_allowed(host: str, allowed: Iterable[str]) -> bool:
    """Whether a host is one the run allows, matched the way clients read `NO_PROXY`."""
    host = host.strip().strip("[]").lower().rstrip(".")
    for entry in (*LOOPBACK, *allowed):
        name = no_proxy_entry(entry).lstrip(".")
        if name and (host == name or host.endswith("." + name)):
            return True
    return False


class _Refused:
    """Neither runs nor knows an actor: the proxy only ever refuses through the door."""

    def run(self, call: ToolCall) -> Executed:  # pragma: no cover - never called
        return Executed(403, error="outside the test world")

    def actor(self, actor_id: str) -> Mapping[str, Any] | None:  # pragma: no cover - never called
        return None


class EgressProxy:
    """A local proxy that refuses every host the run does not allow and logs each refusal."""

    def __init__(
        self,
        allowed: Iterable[str],
        log: ActionLog | None = None,
        tools_by_host: Mapping[str, str] | None = None,
    ) -> None:
        self.allowed = tuple(allowed)
        self.door = WorldDoor(_Refused(), log, _Refused()) if log is not None else None
        self.tools_by_host = {key.lower(): value for key, value in (tools_by_host or {}).items()}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self))
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def environment(self, hosts: Iterable[str] = ()) -> dict[str, str]:
        """The proxy variables for the agent's process, in both cases, since clients read one or the other."""
        excluded = no_proxy(self.allowed, hosts)
        return {
            "HTTP_PROXY": self.url,
            "HTTPS_PROXY": self.url,
            "http_proxy": self.url,
            "https_proxy": self.url,
            "NO_PROXY": excluded,
            "no_proxy": excluded,
            # Node's built-in fetch and http read the variables above only when this is set.
            "NODE_USE_ENV_PROXY": "1",
        }

    def refuse(self, host: str, method: str) -> None:
        if self.door is None:
            return
        tool = self.tools_by_host.get(host.lower())
        arguments = {"host": host, "method": method, **({"tool": tool} if tool else {})}
        self.door.refuse(
            ToolCall(action=f"egress.{host}", name=host, arguments=arguments, actor=AGENT, method=method),
            REFUSAL_CODE,
            f"{host} is outside the test world",
        )

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _handler(proxy: EgressProxy) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_CONNECT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            host, _, port = self.path.rpartition(":")
            host = host.strip("[]") or self.path
            if not host_allowed(host, proxy.allowed):
                proxy.refuse(host, "CONNECT")
                self._refuse(host)
                return
            try:
                upstream = socket.create_connection((host, int(port or 443)), timeout=30)
            except (OSError, ValueError):
                self.send_error(502)
                return
            self.send_response(200, "Connection established")
            self.end_headers()
            self._pipe(upstream)

        def _pipe(self, upstream: socket.socket) -> None:
            sockets = [self.connection, upstream]
            try:
                while True:
                    ready, _, _ = select.select(sockets, [], [], _TUNNEL_SECONDS)
                    if not ready:
                        return
                    for one in ready:
                        data = one.recv(65536)
                        if not data:
                            return
                        (upstream if one is self.connection else self.connection).sendall(data)
            except OSError:
                return
            finally:
                upstream.close()

        def _forward(self) -> None:
            target = urlsplit(self.path)
            host = target.hostname or ""
            if not host_allowed(host, proxy.allowed):
                proxy.refuse(host, self.command)
                self._refuse(host)
                return
            size = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(size) if size else None
            path = target.path or "/"
            if target.query:
                path += "?" + target.query
            headers = {key: value for key, value in self.headers.items() if key.lower() != "proxy-connection"}
            try:
                connection = http.client.HTTPConnection(host, target.port or 80, timeout=30)
                connection.request(self.command, path, body=body, headers=headers)
                answer = connection.getresponse()
                content = answer.read()
            except OSError:
                self.send_error(502)
                return
            self.send_response(answer.status, answer.reason)
            for key, value in answer.getheaders():
                if key.lower() not in ("transfer-encoding", "connection", "content-length"):
                    self.send_header(key, value)
            self.send_header("content-length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            connection.close()

        def _refuse(self, host: str) -> None:
            content = f"{host} is outside the test world".encode()
            self.send_response(403, "Forbidden")
            self.send_header("content-type", "text/plain")
            self.send_header("content-length", str(len(content)))
            self.send_header("connection", "close")
            self.end_headers()
            self.wfile.write(content)
            self.close_connection = True

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _forward  # noqa: N815

    return Handler
