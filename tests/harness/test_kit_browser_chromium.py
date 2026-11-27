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
    # Masked by the page itself: dots on screen, though not a password field.
    "/masked": """<html><body>
<input id=pin aria-label="PIN" type=text autocomplete=off style="-webkit-text-security: disc">
<input id=note aria-label="Note" type=text>
</body></html>""",
    # A one-time code split over one-character boxes, the code's autofill name on the first box only.
    "/otp-boxes": """<html><body><form><div>
<input id=b0 maxlength=1 autocomplete="one-time-code"><input id=b1 maxlength=1><input id=b2 maxlength=1>
<input id=b3 maxlength=1><input id=b4 maxlength=1><input id=b5 maxlength=1>
</div><input id=q aria-label="Search" maxlength=1></form></body></html>""",
    # One shop page for the client runs: a button, a search field, and a button that opens a popup (a new tab the
    # client is told of and waits on).
    "/journey": """<html><body><h1>Shop</h1>
<button id=buy>Buy now</button>
<input id=q aria-label="Search" type=text>
<button id=open onclick="window.open('/popped', '_blank')">Open offers</button>
</body></html>""",
    "/script-off": """<html><body>moving<script>
setTimeout(() => { location.href = 'http://pagemade.example/landing'; }, 100);
</script></body></html>""",
    "/link-off": """<html><body><a id=go href="http://agentmade.example/clicked">go</a></body></html>""",
    # A single-page app's button: its own handler moves the page when the agent clicks it.
    "/button-off": """<html><body><button id=go onclick="location.href='http://clickscript.example/x'">Go</button>
</body></html>""",
    # The page types into its own field through the editing command, which fires a trusted input event.
    "/forge-typing": """<html><body><input id=s aria-label="Search"><script>
setTimeout(() => { const s = document.getElementById('s'); s.focus();
  document.execCommand('insertText', false, 'page-forged-words'); }, 100);
</script></body></html>""",
    # Split codes as hand-built widgets make them: no box named, or the named one's neighbours unbounded, or each
    # box wrapped deep. And a short form that is no code.
    "/otp-unnamed": """<html><body><div class=otp>""" + "".join(
        f"<input id=b{i} inputmode=numeric maxlength=1>" for i in range(6)
    ) + """</div></body></html>""",
    "/otp-unbounded": """<html><body><div class=otp>""" + "".join(
        f"<input id=b{i} inputmode=numeric" + (' autocomplete="one-time-code"' if i == 0 else "") + ">" for i in range(6)
    ) + """</div></body></html>""",
    "/otp-deep": """<html><body><div class=otp>""" + "".join(
        f"<span><span><span><input id=b{i} inputmode=numeric maxlength=1"
        + (' autocomplete="one-time-code"' if i == 0 else "") + "></span></span></span>" for i in range(6)
    ) + """</div></body></html>""",
    "/short-form": """<html><body><form><input id=b0 aria-label="Name"><input id=b1 aria-label="Town">
<input id=b2 aria-label="Street"><input id=b3 aria-label="Code" autocomplete="one-time-code"></form></body></html>""",
    "/meta-off": """<html><head><meta http-equiv=refresh content="0;url=http://metamade.example/landing"></head>
<body>moving</body></html>""",
    # A button whose handler moves the page only after a slow step, once the browser's input mark has lapsed.
    "/button-slow-off": """<html><body><button id=go
onclick="setTimeout(() => { location.href = 'http://slowclick.example/x'; }, 6500)">Pay</button></body></html>""",
    # A button whose handler opens a window first, which uses up the browser's input mark, then moves the page.
    "/button-popup-off": """<html><body><button id=go
onclick="window.open('/popped', '_blank'); location.href = 'http://popclick.example/x'">Go</button></body></html>""",
    "/meta-fragment-off": """<html><head><meta http-equiv=refresh content="0;url=http://metafragment.example/x#part">
</head><body>moving</body></html>""",
    "/card-switched": """<html><body><script>
const num = document.createElement('input'); num.id = 'num'; num.setAttribute('aria-label', 'Card number');
num.setAttribute('autocomplete', 'cc-number'); document.body.appendChild(num); num.setAttribute('autocomplete', 'off');
</script></body></html>""",
    # Added and changed in one task, so the listener first sees the field already changed: only the change's old
    # value says it was a secret field.
    "/switched": """<html><body><script>
const pw = document.createElement('input'); pw.id = 'pw'; pw.name = 'secret'; pw.type = 'password';
document.body.appendChild(pw); pw.type = 'text';
</script></body></html>""",
}


class _Page(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/redirect-off":
            self.send_response(302)
            self.send_header("location", "http://pagemade.example/after-redirect")
            self.send_header("content-length", "0")
            self.end_headers()
            return
        if self.path == "/header-refresh-off":
            content = b"<html><body>moving</body></html>"
            self.send_response(200)
            self.send_header("refresh", "0;url=http://metamade.example/by-header")
            self.send_header("content-type", "text/html")
            self.send_header("content-length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        if self.path.startswith("/fill/"):
            token = self.path.removeprefix("/fill/")
            page = f'<html><body><input id=f aria-label="Field" autocomplete="{token}"></body></html>'
        else:
            page = _PAGES.get(self.path, f"<html><body>page {self.path}</body></html>")
        content = page.encode()
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
        # What the browser sent this client besides answers: its events.
        self.events: list[dict] = []
        self.last: list[str] = []

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
            if "method" in answer:
                self.events.append(answer)

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
                    "expression": "[document.readyState, document.body ? document.body.innerText : '',"
                    " location.href, document.documentElement ? document.documentElement.outerHTML.slice(0, 600) : '']",
                    "returnByValue": True,
                },
            )
            state, text, *where = found["result"]["result"].get("value") or ["", "", "", ""]
            # Where the tab ended, for a failure's message: its address, its state and the start of its page.
            self.last = [*where[:1], state, *where[1:]]
            if (state == "complete" and text) or time.monotonic() >= deadline:
                return str(text)
            await asyncio.sleep(0.25)

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
        ), page.last

    shop, other, where = _run(kit, steps)
    assert "page /cart" in shop
    # The proxy refuses with its own 403 page; Chromium as the hosted image pins it shows its own error page for it
    # instead (an older build shows the proxy's text). Either way the agent's page is not the site's.
    assert "outside the test world" in other or where[:1] == ["chrome-error://chromewebdata/"], where
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


def test_a_browser_under_a_work_folder_longer_than_a_socket_s_path_starts(tmp_path) -> None:
    # Chromium's socket goes under its temporary folder, which is the kit's own and short, whatever the work folder.
    directory = tmp_path / ("a_long_work_folder_" * 6) / "worlds" / "w0" / ".kit-browser"
    assert len(str(directory)) > 107
    kit = KitBrowser(
        binary=BINARY,
        sites=["shop.test"],
        log=MemoryLog(),
        directory=directory,
        state_path=tmp_path / "w0.json",
        launch=_launch,
        stop=_stop,
        connect=lambda host, port: (_ for _ in ()).throw(OSError(f"no route to {host}")),
    )
    try:
        kit.start()
        assert kit._handle.poll() is None
        with request.urlopen(f"{kit.relay.url}/json/version", timeout=10) as answer:
            assert "Browser" in json.loads(answer.read())
    finally:
        kit.close()


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


def test_a_page_s_own_redirects_off_the_list_are_its_requests_not_pages_the_agent_tried(browser) -> None:
    kit, _ = browser

    async def steps(page):
        await page.open("http://shop.test/redirect-off")
        await page.open("http://shop.test/script-off")
        await asyncio.sleep(1.5)

    _run(kit, steps)
    body = _settled(kit)
    assert not [one for one in body["blocked_navigations"] if "pagemade.example" in one["url"]]
    assert {"host": "pagemade.example", "count": 2} in body["blocked_page_requests"]


def test_what_the_agent_opens_off_the_list_is_its_attempt_and_the_refusal_s_icon_is_no_page_request(browser) -> None:
    kit, _ = browser

    async def steps(page):
        await page.open("http://shop.test/link-off")
        await page.click("#go")
        await asyncio.sleep(1)
        await page.open("http://agentmade.example/typed")
        await asyncio.sleep(1)

    _run(kit, steps)
    body = _settled(kit)
    tried = [one["url"] for one in body["blocked_navigations"] if "agentmade.example" in one["url"]]
    assert tried == ["http://agentmade.example/clicked", "http://agentmade.example/typed"]
    # The browser asks a refused host for its icon to show beside the refusal: neither the agent's nor a page's.
    assert not [one for one in body["blocked_page_requests"] if one["host"] == "agentmade.example"]


def test_a_button_the_agent_clicks_that_moves_the_page_by_script_is_the_agent_s_attempt(browser) -> None:
    kit, _ = browser

    async def steps(page):
        await page.open("http://shop.test/button-off")
        await page.click("#go")
        await asyncio.sleep(1)

    _run(kit, steps)
    body = _settled(kit)
    tried = [one["url"] for one in body["blocked_navigations"] if "clickscript.example" in one["url"]]
    assert tried == ["http://clickscript.example/x"]
    assert not [one for one in body["blocked_page_requests"] if one["host"] == "clickscript.example"]


def test_a_button_the_agent_clicks_that_moves_the_page_after_a_slow_step_is_the_agent_s_attempt(browser) -> None:
    kit, _ = browser

    async def steps(page):
        await page.open("http://shop.test/button-slow-off")
        await page.click("#go")
        await asyncio.sleep(8)

    _run(kit, steps)
    body = _settled(kit)
    tried = [one["url"] for one in body["blocked_navigations"] if "slowclick.example" in one["url"]]
    assert tried == ["http://slowclick.example/x"]
    assert not [one for one in body["blocked_page_requests"] if one["host"] == "slowclick.example"]


def test_a_button_the_agent_clicks_that_opens_a_window_then_moves_the_page_is_the_agent_s_attempt(browser) -> None:
    kit, _ = browser

    async def steps(page):
        await page.open("http://shop.test/button-popup-off")
        await page.click("#go")
        await asyncio.sleep(1.5)

    _run(kit, steps)
    body = _settled(kit)
    tried = [one["url"] for one in body["blocked_navigations"] if "popclick.example" in one["url"]]
    assert tried == ["http://popclick.example/x"]
    assert not [one for one in body["blocked_page_requests"] if one["host"] == "popclick.example"]


def test_a_page_that_refreshes_itself_to_an_address_with_a_fragment_makes_its_own_request(browser) -> None:
    kit, _ = browser

    async def steps(page):
        await page.open("http://shop.test/meta-fragment-off")
        await asyncio.sleep(1.5)

    _run(kit, steps)
    body = _settled(kit)
    assert not [one for one in body["blocked_navigations"] if "metafragment.example" in one["url"]]
    assert {"host": "metafragment.example", "count": 1} in body["blocked_page_requests"]


def test_a_page_that_refreshes_itself_off_the_list_makes_its_own_request(browser) -> None:
    kit, _ = browser

    async def steps(page):
        await page.open("http://shop.test/meta-off")
        await asyncio.sleep(1.5)
        await page.open("http://shop.test/header-refresh-off")
        await asyncio.sleep(1.5)

    _run(kit, steps)
    body = _settled(kit)
    assert not [one for one in body["blocked_navigations"] if "metamade.example" in one["url"]]
    assert {"host": "metamade.example", "count": 2} in body["blocked_page_requests"]


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_a_refused_page_is_neither_a_page_opened_nor_photographed(site, short_dir, scheme) -> None:
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
        directory=short_dir / "w0",
        state_path=short_dir / "w0.json",
        launch=_launch,
        stop=_stop,
        connect=connect,
        recorder=BrowserRecorder(["shop.test"], short_dir / "w0-record.json"),
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


def test_a_tab_is_let_go_even_when_the_recorder_cannot_watch_it(site, short_dir) -> None:
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
        directory=short_dir / "w0",
        state_path=short_dir / "w0.json",
        launch=_launch,
        stop=_stop,
        connect=connect,
        recorder=_Failing(["shop.test"], short_dir / "w0-record.json"),
    )
    kit.start()
    try:

        async def steps(page):
            return await page.open("http://shop.test/cart")

        assert "page /cart" in _run(kit, steps)
        assert kit.recorder.record.body()["complete"] is False
    finally:
        kit.close()
    assert json.loads((short_dir / "w0-record.json").read_text())["complete"] is False


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


# The autofill names the HTML spec gives secrets: written out here, so one dropped from the kit's list is caught.
_SECRET_FILLS = (
    "current-password", "new-password", "one-time-code",
    "cc-number", "cc-csc", "cc-exp", "cc-exp-month", "cc-exp-year",
)


@pytest.mark.parametrize("token", _SECRET_FILLS)
def test_a_field_with_any_secret_autofill_name_is_never_kept_as_text(browser, token) -> None:
    kit, _ = browser
    url = f"http://shop.test/fill/{token}"

    async def steps(page):
        await page.open(url)
        await page.click("#f")
        await page.type("MADEUP-4821")

    _run(kit, steps)
    body = _settled(kit)
    typed = [row for row in body["rows"] if row["kind"] == "typing" and row["url"] == url]
    assert typed and all(row["secret"] is True and row["text"] is None for row in typed)
    assert "MADEUP-4821" not in json.dumps(body)


def test_a_field_the_page_masks_itself_is_never_kept_as_text(browser) -> None:
    kit, _ = browser

    async def steps(page):
        await page.open("http://shop.test/masked")
        for field, text in (("#pin", "MADEUP-PIN-4821"), ("#note", "plain words")):
            await page.click(field)
            await page.type(text)

    _run(kit, steps)
    body = _settled(kit)
    typed = [row for row in body["rows"] if row["kind"] == "typing" and row["url"] == "http://shop.test/masked"]
    assert [(row["target"].get("label"), row["secret"], row["text"]) for row in typed] == [
        ("PIN", True, None),
        ("Note", False, "plain words"),
    ]
    assert "MADEUP-PIN-4821" not in json.dumps(body)


def test_every_box_of_a_split_one_time_code_is_never_kept_as_text(browser) -> None:
    kit, _ = browser

    async def steps(page):
        await page.open("http://shop.test/otp-boxes")
        for index, digit in enumerate("731904"):
            await page.click(f"#b{index}")
            await page.type(digit)

    _run(kit, steps)
    body = _settled(kit)
    typed = [row for row in body["rows"] if row["kind"] == "typing" and row["url"] == "http://shop.test/otp-boxes"]
    assert len(typed) >= 6 and all(row["secret"] is True and row["text"] is None for row in typed)
    held = json.dumps([{key: value for key, value in row.items() if key != "at"} for row in body["rows"]])
    for digit in "731904":
        assert f'"text": "{digit}"' not in held


@pytest.mark.parametrize("path", ["/otp-unnamed", "/otp-unbounded", "/otp-deep"])
def test_a_split_code_built_by_hand_is_never_kept_as_text(browser, path) -> None:
    kit, _ = browser

    async def steps(page):
        await page.open(f"http://shop.test{path}")
        for index, digit in enumerate("582613"):
            await page.click(f"#b{index}")
            await page.type(digit)

    _run(kit, steps)
    body = _settled(kit)
    typed = [row for row in body["rows"] if row["kind"] == "typing" and row["url"] == f"http://shop.test{path}"]
    assert len(typed) >= 6 and all(row["secret"] is True and row["text"] is None for row in typed), typed


def test_a_short_form_beside_a_code_field_keeps_its_other_fields_text(browser) -> None:
    kit, _ = browser

    async def steps(page):
        await page.open("http://shop.test/short-form")
        await page.click("#b0")
        await page.type("Ada")
        await page.click("#b1")
        await page.type("Leeds")

    _run(kit, steps)
    body = _settled(kit)
    typed = [row["text"] for row in body["rows"] if row["kind"] == "typing" and row["url"] == "http://shop.test/short-form"]
    assert "Ada" in typed and "Leeds" in typed


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


def test_typing_sent_as_input_is_kept_and_an_action_made_by_script_is_not(browser) -> None:
    # The limit HOW-IT-WORKS states: only trusted events are rows. A fill sent as input is one; a click or typing a
    # script makes inside the page is not, though the page itself is kept.
    kit, _ = browser

    async def steps(page):
        await page.open("http://shop.test/form")
        await page.click("#q")
        await page.call("Input.insertText", {"text": "blue hats"})
        await page.call(
            "Runtime.evaluate",
            {
                "expression": "document.getElementById('buy').click();"
                " const q = document.getElementById('q'); q.value = 'scripted words';"
                " q.dispatchEvent(new Event('input', {bubbles: true}));"
            },
        )

    _run(kit, steps)
    rows = [row for row in _settled(kit)["rows"] if row["url"] == "http://shop.test/form"]
    assert [row["text"] for row in rows if row["kind"] == "typing"][-1] == "blue hats"
    assert not [row for row in rows if "scripted words" in json.dumps(row)]
    assert any(row["kind"] == "navigation" for row in rows)


def test_a_page_that_types_into_its_own_field_writes_no_typing_row(browser) -> None:
    kit, _ = browser

    async def steps(page):
        await page.open("http://shop.test/forge-typing")
        await asyncio.sleep(1)
        await page.open("http://shop.test/form")
        await page.click("#q")
        await page.type("ok")

    _run(kit, steps)
    rows = _settled(kit)["rows"]
    assert not [row for row in rows if "page-forged-words" in json.dumps(row)]
    # The agent's own keys are still kept.
    assert [row["text"] for row in rows if row["kind"] == "typing" and row["url"] == "http://shop.test/form"][-1] == "ok"


def test_an_agent_that_looks_through_every_world_of_its_page_cannot_write_the_record(browser) -> None:
    # The agent holds the page through DevTools: it is told of every script world, the recorder's among them. In each
    # world not its own it calls every function a fresh world does not have, with a made-up row. None is a row.
    kit, _ = browser

    async def steps(page):
        await page.open("http://shop.test/form")
        await page.call("Runtime.enable")
        await page.call("Runtime.evaluate", {"expression": "1"})
        tree = await page.call("Page.getFrameTree")
        frame = tree["result"]["frameTree"]["frame"]["id"]
        fresh = await page.call("Page.createIsolatedWorld", {"frameId": frame, "worldName": "agent-own"})
        own = fresh["result"]["executionContextId"]
        names = await page.call(
            "Runtime.evaluate",
            {"expression": "Object.getOwnPropertyNames(globalThis)", "contextId": own, "returnByValue": True},
        )
        known = json.dumps(names["result"]["result"]["value"])
        row = json.dumps({"kind": "click", "url": "http://forged.example/", "target": {"text": "forged-any-world"}})
        contexts = [
            one["params"]["context"] for one in page.events if one.get("method") == "Runtime.executionContextCreated"
        ]
        assert any(not (one.get("auxData") or {}).get("isDefault") for one in contexts)
        for context in contexts:
            if context["id"] == own:
                continue
            await page.call(
                "Runtime.evaluate",
                {
                    "expression": f"""(() => {{
                        const known = new Set({known});
                        for (const name of [...Object.getOwnPropertyNames(globalThis), ...Object.keys(globalThis)]) {{
                            if (known.has(name)) continue;
                            try {{ if (typeof globalThis[name] === 'function') globalThis[name]({json.dumps(row)}); }}
                            catch (e) {{}}
                        }}
                    }})()""",
                    "contextId": context["id"],
                },
            )

    _run(kit, steps)
    rows = _settled(kit)["rows"]
    assert not [row for row in rows if "forged-any-world" in json.dumps(row)]
