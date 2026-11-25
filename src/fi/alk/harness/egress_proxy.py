"""The proxy in front of the agent under test in a controlled run.

A call to a host outside the test world is refused and logged as the agent's `egress_blocked` attempt. The proxy is
cooperative (`HTTP(S)_PROXY`): a client that ignores it goes direct, and the sandbox firewall is the boundary."""

from __future__ import annotations

import os
import re
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
# How long a client may go silent while sending a request before its connection is dropped, unless
# `ALK_EGRESS_CLIENT_TIMEOUT_SECONDS` says otherwise.
_CLIENT_SECONDS = 30.0
# Connections served at once for each CPU the sandbox has, unless `ALK_EGRESS_MAX_CLIENTS` says otherwise.
_CLIENTS_PER_CPU = 32
_PIECE = 65536
# Request headers that describe the client's hop to this proxy, never forwarded to the host. `host` is rewritten
# from the request URL, so the host that was checked is the host that is asked.
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "proxy-connection",
        "keep-alive",
        "te",
        "trailer",
        "upgrade",
        "proxy-authenticate",
        "proxy-authorization",
        "host",
    }
)
# What marks where a request body ends: a `connection` header cannot list these away.
_FRAMING = frozenset({"content-length", "transfer-encoding"})


def no_proxy_entry(domain: str) -> str:
    """One allowed destination as `NO_PROXY` reads it: `*.hf.co` is `.hf.co`."""
    entry = domain.strip().lower()
    return entry[1:] if entry.startswith("*.") else entry


def no_proxy(allowed: Iterable[str], hosts: Iterable[str] = ()) -> str:
    """`NO_PROXY` for a controlled run: loopback, the environment's own hosts and every allowed domain."""
    entries = [*LOOPBACK, *(host.strip().lower() for host in hosts), *(no_proxy_entry(one) for one in allowed)]
    return ",".join(dict.fromkeys(one for one in entries if one))


def host_allowed(host: str, allowed: Iterable[str], *, loopback: bool = True) -> bool:
    """Whether a host is one the run allows, matched the way clients read `NO_PROXY`; loopback too unless
    `loopback` is False."""
    host = host.strip().strip("[]").lower().rstrip(".")
    for entry in (*(LOOPBACK if loopback else ()), *allowed):
        name = no_proxy_entry(entry).lstrip(".")
        if name and (host == name or host.endswith("." + name)):
            return True
    return False


def entry_admits(entry: str, host: str) -> bool:
    """Whether one allowed-host entry admits a host, read as the sandbox firewall reads it: `example.com` is
    that host only; `*.example.com` is every host under it, at any depth, not `example.com` itself."""
    host = host.strip().strip("[]").lower().rstrip(".")
    entry = entry.strip().lower().rstrip(".")
    if entry.startswith("*."):
        return bool(entry[2:]) and host.endswith(entry[1:])
    return bool(entry) and host == entry


class _Refused:
    """Neither runs nor knows an actor: the proxy only ever refuses through the door."""

    def run(self, call: ToolCall) -> Executed:  # pragma: no cover - never called
        return Executed(403, error="outside the test world")

    def actor(self, actor_id: str) -> Mapping[str, Any] | None:  # pragma: no cover - never called
        return None


def _setting(name: str, default: float) -> float:
    """A positive number from the environment variable `name`, or `default` when it is unset."""
    configured = os.environ.get(name, "").strip()
    if not configured:
        return default
    try:
        value = float(configured)
    except ValueError:
        value = 0.0
    if not 0 < value < float("inf"):
        raise ValueError(f"{name} must be a positive number, not {configured!r}")
    return value


def _tunnel_seconds() -> float:
    """How long a tunnel or relay may sit idle: as long as the longest call the runner allows, a voice call or one
    chat turn, since an agent's connection may wait that long; or `ALK_EGRESS_TUNNEL_IDLE_SECONDS`."""
    from .call_runner import _DEFAULT_CALL_TIMEOUT_SECONDS
    from .chat_call_runner import _chat_target_timeout_seconds

    longest = max(_DEFAULT_CALL_TIMEOUT_SECONDS, _chat_target_timeout_seconds())
    return _setting("ALK_EGRESS_TUNNEL_IDLE_SECONDS", longest)


def _max_clients() -> int:
    """Connections served at once: `_CLIENTS_PER_CPU` for each CPU of the sandbox's quota, or of the machine when it
    sets none."""
    from .process_runtime import _cgroup_cpu_quota

    cpus = _cgroup_cpu_quota() or os.cpu_count() or 1
    return max(1, int(_setting("ALK_EGRESS_MAX_CLIENTS", cpus * _CLIENTS_PER_CPU)))


class RefusedAddress(OSError):
    """A host the proxy allows by name, refused for the address it resolves to."""


class _BadBody(Exception):
    """The client's request body does not follow its own framing."""


class _Server(ThreadingHTTPServer):
    """One thread per connection, at most `_max_clients()` of them; one more is closed straight away."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(address, handler)
        self.slots = threading.BoundedSemaphore(_max_clients())

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class EgressProxy:
    """A local proxy that refuses every host the run does not allow and logs each refusal."""

    # Names the path a refused call took, in its logged arguments, when it is not the agent's own process.
    via: str | None = None

    def __init__(
        self,
        allowed: Iterable[str],
        log: ActionLog | None = None,
        tools_by_host: Mapping[str, str] | None = None,
    ) -> None:
        self.allowed = tuple(allowed)
        self.door = WorldDoor(_Refused(), log, _Refused()) if log is not None else None
        self.tools_by_host = {key.lower(): value for key, value in (tools_by_host or {}).items()}
        self.server = _Server(("127.0.0.1", 0), _handler(self))
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

    def allows(self, host: str) -> bool:
        return host_allowed(host, self.allowed)

    def open(self, host: str, port: int) -> socket.socket:
        """A connection to an allowed host. Raises OSError when it can't be reached, `RefusedAddress` when it must
        not be."""
        return socket.create_connection((host, port), timeout=30)

    def refuse(self, host: str, method: str) -> None:
        if self.door is None:
            return
        tool = self.tools_by_host.get(host.lower())
        arguments = {
            "host": host,
            "method": method,
            **({"tool": tool} if tool else {}),
            **({"via": self.via} if self.via else {}),
        }
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
        timeout = _setting("ALK_EGRESS_CLIENT_TIMEOUT_SECONDS", _CLIENT_SECONDS)
        tunnel = _tunnel_seconds()

        def log_message(self, *_args: object) -> None:
            return

        def do_CONNECT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            host, _, port = self.path.rpartition(":")
            host = host.strip("[]") or self.path
            if not proxy.allows(host):
                proxy.refuse(host, "CONNECT")
                self._refuse(host)
                return
            upstream = self._upstream(host, port or 443, "CONNECT")
            if upstream is None:
                return
            self.send_response(200, "Connection established")
            self.end_headers()
            # What the client sent right behind its CONNECT already sits in the read buffer, never seen by `_pipe`.
            self.connection.setblocking(False)
            try:
                early = self.rfile.read1(_PIECE)
            except OSError:
                early = b""
            finally:
                self.connection.settimeout(self.timeout)
            try:
                if early:
                    upstream.sendall(early)
            except OSError:
                upstream.close()
                return
            self._pipe(upstream)

        def _upstream(self, host: str, port: int | str, method: str) -> socket.socket | None:
            """A connection to an allowed host, or None once the client has its error: 403 when its address is one
            the proxy refuses, 502 when it cannot be reached, 400 when the address is this proxy's own, which would
            only relay the client back to itself."""
            try:
                upstream = proxy.open(host, int(port))
            except RefusedAddress as refused:
                proxy.refuse(host, method)
                self._refuse(host, str(refused))
                return None
            except (OSError, ValueError):
                self.send_error(502)
                return None
            if upstream.getpeername()[:2] == proxy.server.server_address[:2]:
                upstream.close()
                self.send_error(400, "The proxy cannot be its own destination")
                return None
            upstream.settimeout(self.tunnel)
            return upstream

        def _pipe(self, upstream: socket.socket) -> None:
            sockets = [self.connection, upstream]
            try:
                while True:
                    ready, _, _ = select.select(sockets, [], [], self.tunnel)
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
            try:
                port = target.port or 80
            except ValueError:
                port = 0
            if target.scheme.lower() != "http" or not host or not port:
                # Not a proxy request (`GET /x` names no host): nothing was asked of the world, so nothing is logged.
                self.send_error(400, "A proxy request names an absolute http:// URL")
                return
            if not proxy.allows(host):
                proxy.refuse(host, self.command)
                self._refuse(host)
                return
            framing = self._framing()
            if framing is None:
                self.send_error(400, "The request body's length cannot be told")
                return
            path = target.path or "/"
            if target.query:
                path += "?" + target.query
            authority = (f"[{host}]" if ":" in host else host) + (f":{target.port}" if target.port else "")
            # Connection names more hop-by-hop headers of its own.
            named = {
                one.strip().lower() for value in self.headers.get_all("connection") or [] for one in value.split(",")
            }
            dropped = _HOP_BY_HOP | (named - _FRAMING)
            # Relayed as bytes, as a CONNECT tunnel is: the answer goes back exactly as the host sent it.
            head = [f"{self.command} {path} {self.request_version}", f"host: {authority}"] + [
                f"{key}: {value}" for key, value in self.headers.items() if key.lower() not in dropped
            ]
            upstream = self._upstream(host, port, self.command)
            if upstream is None:
                return
            self.close_connection = True
            try:
                upstream.sendall(("\r\n".join([*head, "connection: close"]) + "\r\n\r\n").encode("latin-1"))
                if framing == "chunked":
                    self._relay_chunks(upstream)
                else:
                    self._relay(upstream, framing)
            except _BadBody:
                upstream.close()
                self.send_error(400, "The request body does not match its framing")
                return
            except OSError:
                upstream.close()
                return
            self._pipe(upstream)

        def _framing(self) -> int | str | None:
            """Where the request body ends: `"chunked"`, its length in bytes, or None when that cannot be told."""
            codings = [
                one.strip().lower()
                for value in self.headers.get_all("transfer-encoding") or []
                for one in value.split(",")
            ]
            lengths = {value.strip() for value in self.headers.get_all("content-length") or []}
            if codings:
                return "chunked" if codings[-1] == "chunked" and not lengths else None
            if len(lengths) > 1 or not all(re.fullmatch(r"[0-9]+", value) for value in lengths):
                return None
            return int(lengths.pop()) if lengths else 0

        def _relay(self, upstream: socket.socket, size: int) -> None:
            """Stream exactly `size` bytes of the request body on, a piece at a time."""
            while size:
                piece = self.rfile.read(min(size, _PIECE))
                if not piece:
                    raise _BadBody
                upstream.sendall(piece)
                size -= len(piece)

        def _relay_chunks(self, upstream: socket.socket) -> None:
            """Stream a chunked request body on as it was sent, up to the end of its trailer section."""
            while True:
                line = self._line()
                size = line.split(b";", 1)[0].strip()
                if not re.fullmatch(rb"[0-9A-Fa-f]+", size):
                    raise _BadBody
                upstream.sendall(line)
                if not int(size, 16):
                    break
                self._relay(upstream, int(size, 16) + 2)
            while (line := self._line()) not in (b"\r\n", b"\n"):
                upstream.sendall(line)
            upstream.sendall(line)

        def _line(self) -> bytes:
            line = self.rfile.readline(_PIECE + 1)
            if len(line) > _PIECE or not line.endswith(b"\n"):
                raise _BadBody
            return line

        def _refuse(self, host: str, why: str = "") -> None:
            content = (why or f"{host} is outside the test world").encode()
            self.send_response(403, "Forbidden")
            self.send_header("content-type", "text/plain")
            self.send_header("content-length", str(len(content)))
            self.send_header("connection", "close")
            self.end_headers()
            self.wfile.write(content)
            self.close_connection = True

        do_GET = do_POST = do_PUT = do_PATCH = _forward  # noqa: N815 - BaseHTTPRequestHandler API
        do_DELETE = do_HEAD = do_OPTIONS = _forward  # noqa: N815 - BaseHTTPRequestHandler API

    return Handler
