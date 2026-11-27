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
import secrets
import shutil
import stat
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from urllib import request as urllib_request
from urllib.parse import urldefrag, urlsplit

from .browser_blocked_log import blocked_from_file, read_kit_file, write_text
from .devtools_socket import CdpSocket
from .egress_proxy import entry_admits
from .kit_browser import browser_used
from .outbound import redact_outbound_text

logger = logging.getLogger(__name__)

RECORD_ROWS = 500
# Screenshots for one trial, in number and in bytes; one place is kept free for the end's.
SHOTS = 20
SHOT_BYTES = 8 * 1024 * 1024
_SHOT = {"format": "jpeg", "quality": 60}
_TEXT = 200
_SAME_ATTEMPT_SECONDS = 2.0
_ACTIONS = frozenset({"click", "typing"})
_TARGET_FIELDS = ("tag", "role", "label", "text")

# Autofill names of fields whose text is never kept (https://html.spec.whatwg.org/#autofill-field).
SECRET_AUTOFILL = frozenset(
    {
        "current-password", "new-password", "one-time-code",
        "cc-number", "cc-csc", "cc-exp", "cc-exp-month", "cc-exp-year",
    }
)

# The page's listener runs in a world of its own and only that world can call the binding. Both names are random and
# new at each attach, so a world the agent makes cannot share them, and neither says what it is.

# A field is a secret field for the rest of the page's life once it has been one: a password, a field whose autofill
# names a password, a card's number, code or expiry, or a one-time code, a field the page shows as dots itself, or a
# one-character box beside a field named as a code's. Nothing here reads a field's value except to report typing into
# a field that is not one.
LISTENER = r"""
(() => {
  // The binding is taken into this closure and gone from the world's globals before any page script runs, so a
  // client that finds this world through DevTools finds nothing in it to call. Gone, it also says this ran.
  const send = globalThis.%(binding)s;
  if (typeof send !== 'function') return;
  delete globalThis.%(binding)s;
  const say = (row) => { try { send(JSON.stringify(row)); } catch (e) {} };
  const secret = new WeakSet();
  const secretFill = new Set(%(fill)s);
  const secretAuto = (value) => {
    const auto = String(value || '').toLowerCase();
    return auto.includes('password') || auto.split(/\s+/).some((token) => secretFill.has(token));
  };
  // A field the page shows as dots itself, though not a password field.
  const masked = (el) => {
    try {
      const style = getComputedStyle(el);
      const shown = style.getPropertyValue('-webkit-text-security') || style.webkitTextSecurity || 'none';
      return shown !== 'none';
    } catch (e) { return false; }
  };
  // One box of a code split over boxes. A run of 4 to 8 text boxes under one element (up to 5 levels up) is a code
  // when each box takes one character, or when one box is named as a code's and each takes one character or digits.
  const textBox = (one) => /^input$/i.test(one.tagName)
    && ['', 'text', 'tel', 'number', 'password'].includes(String(one.getAttribute('type') || '').toLowerCase());
  const oneCharacter = (one) => one.maxLength === 1;
  const codeRun = (el) => {
    if (!textBox(el)) return false;
    let scope = el.parentElement;
    for (let up = 0; scope && up < 5; up += 1, scope = scope.parentElement) {
      const boxes = Array.from(scope.querySelectorAll ? scope.querySelectorAll('input') : []).filter(textBox);
      if (boxes.length > 8) return false;
      if (boxes.length < 4) continue;
      if (boxes.every(oneCharacter)) return true;
      const named = boxes.some((one) => secretAuto(one.getAttribute('autocomplete')));
      return named && boxes.every((one) => oneCharacter(one) || one.inputMode === 'numeric');
    }
    return false;
  };
  // Or a one-character field near a field named as a code's.
  const codeBox = (el) => {
    if (codeRun(el)) return true;
    if (!/^input$/i.test(el.tagName) || el.maxLength !== 1) return false;
    let scope = el.form || el.parentElement;
    for (let up = 0; scope && up < 3; up += 1, scope = scope.parentElement) {
      const others = scope.querySelectorAll ? scope.querySelectorAll('input[autocomplete]') : [];
      for (const other of others) {
        if (other !== el && secretAuto(other.getAttribute('autocomplete'))) return true;
      }
      if (el.form) break;
    }
    return false;
  };
  const isSecret = (el) => {
    if (!el || el.nodeType !== 1) return false;
    const type = String(el.getAttribute('type') || '').toLowerCase();
    if (type === 'password' || secretAuto(el.getAttribute('autocomplete')) || masked(el) || codeBox(el)) {
      secret.add(el);
    }
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
        if (change.attributeName === 'type' ? was === 'password' : secretAuto(was)) {
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
  // Typing the browser was given (keys, a paste, a drop, inserted text) comes with a trusted beforeinput first, in
  // the same task; a page's own editing command fires input alone.
  let given = null;
  addEventListener('beforeinput', (event) => {
    if (!event.isTrusted) return;
    given = event.target;
    setTimeout(() => { given = null; }, 0);
  }, true);
  addEventListener('input', (event) => {
    if (!event.isTrusted || event.target !== given) return;
    given = null;
    const el = event.target;
    const hidden = isSecret(el);
    const typed = hidden || !el || el.isContentEditable ? null : String(el.value || '').slice(0, %(text)d);
    say({kind: 'typing', url: location.href, target: describe(el), secret: hidden, text: typed});
  }, true);
})();
"""


def listener_for(binding: str) -> str:
    """The page's listener, sending its rows through ``binding``."""
    return LISTENER % {"binding": binding, "text": _TEXT, "fill": json.dumps(sorted(SECRET_AUTOFILL))}


def _clean(text: Any, limit: int = _TEXT, secret_values: tuple[str, ...] = ()) -> str:
    return redact_outbound_text(str(text), secret_values)[:limit]


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _web(url: str) -> bool:
    return url.lower().startswith(("http://", "https://"))


# Navigations a page asks for on its own with no script: a meta refresh or a Refresh header (Page.frameRequestedNavigation).
_PAGE_REFRESHES = frozenset({"metaTagRefresh", "httpHeaderRefresh"})


class BrowserRecord:
    """One trial's record of the browser. Safe to feed from the recorder's thread and read from another."""

    def __init__(
        self,
        sites: Iterable[str],
        *,
        cap: int = RECORD_ROWS,
        clock: Callable[[], float] = time.time,
        secret_values: Iterable[str] = (),
    ) -> None:
        self.sites = tuple(sites)
        self.cap = cap
        # The job's declared secrets: masked in everything the record holds, wherever a page let one be typed.
        self._secrets = tuple(one for one in secret_values if isinstance(one, str) and one)
        self.clock = clock
        self._lock = threading.Lock()
        self._rows: list[dict[str, Any]] = []
        self._navigations: list[dict[str, Any]] = []
        self._page_requests: dict[str, int] = {}
        self._tabs: dict[str, int] = {}
        self._last_refused: dict[tuple[str, str, str], float] = {}
        # The hosts each tab was refused a page of: the browser then asks them for the icon of its refusal page.
        self._refused_hosts: dict[str, set[str]] = {}
        self._truncated = False
        self._why_incomplete: str | None = None
        self._shots: list[dict[str, Any]] = []
        self._shots_capped = False
        self._shots_sealed = False

    def keep_shot(self, when: str, size: int, *, shots: int, shot_bytes: int) -> tuple[bool, list[str]]:
        """Whether a screenshot of ``size`` bytes is kept, and the files of earlier ones it displaces. A page's is
        kept while one place stays free for the end's. The end's is kept, displacing the latest pages' to stay within
        the count and the bytes, unless it is larger than all of them together may be or the record is already sealed."""
        with self._lock:
            if self._shots_sealed:
                # The sealed record names its screenshots: none of them may go, and none is added after.
                return False, []
            used = sum(one["bytes"] for one in self._shots)
            if size > shot_bytes:
                # Larger than every screenshot together may be: never kept, never cut.
                self._shots_capped = True
                return False, []
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

    def _kept(self, text: Any, limit: int = _TEXT) -> str:
        return _clean(text, limit, self._secrets)

    def shots_kept(self) -> int:
        with self._lock:
            return len(self._shots)

    def cap_shots(self) -> None:
        """A page not photographed because the screenshots are full."""
        with self._lock:
            self._shots_capped = True

    def seal_shots(self) -> None:
        """The record is sealed: the screenshots it names are the trial's, and none is kept or displaced after."""
        with self._lock:
            self._shots_sealed = True

    def photographed(
        self, tab: str, *, when: str, url: str, file: str, size: int, artifact_id: str | None = None
    ) -> None:
        """A screenshot kept. ``artifact_id`` is the id the platform stores it under, so a row and its stored
        screenshot can be matched."""
        with self._lock:
            self._shots.append(
                {"file": file, "when": when, "url": self._kept(url, 2048), "tab": self._tab(tab), "at": self.clock(),
                 "bytes": size, **({"artifact_id": artifact_id} if artifact_id else {})}
            )

    def allowed(self, url: str) -> bool:
        # The proxy's rule, each site read as the sandbox firewall reads it; loopback is never a site.
        host = _host(url)
        return any(entry_admits(site, host) for site in self.sites)

    def opened(self, url: str) -> bool:
        """Whether ``url`` is a page the agent opened, by its address: not a blank page, not the browser's own error
        page (the pinned Chromium shows it for any page it could not load or the proxy refused), and not an address
        off the sites (where an older build shows the proxy's refusal over http). A refusal is kept in its own
        list."""
        lowered = url.lower()
        if not url or lowered.startswith(("about:blank", "chrome-error:")):
            return False
        return not _web(url) or self.allowed(url)

    def navigated(self, tab: str, url: str) -> None:
        """A page the browser opened; see ``opened``."""
        if not self.opened(url):
            return
        with self._lock:
            self._add({"kind": "navigation", "url": self._kept(url, 2048)}, tab)

    def requested(
        self, tab: str, url: str, *, document: bool, main_frame: bool, by_page: bool = False
    ) -> None:
        """A request the browser made; kept only when its host is off the list, the rule its proxy keeps. A page
        that moves itself off the list (a redirect, or its script) made that request, not the agent: it is counted
        with the page's requests. The icon the browser asks a refused host for, to show beside its refusal, is
        neither's."""
        if not _web(url) or self.allowed(url):
            return
        host = _host(url)
        with self._lock:
            if not document and urlsplit(url).path == "/favicon.ico" and host in self._refused_hosts.get(tab, ()):
                return
            if document and main_frame:
                # Its refusal page is shown in the tab, whoever asked for it.
                self._refused_hosts.setdefault(tab, set()).add(host)
            if not (document and main_frame) or by_page:
                if host in self._page_requests or len(self._page_requests) < self.cap:
                    self._page_requests[host] = self._page_requests.get(host, 0) + 1
                else:
                    self._truncated = True
                return
            # Chromium tries a plain address over https first and back again within a moment: one attempt, not
            # three. The moment runs from the attempt kept, so a page retried for longer is kept once each moment.
            parts = urlsplit(url)
            place = (tab, host, parts.path + ("?" + parts.query if parts.query else ""))
            now = self.clock()
            last = self._last_refused.get(place)
            if last is not None and now - last < _SAME_ATTEMPT_SECONDS:
                return
            if len(self._navigations) >= self.cap:
                self._truncated = True
                return
            self._last_refused[place] = now
            self._navigations.append({"url": self._kept(url, 2048), "at": now, "tab": self._tab(tab)})

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
            "url": self._kept(raw["url"], 2048),
            "target": {key: self._kept(target[key], 80) for key in _TARGET_FIELDS if target.get(key)},
        }
        if raw["kind"] == "typing":
            secret = raw.get("secret") is True
            row["secret"] = secret
            text = raw.get("text")
            row["text"] = None if secret or not isinstance(text, str) else self._kept(text)
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
        secret_values: Iterable[str] = (),
    ) -> None:
        self._shot_seconds = shot_seconds
        self.sites = tuple(sites)
        self._secrets = tuple(secret_values)
        self._name()
        self.record_path = record_path
        self.shots_dir = shots_dir_for(record_path)
        self._shot_limits = {"shots": shots, "shot_bytes": shot_bytes}
        self._shot_count = 0
        # Pages' screenshots asked of the browser and not yet answered, each with its tab's session: each holds a
        # place until it is answered, could not be asked for, or its tab closes.
        self._shots_pending: dict[object, str] = {}
        self._urls: dict[str, str] = {}  # session -> its tab's page now
        # session -> addresses its page asked to refresh to (a meta tag or a Refresh header), not yet requested
        self._refreshes: dict[str, set[str]] = {}
        # Tabs whose page now the agent has clicked or typed on: their script's moves are the agent's (see requested).
        self._acted_on: set[str] = set()
        self._connect = connect
        self._clock = clock
        self.record = BrowserRecord(self.sites, clock=clock, secret_values=self._secrets)
        self._socket: CdpSocket | None = None
        self._socket_lock = threading.Lock()
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

    def _name(self) -> None:
        """New names for the listener's world and binding: random, never fixed ones a world the agent makes could
        take, and saying nothing of what they are."""
        self.listener_world = secrets.token_hex(16)
        self.binding = f"b{secrets.token_hex(16)}"
        self.listener = listener_for(self.binding)

    def attach(self, port: int) -> None:
        """Start a new record and attach to the browser on ``port``: a browser that knows no client yet."""
        self.detach()
        self._name()
        self.record = BrowserRecord(self.sites, clock=self._clock, secret_values=self._secrets)
        self._tabs = {}
        self._urls = {}
        self._refreshes = {}
        self._acted_on = set()
        self._leaving = False
        self._shots_pending = {}
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
            connected = self._connect(address)
            with self._socket_lock:
                self._socket = connected
            self._command(
                "Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True}
            )
        except Exception as exc:  # noqa: BLE001 - the record says it is incomplete; the trial goes on
            self.record.broken(f"the recorder could not attach to the browser ({type(exc).__name__})")
            logger.warning("browser record: could not attach to the browser on port %s", port, exc_info=True)
            self._drop()
            self.write()
            return
        threading.Thread(target=self._read, args=(connected,), daemon=True).start()

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
            self.record.seal_shots()
            self.write(sealed=nonce)

        once = threading.Lock()

        def then_seal(_answer: dict[str, Any] | None = None) -> None:
            # Once: after the end screenshot, or when the browser has not answered for it in time.
            if not once.acquire(blocking=False):
                return
            try:
                self._command("Browser.getVersion", then=sealed)
            except Exception:  # noqa: BLE001 - no connection: nothing more can arrive, the record is as it is
                self.record.seal_shots()
                self.write(sealed=nonce)

        def photograph_end(_answer: dict[str, Any]) -> None:
            # On the reader's thread, once every event the browser sent before the ask is handled: the tab is the one
            # whose page was opened last, among tabs still open showing a page the agent opened.
            session = self._end_session()
            if session is None:
                then_seal()
                return
            url = self._urls[session]

            def answered(answer: dict[str, Any]) -> None:
                # A tab that moved on while it was photographed may show either page: not kept.
                if self._urls.get(session) == url:
                    self._photographed(answer, "end", session, url)
                then_seal()

            try:
                self._command("Page.captureScreenshot", dict(_SHOT), session, then=answered)
            except Exception:  # noqa: BLE001 - no end screenshot; the seal still answers
                then_seal()

        timer = threading.Timer(self._shot_seconds, then_seal)
        timer.daemon = True
        timer.start()
        try:
            self._command("Browser.getVersion", then=photograph_end)
        except Exception:  # noqa: BLE001 - no connection: no end screenshot; the seal still answers
            then_seal()

    def _end_session(self) -> str | None:
        return next(
            (
                session
                for session in reversed(list(self._urls))
                if self._tabs.get(session, ("", ""))[1] and self.record.opened(self._urls[session])
            ),
            None,
        )

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

    def _drop(self, only: CdpSocket | None = None) -> bool:
        """Close the current connection, or only ``only`` if it is still the current one; whether one was closed."""
        with self._socket_lock:
            current = self._socket
            if current is None or (only is not None and current is not only):
                return False
            self._socket = None
        current.close()
        return True

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
        try:
            current.send(message)
        except BaseException:
            # Not sent: no answer will come for it.
            with self._ids_lock:
                self._waiting.pop(message["id"], None)
            raise

    def _read(self, connection: CdpSocket) -> None:
        try:
            while True:
                message = connection.receive()
                try:
                    self.handle(message)
                except Exception as exc:  # noqa: BLE001 - one event handled badly never ends the record
                    self.record.broken(f"an event from the browser was not handled ({type(exc).__name__})")
                    logger.warning("browser record: an event from the browser was not handled", exc_info=True)
        except Exception as exc:  # noqa: BLE001 - the end of the connection, wanted or not
            if not self._leaving and connection is self._socket:
                self.record.broken(f"the browser's DevTools connection ended ({type(exc).__name__})")
                logger.warning("browser record: the DevTools connection ended", exc_info=True)
        finally:
            # A connection no longer read is closed: the browser holds each new tab for the recorder while it is
            # connected, and lets them go when it is not.
            if not self._drop(connection):
                with contextlib.suppress(Exception):
                    connection.close()
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
        elif method == "Target.detachedFromTarget":
            # A closed tab is forgotten; the end's screenshot is of a tab still open.
            gone = str(params.get("sessionId") or "")
            self._tabs.pop(gone, None)
            self._urls.pop(gone, None)
            self._refreshes.pop(gone, None)
            for token in [token for token, one in self._shots_pending.items() if one == gone]:
                del self._shots_pending[token]
        elif method == "Page.frameNavigated":
            frame = params.get("frame") or {}
            tab = self._tabs.get(session or "")
            if tab is not None and not frame.get("parentId") and frame.get("id") == tab[1]:
                url = str(frame.get("url") or "")
                # A new page in the tab: nothing done on it yet.
                self._acted_on.discard(tab[0])
                # Last in the order: the end screenshot falls back to the page opened last.
                self._urls.pop(session or "", None)
                self._urls[session or ""] = url
                self.record.navigated(tab[0], url)
        elif method == "Page.loadEventFired":
            tab = self._tabs.get(session or "")
            if tab is not None and tab[1] and self.record.opened(self._urls.get(session or "", "")):
                url = self._urls[session or ""]
                # One place stays free for the end's: a page past the others' places is not photographed at all.
                # A place is a page screenshot kept or one still being taken.
                if self.record.shots_kept() + len(self._shots_pending) + 1 >= self._shot_limits["shots"]:
                    self.record.cap_shots()
                    return
                token = object()
                self._shots_pending[token] = session or ""

                def answered(answer: dict[str, Any]) -> None:
                    self._shots_pending.pop(token, None)
                    self._photographed(answer, "load", session, url)

                try:
                    self._command("Page.captureScreenshot", dict(_SHOT), session, then=answered)
                except BaseException:
                    self._shots_pending.pop(token, None)
                    raise
        elif method == "Page.frameRequestedNavigation":
            tab = self._tabs.get(session or "")
            if (
                tab is not None
                and params.get("frameId") == tab[1]
                and params.get("reason") in _PAGE_REFRESHES
            ):
                # The browser names the address with its fragment, and requests it without.
                self._refreshes.setdefault(session or "", set()).add(urldefrag(str(params.get("url") or "")).url)
        elif method == "Network.requestWillBeSent":
            tab = self._tabs.get(session or "")
            if tab is not None:
                initiator = params.get("initiator") if isinstance(params.get("initiator"), dict) else {}
                url = str((params.get("request") or {}).get("url") or "")
                document = params.get("type") == "Document"
                main_frame = params.get("frameId") == tab[1]
                refreshed = self._refreshes.get(session or "", set())
                by_refresh = document and main_frame and url in refreshed
                refreshed.discard(url)
                self.record.requested(
                    tab[0],
                    url,
                    document=document,
                    main_frame=main_frame,
                    # The page asked, not the agent: a server's redirect, the page's own refresh, or its script
                    # running with no input behind it. A script the agent's click set off (a button's own handler)
                    # carries that click's mark, and the attempt stays the agent's; the mark lapses after a few
                    # seconds and a window opened uses it up, so once the agent has clicked or typed on the page,
                    # its script's moves stay the agent's until the next page.
                    by_page=bool(params.get("redirectResponse"))
                    or by_refresh
                    or (
                        initiator.get("type") == "script"
                        and not params.get("hasUserGesture")
                        and tab[0] not in self._acted_on
                    ),
                )
        elif method == "Runtime.bindingCalled" and params.get("name") == self.binding:
            tab = self._tabs.get(session or "")
            if tab is not None:
                # Only the listener, in a world of its own, calls the binding, and only for trusted input.
                self._acted_on.add(tab[0])
                self.record.acted(tab[0], str(params.get("payload") or ""))

    def _photographed(self, answer: dict[str, Any], when: str, session: str | None, url: str) -> None:
        """Keep a screenshot the browser took, within the trial's cap. One it could not take is skipped."""
        try:
            data = base64.b64decode(str((answer.get("result") or {}).get("data") or ""), validate=True)
        except ValueError:
            data = b""
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
                ("Runtime.addBinding", {"name": self.binding, "executionContextName": self.listener_world}),
                (
                    "Page.addScriptToEvaluateOnNewDocument",
                    {"source": self.listener, "worldName": self.listener_world, "runImmediately": True},
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
        write_text(seal_request_path(record_path), nonce)
    except OSError:
        logger.warning("browser record: the seal could not be asked for at %s", record_path)
    deadline = time.monotonic() + timeout
    body: Any = None
    while True:
        try:
            body = json.loads(read_kit_file(record_path))
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


def split_blocked(blocked: list[dict[str, Any]], record: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The trial's blocked list with the browser's refusals split in two: each page the agent tried to open, whole,
    and the requests pages made, counted for each host. For a host the record saw, its count stands in for the
    proxy's, which counts each of the browser's own retries. The proxy is the record of what was refused, so a host it
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


# Not told the browser's refusals from their one source: the agent's own list is split as it stands.
_NOT_GIVEN: Any = object()


def sealed_browser_blocked(
    state_changes: Any, record: dict[str, Any] | None, refused: list[dict[str, Any]] | None = _NOT_GIVEN
) -> Any:
    """The trial's sealed changes with the agent's browser refusals split as the record tells them apart, counted
    for each host and kind: the sealed record carries counts, and every address stays in the world_state file. With
    no record the proxy's counts stand. ``refused`` is the browser's refusals from the one source the world_state
    file uses: given, they are the agent's browser rows, in place of whatever the door's own log held; None, that
    source could not be read, so the agent's entry holds none of the browser's rows and says they are unreadable; a
    capped list says it was capped. Never changes its input."""
    if not isinstance(state_changes, list) or not isinstance(record, dict):
        return state_changes
    given = refused is not _NOT_GIVEN
    rows = refused if isinstance(refused, list) else []
    browser_rows = [one for one in rows if one.get("via") == "browser"]
    capped = any(one.get("truncated") is True for one in rows)
    sealed: list[Any] = []
    for entry in state_changes:
        if not (isinstance(entry, dict) and entry.get("who") == "agent"):
            sealed.append(entry)
            continue
        blocked = entry.get("blocked")
        said: dict[str, Any] = {}
        if given:
            blocked = [one for one in blocked or [] if one.get("via") != "browser"] + browser_rows
            if refused is None:
                said["browser_blocked_unreadable"] = True
            elif capped:
                said["browser_blocked_truncated"] = True
        if not isinstance(blocked, list) or (not blocked and "blocked" not in entry and not said):
            sealed.append(entry)
            continue
        if refused is None:
            # As the file holds no list in that case, no browser row stands in for the unread ones.
            sealed.append({**entry, "blocked": blocked, **said})
            continue
        counted: list[dict[str, Any]] = []
        for one in split_blocked(blocked, record):
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
        sealed.append({**entry, "blocked": counted, **said})
    return sealed


SCREENSHOTS_CAPPED = "screenshots_capped"
SCREENSHOTS_NOT_KEPT = "screenshots_not_kept"
SCREENSHOTS_NOT_SENT_AT_LEVEL = "screenshots_not_sent_at_level"


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
    screenshot not kept, whether the kit could not send it whole or the platform did not store it, never fails the
    trial: it is said."""
    from .outbound import ArtifactKind

    notices: list[dict[str, str]] = []
    if not isinstance(evidence, dict):
        return (), notices
    listed = evidence.get("screenshots") or []
    if listed and not screenshots_kept(adapter):
        return (), [level_keeps_no_screenshots_notice(len(listed))]
    if evidence.get("screenshots_capped"):
        notices.append(
            {
                "code": SCREENSHOTS_CAPPED,
                "message": "The browser's screenshots reached their limit for one trial: some pages were not photographed, "
                "or their screenshots not kept.",
            }
        )
    ids: list[str] = []
    lost = 0
    for one in listed:
        row = one if isinstance(one, dict) else {}
        data = _listed_shot(shots_dir, row)
        if data is None:
            # Listed in the record and not sendable whole, as named: not kept, and said.
            lost += 1
            continue
        try:
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


def _listed_shot(shots_dir: Path, row: dict[str, Any]) -> bytes | None:
    """A listed screenshot's bytes: a plain file in the folder, not through a link, no larger than a screenshot may
    be, and the bytes its row names when it names any. None otherwise."""
    name = str(row.get("file") or "")
    if not name or Path(name).name != name:
        return None
    try:
        fd = os.open(shots_dir / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        data = b""
        while len(data) <= SHOT_BYTES:
            piece = os.read(fd, SHOT_BYTES + 1 - len(data))
            if not piece:
                break
            data += piece
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(data) > SHOT_BYTES:
        return None
    named = row.get("artifact_id")
    if isinstance(named, str) and named != "sha256:" + hashlib.sha256(data).hexdigest():
        return None
    return data


def screenshots_kept(adapter: Any) -> bool:
    """Whether the run's artifact level keeps screenshots; an adapter that does not say keeps them."""
    from .outbound import ArtifactKind

    keeps = getattr(adapter, "keeps_artifact", None)
    return not callable(keeps) or bool(keeps(ArtifactKind.SCREENSHOT))


def level_keeps_no_screenshots_notice(count: int) -> dict[str, str]:
    return {
        "code": SCREENSHOTS_NOT_SENT_AT_LEVEL,
        "message": f"The run's artifact level keeps no screenshots, so the browser's {count} were not sent.",
    }


def not_kept_notice(lost: int) -> dict[str, str]:
    return {
        "code": SCREENSHOTS_NOT_KEPT,
        "message": f"{lost} of the browser's screenshots were not kept, either not sent whole or not stored by the "
        "platform; the trial is graded without them.",
    }


def shots_dir_for(record_path: Path) -> Path:
    stem = record_path.name.removesuffix(".json").removesuffix("-record")
    return record_path.with_name(f"{stem}-shots")


def browser_shots_dir(runtime: Any) -> Path:
    browser = (getattr(runtime, "metadata", None) or {}).get("browser")
    record_path = browser.get("record_path") if isinstance(browser, dict) else None
    return shots_dir_for(Path(str(record_path))) if record_path else Path("/nonexistent-browser-shots")


BROWSER_STATE_SCHEMA = "futureagi.browser-world-state.v1"
# Where the sealed record names a world_state file holding the browser's record but not the world's tables.
BROWSER_STATE_KEY = "browser_state_artifact"
NO_WORLD_DOOR = "no_world_door"
WORLD_END_UNREADABLE = "world_end_unreadable"


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


# What the agent typed is the evidence of what it did, so it leaves with the trace (the world_state file). Secret
# fields are never kept as text and declared secrets are masked, at any level. The one setting: True holds typed text
# to `traces-and-recordings`, as screenshots are.
TYPED_TEXT_NEEDS_RECORDINGS = False
TYPED_TEXT_NOT_KEPT_AT_LEVEL = "not_kept_at_level"


def typed_text_kept(adapter: Any) -> bool:
    """Whether what the agent typed into the browser leaves with the run."""
    return screenshots_kept(adapter) or not TYPED_TEXT_NEEDS_RECORDINGS


def _level_typing(evidence: dict[str, Any], adapter: Any) -> dict[str, Any]:
    """The record as the run's level keeps it: where typed text is not kept, each typing row keeps where and that it
    happened, and the record says its text was left out for the level, never that nothing was typed."""
    rows = evidence.get("rows")
    if typed_text_kept(adapter) or not isinstance(rows, list):
        return dict(evidence)
    kept = [
        {**row, "text": None} if isinstance(row, dict) and row.get("kind") == "typing" else row for row in rows
    ]
    return {**evidence, "rows": kept, "typed_text": TYPED_TEXT_NOT_KEPT_AT_LEVEL}


def browser_file_body(
    evidence: dict[str, Any], blocked: list[dict[str, Any]] | None, *, adapter: Any
) -> dict[str, Any]:
    """The browser's part of the trial's world_state file: its record as the run's level keeps it, and the trial's
    blocked list split by it, or that the list could not be read."""
    body: dict[str, Any] = {"browser": _level_typing(evidence, adapter)}
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
    body = {"schema": BROWSER_STATE_SCHEMA, "why": why, **held, **browser_file_body(evidence, blocked, adapter=adapter)}
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
