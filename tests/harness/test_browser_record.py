"""What the world keeps of the agent's browsing: its pages, clicks and typing, and the sites it was refused,
split into the pages it tried to open and the requests the pages made."""

from __future__ import annotations

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
