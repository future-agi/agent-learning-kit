"""The kit's browser for a browser agent: its own proxy, its DevTools relay and its notices."""

from __future__ import annotations

import json
import os
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


# The browser's proxy


def test_the_browser_reaches_its_sites_and_nothing_else(site) -> None:
    log = MemoryLog()
    proxy = BrowserProxy(
        ["shop.test"], log, connect=_to(site)
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
    ("site", "host", "admitted"),
    [
        # A name is that host only.
        ("shop.test", "shop.test", True),
        ("shop.test", "SHOP.test.", True),
        ("shop.test", "www.shop.test", False),
        # A wildcard is every host under it, at any depth, never the name itself.
        ("*.shop.test", "www.shop.test", True),
        ("*.shop.test", "a.b.shop.test", True),
        ("*.shop.test", "shop.test", False),
        ("*.shop.test", "evilshop.test", False),
    ],
)
def test_the_browser_s_proxy_reads_each_site_as_the_sandbox_firewall_does(site, host, admitted) -> None:
    # A host the proxy let through and the firewall dropped would be in no blocked list.
    proxy = BrowserProxy([site], MemoryLog())
    try:
        assert proxy.allows(host) is admitted
    finally:
        proxy.close()


def test_the_agent_s_proxy_still_reads_a_name_as_covering_the_hosts_under_it() -> None:
    from fi.alk.harness.egress_proxy import host_allowed

    assert host_allowed("www.shop.test", ["shop.test"], loopback=False)
    assert host_allowed("shop.test", ["*.shop.test"], loopback=False)


@pytest.mark.parametrize(
    "host", ["localhost", "127.0.0.1", "[::1]", "0.0.0.0", "db.localhost"]
)
def test_the_browser_s_proxy_does_not_admit_loopback(host) -> None:
    # The agent's proxy always lets loopback through; the browser's never does: loopback holds the world.
    def resolve(name: str) -> str:
        raise AssertionError(f"{name} was let through by name")

    log = MemoryLog()
    proxy = BrowserProxy(["shop.test"], log, resolve=resolve)
    try:
        assert _through(proxy, f"http://{host}:5432/")[0] == 403
    finally:
        proxy.close()
    assert [row["arguments"]["via"] for row in log.rows()] == ["browser"]


def test_a_site_whose_name_resolves_to_loopback_is_refused() -> None:
    proxy = BrowserProxy(
        ["shop.test"],
        MemoryLog(),
        resolve=lambda host: ["127.0.0.1"],
    )
    try:
        status, body = _through(proxy, "http://shop.test/")
    finally:
        proxy.close()
    assert status == 403
    assert b"loopback" in body


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["0.0.0.0"],
        ["::"],
        # An IPv4 loopback written as IPv6 is loopback whatever the Python release says of it.
        ["::ffff:127.0.0.1"],
        # A name with a public address and a loopback one leads back as surely as one with loopback alone.
        ["203.0.113.7", "127.0.0.1"],
    ],
)
def test_a_site_whose_name_leads_back_to_this_machine_is_refused_and_logged(addresses) -> None:
    # 0.0.0.0 connects to a listener on 127.0.0.1, so an unspecified address is loopback too.
    log = MemoryLog()
    proxy = BrowserProxy(["shop.test"], log, resolve=lambda host: addresses)
    try:
        assert _through(proxy, "http://shop.test/")[0] == 403
    finally:
        proxy.close()
    assert [row["arguments"] for row in log.rows()] == [{"host": "shop.test", "method": "GET", "via": "browser"}]


def test_each_address_of_a_site_is_tried_in_turn_within_one_time_limit(monkeypatch) -> None:
    # An address that cannot be reached (an IPv6 one where the sandbox has no IPv6 route) gives way to the next.
    tried: list[tuple[str, int, float]] = []
    reached = object()

    def dial(address, timeout):
        tried.append((*address, timeout))
        if address[0] == "2001:db8::1":
            raise OSError("no route")
        return reached

    monkeypatch.setattr(kit_browser.socket, "create_connection", dial)
    proxy = BrowserProxy(
        ["shop.test"], MemoryLog(), resolve=lambda host: ["2001:db8::1", "203.0.113.7"]
    )
    try:
        assert proxy.open("shop.test", 443) is reached
    finally:
        proxy.close()
    assert [(host, port) for host, port, _ in tried] == [("2001:db8::1", 443), ("203.0.113.7", 443)]
    assert all(0 < timeout <= 30 for *_, timeout in tried)


def test_a_site_none_of_whose_addresses_answers_fails_with_the_last_error(monkeypatch) -> None:
    def dial(address, timeout):
        raise OSError(f"{address[0]} refused")

    monkeypatch.setattr(kit_browser.socket, "create_connection", dial)
    for addresses, said in ((["203.0.113.7", "203.0.113.8"], "203.0.113.8 refused"), ([], "no address")):
        proxy = BrowserProxy(["shop.test"], MemoryLog(), resolve=lambda host: addresses)
        try:
            with pytest.raises(OSError, match=said):
                proxy.open("shop.test", 80)
        finally:
            proxy.close()


def test_the_browser_s_own_housekeeping_is_never_the_agent_s_attempt() -> None:
    log = MemoryLog()
    proxy = BrowserProxy(["shop.test"], log)
    try:
        assert _through(proxy, f"http://{HOUSEKEEPING_HOST}/ListAccounts")[0] == 403
    finally:
        proxy.close()
    assert log.rows() == []


def test_a_refusal_is_the_agent_s_whether_or_not_a_client_attached_through_the_relay() -> None:
    # With no client, the browser asks only for its housekeeping name (the real-Chromium test measures it), so a
    # refusal while it runs is a client's, however that client reached the browser.
    log = MemoryLog()
    proxy = BrowserProxy(["shop.test"], log)
    try:
        _through(proxy, "http://www.example.com/")
    finally:
        proxy.close()
    assert [row["arguments"]["host"] for row in log.rows()] == ["www.example.com"]


def test_every_request_through_the_browser_s_proxy_but_its_housekeeping_says_the_browser_was_used(site) -> None:
    used: list[str] = []
    proxy = BrowserProxy(["shop.test"], MemoryLog(), used=lambda: used.append("used"), connect=_to(site))
    try:
        _through(proxy, f"http://{HOUSEKEEPING_HOST}/ListAccounts")
        assert used == []
        _through(proxy, "http://www.example.com/")
        assert used == ["used"]
        _through(proxy, "http://shop.test/")
        assert used == ["used", "used"]
    finally:
        proxy.close()


def test_a_blocked_browser_request_is_reported_and_never_counted_as_an_attempt(
    site,
) -> None:
    log = MemoryLog()
    proxy = BrowserProxy(
        ["shop.test"], log, connect=_to(site)
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


def test_a_browser_whose_proxy_cannot_be_made_leaves_no_relay_open(tmp_path, monkeypatch) -> None:
    made: list[DevToolsRelay] = []
    real = kit_browser.DevToolsRelay

    def relay(state_path):
        made.append(real(state_path))
        return made[-1]

    def no_proxy(*args, **kwargs):
        raise OSError("no port for the proxy")

    monkeypatch.setattr(kit_browser, "DevToolsRelay", relay)
    monkeypatch.setattr(kit_browser, "BrowserProxy", no_proxy)
    with pytest.raises(OSError, match="no port for the proxy"):
        _kit(tmp_path, [])
    [opened] = made
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", opened.port), timeout=1).close()


def test_closing_a_browser_whose_stop_fails_still_closes_its_relay_and_proxy(tmp_path) -> None:
    kit = _kit(tmp_path, [])
    kit.start()

    def broken(handle):
        raise OSError("the browser would not stop")

    kit._stop = broken
    ports = (kit.relay.port, int(kit.proxy.url.rsplit(":", 1)[1].strip("/")))
    with pytest.raises(OSError, match="would not stop"):
        kit.close()
    for port in ports:
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1).close()


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
                self.wfile.flush()
                # Held open, as a browser holds a client's websocket, until the client goes.
                try:
                    while self.connection.recv(4096):
                        pass
                except OSError:
                    pass
                self.close_connection = True
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
        assert json.loads((tmp_path / "browser.json").read_text()) == {"attached": 1, "requested": False}
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
        assert json.loads((tmp_path / "browser.json").read_text()) == {"attached": 0, "requested": False}
    finally:
        relay.close()


def test_the_relay_records_a_request_through_the_browser_s_proxy_once_until_the_next_browser(
    devtools, tmp_path
) -> None:
    state = tmp_path / "browser.json"
    relay = DevToolsRelay(state)
    try:
        relay.used()
        assert json.loads(state.read_text()) == {"attached": 0, "requested": False}
        relay.target(devtools.server_port)
        relay.used()
        relay.used()
        assert json.loads(state.read_text()) == {"attached": 0, "requested": True}
        relay.release()
        relay.used()
        relay.target(devtools.server_port)
        assert json.loads(state.read_text()) == {"attached": 0, "requested": False}
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


def test_a_trial_whose_browser_asked_for_a_site_with_no_client_on_the_relay_was_used(tmp_path) -> None:
    state = tmp_path / "browser.json"
    state.write_text(json.dumps({"attached": 0, "requested": True}))
    assert browser_notices(_runtime({"browser": {"state": "started", "state_path": str(state)}})) == []


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


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '{"attached": "yes"}'])
def test_a_browser_state_that_is_there_but_unreadable_is_not_taken_for_an_attached_client(tmp_path, content) -> None:
    state = tmp_path / "browser.json"
    state.write_text(content)
    notices = browser_notices(_runtime({"browser": {"state": "started", "state_path": str(state)}}))
    assert [one["code"] for one in notices] == [BROWSER_NOT_USED]


def test_a_browser_state_that_is_a_folder_is_not_taken_for_an_attached_client(tmp_path) -> None:
    (tmp_path / "browser.json").mkdir()
    notices = browser_notices(
        _runtime({"browser": {"state": "started", "state_path": str(tmp_path / "browser.json")}})
    )
    assert [one["code"] for one in notices] == [BROWSER_NOT_USED]


def test_sites_for_an_agent_not_read_as_a_browser_agent_say_no_browser_was_started() -> (
    None
):
    [notice] = browser_notices(_runtime({"browser": {"state": "not_started"}}))
    assert notice["code"] == BROWSER_NOT_STARTED


def test_a_world_with_no_browser_says_nothing() -> None:
    assert browser_notices(_runtime({})) == []


# A browser that stops, or never opens its port


class _Handle:
    def __init__(self, running: bool = True) -> None:
        self.running = running

    def is_running(self) -> bool:
        return self.running


def _held_kit(tmp_path, devtools, log):
    """A kit whose browser is the `devtools` stand-in, as a real start would leave it."""

    def launch(argv, cwd, env, log_path):
        (cwd / "DevToolsActivePort").write_text(f"{devtools.server_port}\n/devtools/browser/1\n")
        return _Handle()

    stopped: list = []
    kit = KitBrowser(
        binary="/opt/alk-chromium",
        sites=["shop.test"],
        log=log,
        directory=tmp_path / "browser",
        state_path=tmp_path / "state.json",
        launch=launch,
        stop=stopped.append,
    )
    return kit, stopped


def test_a_stopped_browser_s_refusals_are_never_the_next_trial_s(devtools, tmp_path) -> None:
    log = MemoryLog()
    kit, _ = _held_kit(tmp_path, devtools, log)
    try:
        kit.start()
        _upgrade(kit.relay.port)
        assert kit.relay.attached()
        kit.stop()
        # The last page, or the next browser's own start, asks for a site off the list before any client attaches.
        assert not kit.relay.attached()
        _through(kit.proxy, "http://update.vendor.example/")
    finally:
        kit.close()
    assert log.rows() == []
    # The trial that ended keeps its count until the next browser is ready, so it can still be read.
    assert json.loads((tmp_path / "state.json").read_text()) == {"attached": 1, "requested": False}


def test_a_browser_that_never_opens_its_port_is_stopped(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(kit_browser, "_START_SECONDS", 0.2)
    stopped: list = []
    handle = _Handle()
    kit = KitBrowser(
        binary="/opt/alk-chromium", sites=["shop.test"], log=MemoryLog(), directory=tmp_path / "browser",
        state_path=tmp_path / "state.json", launch=lambda *args: handle, stop=stopped.append,
    )
    try:
        with pytest.raises(RuntimeError, match="within"):
            kit.start()
        # Stopped by the failed start itself, before anything closes the kit.
        assert stopped == [handle]
    finally:
        kit.close()


@pytest.mark.parametrize("kind", ["fifo", "link"])
def test_a_port_file_that_is_not_a_plain_file_is_never_read(tmp_path, monkeypatch, kind) -> None:
    # The browser's own user can write its profile: a pipe there must not hold the start past its time limit, and a
    # link must not point the relay at a port of that user's choosing.
    monkeypatch.setattr(kit_browser, "_START_SECONDS", 0.3)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text("9333\n/devtools/browser/x\n")

    def launch(argv, cwd, env, log_path):
        if kind == "fifo":
            os.mkfifo(cwd / "DevToolsActivePort")
        else:
            (cwd / "DevToolsActivePort").symlink_to(elsewhere)
        return _Handle()

    kit = KitBrowser(
        binary="/opt/alk-chromium", sites=["shop.test"], log=MemoryLog(), directory=tmp_path / "browser",
        state_path=tmp_path / "state.json", launch=launch, stop=lambda handle: None,
    )
    outcome: list = []

    def start() -> None:
        try:
            kit.start()
        except RuntimeError as error:
            outcome.append(error)

    worker = threading.Thread(target=start, daemon=True)
    worker.start()
    worker.join(10)
    try:
        assert not worker.is_alive(), "the start waited on the port file"
        assert outcome and "within" in str(outcome[0])
    finally:
        if worker.is_alive():
            # Free the reader the start left waiting, so the test leaves nothing behind.
            fifo = next((tmp_path / "browser").glob("profile-*/DevToolsActivePort"))
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        kit.close()


def test_a_browser_that_exits_at_once_says_so_with_the_end_of_its_output(tmp_path) -> None:
    from fi.alk.harness.process_runtime import default_process_runner

    binary = tmp_path / "alk-chromium"
    binary.write_text(
        "#!/bin/sh\necho 'alk-chromium: error while loading shared libraries: libnss3.so: cannot open shared object "
        "file'\nexit 127\n"
    )
    binary.chmod(0o755)
    retained: list = []
    kit = KitBrowser(
        binary=str(binary), sites=["shop.test"], log=MemoryLog(), directory=tmp_path / "browser",
        state_path=tmp_path / "state.json",
        launch=lambda argv, cwd, env, log_path: default_process_runner(argv, cwd=cwd, env=env, log_path=log_path),
        stop=lambda handle: None, retain_log=retained.append,
    )
    try:
        with pytest.raises(RuntimeError) as raised:
            kit.start()
    finally:
        kit.close()
    message = str(raised.value)
    assert "exited" in message and "127" in message
    assert "libnss3" in message
    assert "seconds" not in message
    # The whole log is kept with the run's diagnostics before the next start removes it.
    assert [path.name for path in retained] == ["browser.log"]


# The relay's bounds


def test_the_relay_turns_away_a_client_past_its_limit_and_says_so(devtools, tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setattr(kit_browser, "_max_clients", lambda: 1)
    relay = DevToolsRelay(tmp_path / "browser.json")
    relay.target(devtools.server_port)
    try:
        with socket.create_connection(("127.0.0.1", relay.port), timeout=5) as held:
            held.sendall(
                b"GET /devtools/browser/1 HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
            )
            assert b" 101 " in held.recv(4096)
            with socket.create_connection(("127.0.0.1", relay.port), timeout=5) as second:
                second.sendall(b"GET /json/version HTTP/1.1\r\nHost: x\r\n\r\n")
                try:
                    answer = second.recv(4096)
                except ConnectionResetError:
                    answer = b""
                assert answer == b""
    finally:
        relay.close()
    assert "turned away" in caplog.text


def test_the_relay_closes_a_connection_idle_both_ways(devtools, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(kit_browser, "_tunnel_seconds", lambda: 0.3)
    relay = DevToolsRelay(tmp_path / "browser.json")
    relay.target(devtools.server_port)
    try:
        with socket.create_connection(("127.0.0.1", relay.port), timeout=5) as held:
            held.sendall(
                b"GET /devtools/browser/1 HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
            )
            assert b" 101 " in held.recv(4096)
            # Nothing either way: the relay lets it go, as the egress proxy does a tunnel.
            assert held.recv(4096) == b""
    finally:
        relay.close()


def test_a_relay_whose_browser_refuses_it_says_so(tmp_path, caplog) -> None:
    with socket.create_server(("127.0.0.1", 0)) as gone:
        port = gone.getsockname()[1]
    relay = DevToolsRelay(tmp_path / "browser.json")
    relay.target(port)
    try:
        with socket.create_connection(("127.0.0.1", relay.port), timeout=5) as client:
            client.sendall(b"GET /json/version HTTP/1.1\r\nHost: x\r\n\r\n")
            assert client.recv(4096) == b""
    finally:
        relay.close()
    assert "could not reach the browser" in caplog.text
