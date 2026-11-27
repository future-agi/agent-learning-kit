"""The kit's browser driven by the clients agents use: Playwright's `connect_over_cdp`, Puppeteer's `connect` and
browser-use's own browser and REPL. Each runs the same journey: its own new tab, a click, real typing, a popup the
page opens (a tab both the client and the recorder attach to and hold until they let it run), a page off the list,
then the seal. Every step is bounded, so a tab left held fails the run instead of stalling it.

Each client is installed in CI only, each in a job of its own (sdk-smoke.yml); where one is missing its test skips,
and the job fails on a skip.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from fi.alk.harness.browser_record import BrowserRecorder, sealed_record
from fi.alk.harness.kit_browser import KitBrowser
from fi.alk.harness.world_door import MemoryLog

from test_kit_browser_chromium import BINARY, _launch, _run, _stop, site  # noqa: F401 - site is a fixture

pytestmark = pytest.mark.skipif(BINARY is None, reason="no Chromium: set ALK_CHROMIUM or put alk-chromium on PATH")

DRIVERS = Path(__file__).resolve().parent / "fixtures" / "kit_browser_clients"
JOURNEY = "http://shop.test/journey"
OFF_THE_LIST = "http://www.example.com/deals"


@pytest.fixture
def kit(site, tmp_path_factory):  # noqa: F811 - the fixture imported above
    """A browser of its own for each client, allowed `shop.test` only, so each record holds that client's run. Its
    folder's path is short: a test's own tmp_path on a Linux runner leaves too little room for Chromium's socket."""
    tmp_path = tmp_path_factory.mktemp("kc")

    def connect(host: str, port: int) -> socket.socket:
        if host == "shop.test":
            return socket.create_connection(("127.0.0.1", site.server_port), timeout=5)
        raise OSError(f"no route to {host}")

    browser = KitBrowser(
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
    browser.start()
    try:
        yield browser
    finally:
        browser.close()


def _the_journey_is_in_the_sealed_record(kit) -> None:
    sealed = sealed_record(kit.recorder.record_path, timeout=5)
    assert sealed is not None and sealed["complete"] is True, sealed
    rows = sealed["rows"]
    pages = {row["url"] for row in rows if row["kind"] == "navigation"}
    assert {JOURNEY, "http://shop.test/popped"} <= pages, pages
    clicks = [row["target"] for row in rows if row["kind"] == "click" and row["url"] == JOURNEY]
    assert {"tag": "button", "text": "Buy now"} in clicks, clicks
    typed = [(row["target"].get("label"), row["text"]) for row in rows if row["kind"] == "typing"]
    assert ("Search", "red shoes") in typed, typed
    assert OFF_THE_LIST in [one["url"] for one in sealed["blocked_navigations"]], sealed["blocked_navigations"]


def test_the_journey_over_plain_devtools_is_in_the_record_as_each_client_s_must_be(kit) -> None:
    """The control: the same journey sent as raw DevTools calls, which hold no tab, so a client's failure below is
    the client's and not the journey's."""

    async def journey(page) -> None:
        await page.open(JOURNEY)
        await page.click("#buy")
        await page.click("#q")
        await page.type("red shoes")
        await page.click("#open")
        await asyncio.sleep(1.5)
        await page.open(OFF_THE_LIST)

    _run(kit, journey)
    _the_journey_is_in_the_sealed_record(kit)


def test_playwright_connect_over_cdp_runs_the_journey_and_the_record_holds_it(kit) -> None:
    playwright = pytest.importorskip("playwright.async_api")

    async def journey() -> None:
        async with playwright.async_playwright() as driver:
            browser = await driver.chromium.connect_over_cdp(kit.relay.url, timeout=30_000)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()
            page.set_default_timeout(30_000)
            await page.goto(JOURNEY)
            await page.click("#buy")
            await page.click("#q")
            await page.keyboard.type("red shoes")
            async with page.expect_popup() as opened:
                await page.click("#open")
            popup = await opened.value
            await popup.wait_for_load_state()
            try:
                await page.goto(OFF_THE_LIST)
            except playwright.Error:
                pass
            await browser.close()

    asyncio.run(asyncio.wait_for(journey(), 120))
    _the_journey_is_in_the_sealed_record(kit)


def _driven(argv: list[str], env: dict[str, str]) -> None:
    done = subprocess.run(argv, env={**os.environ, **env}, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, (done.stdout[-4000:], done.stderr[-4000:])


def test_puppeteer_connect_runs_the_journey_and_the_record_holds_it(kit) -> None:
    prefix = os.environ.get("ALK_PUPPETEER_PREFIX")
    node = shutil.which("node")
    if not prefix or node is None:
        pytest.skip("no Puppeteer: set ALK_PUPPETEER_PREFIX to the folder puppeteer-core is installed in")
    _driven([node, str(DRIVERS / "puppeteer_drive.mjs"), kit.relay.url], {"ALK_PUPPETEER_PREFIX": prefix})
    _the_journey_is_in_the_sealed_record(kit)


def test_browser_use_runs_the_journey_and_the_record_holds_it(kit, tmp_path) -> None:
    python = os.environ.get("ALK_BROWSER_USE_PYTHON")
    if not python:
        pytest.skip("no browser-use: set ALK_BROWSER_USE_PYTHON to the Python it is installed in")
    workspace = tmp_path / "browser-use"
    workspace.mkdir()
    _driven(
        [python, "-I", str(DRIVERS / "browser_use_drive.py"), kit.relay.url, str(workspace)],
        # No telemetry, no logging setup, nothing read from a model: the cell is written here.
        {"ANONYMIZED_TELEMETRY": "false", "BH_TELEMETRY": "0", "BROWSER_USE_SETUP_LOGGING": "false"},
    )
    _the_journey_is_in_the_sealed_record(kit)
