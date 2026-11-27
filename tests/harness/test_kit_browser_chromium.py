"""The kit's browser against a real Chromium: each condition its bound rests on, tried.

Skipped where no Chromium is found (``ALK_CHROMIUM``, or ``alk-chromium`` on the path, as in the
hosted image).
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request

import pytest

from fi.alk.harness.kit_browser import KitBrowser, chromium_binary
from fi.alk.harness.world_door import MemoryLog

websockets = pytest.importorskip("websockets")
BINARY = chromium_binary()
pytestmark = pytest.mark.skipif(
    BINARY is None, reason="no Chromium: set ALK_CHROMIUM or put alk-chromium on PATH"
)


class _Page(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        content = f"<html><body>page {self.path}</body></html>".encode()
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("content-length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


@pytest.fixture(scope="module")
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()


def _launch(argv, cwd, env, log_path):
    log = open(log_path, "ab")
    try:
        return subprocess.Popen(
            argv, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT
        )
    finally:
        log.close()


def _stop(handle) -> None:
    handle.kill()
    handle.wait(10)


@pytest.fixture(scope="module")
def browser(site, tmp_path_factory):
    """One browser for the module, allowed `shop.test` only, which its proxy reaches on the local page server."""

    def connect(host: str, port: int) -> socket.socket:
        if host == "shop.test":
            return socket.create_connection(("127.0.0.1", site.server_port), timeout=5)
        raise OSError(f"no route to {host}")

    root = tmp_path_factory.mktemp("browser")
    log = MemoryLog()
    kit = KitBrowser(
        binary=BINARY,
        sites=["shop.test"],
        log=log,
        directory=root / "w0",
        state_path=root / "w0.json",
        launch=_launch,
        stop=_stop,
        connect=connect,
    )
    kit.start()
    try:
        yield kit, log
    finally:
        kit.close()


class _Tab:
    """One page of the browser, driven over the DevTools protocol through the relay, as an agent would."""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.next = 0
        self.session = None

    async def call(
        self, method: str, params: dict | None = None, *, session: bool = True
    ) -> dict:
        self.next += 1
        message = {"id": self.next, "method": method, "params": params or {}}
        if session and self.session:
            message["sessionId"] = self.session
        await self.ws.send(json.dumps(message))
        while True:
            answer = json.loads(await self.ws.recv())
            if answer.get("id") == self.next:
                return answer

    async def open(self, url: str) -> str:
        """The page's text once it has loaded and shows some, or as it stands after 10 s: a cold browser on a CI
        runner can take longer than the second a page takes here."""
        await self.call("Page.navigate", {"url": url})
        await asyncio.sleep(1.0)
        deadline = time.monotonic() + 10
        while True:
            found = await self.call(
                "Runtime.evaluate",
                {
                    "expression": "[document.readyState, document.body ? document.body.innerText : '']",
                    "returnByValue": True,
                },
            )
            state, text = found["result"]["result"].get("value") or ["", ""]
            if (state == "complete" and text) or time.monotonic() >= deadline:
                return str(text)
            await asyncio.sleep(0.25)


async def _drive(cdp_url: str, steps):
    with request.urlopen(f"{cdp_url}/json/version", timeout=5) as answer:
        address = json.loads(answer.read())["webSocketDebuggerUrl"]
    async with websockets.connect(address, max_size=None) as ws:
        page = _Tab(ws)
        target = await page.call(
            "Target.createTarget", {"url": "about:blank"}, session=False
        )
        attached = await page.call(
            "Target.attachToTarget",
            {"targetId": target["result"]["targetId"], "flatten": True},
            session=False,
        )
        page.session = attached["result"]["sessionId"]
        return await steps(page)


def _run(kit, steps):
    return asyncio.run(_drive(kit.relay.url, steps))


def test_the_browser_opens_its_site_and_refuses_another_which_is_logged_as_the_agent_s(
    browser,
) -> None:
    kit, log = browser
    before = len(log.rows())

    async def steps(page):
        return await page.open("http://shop.test/cart"), await page.open(
            "http://www.example.com/"
        )

    shop, other = _run(kit, steps)
    assert "page /cart" in shop
    assert "outside the test world" in other
    hosts = [row["arguments"]["host"] for row in log.rows()[before:]]
    assert hosts.count("www.example.com") >= 1
    assert {row["arguments"]["via"] for row in log.rows()[before:]} == {"browser"}


def test_a_page_on_loopback_cannot_be_opened_from_the_browser(browser, site) -> None:
    kit, _ = browser

    async def steps(page):
        return [
            await page.open(f"http://127.0.0.1:{site.server_port}/secret"),
            await page.open(f"http://localhost:{site.server_port}/secret"),
        ]

    for shown in _run(kit, steps):
        assert "page /secret" not in shown


def test_an_idle_browser_asks_for_nothing_the_agent_would_be_blamed_for(
    browser,
) -> None:
    kit, log = browser
    before = len(log.rows())

    async def steps(page):
        await page.open("http://shop.test/")
        await asyncio.sleep(10)

    _run(kit, steps)
    assert [
        (row["arguments"]["host"], row["arguments"]["method"])
        for row in log.rows()[before:]
    ] == []


@pytest.fixture
def short_dir(tmp_path_factory):
    """A folder of its own with a short path: a test's own tmp_path on a Linux runner leaves too little room for
    Chromium's socket under the profile."""
    return tmp_path_factory.mktemp("kb")


def test_a_browser_no_client_holds_asks_for_nothing_on_its_own(short_dir) -> None:
    # With no client, nothing reaches the proxy but the browser's own housekeeping, so a refusal while the browser
    # runs is always the work of a client, however it reached the browser.
    log = MemoryLog()
    kit = KitBrowser(
        binary=BINARY,
        sites=["shop.test"],
        log=log,
        directory=short_dir / "w0",
        state_path=short_dir / "w0.json",
        launch=_launch,
        stop=_stop,
        connect=lambda host, port: (_ for _ in ()).throw(OSError(f"no route to {host}")),
    )
    kit.start()
    try:
        time.sleep(15)
    finally:
        kit.close()
    assert log.rows() == []


def test_each_start_is_a_browser_that_remembers_nothing_at_the_same_address(
    browser,
) -> None:
    kit, _ = browser

    async def set_cookie(page):
        await page.open("http://shop.test/")
        await page.call(
            "Runtime.evaluate",
            {"expression": "document.cookie = 'seen=1; max-age=3600'"},
        )
        found = await page.call("Runtime.evaluate", {"expression": "document.cookie"})
        return found["result"]["result"]["value"]

    async def read_cookie(page):
        await page.open("http://shop.test/")
        found = await page.call("Runtime.evaluate", {"expression": "document.cookie"})
        return found["result"]["result"]["value"]

    assert _run(kit, set_cookie) == "seen=1"
    address = kit.relay.url
    kit.start()
    assert kit.relay.url == address
    assert not kit.relay.attached()
    assert _run(kit, read_cookie) == ""
    assert kit.relay.attached()


def test_the_browser_starts_through_the_kit_s_own_process_runner(short_dir) -> None:
    from fi.alk.harness import process_runtime as pr

    def launch(argv, cwd, env, log_path):
        return pr.default_process_runner(argv, cwd=cwd, env=env, log_path=log_path)

    kit = KitBrowser(
        binary=BINARY,
        sites=["shop.test"],
        log=MemoryLog(),
        directory=short_dir / "w0",
        state_path=short_dir / "w0.json",
        launch=launch,
        stop=lambda handle: pr._terminate_and_wait(handle),
    )
    try:
        kit.start()
        port = kit.relay.port
        with request.urlopen(f"{kit.relay.url}/json/version", timeout=5) as answer:
            version = json.loads(answer.read())
    finally:
        kit.close()
    assert version["webSocketDebuggerUrl"].startswith(f"ws://127.0.0.1:{port}/")
