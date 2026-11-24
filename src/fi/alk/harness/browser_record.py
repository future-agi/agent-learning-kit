"""What the world keeps of the agent's browsing in the environment's browser: the pages it opened, what it clicked
and typed, and the sites it was refused.

The kit keeps this record, not the agent. A second DevTools client is not told of the clicks and keys another
client sends, so a small listener runs inside each page, in a world of its own that the page's scripts cannot
reach, and reports through a binding. Pages, and the requests they make, are read from the DevTools protocol.

The record is the world's, never the agent's tool calls: it is kept apart from the action log, so nothing that
counts tool calls or world actions reads it.

Two lists of refused sites, because a page asks many hosts for scripts, fonts and trackers:

- a page the agent tried to open off the list, kept whole each time;
- a request a page made off the list, counted for each host.

Text typed into a password field is never kept, only that typing happened. Every text in a row passes the kit's
cleaner.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import socket
import struct
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from urllib import request as urllib_request
from urllib.parse import urlsplit

from .egress_proxy import host_allowed
from .outbound import redact_outbound_text

logger = logging.getLogger(__name__)

RECORD_ROWS = 500
_TEXT = 200
_SAME_ATTEMPT_SECONDS = 2.0
_ACTIONS = frozenset({"click", "typing"})
_TARGET_FIELDS = ("tag", "role", "label", "text")

# The page's listener runs in this world and only this world can call the binding.
LISTENER_WORLD = "alk-browser-record"
BINDING = "alkBrowserRecord"

# A field is a password field for the rest of the page's life once it has been one. Nothing here reads a field's
# value except to report typing into a field that is not one.
LISTENER = r"""
(() => {
  if (globalThis.__alkBrowserRecord) return;
  globalThis.__alkBrowserRecord = true;
  const send = globalThis.%(binding)s;
  if (typeof send !== 'function') return;
  const say = (row) => { try { send(JSON.stringify(row)); } catch (e) {} };
  const secret = new WeakSet();
  const isSecret = (el) => {
    if (!el || el.nodeType !== 1) return false;
    const type = String(el.getAttribute('type') || '').toLowerCase();
    const auto = String(el.getAttribute('autocomplete') || '').toLowerCase();
    if (type === 'password' || auto.includes('password')) secret.add(el);
    return secret.has(el);
  };
  const cut = (text) => String(text || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  const field = (el) => /^(input|textarea|select)$/i.test(el.tagName) || el.isContentEditable;
  const describe = (el) => {
    if (!el || el.nodeType !== 1) return {};
    const label = el.getAttribute('aria-label') || el.getAttribute('name') || el.getAttribute('placeholder')
      || el.getAttribute('title') || el.getAttribute('alt') || '';
    return {
      tag: el.tagName.toLowerCase(),
      role: cut(el.getAttribute('role')),
      label: cut(label),
      text: field(el) || isSecret(el) ? '' : cut(el.innerText),
    };
  };
  addEventListener('click', (event) => {
    const hit = event.target && event.target.closest
      ? event.target.closest('a,button,input,select,textarea,label,[role]') || event.target : event.target;
    say({kind: 'click', url: location.href, target: describe(hit)});
  }, true);
  addEventListener('input', (event) => {
    const el = event.target;
    const hidden = isSecret(el);
    const typed = hidden || !el || el.isContentEditable ? null : String(el.value || '').slice(0, %(text)d);
    say({kind: 'typing', url: location.href, target: describe(el), secret: hidden, text: typed});
  }, true);
})();
""" % {"binding": BINDING, "text": _TEXT}


def _clean(text: Any, limit: int = _TEXT) -> str:
    return redact_outbound_text(str(text))[:limit]


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _web(url: str) -> bool:
    return url.lower().startswith(("http://", "https://"))


class BrowserRecord:
    """One trial's record of the browser. Safe to feed from the recorder's thread and read from another."""

    def __init__(
        self, sites: Iterable[str], *, cap: int = RECORD_ROWS, clock: Callable[[], float] = time.time
    ) -> None:
        self.sites = tuple(sites)
        self.cap = cap
        self.clock = clock
        self._lock = threading.Lock()
        self._rows: list[dict[str, Any]] = []
        self._navigations: list[dict[str, Any]] = []
        self._page_requests: dict[str, int] = {}
        self._tabs: dict[str, int] = {}
        self._last_refused: dict[tuple[str, str], float] = {}
        self._truncated = False
        self._why_incomplete: str | None = None

    def allowed(self, url: str) -> bool:
        return host_allowed(_host(url), self.sites, loopback=False)

    def navigated(self, tab: str, url: str) -> None:
        """A page the browser opened. Its error page at a refused address is the refusal, already kept."""
        if url in ("", "about:blank") or (_web(url) and not self.allowed(url)):
            return
        with self._lock:
            self._add({"kind": "navigation", "url": _clean(url, 2048)}, tab)

    def requested(self, tab: str, url: str, *, document: bool, main_frame: bool) -> None:
        """A request the browser made; kept only when its host is off the list, the rule its proxy keeps."""
        if not _web(url) or self.allowed(url):
            return
        host = _host(url)
        with self._lock:
            if not (document and main_frame):
                self._page_requests[host] = self._page_requests.get(host, 0) + 1
                return
            # Chromium tries a plain address over https first and back again: one attempt, not three.
            parts = urlsplit(url)
            place = (host, parts.path + ("?" + parts.query if parts.query else ""))
            now = self.clock()
            last = self._last_refused.get(place)
            self._last_refused[place] = now
            if last is not None and now - last < _SAME_ATTEMPT_SECONDS:
                return
            if len(self._navigations) >= self.cap:
                self._truncated = True
                return
            self._navigations.append({"url": _clean(url, 2048), "at": now, "tab": self._tab(tab)})

    def acted(self, tab: str, payload: str) -> None:
        """A click or typing the page's listener saw. Anything else the page sends is dropped."""
        try:
            raw = json.loads(payload)
        except (TypeError, ValueError):
            return
        if not isinstance(raw, dict) or raw.get("kind") not in _ACTIONS or not isinstance(raw.get("url"), str):
            return
        target = raw.get("target") if isinstance(raw.get("target"), dict) else {}
        row: dict[str, Any] = {
            "kind": raw["kind"],
            "url": _clean(raw["url"], 2048),
            "target": {key: _clean(target[key], 80) for key in _TARGET_FIELDS if target.get(key)},
        }
        if raw["kind"] == "typing":
            secret = raw.get("secret") is True
            row["secret"] = secret
            text = raw.get("text")
            row["text"] = None if secret or not isinstance(text, str) else _clean(text)
        with self._lock:
            last = self._rows[-1] if self._rows else None
            if (
                row["kind"] == "typing"
                and last is not None
                and last["kind"] == "typing"
                and last["tab"] == self._tab(tab)
                and (last["url"], last["target"]) == (row["url"], row["target"])
            ):
                last.update(text=row["text"], secret=row["secret"], at=self.clock())
                return
            self._add(row, tab)

    def broken(self, why: str) -> None:
        with self._lock:
            if self._why_incomplete is None:
                self._why_incomplete = why

    def body(self) -> dict[str, Any]:
        with self._lock:
            body: dict[str, Any] = {
                "complete": self._why_incomplete is None,
                "truncated": self._truncated,
                "rows": [dict(row) for row in self._rows],
                "blocked_navigations": [dict(one) for one in self._navigations],
                "blocked_page_requests": [
                    {"host": host, "count": count} for host, count in sorted(self._page_requests.items())
                ],
            }
            if self._why_incomplete is not None:
                body["why_incomplete"] = self._why_incomplete
            return body

    def _tab(self, tab: str) -> int:
        return self._tabs.setdefault(tab, len(self._tabs) + 1)

    def _add(self, row: dict[str, Any], tab: str) -> None:
        if len(self._rows) >= self.cap:
            self._truncated = True
            return
        self._rows.append({**row, "tab": self._tab(tab), "at": self.clock()})


# The DevTools connection


_MESSAGE_BYTES = 32 * 1024 * 1024
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class FrameTooLarge(ConnectionError):
    pass


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()


class CdpSocket:
    """A WebSocket client for one DevTools connection, on the standard library: text messages, masked as a client
    must, pieces put back together, pings answered, and nothing over a fixed size read into memory."""

    def __init__(self, connection: socket.socket) -> None:
        self._connection = connection
        self._buffer = b""
        self._sending = threading.Lock()

    @classmethod
    def connect(cls, url: str, *, timeout: float = 10.0) -> CdpSocket:
        parts = urlsplit(url)
        if parts.scheme != "ws" or not parts.hostname or not parts.port:
            raise ConnectionError(f"not a local DevTools address: {url}")
        connection = socket.create_connection((parts.hostname, parts.port), timeout=timeout)
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            connection.sendall(
                (
                    f"GET {parts.path or '/'} HTTP/1.1\r\nHost: {parts.hostname}:{parts.port}\r\n"
                    f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                    "Sec-WebSocket-Version: 13\r\n\r\n"
                ).encode()
            )
            client = cls(connection)
            head = client._until(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            if " 101 " not in lines[0] + " ":
                raise ConnectionError(f"the browser refused the DevTools connection: {lines[0]}")
            answers = {
                name.strip().lower(): value.strip()
                for name, _, value in (line.partition(":") for line in lines[1:])
            }
            if answers.get("sec-websocket-accept") != accept_key(key):
                raise ConnectionError("the DevTools connection answered with the wrong key")
            connection.settimeout(None)
            return client
        except BaseException:
            connection.close()
            raise

    def send(self, message: dict[str, Any]) -> None:
        data = json.dumps(message).encode()
        mask = os.urandom(4)
        length = len(data)
        if length < 126:
            head = struct.pack("!BB", 0x81, 0x80 | length)
        elif length < 65536:
            head = struct.pack("!BBH", 0x81, 0x80 | 126, length)
        else:
            head = struct.pack("!BBQ", 0x81, 0x80 | 127, length)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        with self._sending:
            self._connection.sendall(head + mask + masked)

    def receive(self) -> dict[str, Any]:
        parts: list[bytes] = []
        size = 0
        while True:
            first, second = self._exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._exact(8))[0]
            if size + length > _MESSAGE_BYTES:
                raise FrameTooLarge(f"a DevTools message over {_MESSAGE_BYTES} bytes")
            mask = self._exact(4) if second & 0x80 else b""
            data = self._exact(length) if length else b""
            if mask:
                data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
            if opcode == 8:
                raise ConnectionError("the browser closed the DevTools connection")
            if opcode == 9:
                with self._sending:
                    self._connection.sendall(struct.pack("!BB", 0x8A, 0x80) + os.urandom(4))
                continue
            if opcode == 10:
                continue
            parts.append(data)
            size += length
            if first & 0x80:
                return json.loads(b"".join(parts))

    def close(self) -> None:
        try:
            self._connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._connection.close()

    def _until(self, marker: bytes) -> bytes:
        while marker not in self._buffer:
            if len(self._buffer) > 65536:
                raise ConnectionError("the DevTools handshake was too long")
            self._fill()
        head, _, self._buffer = self._buffer.partition(marker)
        return head

    def _exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            self._fill()
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def _fill(self) -> None:
        chunk = self._connection.recv(65536)
        if not chunk:
            raise ConnectionError("the DevTools connection closed")
        self._buffer += chunk


def _write_json(path: Path, value: Any) -> None:
    fresh = path.with_name(f"{path.name}.{uuid.uuid4().hex}.new")
    fresh.write_text(json.dumps(value), encoding="utf-8")
    os.replace(fresh, path)


class BrowserRecorder:
    """The kit's own DevTools client to a world's browser. It attaches to every tab before the agent can reach the
    browser, holds each new tab until the page's listener is in place, and lets it go whatever happens. It never
    fails the trial: a record it could not finish says so."""

    def __init__(
        self,
        sites: Iterable[str],
        record_path: Path,
        *,
        connect: Callable[..., CdpSocket] = CdpSocket.connect,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.sites = tuple(sites)
        self.record_path = record_path
        self._connect = connect
        self._clock = clock
        self.record = BrowserRecord(self.sites, clock=clock)
        self._socket: CdpSocket | None = None
        self._ids = 0
        self._ids_lock = threading.Lock()
        self._tabs: dict[str, tuple[str, str]] = {}  # session -> (tab, its main frame)
        self._leaving = False
        # A seal asked for at the trial's end: answered once the browser has answered a command sent after it, so
        # every event before the ask is in the record.
        self._seals: dict[int, str] = {}
        self._closed = threading.Event()
        threading.Thread(target=self._watch_seals, daemon=True).start()

    def attach(self, port: int) -> None:
        """Start a new record and attach to the browser on ``port``: a browser that knows no client yet."""
        self.detach()
        self.record = BrowserRecord(self.sites, clock=self._clock)
        self._tabs = {}
        self._leaving = False
        try:
            opener = urllib_request.build_opener(urllib_request.ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{port}/json/version", timeout=10) as answer:
                address = json.loads(answer.read())["webSocketDebuggerUrl"]
            self._socket = self._connect(address)
            self._command(
                "Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True}
            )
        except Exception as exc:  # noqa: BLE001 - the record says it is incomplete; the trial goes on
            self.record.broken(f"the recorder could not attach to the browser ({type(exc).__name__})")
            logger.warning("browser record: could not attach to the browser on port %s", port, exc_info=True)
            self._drop()
            self.write()
            return
        threading.Thread(target=self._read, args=(self._socket,), daemon=True).start()

    def detach(self) -> None:
        self._leaving = True
        self._drop()
        self.write()

    def close(self) -> None:
        self.detach()
        self._closed.set()

    def seal(self, nonce: str) -> None:
        """Write the record marked with ``nonce`` once every event the browser sent before now is in it."""
        try:
            self._command("Browser.getVersion", seal=nonce)
        except Exception:  # noqa: BLE001 - no connection: nothing more can arrive, the record is as it is
            with self._ids_lock:
                self._seals = {}
            self.write(sealed=nonce)

    def _watch_seals(self) -> None:
        asked = seal_request_path(self.record_path)
        while not self._closed.wait(0.05):
            try:
                nonce = asked.read_text(encoding="utf-8").strip()
                asked.unlink()
            except OSError:
                continue
            if nonce:
                self.seal(nonce)

    def write(self, **extra: Any) -> None:
        try:
            _write_json(self.record_path, {**self.record.body(), **extra})
        except OSError:
            logger.warning("browser record: could not be written to %s", self.record_path)

    def _drop(self) -> None:
        current, self._socket = self._socket, None
        if current is not None:
            current.close()

    def _command(
        self, method: str, params: dict[str, Any] | None = None, session: str | None = None, *, seal: str = ""
    ) -> None:
        current = self._socket
        if current is None:
            raise ConnectionError("the recorder is not attached")
        with self._ids_lock:
            self._ids += 1
            message: dict[str, Any] = {"id": self._ids, "method": method, "params": params or {}}
            if seal:
                self._seals[self._ids] = seal
        if session is not None:
            message["sessionId"] = session
        current.send(message)

    def _read(self, connection: CdpSocket) -> None:
        try:
            while True:
                self.handle(connection.receive())
        except Exception as exc:  # noqa: BLE001 - the end of the connection, wanted or not
            if not self._leaving and connection is self._socket:
                self.record.broken(f"the browser's DevTools connection ended ({type(exc).__name__})")
                logger.warning("browser record: the DevTools connection ended", exc_info=True)
        finally:
            if connection is self._socket or self._socket is None:
                self.write()

    def handle(self, message: dict[str, Any]) -> None:
        if "id" in message:
            with self._ids_lock:
                nonce = self._seals.pop(message["id"], None)
            if nonce is not None:
                self.write(sealed=nonce)
            return
        method = message.get("method")
        params = message.get("params") or {}
        session = message.get("sessionId")
        if method == "Target.attachedToTarget":
            self._attached(params, session)
        elif method == "Page.frameNavigated":
            frame = params.get("frame") or {}
            tab = self._tabs.get(session or "")
            if tab is not None and not frame.get("parentId") and frame.get("id") == tab[1]:
                self.record.navigated(tab[0], str(frame.get("url") or ""))
        elif method == "Network.requestWillBeSent":
            tab = self._tabs.get(session or "")
            if tab is not None:
                self.record.requested(
                    tab[0],
                    str((params.get("request") or {}).get("url") or ""),
                    document=params.get("type") == "Document",
                    main_frame=params.get("frameId") == tab[1],
                )
        elif method == "Runtime.bindingCalled" and params.get("name") == BINDING:
            tab = self._tabs.get(session or "")
            if tab is not None:
                self.record.acted(tab[0], str(params.get("payload") or ""))

    def _attached(self, params: dict[str, Any], parent: str | None) -> None:
        session = str(params.get("sessionId") or "")
        info = params.get("targetInfo") or {}
        kind = info.get("type")
        target = str(info.get("targetId") or "")
        try:
            if kind == "page":
                # A tab's main frame has the tab's own id.
                self._tabs[session] = (target, target)
            elif kind == "iframe" and parent in self._tabs:
                # A frame in its own process belongs to its tab; it is never the tab's main frame.
                self._tabs[session] = (self._tabs[parent][0], "")
            else:
                return
            for method, command in (
                ("Page.enable", {}),
                ("Network.enable", {}),
                # The binding's calls are Runtime events.
                ("Runtime.enable", {}),
                ("Runtime.addBinding", {"name": BINDING, "executionContextName": LISTENER_WORLD}),
                (
                    "Page.addScriptToEvaluateOnNewDocument",
                    {"source": LISTENER, "worldName": LISTENER_WORLD, "runImmediately": True},
                ),
                ("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True}),
            ):
                self._command(method, command, session)
        except Exception as exc:  # noqa: BLE001 - the tab is let go below whatever happened
            self.record.broken(f"the recorder could not watch a tab ({type(exc).__name__})")
        finally:
            if params.get("waitingForDebugger"):
                try:
                    self._command("Runtime.runIfWaitingForDebugger", None, session)
                except Exception:  # noqa: BLE001 - a lost connection lets every held tab go
                    pass


def seal_request_path(record_path: Path) -> Path:
    return record_path.with_name(record_path.name + ".seal")


def sealed_record(record_path: Path, *, timeout: float = 5.0) -> dict[str, Any] | None:
    """The world's record of its browser at the trial's end: asks the kit's client to seal it and waits, within
    ``timeout``, for the record that answers. One not sealed in time is read as it stands and says it is incomplete;
    None when there is no record at all."""
    nonce = uuid.uuid4().hex
    try:
        _write_text(seal_request_path(record_path), nonce)
    except OSError:
        logger.warning("browser record: the seal could not be asked for at %s", record_path)
    deadline = time.monotonic() + timeout
    body: Any = None
    while True:
        try:
            body = json.loads(record_path.read_text(encoding="utf-8")[: 64 * 1024 * 1024])
        except (OSError, ValueError):
            body = None
        if isinstance(body, dict) and body.get("sealed") == nonce:
            body.pop("sealed", None)
            return body
        if time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    if not isinstance(body, dict):
        return None
    body.pop("sealed", None)
    body["complete"] = False
    body.setdefault("why_incomplete", "the record was not sealed in time")
    return body


def _write_text(path: Path, text: str) -> None:
    fresh = path.with_name(f"{path.name}.{uuid.uuid4().hex}.new")
    fresh.write_text(text, encoding="utf-8")
    os.replace(fresh, path)
