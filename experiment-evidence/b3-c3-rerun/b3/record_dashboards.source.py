"""Test-only native Playwright recording of completed SDK evidence, not SDK execution."""
import argparse
import hashlib
import http.server
import importlib.metadata
import json
import shutil
import subprocess
import threading
import time
from functools import partial
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent
REPO = Path("/home/ubuntu/repos/agent-learning-kit")
SHA = "023abdf3655435a4b63177b3e24ee4c14df874ca"
LABEL = "ALK sandbox experiment using public APIs — not the production Future AGI platform."
SIZE = {"width": 1440, "height": 900}

def preconditions():
    assert subprocess.check_output(["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip() == SHA
    assert not subprocess.check_output(["git", "-C", str(REPO), "status", "--porcelain"])
    for name, count in (("b3", 41), ("c3", 15)):
        d = json.loads((ROOT / name / "result.json").read_text())
        assert d["status"] == "pass" and len(d["assertions"]) == count
        assert all(a["status"] == "pass" for a in d["assertions"])
        assert d["commit_sha"] == SHA and d["label"] == LABEL

class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

def exercise(browser, name, base, record):
    directory = ROOT / name
    result = json.loads((directory / "result.json").read_text())
    options = {"viewport": SIZE, "device_scale_factor": 1, "color_scheme": "light"}
    if record:
        options.update(record_video_dir=str(directory / "native"), record_video_size=SIZE)
    context = browser.new_context(**options)
    # Portable proof: no CDN/network request is allowed; only the evidence server.
    context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base) else route.abort())
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    checks = []
    started = time.monotonic()
    def check(description):
        checks.append({"elapsed_seconds": round(time.monotonic()-started, 2), "assertion": description, "status": "pass"})
        print(name, description, flush=True)
    def pause(seconds=3):
        if record:
            page.wait_for_timeout(seconds*1000)
    def tab(name):
        page.get_by_role("tab", name=name, exact=True).click()
        expect(page.get_by_role("tabpanel", name=name, exact=True)).to_be_visible()
        expect(page.get_by_text(LABEL, exact=True)).to_be_visible()
    page.goto(base + name + "/dashboard.html", wait_until="networkidle")
    expect(page.get_by_text(LABEL, exact=True)).to_be_visible()
    expect(page.get_by_text(result["summary"], exact=False)).to_be_visible()
    expect(page.get_by_text("No SDK calls run in this dashboard.", exact=True)).to_be_visible()
    check(f"Overview: exact disclaimer, {len(result['assertions'])}/{len(result['assertions'])} recorded passes, read-only scope")
    if not record:
        page.screenshot(path=str(directory / "preview.png"))
    pause(6)
    tab("Fixtures")
    for key, value in result["fixtures"].items():
        page.get_by_label("Fixture", exact=True).select_option(key)
        expect(page.get_by_test_id("fixture-json")).to_have_text(json.dumps(value, indent=2, ensure_ascii=False))
        pause(3)
        pre = page.get_by_test_id("fixture-json")
        pre.hover()
        # Real wheel scroll reveals the rest of long fixture JSON.
        for _ in range(2 if key == "malformed_entries" else 1):
            page.mouse.wheel(0, 330)
            pause(2)
        check(f"Fixture '{key}' matches result.json exactly")
    tab("API calls")
    for call in result["api_calls"]:
        code = page.get_by_role("tabpanel").locator("pre").filter(has_text=call).first
        code.scroll_into_view_if_needed()
        expect(code).to_have_text(call)
        pause(3)
    page.get_by_text("Complete fixture and experiment harness source", exact=True).click()
    expect(page.get_by_role("tabpanel").locator("details pre")).to_contain_text("def " + name + "(e):")
    pause(3)
    check("All recorded API excerpts match result.json; full construction harness is available")
    tab("Assertions")
    seen = []
    for offset in range(0, len(result["assertions"]), 2):
        visible = page.get_by_test_id("assertion-row")
        expected_rows = result["assertions"][offset:offset+2]
        expect(visible).to_have_count(len(expected_rows))
        for index, assertion in enumerate(expected_rows):
            row = visible.nth(index)
            expect(row.locator("strong")).to_have_text(assertion["id"])
            expect(row.locator("td").nth(1)).to_have_text(assertion["expression"])
            assert json.loads(row.locator("pre").inner_text()) == assertion["actual"]
            expect(row.locator(".pass")).to_have_text("PASS")
            seen.append(assertion["id"])
        pause(5)
        if offset+2 < len(result["assertions"]):
            page.get_by_role("button", name="Next", exact=True).click()
    assert len(seen) == len(result["assertions"])
    check(f"All {len(seen)} assertion IDs, exact expressions, statuses and observed JSON verified through ledger pagination")
    representative = ["snapshot/recomputed-unkeyed-digest-classification", "snapshot/source-content-outer-rehash-only"] if name == "b3" else ["mixed/source-cases", "structural/not-source-readiness", "all-blocked/no-tool", "private-blocking/public-payloads-identical", "private-blocking/trusted-readiness"]
    for identifier in representative:
        page.get_by_label("Filter assertions").fill(identifier)
        expect(page.get_by_test_id("assertion-row")).to_have_count(1)
        expect(page.get_by_test_id("assertion-row").locator("strong")).to_have_text(identifier)
        pause(4)
        # Long observed reports are scrollable JSON, not abridged evidence.
        cell = page.get_by_test_id("assertion-row").locator("pre")
        cell.hover()
        page.mouse.wheel(0, 300)
        pause(2)
        if identifier in ("all-blocked/no-tool", "private-blocking/trusted-readiness", "structural/not-source-readiness"):
            page.mouse.wheel(0, 450)
            pause(2)
            page.mouse.wheel(0, 10000)
            pause(3)
    check("Representative security/readiness evidence isolated with the visible filter")
    tab("Limitations")
    for limitation in result["limitations"]:
        expect(page.get_by_text(limitation, exact=True)).to_be_visible()
    pause(8)
    check("Every recorded limitation is visible, including non-production scope")
    tab("Raw observations")
    assert json.loads(page.get_by_role("tabpanel").locator("pre").inner_text()) == result["observations"]
    pause(3)
    check("Complete observations object matches original result.json")
    tab("Overview")
    pause(3)
    page.screenshot(path=str(directory / ("final.png" if record else "preview.png")))
    assert not errors, errors
    check("No browser JavaScript or console errors; CDN requests blocked")
    video = page.video
    await_path = None
    if record:
        await_path = video.path()
    context.close()
    if record:
        native = directory / "browser.native.webm"
        shutil.move(await_path, native)
        (directory / "native").rmdir()
        payload = {
            "status": "pass", "product_sha": SHA, "product_worktree_clean_before_capture": True,
            "result_assertions": len(result["assertions"]), "viewport": SIZE,
            "browser": browser.version, "playwright_version": importlib.metadata.version("playwright"),
            "capture": "One continuous native Playwright context video; no screenshots used as frames; no synthetic cursor",
            "record_video_dir": str(directory / "native"), "record_video_size": SIZE,
            "native_video": str(native), "native_sha256": hashlib.sha256(native.read_bytes()).hexdigest(),
            "browser_errors": errors, "checks": checks,
        }
        (directory / "browser-validation.json").write_text(json.dumps(payload, indent=2)+"\n")

def main():
    args = argparse.ArgumentParser()
    args.add_argument("--record", action="store_true")
    args.add_argument("--experiment", choices=["b3", "c3", "both"], default="both")
    options = args.parse_args()
    preconditions()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for name in (["b3", "c3"] if options.experiment == "both" else [options.experiment]):
            exercise(browser, name, base, options.record)
        browser.close()
    server.shutdown()
    preconditions()

if __name__ == "__main__":
    main()
