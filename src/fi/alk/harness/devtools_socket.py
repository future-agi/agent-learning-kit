"""The kit's own WebSocket client for one DevTools connection to the environment's browser, on the standard library.

It sends text messages masked as a client must, puts pieces back together, answers pings, and reads nothing over a
fixed size into memory. The browser record's recorder is its one user.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import threading
from typing import Any
from urllib.parse import urlsplit


_MESSAGE_BYTES = 32 * 1024 * 1024
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class FrameTooLarge(ConnectionError):
    pass


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()


class CdpSocket:
    """A WebSocket client for one DevTools connection, on the standard library: text messages, masked as a client
    must, pieces put back together, pings answered, and nothing over a fixed size read into memory."""

    def __init__(self, connection: socket.socket) -> None:
        self._connection = connection
        self._buffer = b""
        self._sending = threading.Lock()

    @classmethod
    def connect(cls, url: str, *, timeout: float = 10.0) -> CdpSocket:
        parts = urlsplit(url)
        if parts.scheme != "ws" or not parts.hostname or not parts.port:
            raise ConnectionError(f"not a local DevTools address: {url}")
        connection = socket.create_connection((parts.hostname, parts.port), timeout=timeout)
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            connection.sendall(
                (
                    f"GET {parts.path or '/'} HTTP/1.1\r\nHost: {parts.hostname}:{parts.port}\r\n"
                    f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                    "Sec-WebSocket-Version: 13\r\n\r\n"
                ).encode()
            )
            client = cls(connection)
            head = client._until(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            if " 101 " not in lines[0] + " ":
                raise ConnectionError(f"the browser refused the DevTools connection: {lines[0]}")
            answers = {
                name.strip().lower(): value.strip()
                for name, _, value in (line.partition(":") for line in lines[1:])
            }
            if answers.get("sec-websocket-accept") != accept_key(key):
                raise ConnectionError("the DevTools connection answered with the wrong key")
            connection.settimeout(None)
            return client
        except BaseException:
            connection.close()
            raise

    def send(self, message: dict[str, Any]) -> None:
        data = json.dumps(message).encode()
        mask = os.urandom(4)
        length = len(data)
        if length < 126:
            head = struct.pack("!BB", 0x81, 0x80 | length)
        elif length < 65536:
            head = struct.pack("!BBH", 0x81, 0x80 | 126, length)
        else:
            head = struct.pack("!BBQ", 0x81, 0x80 | 127, length)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        with self._sending:
            self._connection.sendall(head + mask + masked)

    def receive(self) -> dict[str, Any]:
        parts: list[bytes] = []
        size = 0
        while True:
            first, second = self._exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._exact(8))[0]
            if size + length > _MESSAGE_BYTES:
                raise FrameTooLarge(f"a DevTools message over {_MESSAGE_BYTES} bytes")
            mask = self._exact(4) if second & 0x80 else b""
            data = self._exact(length) if length else b""
            if mask:
                data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
            if opcode == 8:
                raise ConnectionError("the browser closed the DevTools connection")
            if opcode == 9:
                with self._sending:
                    self._connection.sendall(struct.pack("!BB", 0x8A, 0x80) + os.urandom(4))
                continue
            if opcode == 10:
                continue
            parts.append(data)
            size += length
            if first & 0x80:
                return json.loads(b"".join(parts))

    def close(self) -> None:
        try:
            self._connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._connection.close()

    def _until(self, marker: bytes) -> bytes:
        while marker not in self._buffer:
            if len(self._buffer) > 65536:
                raise ConnectionError("the DevTools handshake was too long")
            self._fill()
        head, _, self._buffer = self._buffer.partition(marker)
        return head

    def _exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            self._fill()
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def _fill(self) -> None:
        chunk = self._connection.recv(65536)
        if not chunk:
            raise ConnectionError("the DevTools connection closed")
        self._buffer += chunk
