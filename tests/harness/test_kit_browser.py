"""The kit's browser for a browser agent: its own proxy, its DevTools relay and its notices."""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error, request

import pytest

from fi.alk.harness import kit_browser
from fi.alk.harness.call_runner import trial_state_changes
from fi.alk.harness.kit_browser import (
    BROWSER_NOT_STARTED,
    BROWSER_NOT_USED,
    HOUSEKEEPING_HOST,
    BrowserProxy,
    DevToolsRelay,
    KitBrowser,
    browser_notices,
    chromium_arguments,
)
from fi.alk.harness.process_runtime import EnvironmentRuntime, RuntimeState
from fi.alk.harness.world_door import MemoryLog


class _Page(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        content = b"<html><body>a page</body></html>"
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("content-length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


@pytest.fixture()
def site():
    """A page served on loopback, reached by the browser's proxy as `shop.test` through its connect seam."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()


def _to(site_server):
    def connect(host: str, port: int) -> socket.socket:
        if host == "shop.test":
            return socket.create_connection(
                ("127.0.0.1", site_server.server_port), timeout=5
            )
        raise OSError(f"no route to {host}")

    return connect


def _through(proxy, url: str) -> tuple[int, bytes]:
    opener = request.build_opener(request.ProxyHandler({"http": proxy.url}))
    try:
        with opener.open(url, timeout=5) as answer:
            return answer.status, answer.read()
    except error.HTTPError as exc:
        return exc.code, exc.read()


class _Attached:
    def __init__(self, attached: bool) -> None:
        self.attached = attached

    def __call__(self) -> bool:
        return self.attached


# The browser's proxy


def test_the_browser_reaches_its_sites_and_nothing_else(site) -> None:
    log = MemoryLog()
    proxy = BrowserProxy(
        ["shop.test"], log, attached=_Attached(True), connect=_to(site)
    )
    try:
        assert _through(proxy, "http://shop.test/cart")[0] == 200
        assert _through(proxy, "http://www.example.com/")[0] == 403
    finally:
        proxy.close()
    [row] = log.rows()
    assert (row["outcome"], row["refusal_code"], row["actor_id"]) == (
        "refused",
        "egress_blocked",
        "agent",
    )
    assert row["arguments"] == {
        "host": "www.example.com",
        "method": "GET",
        "via": "browser",
    }


@pytest.mark.parametrize(
    "host", ["localhost", "127.0.0.1", "[::1]", "0.0.0.0", "db.localhost"]
)
def test_the_browser_s_proxy_does_not_admit_loopback(host) -> None:
    # The agent's proxy always lets loopback through; the browser's never does: loopback holds the world.
    def resolve(name: str) -> str:
        raise AssertionError(f"{name} was let through by name")

    log = MemoryLog()
    proxy = BrowserProxy(["shop.test"], log, attached=_Attached(True), resolve=resolve)
    try:
        assert _through(proxy, f"http://{host}:5432/")[0] == 403
    finally:
        proxy.close()
    assert [row["arguments"]["via"] for row in log.rows()] == ["browser"]


def test_a_site_whose_name_resolves_to_loopback_is_refused() -> None:
    proxy = BrowserProxy(
        ["shop.test"],
        MemoryLog(),
        attached=_Attached(True),
        resolve=lambda host: "127.0.0.1",
    )
    try:
        status, body = _through(proxy, "http://shop.test/")
    finally:
        proxy.close()
    assert status == 403
    assert b"loopback" in body


def test_the_browser_s_own_housekeeping_is_never_the_agent_s_attempt() -> None:
    log = MemoryLog()
    proxy = BrowserProxy(["shop.test"], log, attached=_Attached(True))
    try:
        assert _through(proxy, f"http://{HOUSEKEEPING_HOST}/ListAccounts")[0] == 403
    finally:
        proxy.close()
    assert log.rows() == []


def test_a_refusal_while_no_client_holds_the_browser_is_not_the_agent_s() -> None:
    # Before any client attaches, only the browser itself can have asked.
    log = MemoryLog()
    attached = _Attached(False)
    proxy = BrowserProxy(["shop.test"], log, attached=attached)
    try:
        _through(proxy, "http://www.google.com/")
        attached.attached = True
        _through(proxy, "http://www.example.com/")
    finally:
        proxy.close()
    assert [row["arguments"]["host"] for row in log.rows()] == ["www.example.com"]


def test_a_blocked_browser_request_is_reported_and_never_counted_as_an_attempt(
    site,
) -> None:
    log = MemoryLog()
    proxy = BrowserProxy(
        ["shop.test"], log, attached=_Attached(True), connect=_to(site)
    )
    try:
        _through(proxy, "http://www.example.com/")
        _through(proxy, "http://www.example.com/again")
    finally:
        proxy.close()
    [agent] = trial_state_changes(log.rows(), {})
    assert agent["attempted"] == 0 and agent["committed"] == 0
    assert agent["blocked"] == [
        {"host": "www.example.com", "method": "GET", "via": "browser", "count": 2}
    ]


# How the browser is started


def test_the_browser_is_started_with_its_proxy_for_loopback_too_and_no_traffic_around_it(
    tmp_path,
) -> None:
    argv = chromium_arguments(
        "/opt/alk-chromium", profile=tmp_path, proxy_url="http://127.0.0.1:4100"
    )
    assert argv[0] == "/opt/alk-chromium"
    assert "--proxy-server=http://127.0.0.1:4100" in argv
    # Chromium sends loopback around its proxy unless told otherwise.
    assert "--proxy-bypass-list=<-loopback>" in argv
    assert "--disable-quic" in argv
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in argv
    assert f"--user-data-dir={tmp_path}" in argv
    assert "--remote-debugging-address=127.0.0.1" in argv
    assert "--remote-debugging-port=0" in argv
    # Chromium reads only the last of a repeated switch: the features it must not run are named once.
    assert sum(one.startswith("--disable-features=") for one in argv) == 1
    assert f"--gaia-url=http://{HOUSEKEEPING_HOST}" in argv


def test_the_browser_s_environment_holds_no_key_token_or_proxy_setting(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ALK_HOSTED_TOKEN", "t")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    env = kit_browser.browser_environment(home=tmp_path)
    assert set(env) <= {"PATH", "HOME", "TMPDIR", "LANG", "FONTCONFIG_PATH"}
    assert env["HOME"] == str(tmp_path)


def _kit(tmp_path, profiles):
    def launch(argv, cwd, env, log_path):
        profiles.append(cwd)
        (cwd / "DevToolsActivePort").write_text("9333\n/devtools/browser/x\n")
        return object()

    return KitBrowser(
        binary="/opt/alk-chromium",
        sites=["shop.test"],
        log=MemoryLog(),
        directory=tmp_path / "browser",
        state_path=tmp_path / "state.json",
        launch=launch,
        stop=lambda handle: None,
    )


def test_each_start_is_a_new_empty_profile_and_the_last_one_is_gone(tmp_path) -> None:
    profiles: list = []
    kit = _kit(tmp_path, profiles)
    try:
        kit.start()
        (profiles[0] / "Cookies").write_text("session=1")
        kit.start()
    finally:
        kit.close()
    first, second = profiles
    assert first != second
    assert not first.exists()
    assert sorted(path.name for path in second.iterdir()) == ["DevToolsActivePort"]


def test_a_last_profile_that_cannot_be_removed_is_logged_and_never_reused(
    tmp_path, monkeypatch, caplog
) -> None:
    profiles: list = []
    kit = _kit(tmp_path, profiles)
    try:
        kit.start()
    finally:
        kit.close()
    (profiles[0] / "Cookies").write_text("session=1")

    def refuse(path, *args, **kwargs):
        raise PermissionError(path)

    monkeypatch.setattr(kit_browser.shutil, "rmtree", refuse)
    # A new browser for the same world, as after the world is provisioned again.
    kit = _kit(tmp_path, profiles)
    try:
        kit.start()
    finally:
        kit.close()
    first, second = profiles
    assert first != second and first.parent == second.parent
    assert sorted(path.name for path in second.iterdir()) == ["DevToolsActivePort"]
    assert "could not be removed" in caplog.text


# The DevTools relay


@pytest.fixture()
def devtools():
    """Stands in for the browser's DevTools port: answers `/json/version` naming the host it was asked on."""

    class _DevTools(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path.startswith("/devtools/"):
                self.send_response(101, "Switching Protocols")
                self.send_header("upgrade", "websocket")
                self.send_header("connection", "Upgrade")
                self.end_headers()
                return
            content = json.dumps(
                {
                    "webSocketDebuggerUrl": f"ws://{self.headers['host']}/devtools/browser/1"
                }
            ).encode()
            self.send_response(200)
            self.send_header("content-length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _DevTools)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()


def _upgrade(port: int) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        connection.sendall(
            b"GET /devtools/browser/1 HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
        )
        return connection.recv(4096)


def test_the_relay_counts_a_client_that_attaches_and_not_one_that_only_asks(
    devtools, tmp_path
) -> None:
    relay = DevToolsRelay(tmp_path / "browser.json")
    relay.target(devtools.server_port)
    try:
        with request.urlopen(f"{relay.url}/json/version", timeout=5) as answer:
            version = json.loads(answer.read())
        # The address it hands back is the relay's, so the client attaches through it.
        assert version["webSocketDebuggerUrl"].startswith(
            f"ws://127.0.0.1:{relay.port}/"
        )
        assert not relay.attached()
        assert b" 101 " in _upgrade(relay.port)
        assert relay.attached()
        assert json.loads((tmp_path / "browser.json").read_text()) == {"attached": 1}
    finally:
        relay.close()


def test_the_relay_keeps_its_address_when_the_browser_starts_again_and_forgets_earlier_clients(
    devtools, tmp_path
) -> None:
    relay = DevToolsRelay(tmp_path / "browser.json")
    relay.target(devtools.server_port)
    try:
        url = relay.url
        _upgrade(relay.port)
        relay.target(devtools.server_port)
        assert relay.url == url
        assert not relay.attached()
        assert json.loads((tmp_path / "browser.json").read_text()) == {"attached": 0}
    finally:
        relay.close()


# What a trial says of its browser


def _runtime(metadata) -> EnvironmentRuntime:
    return EnvironmentRuntime(
        runtime_id="r",
        world_index=0,
        bundle_digest="d",
        state=RuntimeState.READY,
        metadata=metadata,
    )


def test_a_trial_whose_agent_never_attached_says_the_browser_was_not_used(
    tmp_path,
) -> None:
    state = tmp_path / "browser.json"
    state.write_text(json.dumps({"attached": 0}))
    [notice] = browser_notices(
        _runtime({"browser": {"state": "started", "state_path": str(state)}})
    )
    assert notice["code"] == BROWSER_NOT_USED
    assert "ALK_BROWSER_CDP_URL" in notice["message"]


def test_a_trial_whose_agent_attached_says_nothing_of_the_browser(tmp_path) -> None:
    state = tmp_path / "browser.json"
    state.write_text(json.dumps({"attached": 2}))
    assert (
        browser_notices(
            _runtime({"browser": {"state": "started", "state_path": str(state)}})
        )
        == []
    )


def test_a_browser_state_that_cannot_be_read_is_not_taken_for_an_attached_client(
    tmp_path,
) -> None:
    assert [
        one["code"]
        for one in browser_notices(
            _runtime(
                {
                    "browser": {
                        "state": "started",
                        "state_path": str(tmp_path / "missing.json"),
                    }
                }
            )
        )
    ] == [BROWSER_NOT_USED]


def test_sites_for_an_agent_not_read_as_a_browser_agent_say_no_browser_was_started() -> (
    None
):
    [notice] = browser_notices(_runtime({"browser": {"state": "not_started"}}))
    assert notice["code"] == BROWSER_NOT_STARTED


def test_a_world_with_no_browser_says_nothing() -> None:
    assert browser_notices(_runtime({})) == []
