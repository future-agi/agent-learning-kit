"""The browser a browser environment gives its agent: one headless Chromium for each world.

The kit starts it, not the agent, so it can be held to the environment's sites and watched without
the agent's help. The agent attaches to it by its DevTools address, given in ``ALK_BROWSER_CDP_URL``.

Two bounds hold, and they are not the same:

- The browser reaches only the environment's sites, through a refusing proxy of its own
  (``BrowserProxy``). Unlike the agent's proxy it never admits loopback, where the world's database
  and the kit's own services listen, and Chromium is told to send loopback through it too. QUIC and
  WebRTC, which would go around a proxy, are off.
- The sandbox as a whole reaches the firewall's wider list. The agent's own code, or a browser it
  starts itself, is bound only by that.

The first bound covers what the browser is asked to load. It does not hold against a client that
changes the browser's own settings through its DevTools address, which that address allows; a
filter between the agent and the browser would be needed for that.

The browser runs as the agent's user with a bare environment, starts on an empty profile at each
reset, and its own background traffic is switched off as far as Chromium's switches allow. What it
still asks for on its own is never logged as the agent's attempt.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import shutil
import socket
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .egress_proxy import EgressProxy, RefusedAddress, host_allowed
from .world_door import ActionLog

logger = logging.getLogger(__name__)

BROWSER_CDP_URL_ENV = "ALK_BROWSER_CDP_URL"
BROWSER_NOT_USED = "browser_not_used"
BROWSER_NOT_STARTED = "browser_not_started"
# Where Chromium's own account, messaging and update services are pointed instead of Google's: a
# name that resolves nowhere (`.invalid` is reserved), refused by the browser's proxy and never logged.
HOUSEKEEPING_HOST = "kit-browser.invalid"
# How long Chromium may take to open its DevTools port.
_START_SECONDS = 30.0

# Features that fetch on their own. Chromium reads only the last `--disable-features`, so they are one switch.
_QUIET_FEATURES = (
    "AcceptCHFrame",
    "AutofillServerCommunication",
    "CaptivePortalDetection",
    "CertificateTransparencyComponentUpdater",
    "DialMediaRouteProvider",
    "InterestFeedContentSuggestions",
    "LensOverlay",
    "MediaRouter",
    "NetworkTimeServiceQuerying",
    "OptimizationGuideModelDownloading",
    "OptimizationHints",
    "OptimizationHintsFetching",
    "OptimizationTargetPrediction",
    "PreconnectToSearch",
    "SearchPrefetchServicePrefetching",
    "Translate",
)
# The service addresses Chromium takes as switches, each pointed at the housekeeping name.
_SERVICE_URLS = (
    "gaia-url",
    "google-apis-url",
    "google-base-url",
    "lso-url",
    "gcm-checkin-url",
    "gcm-mcs-endpoint",
    "gcm-registration-url",
    "sync-url",
    "variations-server-url",
)


def chromium_binary() -> str | None:
    """The kit's Chromium: ``ALK_CHROMIUM`` for a local run, else ``alk-chromium`` from the image."""
    configured = os.environ.get("ALK_CHROMIUM", "").strip()
    if configured:
        return configured
    return shutil.which("alk-chromium")


def chromium_arguments(binary: str, *, profile: Path, proxy_url: str) -> list[str]:
    """How the kit starts its browser: headless, on ``profile``, every request through ``proxy_url``."""
    housekeeping = f"http://{HOUSEKEEPING_HOST}"
    return [
        binary,
        "--headless=new",
        # The image's sandbox has no user namespaces for Chromium's own sandbox (as in its certification).
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        f"--user-data-dir={profile}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0",
        f"--proxy-server={proxy_url}",
        # Chromium sends loopback around its proxy unless told otherwise.
        "--proxy-bypass-list=<-loopback>",
        # UDP that would go around the proxy.
        "--disable-quic",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        # The browser's own background traffic.
        "--no-first-run",
        "--no-default-browser-check",
        "--no-pings",
        "--no-service-autorun",
        "--disable-background-networking",
        "--disable-breakpad",
        "--disable-client-side-phishing-detection",
        "--disable-component-extensions-with-background-pages",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-domain-reliability",
        "--disable-extensions",
        "--disable-field-trial-config",
        "--disable-search-engine-choice-screen",
        "--disable-sync",
        "--metrics-recording-only",
        "--safebrowsing-disable-auto-update",
        "--password-store=basic",
        "--use-mock-keychain",
        f"--disable-features={','.join(_QUIET_FEATURES)}",
        *(f"--{switch}={housekeeping}" for switch in _SERVICE_URLS),
        "about:blank",
    ]


def browser_environment(*, home: Path) -> dict[str, str]:
    """The browser's whole environment: no key, no kit token and no proxy variable. Its proxy is on its command
    line, and whoever holds its DevTools address controls it."""
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(home),
        "LANG": "C.UTF-8",
    }


def _resolve(host: str) -> str:
    return socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)[0][4][0]


class BrowserProxy(EgressProxy):
    """The browser's own refusing proxy: the environment's sites, never loopback, and nothing else.

    A refusal is logged as the agent's ``egress_blocked`` attempt, marked ``via: browser``, only while a
    client holds the browser: before that only the browser itself can have asked. A request for the
    housekeeping name is never logged.
    """

    via = "browser"

    def __init__(
        self,
        sites: Iterable[str],
        log: ActionLog | None,
        *,
        attached: Callable[[], bool],
        connect: Callable[[str, int], socket.socket] | None = None,
        resolve: Callable[[str], str] = _resolve,
    ) -> None:
        self._attached = attached
        self._connect = connect
        self._resolve = resolve
        super().__init__(sites, log)

    def allows(self, host: str) -> bool:
        return host_allowed(host, self.allowed, loopback=False)

    def open(self, host: str, port: int) -> socket.socket:
        if self._connect is not None:
            return self._connect(host, port)
        address = self._resolve(host)
        found = ipaddress.ip_address(address.split("%", 1)[0])
        if found.is_loopback or found.is_unspecified:
            # A site's name that leads back to this machine would reach the world itself.
            raise RefusedAddress(
                f"{host} resolves to a loopback address, outside the test world"
            )
        return socket.create_connection((address, port), timeout=30)

    def refuse(self, host: str, method: str) -> None:
        name = host.strip().strip("[]").lower().rstrip(".")
        if name == HOUSEKEEPING_HOST or name.endswith("." + HOUSEKEEPING_HOST):
            return
        if not self._attached():
            return
        super().refuse(host, method)


def _write_json(path: Path, value: Any) -> None:
    fresh = path.with_name(path.name + ".new")
    fresh.write_text(json.dumps(value), encoding="utf-8")
    os.replace(fresh, path)


class DevToolsRelay:
    """The browser's DevTools address as the agent is given it: fixed for the world's life, relayed to
    whichever browser runs now. It counts the clients that attach (a websocket to ``/devtools/``) since the
    browser last started, in ``state_path``, for the trial to say whether the browser was used.

    Chromium names the address it was asked on in the websocket address it hands back, so a client that
    asked here attaches here too.
    """

    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self._lock = threading.Lock()
        self._upstream: int | None = None
        self._attached = 0
        self._listener = socket.create_server(("127.0.0.1", 0))
        self._closed = False
        self._write()
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def port(self) -> int:
        return self._listener.getsockname()[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def target(self, port: int) -> None:
        """Relay to the browser now listening on ``port``, which knows no client yet."""
        with self._lock:
            self._upstream = port
            self._attached = 0
            self._write()

    def attached(self) -> bool:
        with self._lock:
            return self._attached > 0

    def close(self) -> None:
        self._closed = True
        try:
            self._listener.close()
        except OSError:
            pass

    def _write(self) -> None:
        try:
            _write_json(self.state_path, {"attached": self._attached})
        except OSError:
            logger.warning(
                "browser: its state could not be written to %s", self.state_path
            )

    def _serve(self) -> None:
        while not self._closed:
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._relay, args=(client,), daemon=True).start()

    def _relay(self, client: socket.socket) -> None:
        with self._lock:
            port = self._upstream
        if port is None:
            client.close()
            return
        try:
            client.settimeout(30)
            first = client.recv(65536)
            upstream = socket.create_connection(("127.0.0.1", port), timeout=30)
        except OSError:
            client.close()
            return
        if first.startswith(b"GET /devtools/"):
            with self._lock:
                if self._upstream == port:
                    self._attached += 1
                    self._write()
        try:
            upstream.sendall(first)
        except OSError:
            client.close()
            upstream.close()
            return
        client.settimeout(None)
        upstream.settimeout(None)
        threading.Thread(target=_pump, args=(upstream, client), daemon=True).start()
        _pump(client, upstream)


def _pump(source: socket.socket, sink: socket.socket) -> None:
    try:
        while data := source.recv(65536):
            sink.sendall(data)
    except OSError:
        pass
    finally:
        for one in (source, sink):
            try:
                one.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class KitBrowser:
    """One world's browser, its proxy and its DevTools relay. ``start`` runs a new browser on an empty profile;
    the relay's address stays the same."""

    def __init__(
        self,
        *,
        binary: str,
        sites: Iterable[str],
        log: ActionLog | None,
        directory: Path,
        state_path: Path,
        launch: Callable[[list[str], Path, dict[str, str], Path], Any],
        stop: Callable[[Any], None],
        prepare: Callable[[Path], None] = lambda path: None,
        connect: Callable[[str, int], socket.socket] | None = None,
        recorder: Any = None,
    ) -> None:
        self.binary = binary
        self.log = log
        # The kit's own client to the browser: what the agent did there, kept as the world's record.
        self.recorder = recorder
        self.directory = directory
        self.relay = DevToolsRelay(state_path)
        self.proxy = BrowserProxy(
            sites, log, attached=self.relay.attached, connect=connect
        )
        self._launch = launch
        self._stop = stop
        self._prepare = prepare
        self._handle: Any = None

    def environment(self) -> dict[str, str]:
        return {BROWSER_CDP_URL_ENV: self.relay.url}

    def metadata(self) -> dict[str, str]:
        found = {"state": "started", "state_path": str(self.relay.state_path)}
        if self.recorder is not None:
            found["record_path"] = str(self.recorder.record_path)
        if isinstance(getattr(self.log, "path", None), Path):
            # A world with no door keeps the browser's refusals in a kit-owned file.
            found["blocked_log_path"] = str(self.log.path)
        return found

    def start(self) -> None:
        """Stop any browser running now and start a new one that remembers nothing: a new, empty profile folder,
        whether or not the last one could be removed."""
        self.stop()
        reset = getattr(self.log, "reset", None)
        if callable(reset):
            # A new trial: the last one's refusals are not this one's.
            reset()
        if self.directory.exists():
            try:
                shutil.rmtree(self.directory)
            except OSError:
                logger.warning("browser: the last profile under %s could not be removed", self.directory)
        profile = self.directory / f"profile-{uuid.uuid4().hex}"
        profile.mkdir(parents=True)
        self._prepare(profile)
        argv = chromium_arguments(
            self.binary, profile=profile, proxy_url=self.proxy.url
        )
        self._handle = self._launch(
            argv,
            profile,
            browser_environment(home=profile),
            self.directory / "browser.log",
        )
        port = self._devtools_port(profile)
        if self.recorder is not None:
            # Before the relay lets the agent in, so every tab it opens is watched from its first page.
            self.recorder.attach(port)
        self.relay.target(port)

    def stop(self) -> None:
        if self.recorder is not None:
            self.recorder.detach()
        handle, self._handle = self._handle, None
        if handle is not None:
            self._stop(handle)

    def close(self) -> None:
        self.stop()
        if self.recorder is not None:
            self.recorder.close()
        self.relay.close()
        self.proxy.close()

    def _devtools_port(self, profile: Path) -> int:
        """The port Chromium chose, from the file it writes in its profile once its DevTools port is open."""
        written = profile / "DevToolsActivePort"
        deadline = time.monotonic() + _START_SECONDS
        while time.monotonic() < deadline:
            try:
                first = written.read_text(encoding="utf-8").split("\n", 1)[0].strip()
            except OSError:
                first = ""
            if first.isdigit():
                return int(first)
            running = getattr(self._handle, "is_running", None)
            if callable(running) and not running():
                break
            time.sleep(0.05)
        self.stop()
        raise RuntimeError(
            f"the browser did not open its DevTools port within {_START_SECONDS:.0f} seconds"
        )


def browser_notices(runtime: Any) -> list[dict[str, Any]]:
    """What a trial says of its world's browser: not started for sites the agent was not read to need, and not
    used when no client attached to it. A state that can't be read is never taken for an attached client."""
    browser = (getattr(runtime, "metadata", None) or {}).get("browser")
    if not isinstance(browser, dict):
        return []
    if browser.get("state") == "not_started":
        return [
            {
                "code": BROWSER_NOT_STARTED,
                "message": "This environment has sites for a browser, and the agent was not read as a browser "
                "agent, so no browser was started for it.",
            }
        ]
    if browser.get("state") != "started":
        return []
    attached = 0
    path = Path(str(browser.get("state_path") or ""))
    try:
        if path.is_file():
            with open(path, "rb") as handle:
                state = json.loads(handle.read(4096))
            attached = state.get("attached", 0) if isinstance(state, dict) else 0
    except (OSError, ValueError):
        attached = 0
    if isinstance(attached, int) and attached > 0:
        return []
    return [
        {
            "code": BROWSER_NOT_USED,
            "message": "The environment's browser was not used in this trial: no client attached to it. An agent "
            f"drives it by attaching to the address in {BROWSER_CDP_URL_ENV}. A browser the agent starts itself is "
            "bound only by the sandbox's firewall, not by the environment's sites.",
        }
    ]
