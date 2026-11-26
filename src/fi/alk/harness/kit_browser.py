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

The first bound covers what the browser is asked to load over the network. It does not hold against a
client that changes the browser's own settings through its DevTools address, which that address
allows; a filter between the agent and the browser would be needed for that. Nor does it cover
local files: the browser can open what the agent's user can read, as the agent itself can.

The browser runs as the agent's user with a bare environment, starts on an empty profile at each
reset, and its own background traffic is switched off as far as Chromium's switches allow. What it
asks for before a client attaches is never logged as the agent's attempt.

What the proxy logs is per host: it sees a host name, not who asked. Once a client has attached, a
refused request is logged whether the agent's navigation asked for it or a page it opened did (a
script, a font, a favicon), and a refused https site is asked for again by the browser itself.
Telling those apart is the browser record's work, from inside the browser.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import select
import shutil
import socket
import stat
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .egress_proxy import (
    _CLIENT_SECONDS,
    _PIECE,
    EgressProxy,
    RefusedAddress,
    _max_clients,
    _tunnel_seconds,
    entry_admits,
)
from .world_door import ActionLog

logger = logging.getLogger(__name__)

BROWSER_CDP_URL_ENV = "ALK_BROWSER_CDP_URL"
BROWSER_NOT_USED = "browser_not_used"
BROWSER_NOT_STARTED = "browser_not_started"
# Where Chromium's own account, messaging and update services are pointed instead of Google's: a
# name that resolves nowhere (`.invalid` is reserved), refused by the browser's proxy and never logged.
HOUSEKEEPING_HOST = "kit-browser.invalid"
# How long Chromium may take to open its DevTools port, and how often to look.
_START_SECONDS = 30.0
_START_POLL_SECONDS = 0.05
# How long the browser's proxy tries a site's addresses, all of them together.
_CONNECT_SECONDS = 30.0
# How much of a browser's output a start failure quotes.
_FAILURE_TAIL = 1024

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


def _resolve(host: str) -> list[str]:
    """Every address of a name, in the resolver's order, each once."""
    addresses: list[str] = []
    for *_, place in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
        if place[0] not in addresses:
            addresses.append(place[0])
    return addresses


def _leads_back(address: str) -> bool:
    found = ipaddress.ip_address(address.split("%", 1)[0])
    # An IPv4 address written as IPv6 is read as the IPv4 one, whatever the Python release says of it.
    mapped = getattr(found, "ipv4_mapped", None)
    found = mapped if mapped is not None else found
    return found.is_loopback or found.is_unspecified


class BrowserProxy(EgressProxy):
    """The browser's own refusing proxy: the environment's sites, never loopback, and nothing else.

    A refusal is logged as the agent's ``egress_blocked`` attempt, marked ``via: browser``, once a client
    has attached to the browser now running, and until it stops: before that only the browser itself can
    have asked. It is logged by host, whoever in the browser asked. A request for the housekeeping name is
    never logged.
    """

    via = "browser"

    def __init__(
        self,
        sites: Iterable[str],
        log: ActionLog | None,
        *,
        attached: Callable[[], bool],
        connect: Callable[[str, int], socket.socket] | None = None,
        resolve: Callable[[str], list[str]] = _resolve,
    ) -> None:
        self._attached = attached
        self._connect = connect
        self._resolve = resolve
        super().__init__(sites, log)

    def allows(self, host: str) -> bool:
        # Read as the sandbox firewall reads each entry, never wider: a host this let through and the firewall
        # dropped would be in no blocked list. Loopback is never a site.
        return any(entry_admits(site, host) for site in self.allowed)

    def open(self, host: str, port: int) -> socket.socket:
        if self._connect is not None:
            return self._connect(host, port)
        addresses = self._resolve(host)
        if not addresses:
            raise OSError(f"{host} resolves to no address")
        if any(_leads_back(address) for address in addresses):
            # A site's name that leads back to this machine would reach the world itself, whichever address is used.
            raise RefusedAddress(
                f"{host} resolves to a loopback address, outside the test world"
            )
        # Each address in turn, as a direct connection would, all within one time limit.
        deadline = time.monotonic() + _CONNECT_SECONDS
        failed: OSError | None = None
        for address in addresses:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            try:
                return socket.create_connection((address, port), timeout=left)
            except OSError as error:
                failed = error
        raise failed or OSError(f"{host} could not be reached in {_CONNECT_SECONDS} seconds")

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
        # The same bounds as the egress proxy beside it: so many clients at once, and a connection idle both ways
        # as long as the longest call the runner allows.
        self._slots = threading.BoundedSemaphore(_max_clients())
        self._idle = _tunnel_seconds()
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

    def release(self) -> None:
        """No browser behind the relay: no client is taken to hold one until the next ``target``. The count in the
        state file stays, for the trial that ended to be read."""
        with self._lock:
            self._upstream = None

    def attached(self) -> bool:
        with self._lock:
            return self._upstream is not None and self._attached > 0

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
            if not self._slots.acquire(blocking=False):
                logger.warning("browser: a DevTools client was turned away, the relay holds as many as it serves")
                client.close()
                continue
            threading.Thread(target=self._held, args=(client,), daemon=True).start()

    def _held(self, client: socket.socket) -> None:
        try:
            self._relay(client)
        finally:
            self._slots.release()

    def _relay(self, client: socket.socket) -> None:
        with self._lock:
            port = self._upstream
        if port is None:
            client.close()
            return
        try:
            client.settimeout(_CLIENT_SECONDS)
            first = client.recv(_PIECE)
        except OSError:
            client.close()
            return
        try:
            upstream = socket.create_connection(("127.0.0.1", port), timeout=_CLIENT_SECONDS)
        except OSError:
            logger.warning("browser: the relay could not reach the browser on its DevTools port %s", port)
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
        _pipe(client, upstream, self._idle)


def _pipe(client: socket.socket, upstream: socket.socket, idle: float) -> None:
    """Carry bytes both ways until either side closes, or neither sends for ``idle`` seconds."""
    sockets = [client, upstream]
    try:
        while True:
            ready, _, _ = select.select(sockets, [], [], idle)
            if not ready:
                return
            for one in ready:
                data = one.recv(_PIECE)
                if not data:
                    return
                (upstream if one is client else client).sendall(data)
    except (OSError, ValueError):
        return
    finally:
        for one in sockets:
            try:
                one.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            one.close()


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
        retain_log: Callable[[Path], None] | None = None,
    ) -> None:
        self.binary = binary
        self.directory = directory
        self.relay = DevToolsRelay(state_path)
        self.proxy = BrowserProxy(
            sites, log, attached=self.relay.attached, connect=connect
        )
        self._launch = launch
        self._stop = stop
        self._prepare = prepare
        self._retain_log = retain_log
        self._handle: Any = None

    def environment(self) -> dict[str, str]:
        return {BROWSER_CDP_URL_ENV: self.relay.url}

    def metadata(self) -> dict[str, str]:
        return {"state": "started", "state_path": str(self.relay.state_path)}

    def start(self) -> None:
        """Stop any browser running now and start a new one that remembers nothing: a new, empty profile folder,
        whether or not the last one could be removed."""
        self.stop()
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
        self.relay.target(self._devtools_port(profile))

    def stop(self) -> None:
        """Stop the browser. From here no refusal is the agent's until the next browser has started and a client
        attached to it."""
        self.relay.release()
        handle, self._handle = self._handle, None
        if handle is not None:
            self._stop(handle)

    def close(self) -> None:
        self.stop()
        self.relay.close()
        self.proxy.close()

    def _devtools_port(self, profile: Path) -> int:
        """The port Chromium chose, from the file it writes in its profile once its DevTools port is open. A browser
        that exits first, or never opens it, is stopped; its output is kept and the end of it quoted."""
        written = profile / "DevToolsActivePort"
        deadline = time.monotonic() + _START_SECONDS
        exited = False
        while time.monotonic() < deadline:
            try:
                first = written.read_text(encoding="utf-8").split("\n", 1)[0].strip()
            except OSError:
                first = ""
            if first.isdigit():
                return int(first)
            running = getattr(self._handle, "is_running", None)
            if callable(running) and not running():
                exited = True
                break
            time.sleep(_START_POLL_SECONDS)
        code = _exit_code(self._handle)
        self.stop()
        log_path = self.directory / "browser.log"
        if self._retain_log is not None:
            try:
                self._retain_log(log_path)
            except Exception:  # noqa: BLE001 - the start failure is reported whatever becomes of its log
                logger.warning("browser: its output could not be kept", exc_info=True)
        said = _log_tail(log_path)
        if exited:
            what = "the browser exited before it opened its DevTools port" + (
                f" (exit code {code})" if code is not None else ""
            )
        else:
            what = f"the browser did not open its DevTools port within {_START_SECONDS:.0f} seconds"
        raise RuntimeError(f"{what}: {said}" if said else what)


def _exit_code(handle: Any) -> int | None:
    popen = getattr(handle, "popen", None)
    code = popen.poll() if popen is not None else None
    return code if isinstance(code, int) else None


def _log_tail(path: Path) -> str:
    """The end of the browser's output, which its own user can write: a regular file only, never followed through a
    link, read for a bounded size, and cleaned as the kit cleans what it sends."""
    from .outbound import redact_outbound_text

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return ""
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return ""
        offset = max(0, info.st_size - _FAILURE_TAIL)
        os.lseek(fd, offset, os.SEEK_SET)
        raw = os.read(fd, _FAILURE_TAIL)
    except OSError:
        return ""
    finally:
        os.close(fd)
    if offset:
        raw = raw.partition(b"\n")[2]
    text = " ".join(raw.decode("utf-8", errors="replace").split())
    return "".join(one for one in redact_outbound_text(text) if one.isprintable())


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
