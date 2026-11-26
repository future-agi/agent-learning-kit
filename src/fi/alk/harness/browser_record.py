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

The record holds against the page, not against a client holding the browser's DevTools address: such a client can
do whatever DevTools can, so the record says what the browser did, never that nothing else touched it.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import shutil
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

from .egress_proxy import entry_admits
from .kit_browser import browser_used
from .outbound import redact_outbound_text
from .world_door import MemoryLog

logger = logging.getLogger(__name__)

RECORD_ROWS = 500
# Screenshots for one trial, in number and in bytes; the end's is always kept.
SHOTS = 20
SHOT_BYTES = 8 * 1024 * 1024
_SHOT = {"format": "jpeg", "quality": 60}
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
  // Marked as the page is built and as it changes, so a field the page turns from password to text stays one.
  const mark = (node) => {
    if (!node || node.nodeType !== 1) return;
    isSecret(node);
    if (node.querySelectorAll) node.querySelectorAll('input').forEach(isSecret);
  };
  new MutationObserver((changes) => {
    for (const change of changes) {
      if (change.type === 'attributes') {
        const was = String(change.oldValue || '').toLowerCase();
        if (was === 'password' || (change.attributeName === 'autocomplete' && was.includes('password'))) {
          secret.add(change.target);
        }
        isSecret(change.target);
      } else {
        change.addedNodes.forEach(mark);
      }
    }
  }).observe(document, {
    subtree: true, childList: true, attributes: true, attributeOldValue: true,
    attributeFilter: ['type', 'autocomplete'],
  });
  mark(document.documentElement);
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
  // Only what the browser itself was made to do: a page's own click() or made-up event is not the agent's.
  addEventListener('click', (event) => {
    if (!event.isTrusted) return;
    const hit = event.target && event.target.closest
      ? event.target.closest('a,button,input,select,textarea,label,[role]') || event.target : event.target;
    say({kind: 'click', url: location.href, target: describe(hit)});
  }, true);
  addEventListener('input', (event) => {
    if (!event.isTrusted) return;
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
        self._shots: list[dict[str, Any]] = []
        self._shots_capped = False

    def keep_shot(self, when: str, size: int, *, shots: int, shot_bytes: int) -> tuple[bool, list[str]]:
        """Whether a screenshot of ``size`` bytes is kept, and the files of earlier ones it displaces. A page's is
        kept while one place stays free for the end's; the end's is always kept, displacing the latest pages' to
        stay within the bytes."""
        with self._lock:
            used = sum(one["bytes"] for one in self._shots)
            if when != "end":
                if len(self._shots) + 1 < shots and used + size <= shot_bytes:
                    return True, []
                self._shots_capped = True
                return False, []
            dropped: list[str] = []
            while self._shots and (len(self._shots) >= shots or used + size > shot_bytes):
                gone = self._shots.pop()
                used -= gone["bytes"]
                dropped.append(gone["file"])
                self._shots_capped = True
            return True, dropped

    def photographed(
        self, tab: str, *, when: str, url: str, file: str, size: int, artifact_id: str | None = None
    ) -> None:
        """A screenshot kept. ``artifact_id`` is the id the platform stores it under, so a row and its stored
        screenshot can be matched."""
        with self._lock:
            self._shots.append(
                {"file": file, "when": when, "url": _clean(url, 2048), "tab": self._tab(tab), "at": self.clock(),
                 "bytes": size, **({"artifact_id": artifact_id} if artifact_id else {})}
            )

    def allowed(self, url: str) -> bool:
        # The proxy's rule, each site read as the sandbox firewall reads it; loopback is never a site.
        host = _host(url)
        return any(entry_admits(site, host) for site in self.sites)

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
                "screenshots": [dict(one) for one in self._shots],
                "screenshots_capped": self._shots_capped,
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
        shots: int = SHOTS,
        shot_bytes: int = SHOT_BYTES,
        shot_seconds: float = 3.0,
    ) -> None:
        self._shot_seconds = shot_seconds
        self.sites = tuple(sites)
        self.record_path = record_path
        self.shots_dir = shots_dir_for(record_path)
        self._shot_limits = {"shots": shots, "shot_bytes": shot_bytes}
        self._shot_count = 0
        self._urls: dict[str, str] = {}  # session -> its tab's page now
        self._last_session: str | None = None
        self._connect = connect
        self._clock = clock
        self.record = BrowserRecord(self.sites, clock=clock)
        self._socket: CdpSocket | None = None
        self._ids = 0
        self._ids_lock = threading.Lock()
        self._tabs: dict[str, tuple[str, str]] = {}  # session -> (tab, its main frame)
        self._leaving = False
        # What to do with the browser's answer to a command: a screenshot's picture, or a seal asked for at the
        # trial's end, written once the browser has answered a command sent after it, so every event before the ask
        # is in the record.
        self._waiting: dict[int, Callable[[dict[str, Any]], None]] = {}
        self._closed = threading.Event()
        threading.Thread(target=self._watch_seals, daemon=True).start()

    def attach(self, port: int) -> None:
        """Start a new record and attach to the browser on ``port``: a browser that knows no client yet."""
        self.detach()
        self.record = BrowserRecord(self.sites, clock=self._clock)
        self._tabs = {}
        self._urls = {}
        self._last_session = None
        self._leaving = False
        try:
            if self.shots_dir.exists():
                shutil.rmtree(self.shots_dir)
            self.shots_dir.mkdir(parents=True)
        except OSError:
            logger.warning("browser record: the screenshots folder %s could not be made new", self.shots_dir)
        # On disk at once: a trial that reads it before any seal never sees the last trial's rows.
        self.write()
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

        def sealed(_answer: dict[str, Any]) -> None:
            self.write(sealed=nonce)

        once = threading.Lock()

        def then_seal(_answer: dict[str, Any] | None = None) -> None:
            # Once: after the end screenshot, or when the browser has not answered for it in time.
            if not once.acquire(blocking=False):
                return
            try:
                self._command("Browser.getVersion", then=sealed)
            except Exception:  # noqa: BLE001 - no connection: nothing more can arrive, the record is as it is
                self.write(sealed=nonce)

        session = self._last_session
        if session is None:
            then_seal()
            return
        timer = threading.Timer(self._shot_seconds, then_seal)
        timer.daemon = True
        timer.start()
        try:
            self._command(
                "Page.captureScreenshot", dict(_SHOT), session,
                then=lambda answer: (self._photographed(answer, "end", session, self._urls.get(session, "")), then_seal()),
            )
        except Exception:  # noqa: BLE001 - no end screenshot; the seal still answers
            then_seal()

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
        self,
        method: str,
        params: dict[str, Any] | None = None,
        session: str | None = None,
        *,
        then: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        current = self._socket
        if current is None:
            raise ConnectionError("the recorder is not attached")
        with self._ids_lock:
            self._ids += 1
            message: dict[str, Any] = {"id": self._ids, "method": method, "params": params or {}}
            if then is not None:
                self._waiting[self._ids] = then
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
                then = self._waiting.pop(message["id"], None)
            if then is not None:
                try:
                    then(message)
                except Exception:  # noqa: BLE001 - one answer handled badly never ends the record
                    logger.warning("browser record: an answer from the browser was not handled", exc_info=True)
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
                url = str(frame.get("url") or "")
                self._urls[session or ""] = url
                self._last_session = session
                self.record.navigated(tab[0], url)
        elif method == "Page.loadEventFired":
            tab = self._tabs.get(session or "")
            if tab is not None and tab[1] and self._urls.get(session or "", "") not in ("", "about:blank"):
                url = self._urls[session or ""]
                self._command(
                    "Page.captureScreenshot", dict(_SHOT), session,
                    then=lambda answer: self._photographed(answer, "load", session, url),
                )
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

    def _photographed(self, answer: dict[str, Any], when: str, session: str | None, url: str) -> None:
        """Keep a screenshot the browser took, within the trial's cap. One it could not take is skipped."""
        try:
            data = base64.b64decode(str((answer.get("result") or {}).get("data") or ""), validate=True)
        except ValueError:
            return
        if not data:
            return
        keep, dropped = self.record.keep_shot(when, len(data), **self._shot_limits)
        for name in dropped:
            with contextlib.suppress(OSError):
                (self.shots_dir / name).unlink()
        if not keep:
            return
        self._shot_count += 1
        name = f"shot-{self._shot_count:03d}-{when}.jpg"
        try:
            (self.shots_dir / name).write_bytes(data)
        except OSError:
            logger.warning("browser record: a screenshot could not be written to %s", self.shots_dir)
            return
        tab = self._tabs.get(session or "")
        self.record.photographed(
            tab[0] if tab else "", when=when, url=url, file=name, size=len(data),
            artifact_id="sha256:" + hashlib.sha256(data).hexdigest(),
        )

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


def split_blocked(blocked: list[dict[str, Any]], record: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The trial's blocked list with the browser's refusals split in two: each page the agent tried to open, whole,
    and the requests pages made, counted for each host. The proxy is the record of what was refused, so a host it
    refused that the record never saw stays as the proxy counted it; with no record its counts stand."""
    if not isinstance(record, dict):
        return list(blocked)
    navigations: dict[str, list[dict[str, Any]]] = {}
    for one in record.get("blocked_navigations") or []:
        if isinstance(one, dict) and isinstance(one.get("url"), str):
            navigations.setdefault(_host(one["url"]), []).append(one)
    requests = {
        str(one["host"]): int(one.get("count") or 0)
        for one in record.get("blocked_page_requests") or []
        if isinstance(one, dict) and one.get("host")
    }
    kept = [one for one in blocked if one.get("via") != "browser"]
    browser_hosts = [str(one.get("host") or "") for one in blocked if one.get("via") == "browser"]
    seen: set[str] = set()
    for host in [*browser_hosts, *navigations, *requests]:
        if host in seen:
            continue
        seen.add(host)
        if host not in navigations and host not in requests:
            kept.extend(one for one in blocked if one.get("via") == "browser" and one.get("host") == host)
            continue
        for one in navigations.get(host, []):
            kept.append({"host": host, "via": "browser", "what": "navigation", "url": one["url"], "at": one.get("at")})
        if host in requests:
            kept.append({"host": host, "via": "browser", "what": "page_request", "count": requests[host]})
    return kept


def sealed_browser_blocked(state_changes: Any, record: dict[str, Any] | None) -> Any:
    """The trial's sealed changes with the agent's browser refusals split as the record tells them apart, counted
    for each host and kind: the sealed record carries counts, and every address stays in the world_state file. With
    no record the proxy's counts stand. Never changes its input."""
    if not isinstance(state_changes, list) or not isinstance(record, dict):
        return state_changes
    sealed: list[Any] = []
    for entry in state_changes:
        if not (isinstance(entry, dict) and entry.get("who") == "agent" and isinstance(entry.get("blocked"), list)):
            sealed.append(entry)
            continue
        counted: list[dict[str, Any]] = []
        for one in split_blocked(entry["blocked"], record):
            if one.get("what") != "navigation":
                counted.append(one)
                continue
            same = next(
                (had for had in counted if had.get("what") == "navigation" and had.get("host") == one.get("host")),
                None,
            )
            if same is None:
                counted.append({"host": one["host"], "via": "browser", "what": "navigation", "count": 1})
            else:
                same["count"] += 1
        sealed.append({**entry, "blocked": counted})
    return sealed


SCREENSHOTS_CAPPED = "screenshots_capped"
SCREENSHOTS_NOT_KEPT = "screenshots_not_kept"


def browser_evidence(runtime: Any, *, timeout: float = 5.0) -> dict[str, Any] | None:
    """What the trial keeps of its world's browser: None for a world without one; ``not_started`` or ``not_used``
    said as such, never an empty record that reads as clean; otherwise the record, sealed at the trial's end."""
    browser = (getattr(runtime, "metadata", None) or {}).get("browser")
    if not isinstance(browser, dict):
        return None
    if browser.get("state") != "started":
        return {"state": str(browser.get("state") or "unknown")}
    if not browser_used(browser.get("state_path")):
        return {"state": "not_used"}
    record = sealed_record(Path(str(browser.get("record_path") or "")), timeout=timeout) if browser.get(
        "record_path"
    ) else None
    if record is None:
        return {"state": "used", "complete": False, "why_incomplete": "the browser record could not be read"}
    return {"state": "used", **record}


async def upload_screenshots(
    adapter: Any, scenario_key: str, evidence: dict[str, Any] | None, shots_dir: Path
) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    """Upload the trial's screenshots, after its transcript and end state so they never crowd those out. A
    screenshot the platform did not keep never fails the trial: it is said."""
    from .outbound import ArtifactKind

    notices: list[dict[str, str]] = []
    if not isinstance(evidence, dict):
        return (), notices
    if evidence.get("screenshots_capped"):
        notices.append(
            {
                "code": SCREENSHOTS_CAPPED,
                "message": "The browser's screenshots reached their limit for one trial; the last one is kept.",
            }
        )
    ids: list[str] = []
    lost = 0
    for one in evidence.get("screenshots") or []:
        name = str((one or {}).get("file") or "")
        path = shots_dir / name
        if not name or Path(name).name != name or not path.is_file() or path.is_symlink():
            continue
        try:
            data = path.read_bytes()[:SHOT_BYTES]
            stored = await adapter.upload_artifact(data, kind=ArtifactKind.SCREENSHOT, scenario_key=scenario_key)
        except Exception:  # noqa: BLE001 - a lost screenshot never fails the trial
            stored = None
        if stored:
            ids.append(stored)
        else:
            lost += 1
    if lost:
        notices.append(not_kept_notice(lost))
    return tuple(ids), notices


def not_kept_notice(lost: int) -> dict[str, str]:
    return {
        "code": SCREENSHOTS_NOT_KEPT,
        "message": f"The platform did not keep {lost} of the browser's screenshots; the trial is graded without them.",
    }


def shots_dir_for(record_path: Path) -> Path:
    stem = record_path.name.removesuffix(".json").removesuffix("-record")
    return record_path.with_name(f"{stem}-shots")


def browser_shots_dir(runtime: Any) -> Path:
    browser = (getattr(runtime, "metadata", None) or {}).get("browser")
    record_path = browser.get("record_path") if isinstance(browser, dict) else None
    return shots_dir_for(Path(str(record_path))) if record_path else Path("/nonexistent-browser-shots")


BROWSER_STATE_SCHEMA = "futureagi.browser-world-state.v1"
NO_WORLD_DOOR = "no_world_door"
WORLD_END_UNREADABLE = "world_end_unreadable"


class BlockedFileLog(MemoryLog):
    """The browser's refused attempts in a world with no door: a kit-owned file the agent's user cannot write, cut
    at each start of the browser, capped, and cleaned as other rows are."""

    def __init__(self, path: Path, *, cap: int = RECORD_ROWS) -> None:
        super().__init__()
        self.path = path
        self.cap = cap
        self._written = 0
        self._file_lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.reset()

    def reset(self) -> None:
        with self._file_lock:
            self._written = 0
            try:
                _write_text(self.path, "")
            except OSError:
                logger.warning("browser record: the blocked log %s could not be cut", self.path)
        with self._lock:
            self._rows.clear()

    def write(self, row: dict[str, Any]) -> None:
        super().write(row)
        arguments = row.get("arguments") if isinstance(row.get("arguments"), dict) else {}
        kept = {
            **{key: row[key] for key in ("actor_id", "outcome", "refusal_code", "method") if key in row},
            "arguments": {key: _clean(arguments[key], 253) for key in ("host", "method", "via") if arguments.get(key)},
            "at": time.time(),
        }
        with self._file_lock:
            if self._written > self.cap:
                return
            self._written += 1
            line = {"truncated": True} if self._written > self.cap else kept
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(line, default=str) + "\n")
            except OSError:
                logger.warning("browser record: the blocked log %s could not be written", self.path)


def blocked_from_file(path: Path) -> list[dict[str, Any]] | None:
    """The browser's refusals from its kit-owned file, grouped as a trial's blocked list; None when the file cannot be
    read, never an empty list in its place."""
    from .call_runner import trial_state_changes

    try:
        lines = path.read_text(encoding="utf-8")[: 16 * 1024 * 1024].splitlines()
        rows = [json.loads(line) for line in lines if line.strip()]
    except (OSError, ValueError):
        return None
    if not all(isinstance(row, dict) for row in rows):
        return None
    truncated = any(row.get("truncated") is True for row in rows)
    changes = trial_state_changes([row for row in rows if "truncated" not in row], {})
    blocked = next((list(entry.get("blocked") or []) for entry in changes if entry.get("who") == "agent"), [])
    return blocked + ([{"truncated": True}] if truncated else [])


def browser_counts(evidence: dict[str, Any]) -> dict[str, Any]:
    """What the sealed record says of the browser: counts, never rows. The rows are in the trial's world_state file."""
    counts: dict[str, Any] = {"state": evidence.get("state")}
    if "complete" in evidence:
        counts["complete"] = evidence["complete"]
    for key in ("rows", "blocked_navigations", "screenshots"):
        if key in evidence:
            counts[key] = len(evidence.get(key) or [])
    if "blocked_page_requests" in evidence:
        counts["blocked_page_requests"] = sum(
            int(one.get("count") or 0) for one in evidence.get("blocked_page_requests") or [] if isinstance(one, dict)
        )
    if evidence.get("truncated"):
        counts["truncated"] = True
    return counts


def browser_file_body(evidence: dict[str, Any], blocked: list[dict[str, Any]] | None) -> dict[str, Any]:
    """The browser's part of the trial's world_state file: its record, and the trial's blocked list split by it, or
    that the list could not be read."""
    body: dict[str, Any] = {"browser": dict(evidence)}
    if blocked is None:
        body["blocked_unreadable"] = True
    else:
        body["blocked"] = split_blocked(blocked, evidence)
    return body


async def upload_browser_state(
    adapter: Any,
    scenario_key: str,
    evidence: dict[str, Any],
    blocked: list[dict[str, Any]] | None,
    *,
    why: str = NO_WORLD_DOOR,
    action_log: list[dict[str, Any]] | None = None,
) -> str | None:
    """The world_state file for a world with a browser whose records were not kept. With no door it says the world's
    records were not recorded, and holds no tables or action log in their place. Where a door stood but the world's
    end could not be read, it holds the door's ``action_log`` and says only the tables are missing. A lost upload
    never fails the trial."""
    from .outbound import ArtifactKind

    held = (
        {"recorded": False, "not_held": ["tables", "action_log"]}
        if action_log is None
        else {"not_held": ["tables"], "action_log": [dict(row) for row in action_log]}
    )
    body = {"schema": BROWSER_STATE_SCHEMA, "why": why, **held, **browser_file_body(evidence, blocked)}
    try:
        return await adapter.upload_artifact(
            json.dumps(body, sort_keys=True, default=str).encode("utf-8"),
            kind=ArtifactKind.WORLD_STATE,
            scenario_key=scenario_key,
        )
    except Exception:  # noqa: BLE001 - a lost record never fails the trial
        logger.warning("browser record: the trial's world_state file was not stored")
        return None


def trial_blocked(log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The agent's blocked list from a trial's action log, as the trial's world record groups it."""
    from .call_runner import trial_state_changes

    return next(
        (list(entry.get("blocked") or []) for entry in trial_state_changes(log, {}) if entry.get("who") == "agent"), []
    )


def browser_blocked_from_runtime(runtime: Any) -> list[dict[str, Any]] | None:
    """The browser's refusals from the kit-owned file its metadata names, used where no door's database keeps them;
    None when there is none to read."""
    browser = (getattr(runtime, "metadata", None) or {}).get("browser")
    path = browser.get("blocked_log_path") if isinstance(browser, dict) else None
    return blocked_from_file(Path(str(path))) if path else None


def browser_blocked(runtime: Any, door_log: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """The browser's refusals from the one place the world sent them: the kit-owned file when the browser was given
    one, else the door's log. None when neither can be read."""
    browser = (getattr(runtime, "metadata", None) or {}).get("browser")
    if isinstance(browser, dict) and browser.get("blocked_log_path"):
        return browser_blocked_from_runtime(runtime)
    return None if door_log is None else trial_blocked(door_log)
