"""The kit's own WebSocket client to the browser's DevTools: masked frames, pieces put back together, pings
answered, a message over the cap refused, and a closed connection ending the read."""

from __future__ import annotations

import json
import socket
import struct
import threading

import pytest

from fi.alk.harness.devtools_socket import CdpSocket, FrameTooLarge, accept_key


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
                b"Sec-WebSocket-Accept: " + accept_key(key.decode()).encode() + b"\r\n\r\n"
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


def test_two_threads_never_write_their_messages_into_each_other() -> None:
    # The reader answers pings, the trial's end sends the seal, a timer sends its own: one frame at a time.
    inside, release, second_in = threading.Event(), threading.Event(), threading.Event()
    written: list[bytes] = []

    class _Connection:
        def sendall(self, data: bytes) -> None:
            if not inside.is_set():
                inside.set()
                release.wait(5)
            else:
                second_in.set()
            written.append(data)

    client = CdpSocket(_Connection())
    first = threading.Thread(target=client.send, args=({"id": 1, "method": "a"},))
    first.start()
    assert inside.wait(5)
    second = threading.Thread(target=client.send, args=({"id": 2, "method": "b"},))
    second.start()
    # The second waits while the first is still writing; a bounded wait, since nothing else would end it.
    assert not second_in.wait(0.5)
    release.set()
    first.join(5)
    second.join(5)
    assert second_in.is_set() and len(written) == 2


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
