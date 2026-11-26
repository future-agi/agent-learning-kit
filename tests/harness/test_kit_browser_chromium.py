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
from urllib.parse import urlsplit

import pytest

from fi.alk.harness.browser_record import BrowserRecorder, sealed_record
from fi.alk.harness.kit_browser import KitBrowser, chromium_binary
from fi.alk.harness.world_door import MemoryLog

websockets = pytest.importorskip("websockets")
BINARY = chromium_binary()
pytestmark = pytest.mark.skipif(
    BINARY is None, reason="no Chromium: set ALK_CHROMIUM or put alk-chromium on PATH"
)


_PAGES = {
    "/form": """<html><body><h1>Shop</h1>
<button id=buy>Buy now</button>
<input id=q aria-label="Search" type=text>
<input id=pw name=password type=password>
</body></html>""",
    "/tracked": """<html><body>tracked<img src="http://tracker.example.net/pixel.gif"></body></html>""",
    "/forged": """<html><body>forged<button id=f>forged-click</button><input id=i aria-label=forged-typing><script>
for (const name of ['alkBrowserRecord', '__alkBrowserRecord']) {
  try { globalThis[name](JSON.stringify({kind: 'click', url: 'http://forged.example/'})); } catch (e) {}
}
setTimeout(() => {
  document.getElementById('f').click();
  document.getElementById('i').dispatchEvent(new InputEvent('input', {bubbles: true}));
}, 200);
</script></body></html>""",
    "/card": """<html><body>
<input id=name aria-label="Name on card" autocomplete="cc-name">
<input id=num aria-label="Card number" autocomplete="cc-number">
<input id=csc aria-label="Security code" autocomplete="section-a billing cc-csc">
<input id=otp aria-label="Code" autocomplete="one-time-code">
</body></html>""",
    "/card-switched": """<html><body><input id=num aria-label="Card number" autocomplete="cc-number">
<script>document.getElementById('num').setAttribute('autocomplete', 'off');</script></body></html>""",
    "/switched": """<html><body><input id=pw name=secret type=password>
<script>document.getElementById('pw').type = 'text';</script></body></html>""",
}


class _Page(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        content = _PAGES.get(self.path, f"<html><body>page {self.path}</body></html>").encode()
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
        recorder=BrowserRecorder(["shop.test"], root / "w0-record.json"),
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
        await self.call("Page.navigate", {"url": url})
        await asyncio.sleep(1.0)
        found = await self.call(
            "Runtime.evaluate",
            {"expression": "document.body ? document.body.innerText : ''"},
        )
        return str(found["result"]["result"].get("value"))

    async def click(self, selector: str) -> None:
        found = await self.call(
            "Runtime.evaluate",
            {
                "expression": f"(() => {{ const box = document.querySelector('{selector}').getBoundingClientRect();"
                " return [box.x + box.width / 2, box.y + box.height / 2]; })()",
                "returnByValue": True,
            },
        )
        x, y = found["result"]["result"]["value"]
        for kind in ("mousePressed", "mouseReleased"):
            await self.call(
                "Input.dispatchMouseEvent",
                {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1},
            )

    async def type(self, text: str) -> None:
        for character in text:
            await self.call("Input.dispatchKeyEvent", {"type": "keyDown", "text": character, "key": character})
            await self.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": character})


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


def test_a_browser_no_client_holds_asks_for_nothing_on_its_own(tmp_path) -> None:
    # With no client, nothing reaches the proxy but the browser's own housekeeping, so a refusal while the browser
    # runs is always the work of a client, however it reached the browser.
    log = MemoryLog()
    kit = KitBrowser(
        binary=BINARY,
        sites=["shop.test"],
        log=log,
        directory=tmp_path / "w0",
        state_path=tmp_path / "w0.json",
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


def test_the_browser_starts_through_the_kit_s_own_process_runner(tmp_path) -> None:
    from fi.alk.harness import process_runtime as pr

    def launch(argv, cwd, env, log_path):
        return pr.default_process_runner(argv, cwd=cwd, env=env, log_path=log_path)

    kit = KitBrowser(
        binary=BINARY,
        sites=["shop.test"],
        log=MemoryLog(),
        directory=tmp_path / "w0",
        state_path=tmp_path / "w0.json",
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


# The world's record of the browser


def _settled(kit) -> dict:
    asyncio.run(asyncio.sleep(0.5))
    return kit.recorder.record.body()


def test_what_the_agent_clicked_and_typed_is_kept_and_a_password_never_is(browser) -> None:
    kit, _ = browser

    async def steps(page):
        await page.open("http://shop.test/form")
        await page.click("#buy")
        await page.click("#q")
        await page.type("red shoes")
        await page.click("#pw")
        await page.type("hunter2")

    _run(kit, steps)
    body = _settled(kit)
    rows = [row for row in body["rows"] if row["url"] == "http://shop.test/form"]
    assert [row["target"] for row in rows if row["kind"] == "click"][0] == {"tag": "button", "text": "Buy now"}
    typed = [row for row in rows if row["kind"] == "typing"]
    assert [(row["target"].get("label"), row["secret"], row["text"]) for row in typed] == [
        ("Search", False, "red shoes"),
        ("password", True, None),
    ]
    assert "hunter2" not in json.dumps(body)
    sealed = sealed_record(kit.recorder.record_path, timeout=5)
    assert sealed["complete"] is True and len(sealed["rows"]) == len(body["rows"])
    end = sealed["screenshots"][-1]
    assert (end["when"], end["url"]) == ("end", "http://shop.test/form")
    assert (kit.recorder.shots_dir / end["file"]).read_bytes()[:2] == b"\xff\xd8"
    assert any(row["kind"] == "navigation" and row["url"] == "http://shop.test/form" for row in body["rows"])


def test_a_refused_page_is_kept_whole_and_a_page_s_refused_requests_are_counted(browser) -> None:
    kit, _ = browser

    async def steps(page):
        await page.open("http://shop.test/tracked")
        await page.open("http://www.example.com/deals")

    _run(kit, steps)
    body = _settled(kit)
    assert "http://www.example.com/deals" in [one["url"] for one in body["blocked_navigations"]]
    assert "tracker.example.net" in [one["host"] for one in body["blocked_page_requests"]]
    assert not any(one["url"].startswith("http://tracker.") for one in body["blocked_navigations"])
    _no_page_of(body, "www.example.com")


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_a_refused_page_is_neither_a_page_opened_nor_photographed(site, tmp_path, scheme) -> None:
    # Refused over http, the proxy's own answer is shown at the refused address; over https, the browser's error
    # page. Neither is a page the agent opened: no row, no screenshot, and the end screenshot is not of it.
    # A browser of its own: the module's record is sealed by an earlier test, and a sealed record keeps no more.
    refused = f"{scheme}://www.example.com/deals"

    def connect(host: str, port: int) -> socket.socket:
        if host == "shop.test":
            return socket.create_connection(("127.0.0.1", site.server_port), timeout=5)
        raise OSError(f"no route to {host}")

    kit = KitBrowser(
        binary=BINARY,
        sites=["shop.test"],
        log=MemoryLog(),
        directory=tmp_path / "w0",
        state_path=tmp_path / "w0.json",
        launch=_launch,
        stop=_stop,
        connect=connect,
        recorder=BrowserRecorder(["shop.test"], tmp_path / "w0-record.json"),
    )
    kit.start()
    try:

        async def steps(page):
            await page.open("http://shop.test/form")
            await page.open(refused)

        _run(kit, steps)
        body = _settled(kit)
        sealed = sealed_record(kit.recorder.record_path, timeout=5)
    finally:
        kit.close()
    assert refused in [one["url"] for one in body["blocked_navigations"]]
    # The page before it was photographed, so a screenshot of the refused page would have been kept.
    assert [(one["when"], one["url"]) for one in sealed["screenshots"] if one["when"] == "load"] == [
        ("load", "http://shop.test/form")
    ]
    _no_page_of(body, "www.example.com")
    _no_page_of(sealed, "www.example.com")


def _no_page_of(body: dict, host: str) -> None:
    def refused(url: str) -> bool:
        return url.startswith("chrome-error:") or urlsplit(url).hostname == host

    assert not [row for row in body["rows"] if refused(row["url"])]
    assert not [one for one in body["screenshots"] if refused(one["url"])]


def test_a_page_s_own_script_cannot_write_the_record(browser) -> None:
    kit, _ = browser

    async def steps(page):
        await page.open("http://shop.test/forged")

    _run(kit, steps)
    rows = _settled(kit)["rows"]
    assert not any(urlsplit(row["url"]).hostname == "forged.example" for row in rows)
    # Only what the browser itself was made to do: a page's own click() or made-up event is not the agent's.
    assert not any(row.get("target", {}).get("text") == "forged-click" for row in rows)
    assert not any(row.get("target", {}).get("label") == "forged-typing" for row in rows)


def test_a_password_field_the_page_turns_into_text_stays_a_password_field(browser) -> None:
    kit, _ = browser

    async def steps(page):
        await page.open("http://shop.test/switched")
        await page.click("#pw")
        await page.type("hunter2")

    _run(kit, steps)
    body = _settled(kit)
    typed = [row for row in body["rows"] if row["kind"] == "typing" and row["url"] == "http://shop.test/switched"]
    assert typed and all(row["secret"] is True and row["text"] is None for row in typed)
    assert "hunter2" not in json.dumps(body)


def test_a_tab_is_let_go_even_when_the_recorder_cannot_watch_it(site, tmp_path) -> None:
    class _Failing(BrowserRecorder):
        def _command(self, method, params=None, session=None, **kwargs):
            if method == "Page.addScriptToEvaluateOnNewDocument":
                raise ConnectionError("lost")
            super()._command(method, params, session, **kwargs)

    def connect(host: str, port: int) -> socket.socket:
        return socket.create_connection(("127.0.0.1", site.server_port), timeout=5)

    kit = KitBrowser(
        binary=BINARY,
        sites=["shop.test"],
        log=MemoryLog(),
        directory=tmp_path / "w0",
        state_path=tmp_path / "w0.json",
        launch=_launch,
        stop=_stop,
        connect=connect,
        recorder=_Failing(["shop.test"], tmp_path / "w0-record.json"),
    )
    kit.start()
    try:

        async def steps(page):
            return await page.open("http://shop.test/cart")

        assert "page /cart" in _run(kit, steps)
        assert kit.recorder.record.body()["complete"] is False
    finally:
        kit.close()
    assert json.loads((tmp_path / "w0-record.json").read_text())["complete"] is False


def test_card_numbers_security_codes_and_one_time_codes_are_never_kept_as_text(browser) -> None:
    kit, _ = browser

    async def steps(page):
        await page.open("http://shop.test/card")
        for field, text in (("#name", "Pat Lee"), ("#num", "4111111111111111"), ("#csc", "737"), ("#otp", "482913")):
            await page.click(field)
            await page.type(text)

    _run(kit, steps)
    body = _settled(kit)
    typed = [row for row in body["rows"] if row["kind"] == "typing" and row["url"] == "http://shop.test/card"]
    assert [(row["target"].get("label"), row["secret"], row["text"]) for row in typed] == [
        ("Name on card", False, "Pat Lee"),
        ("Card number", True, None),
        ("Security code", True, None),
        ("Code", True, None),
    ]
    # Times are left out: a short code can be part of one.
    held = json.dumps([{key: value for key, value in row.items() if key != "at"} for row in body["rows"]])
    for kept in ("4111111111111111", "737", "482913"):
        assert kept not in held


def test_a_card_field_the_page_stops_marking_stays_one(browser) -> None:
    kit, _ = browser

    async def steps(page):
        await page.open("http://shop.test/card-switched")
        await page.click("#num")
        await page.type("4111111111111111")

    _run(kit, steps)
    body = _settled(kit)
    typed = [row for row in body["rows"] if row["kind"] == "typing" and row["url"] == "http://shop.test/card-switched"]
    assert typed and all(row["secret"] is True and row["text"] is None for row in typed)
    assert "4111111111111111" not in json.dumps(body)


def test_a_world_the_agent_makes_under_the_recorder_s_old_name_cannot_write_the_record(browser) -> None:
    # The recorder's world and binding names are its own, new for each browser: a world the agent makes with the
    # name the recorder once had is just the agent's, and what it sends is never a row.
    kit, _ = browser

    async def steps(page):
        await page.open("http://shop.test/form")
        tree = await page.call("Page.getFrameTree")
        frame = tree["result"]["frameTree"]["frame"]["id"]
        world = await page.call("Page.createIsolatedWorld", {"frameId": frame, "worldName": "alk-browser-record"})
        row = {"kind": "click", "url": "http://forged.example/", "target": {"text": "forged-world"}}
        await page.call(
            "Runtime.evaluate",
            {
                "expression": f"try {{ alkBrowserRecord({json.dumps(json.dumps(row))}) }} catch (e) {{}}",
                "contextId": world["result"]["executionContextId"],
            },
        )

    _run(kit, steps)
    rows = _settled(kit)["rows"]
    assert not [row for row in rows if "forged.example" in row["url"] or "forged-world" in json.dumps(row)]
    assert kit.recorder.binding != "alkBrowserRecord" and kit.recorder.listener_world != "alk-browser-record"
