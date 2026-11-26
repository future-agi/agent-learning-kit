"""What the world keeps of the agent's browsing: its pages, clicks and typing, and the sites it was refused,
split into the pages it tried to open and the requests the pages made."""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading

import pytest

from fi.alk.harness import browser_record
from fi.alk.harness.browser_record import BrowserRecord, CdpSocket, FrameTooLarge


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _record(**kwargs) -> tuple[BrowserRecord, _Clock]:
    clock = _Clock()
    return BrowserRecord(["shop.test"], clock=clock, **kwargs), clock


def _action(kind: str, **fields) -> str:
    return json.dumps({"kind": kind, "url": "http://shop.test/", **fields})


# Pages, clicks and typing


def test_pages_clicks_and_typing_are_kept_in_order_with_their_times() -> None:
    record, clock = _record()
    record.navigated("tab-1", "http://shop.test/")
    clock.now += 1
    record.acted("tab-1", _action("click", target={"tag": "button", "text": "Buy now"}))
    clock.now += 1
    record.acted("tab-1", _action("typing", target={"tag": "input", "label": "Search"}, text="red shoes"))
    body = record.body()
    assert [(row["kind"], row["at"]) for row in body["rows"]] == [
        ("navigation", 1000.0),
        ("click", 1001.0),
        ("typing", 1002.0),
    ]
    assert body["rows"][1]["target"] == {"tag": "button", "text": "Buy now"}
    assert body["rows"][2]["text"] == "red shoes"
    assert {row["tab"] for row in body["rows"]} == {1}


def test_typing_into_one_field_is_one_row_holding_what_it_came_to() -> None:
    record, clock = _record()
    for text in ["r", "re", "red"]:
        clock.now += 1
        record.acted("tab-1", _action("typing", target={"tag": "input", "label": "Search"}, text=text))
    [row] = record.body()["rows"]
    assert (row["text"], row["at"]) == ("red", 1003.0)


def test_text_typed_into_a_password_field_is_never_kept(tmp_path) -> None:
    # The page's listener sends no text for a password field; a row that brings some anyway is cut.
    record, _ = _record()
    record.acted(
        "tab-1",
        _action("typing", target={"tag": "input", "label": "password"}, secret=True, text="hunter2"),
    )
    [row] = record.body()["rows"]
    assert row["text"] is None and row["secret"] is True
    assert "hunter2" not in json.dumps(record.body())


def test_what_a_row_holds_passes_the_kit_s_cleaner() -> None:
    record, _ = _record()
    record.acted(
        "tab-1",
        _action("typing", target={"tag": "textarea"}, text="see https://bob:hunter22@shop.test/a?token=abc"),
    )
    [row] = record.body()["rows"]
    assert "hunter22" not in row["text"] and "token=abc" not in row["text"]


@pytest.mark.parametrize(
    "payload",
    ["not json", json.dumps(["click"]), json.dumps({"kind": "scroll"}), json.dumps({"kind": "click", "url": 7})],
)
def test_a_row_the_page_made_up_is_dropped(payload) -> None:
    record, _ = _record()
    record.acted("tab-1", payload)
    assert record.body()["rows"] == []


def test_a_record_that_reaches_its_cap_says_so() -> None:
    record, _ = _record(cap=3)
    for index in range(5):
        record.acted("tab-1", _action("click", target={"tag": "a", "text": str(index)}))
    body = record.body()
    assert len(body["rows"]) == 3 and body["truncated"] is True


# The sites it was refused: two lists


def test_a_page_the_agent_tried_to_open_off_the_list_is_kept_whole_each_time() -> None:
    record, clock = _record()
    record.requested("tab-1", "http://www.example.com/deals", document=True, main_frame=True)
    clock.now += 10
    record.requested("tab-1", "http://www.example.com/deals", document=True, main_frame=True)
    body = record.body()
    assert [one["url"] for one in body["blocked_navigations"]] == [
        "http://www.example.com/deals",
        "http://www.example.com/deals",
    ]
    assert body["blocked_page_requests"] == []


def test_one_attempt_that_chrome_tries_over_https_first_is_one_attempt() -> None:
    record, clock = _record()
    for url in ["http://www.example.com/", "https://www.example.com/", "http://www.example.com/"]:
        record.requested("tab-1", url, document=True, main_frame=True)
        clock.now += 0.2
    assert len(record.body()["blocked_navigations"]) == 1


def test_requests_a_page_made_off_the_list_are_counted_by_host() -> None:
    record, _ = _record()
    for url in ["http://tracker.example.net/a.gif", "http://tracker.example.net/b.js", "https://cdn.example.org/x.css"]:
        record.requested("tab-1", url, document=False, main_frame=False)
    # A frame inside the page is the page's request, not a page the agent opened.
    record.requested("tab-1", "http://ads.example.net/frame", document=True, main_frame=False)
    assert record.body()["blocked_page_requests"] == [
        {"host": "ads.example.net", "count": 1},
        {"host": "cdn.example.org", "count": 1},
        {"host": "tracker.example.net", "count": 2},
    ]
    assert record.body()["blocked_navigations"] == []


def test_requests_to_the_sites_are_not_blocked_and_a_refused_page_is_no_navigation() -> None:
    record, _ = _record()
    record.requested("tab-1", "http://shop.test/cart", document=True, main_frame=True)
    record.requested("tab-1", "http://www.example.com/", document=True, main_frame=True)
    # Chromium shows its error page at the refused address: that is the refusal, not a page opened.
    record.navigated("tab-1", "http://www.example.com/")
    record.navigated("tab-1", "about:blank")
    body = record.body()
    assert body["rows"] == []
    assert [one["url"] for one in body["blocked_navigations"]] == ["http://www.example.com/"]


def test_loopback_is_never_one_of_the_sites() -> None:
    record, _ = _record()
    record.requested("tab-1", "http://127.0.0.1:5432/", document=True, main_frame=True)
    assert len(record.body()["blocked_navigations"]) == 1


def test_a_site_is_read_as_the_browser_s_proxy_reads_it() -> None:
    # A name is that host only; a wildcard is every host under it, not the name itself. The record keeps
    # the proxy's rule, so what the proxy refused is what the record shows as blocked.
    record = BrowserRecord(["shop.test", "*.pay.test"], clock=_Clock())
    record.requested("tab-1", "http://img.shop.test/a.png", document=False, main_frame=False)
    record.requested("tab-1", "http://a.b.pay.test/x.js", document=False, main_frame=False)
    record.requested("tab-1", "http://pay.test/", document=True, main_frame=True)
    body = record.body()
    assert body["blocked_page_requests"] == [{"host": "img.shop.test", "count": 1}]
    assert [one["url"] for one in body["blocked_navigations"]] == ["http://pay.test/"]


def test_a_record_the_recorder_could_not_finish_says_so() -> None:
    record, _ = _record()
    record.broken("the browser's DevTools connection closed")
    body = record.body()
    assert body["complete"] is False
    assert "closed" in body["why_incomplete"]


def test_tabs_are_numbered_in_the_order_they_were_first_seen() -> None:
    record, _ = _record()
    record.navigated("B", "http://shop.test/")
    record.navigated("A", "http://shop.test/next")
    record.navigated("B", "http://shop.test/again")
    assert [row["tab"] for row in record.body()["rows"]] == [1, 2, 1]


# The DevTools connection: a WebSocket on the standard library


class _Server:
    """A WebSocket server for one connection: completes the handshake, then runs `script` on the socket."""

    def __init__(self, script) -> None:
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.received: list[bytes] = []
        self.script = script
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        connection, _ = self.listener.accept()
        with connection:
            request = b""
            while b"\r\n\r\n" not in request:
                request += connection.recv(4096)
            key = next(
                line.split(b":", 1)[1].strip()
                for line in request.split(b"\r\n")
                if line.lower().startswith(b"sec-websocket-key")
            )
            connection.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                b"Sec-WebSocket-Accept: " + browser_record.accept_key(key.decode()).encode() + b"\r\n\r\n"
            )
            self.script(connection, self)


def _frame(payload: bytes, *, opcode: int = 1, fin: bool = True) -> bytes:
    head = bytes([(0x80 if fin else 0) | opcode])
    if len(payload) < 126:
        return head + bytes([len(payload)]) + payload
    if len(payload) < 65536:
        return head + bytes([126]) + struct.pack("!H", len(payload)) + payload
    return head + bytes([127]) + struct.pack("!Q", len(payload)) + payload


def _read_client_frame(connection: socket.socket) -> tuple[int, bool, bytes]:
    first, second = connection.recv(2, socket.MSG_WAITALL)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", connection.recv(2, socket.MSG_WAITALL))[0]
    elif length == 127:
        length = struct.unpack("!Q", connection.recv(8, socket.MSG_WAITALL))[0]
    mask = connection.recv(4, socket.MSG_WAITALL) if second & 0x80 else b""
    data = connection.recv(length, socket.MSG_WAITALL) if length else b""
    if mask:
        data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
    return first & 0x0F, bool(second & 0x80), data


def test_the_client_masks_what_it_sends_and_reads_a_message_sent_in_pieces() -> None:
    seen: list = []

    def script(connection, _server):
        seen.append(_read_client_frame(connection))
        message = json.dumps({"id": 1, "result": {"big": "x" * 70000}}).encode()
        connection.sendall(_frame(message[:10], fin=False) + _frame(b"", opcode=9) + _frame(message[10:], opcode=0))

    server = _Server(script)
    client = CdpSocket.connect(f"ws://127.0.0.1:{server.port}/devtools/browser/x", timeout=5)
    try:
        client.send({"id": 1, "method": "Browser.getVersion"})
        answer = client.receive()
    finally:
        client.close()
    opcode, masked, data = seen[0]
    assert (opcode, masked) == (1, True)
    assert json.loads(data) == {"id": 1, "method": "Browser.getVersion"}
    assert len(answer["result"]["big"]) == 70000


def test_a_message_over_the_cap_is_refused_not_read_into_memory() -> None:
    def script(connection, _server):
        connection.sendall(bytes([0x81, 127]) + struct.pack("!Q", 1 << 40))

    server = _Server(script)
    client = CdpSocket.connect(f"ws://127.0.0.1:{server.port}/devtools/browser/x", timeout=5)
    try:
        with pytest.raises(FrameTooLarge):
            client.receive()
    finally:
        client.close()


def test_a_closed_connection_ends_the_read() -> None:
    def script(connection, _server):
        connection.sendall(_frame(b"\x03\xe8", opcode=8))

    server = _Server(script)
    client = CdpSocket.connect(f"ws://127.0.0.1:{server.port}/devtools/browser/x", timeout=5)
    try:
        with pytest.raises(ConnectionError):
            client.receive()
    finally:
        client.close()


# Sealing the record at the trial's end


class _FakeCdp:
    """A DevTools connection that answers each command after the events queued before it."""

    def __init__(self) -> None:
        self.inbox: list[dict] = []
        self.sent: list[dict] = []
        self.ready = threading.Condition()
        self.closed = False

    shot = b"\xff\xd8\xff" + b"j" * 97
    fail_shots = False

    def send(self, message: dict) -> None:
        with self.ready:
            self.sent.append(message)
            if message["method"] == "Page.captureScreenshot":
                answer = (
                    {"id": message["id"], "error": {"message": "no"}}
                    if self.fail_shots
                    else {"id": message["id"], "result": {"data": base64.b64encode(self.shot).decode()}}
                )
            else:
                answer = {"id": message["id"], "result": {}}
            self.inbox.append(answer)
            self.ready.notify_all()

    def queue(self, message: dict) -> None:
        with self.ready:
            self.inbox.append(message)
            self.ready.notify_all()

    def receive(self) -> dict:
        with self.ready:
            while not self.inbox:
                if self.closed:
                    raise ConnectionError("closed")
                self.ready.wait(0.05)
            return self.inbox.pop(0)

    def close(self) -> None:
        with self.ready:
            self.closed = True
            self.ready.notify_all()


@pytest.fixture()
def devtools_port():
    """`/json/version` for a recorder that then connects through a fake."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Version(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            content = json.dumps({"webSocketDebuggerUrl": "ws://127.0.0.1:1/devtools/browser/x"}).encode()
            self.send_response(200)
            self.send_header("content-length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Version)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_port
    finally:
        server.shutdown()


def _attached(page: str = "T1") -> dict:
    return {
        "method": "Target.attachedToTarget",
        "params": {"sessionId": "S1", "targetInfo": {"type": "page", "targetId": page}, "waitingForDebugger": True},
    }


def test_a_sealed_record_holds_every_event_the_browser_sent_before_the_seal(tmp_path, devtools_port) -> None:
    fake = _FakeCdp()
    recorder = browser_record.BrowserRecorder(["shop.test"], tmp_path / "w0-record.json", connect=lambda url: fake)
    recorder.attach(devtools_port)
    try:
        fake.queue(_attached())
        fake.queue(
            {
                "method": "Page.frameNavigated",
                "sessionId": "S1",
                "params": {"frame": {"id": "T1", "url": "http://shop.test/cart"}},
            }
        )
        body = browser_record.sealed_record(tmp_path / "w0-record.json", timeout=5)
    finally:
        recorder.close()
    assert [row["url"] for row in body["rows"]] == ["http://shop.test/cart"]
    assert body["complete"] is True
    # A held tab is let go after its listener is set up.
    methods = [one["method"] for one in fake.sent]
    assert methods.index("Runtime.runIfWaitingForDebugger") > methods.index("Page.addScriptToEvaluateOnNewDocument")


def test_a_record_not_sealed_in_time_is_read_as_incomplete(tmp_path) -> None:
    path = tmp_path / "w0-record.json"
    path.write_text(json.dumps({"complete": True, "truncated": False, "rows": [], "blocked_navigations": []}))
    body = browser_record.sealed_record(path, timeout=0.2)
    assert body["complete"] is False
    assert "not sealed" in body["why_incomplete"]


def test_no_record_at_all_is_none(tmp_path) -> None:
    assert browser_record.sealed_record(tmp_path / "w0-record.json", timeout=0.2) is None


def test_a_connection_that_ends_while_the_browser_runs_leaves_an_incomplete_record(tmp_path, devtools_port) -> None:
    fake = _FakeCdp()
    recorder = browser_record.BrowserRecorder(["shop.test"], tmp_path / "w0-record.json", connect=lambda url: fake)
    recorder.attach(devtools_port)
    try:
        fake.close()
        deadline = __import__("time").monotonic() + 5
        while recorder.record.body()["complete"] and __import__("time").monotonic() < deadline:
            __import__("time").sleep(0.02)
    finally:
        recorder.close()
    assert json.loads((tmp_path / "w0-record.json").read_text())["complete"] is False



# Screenshots


def _loaded(url: str) -> list[dict]:
    return [
        {"method": "Page.frameNavigated", "sessionId": "S1", "params": {"frame": {"id": "T1", "url": url}}},
        {"method": "Page.loadEventFired", "sessionId": "S1", "params": {}},
    ]


def _recording(tmp_path, devtools_port, **kwargs):
    fake = _FakeCdp()
    recorder = browser_record.BrowserRecorder(
        ["shop.test"], tmp_path / "w0-record.json", connect=lambda url: fake, **kwargs
    )
    recorder.attach(devtools_port)
    fake.queue(_attached())
    return fake, recorder


def test_each_page_and_the_trial_s_end_are_photographed(tmp_path, devtools_port) -> None:
    fake, recorder = _recording(tmp_path, devtools_port)
    try:
        for message in _loaded("http://shop.test/") + _loaded("http://shop.test/cart"):
            fake.queue(message)
        body = browser_record.sealed_record(tmp_path / "w0-record.json", timeout=5)
    finally:
        recorder.close()
    shots = body["screenshots"]
    assert [(one["when"], one["url"]) for one in shots] == [
        ("load", "http://shop.test/"),
        ("load", "http://shop.test/cart"),
        ("end", "http://shop.test/cart"),
    ]
    for one in shots:
        assert (tmp_path / "w0-shots" / one["file"]).read_bytes() == fake.shot
        # The id the platform will store it under, so a row and its stored screenshot can be matched.
        assert one["artifact_id"] == "sha256:" + hashlib.sha256(fake.shot).hexdigest()
    assert body["screenshots_capped"] is False


def test_screenshots_stop_at_their_cap_and_the_last_is_always_kept(tmp_path, devtools_port) -> None:
    fake, recorder = _recording(tmp_path, devtools_port, shots=2)
    try:
        for index in range(4):
            for message in _loaded(f"http://shop.test/{index}"):
                fake.queue(message)
        body = browser_record.sealed_record(tmp_path / "w0-record.json", timeout=5)
    finally:
        recorder.close()
    assert [one["when"] for one in body["screenshots"]] == ["load", "end"]
    assert body["screenshots_capped"] is True
    assert sorted(path.name for path in (tmp_path / "w0-shots").iterdir()) == sorted(
        one["file"] for one in body["screenshots"]
    )


def test_screenshots_stop_at_their_bytes_too(tmp_path, devtools_port) -> None:
    fake, recorder = _recording(tmp_path, devtools_port, shot_bytes=250)
    try:
        for index in range(4):
            for message in _loaded(f"http://shop.test/{index}"):
                fake.queue(message)
        body = browser_record.sealed_record(tmp_path / "w0-record.json", timeout=5)
    finally:
        recorder.close()
    assert sum(len(fake.shot) for _ in body["screenshots"]) <= 250
    assert body["screenshots"][-1]["when"] == "end" and body["screenshots_capped"] is True


def test_a_screenshot_the_browser_could_not_take_is_skipped_and_the_seal_still_answers(
    tmp_path, devtools_port
) -> None:
    fake, recorder = _recording(tmp_path, devtools_port)
    fake.fail_shots = True
    try:
        for message in _loaded("http://shop.test/"):
            fake.queue(message)
        body = browser_record.sealed_record(tmp_path / "w0-record.json", timeout=5)
    finally:
        recorder.close()
    assert body["complete"] is True and body["screenshots"] == []


def test_a_new_record_starts_with_no_screenshots_of_the_last_trial(tmp_path, devtools_port) -> None:
    fake, recorder = _recording(tmp_path, devtools_port)
    try:
        for message in _loaded("http://shop.test/"):
            fake.queue(message)
        browser_record.sealed_record(tmp_path / "w0-record.json", timeout=5)
        recorder.attach(devtools_port)
        assert recorder.record.body()["screenshots"] == []
        assert not any((tmp_path / "w0-shots").iterdir())
    finally:
        recorder.close()


# The trial's blocked list: the proxy's refusals, split by the record


def test_the_browser_s_refusals_are_split_into_pages_tried_and_requests_counted() -> None:
    proxy = [
        {"host": "www.example.com", "method": "GET", "via": "browser", "count": 3},
        {"host": "tracker.example.net", "method": "GET", "via": "browser", "count": 2},
        {"host": "api.example.org", "method": "POST", "tool": "pay", "count": 1},
    ]
    record = {
        "blocked_navigations": [{"url": "http://www.example.com/deals", "at": 1.0, "tab": 1}],
        "blocked_page_requests": [{"host": "tracker.example.net", "count": 1}],
    }
    assert browser_record.split_blocked(proxy, record) == [
        # The agent's own calls are untouched.
        {"host": "api.example.org", "method": "POST", "tool": "pay", "count": 1},
        {"host": "www.example.com", "via": "browser", "what": "navigation", "url": "http://www.example.com/deals", "at": 1.0},
        {"host": "tracker.example.net", "via": "browser", "what": "page_request", "count": 1},
    ]


def test_a_refusal_the_record_never_saw_stays_as_the_proxy_counted_it() -> None:
    # A worker's request, a tab watched late, or a name that resolved to loopback: the proxy is the record of refusal.
    proxy = [{"host": "localhost", "method": "GET", "via": "browser", "count": 1}]
    record = {"blocked_navigations": [], "blocked_page_requests": []}
    assert browser_record.split_blocked(proxy, record) == proxy


def test_with_no_record_the_proxy_s_counts_stand() -> None:
    proxy = [{"host": "www.example.com", "method": "GET", "via": "browser", "count": 3}]
    assert browser_record.split_blocked(proxy, None) == proxy


# What the trial keeps of its browser


def _runtime(metadata):
    from fi.alk.harness.process_runtime import EnvironmentRuntime, RuntimeState

    return EnvironmentRuntime(
        runtime_id="r", world_index=0, bundle_digest="d", state=RuntimeState.READY, metadata=metadata
    )


def _started(tmp_path, attached: int, record: dict | None = None, *, requested: bool = False):
    state = tmp_path / "w0.json"
    state.write_text(json.dumps({"attached": attached, "requested": requested}))
    record_path = tmp_path / "w0-record.json"
    if record is not None:
        record_path.write_text(json.dumps(record))
    return _runtime({"browser": {"state": "started", "state_path": str(state), "record_path": str(record_path)}})


def test_a_browser_nobody_attached_to_reads_not_used_never_an_empty_record(tmp_path) -> None:
    evidence = browser_record.browser_evidence(_started(tmp_path, 0, {"rows": []}), timeout=0.2)
    assert evidence == {"state": "not_used"}


def test_a_browser_asked_for_a_site_with_no_client_on_the_relay_was_used_as_its_notices_say(tmp_path) -> None:
    # The record and the trial's notices read one answer: a browser its proxy was asked through was used.
    from fi.alk.harness.kit_browser import browser_notices

    runtime = _started(tmp_path, 0, requested=True)
    assert browser_notices(runtime) == []
    evidence = browser_record.browser_evidence(runtime, timeout=0.2)
    assert evidence["state"] == "used"


def test_a_world_with_no_browser_has_no_browser_evidence(tmp_path) -> None:
    assert browser_record.browser_evidence(_runtime({}), timeout=0.2) is None
    assert browser_record.browser_evidence(_runtime({"browser": {"state": "not_started"}}), timeout=0.2) == {
        "state": "not_started"
    }


def test_a_used_browser_s_record_is_sealed_and_kept(tmp_path) -> None:
    path = tmp_path / "w0-record.json"
    runtime = _started(tmp_path, 1)

    def answer():
        # The kit's client answers the seal the trial asks for.
        asked = browser_record.seal_request_path(path)
        for _ in range(100):
            if asked.exists():
                nonce = asked.read_text()
                path.write_text(json.dumps({"complete": True, "rows": [{"kind": "click"}], "sealed": nonce}))
                return
            __import__("time").sleep(0.02)

    threading.Thread(target=answer, daemon=True).start()
    evidence = browser_record.browser_evidence(runtime, timeout=5)
    assert evidence == {"state": "used", "complete": True, "rows": [{"kind": "click"}]}


class _Uploads:
    def __init__(self, refuse: bool = False) -> None:
        self.refuse = refuse
        self.kinds: list = []

    async def upload_artifact(self, data, *, kind, scenario_key=None, deadline=None):
        self.kinds.append((kind, data))
        return None if self.refuse else f"artifact-{len(self.kinds)}"


def test_screenshots_are_uploaded_as_their_own_kind(tmp_path) -> None:
    import asyncio

    from fi.alk.harness.outbound import ArtifactKind

    shots = tmp_path / "w0-shots"
    shots.mkdir()
    (shots / "shot-001-load.jpg").write_bytes(b"\xff\xd8one")
    (shots / "shot-002-end.jpg").write_bytes(b"\xff\xd8two")
    evidence = {
        "state": "used",
        "screenshots": [{"file": "shot-001-load.jpg"}, {"file": "shot-002-end.jpg"}, {"file": "../escape.jpg"}],
    }
    adapter = _Uploads()
    ids, notices = asyncio.run(browser_record.upload_screenshots(adapter, "one", evidence, shots))
    assert ids == ("artifact-1", "artifact-2")
    assert [kind for kind, _ in adapter.kinds] == [ArtifactKind.SCREENSHOT] * 2
    assert notices == []


def test_screenshots_the_platform_did_not_keep_are_said_and_never_fail_the_trial(tmp_path) -> None:
    import asyncio

    shots = tmp_path / "w0-shots"
    shots.mkdir()
    (shots / "shot-001-end.jpg").write_bytes(b"\xff\xd8")
    evidence = {"state": "used", "screenshots": [{"file": "shot-001-end.jpg"}], "screenshots_capped": True}
    ids, notices = asyncio.run(browser_record.upload_screenshots(_Uploads(refuse=True), "one", evidence, shots))
    assert ids == ()
    assert [one["code"] for one in notices] == ["screenshots_capped", "screenshots_not_kept"]


def test_a_new_trial_s_record_never_shows_the_last_trial_s_rows(tmp_path, devtools_port) -> None:
    fake, recorder = _recording(tmp_path, devtools_port)
    try:
        for message in _loaded("http://shop.test/old"):
            fake.queue(message)
        assert browser_record.sealed_record(tmp_path / "w0-record.json", timeout=5)["rows"]
        recorder.attach(devtools_port)
        # Read from the file, as the trial reads it, before any seal is answered.
        on_disk = json.loads((tmp_path / "w0-record.json").read_text())
        assert on_disk["rows"] == [] and on_disk["screenshots"] == []
    finally:
        recorder.close()


def test_an_end_screenshot_the_browser_never_answers_does_not_hold_the_seal(tmp_path, devtools_port) -> None:
    class _Silent(_FakeCdp):
        def send(self, message: dict) -> None:
            if message["method"] == "Page.captureScreenshot":
                with self.ready:
                    self.sent.append(message)
                return
            super().send(message)

    fake = _Silent()
    recorder = browser_record.BrowserRecorder(
        ["shop.test"], tmp_path / "w0-record.json", connect=lambda url: fake, shot_seconds=0.3
    )
    recorder.attach(devtools_port)
    try:
        fake.queue(_attached())
        fake.queue({"method": "Page.frameNavigated", "sessionId": "S1", "params": {"frame": {"id": "T1", "url": "http://shop.test/"}}})
        body = browser_record.sealed_record(tmp_path / "w0-record.json", timeout=3)
    finally:
        recorder.close()
    assert body["complete"] is True
    assert [row["url"] for row in body["rows"]] == ["http://shop.test/"]


# A world with no door: the browser's refusals in a kit-owned file, and the world_state file that says what it holds


def _refused(log, host: str) -> None:
    from fi.alk.harness.egress_proxy import EgressProxy

    class _Proxy(EgressProxy):
        via = "browser"

    proxy = _Proxy([], log)
    try:
        proxy.refuse(host, "GET")
    finally:
        proxy.close()


def test_a_world_with_no_door_keeps_the_browser_s_refusals_in_a_kit_file_cut_at_each_trial(tmp_path) -> None:
    log = browser_record.BlockedFileLog(tmp_path / "w0-blocked.jsonl")
    _refused(log, "www.example.com")
    _refused(log, "www.example.com")
    assert browser_record.blocked_from_file(tmp_path / "w0-blocked.jsonl") == [
        {"host": "www.example.com", "method": "GET", "via": "browser", "count": 2}
    ]
    log.reset()
    assert browser_record.blocked_from_file(tmp_path / "w0-blocked.jsonl") == []


def test_a_blocked_file_is_capped_and_says_so(tmp_path) -> None:
    log = browser_record.BlockedFileLog(tmp_path / "w0-blocked.jsonl", cap=2)
    for host in ["a.example.com", "b.example.com", "c.example.com"]:
        _refused(log, host)
    found = browser_record.blocked_from_file(tmp_path / "w0-blocked.jsonl")
    assert [one["host"] for one in found if "host" in one] == ["a.example.com", "b.example.com"]
    assert {"truncated": True} in found


def test_a_blocked_file_that_cannot_be_read_is_unreadable_never_empty(tmp_path) -> None:
    assert browser_record.blocked_from_file(tmp_path / "missing.jsonl") is None
    (tmp_path / "bad.jsonl").write_text("{not json\n")
    assert browser_record.blocked_from_file(tmp_path / "bad.jsonl") is None


def test_the_sealed_record_carries_counts_never_rows() -> None:
    evidence = {
        "state": "used",
        "complete": True,
        "rows": [{"kind": "click"}, {"kind": "typing", "text": "red"}],
        "blocked_navigations": [{"url": "http://www.example.com/"}],
        "blocked_page_requests": [{"host": "t.example.net", "count": 4}],
        "screenshots": [{"file": "shot-001-end.jpg"}],
        "blocked": [{"host": "www.example.com"}],
    }
    assert browser_record.browser_counts(evidence) == {
        "state": "used",
        "complete": True,
        "rows": 2,
        "blocked_navigations": 1,
        "blocked_page_requests": 4,
        "screenshots": 1,
    }
    assert browser_record.browser_counts({"state": "not_used"}) == {"state": "not_used"}


def test_the_file_for_a_world_with_no_door_says_its_world_was_not_recorded(tmp_path) -> None:
    import asyncio

    adapter = _Uploads()
    stored = asyncio.run(
        browser_record.upload_browser_state(
            adapter, "one", {"state": "used", "rows": [{"kind": "click"}]}, [{"host": "x.example.com", "count": 1}]
        )
    )
    [(kind, data)] = adapter.kinds
    body = json.loads(data)
    assert stored == "artifact-1" and kind.value == "world_state"
    assert body["schema"] != "futureagi.world-state.v1"
    assert body["recorded"] is False and body["why"] == "no_world_door"
    # Absent, never empty: no tables and no action log stand in for a world that was not read.
    assert "tables" not in body and "action_log" not in body
    assert body["browser"]["rows"] == [{"kind": "click"}]
    assert body["blocked"] == [{"host": "x.example.com", "count": 1}]


def test_a_blocked_list_that_could_not_be_read_is_said_to_be_unreadable(tmp_path) -> None:
    import asyncio

    adapter = _Uploads()
    asyncio.run(browser_record.upload_browser_state(adapter, "one", {"state": "used"}, None))
    body = json.loads(adapter.kinds[0][1])
    assert body["blocked_unreadable"] is True and "blocked" not in body


def test_the_sealed_record_holds_the_browser_s_refusals_split_and_counted_never_their_addresses() -> None:
    proxy = {"host": "www.example.com", "method": "GET", "via": "browser", "count": 5}
    own = {"host": "api.other.test", "method": "POST", "tool": "lookup", "count": 1}
    changes = [
        {"who": "agent", "attempted": 0, "blocked": [proxy, own]},
        {"who": "pat", "attempted": 1, "blocked": []},
    ]
    record = {
        "blocked_navigations": [
            {"url": "http://www.example.com/deals?id=1", "at": 1.0},
            {"url": "http://www.example.com/cart", "at": 2.0},
        ],
        "blocked_page_requests": [{"host": "www.example.com", "count": 3}],
    }
    sealed = browser_record.sealed_browser_blocked(changes, record)
    assert sealed[0]["blocked"] == [
        own,
        {"host": "www.example.com", "via": "browser", "what": "navigation", "count": 2},
        {"host": "www.example.com", "via": "browser", "what": "page_request", "count": 3},
    ]
    assert sealed[1] == changes[1]
    # The addresses stay in the world_state file; the input is left as it was.
    assert "url" not in json.dumps(sealed) and changes[0]["blocked"] == [proxy, own]


def test_with_no_record_the_sealed_record_keeps_the_proxy_s_counts() -> None:
    changes = [{"who": "agent", "blocked": [{"host": "a.test", "method": "GET", "via": "browser", "count": 2}]}]
    assert browser_record.sealed_browser_blocked(changes, None) == changes
