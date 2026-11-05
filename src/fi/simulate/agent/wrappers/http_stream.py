"""Validated live server-sent event framing for the HTTP agent wrapper."""

from __future__ import annotations

import codecs
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_STREAM_BYTES = 1024 * 1024
MAX_STREAM_EVENT_BYTES = 64 * 1024
MAX_STREAM_EVENTS = 4096


class HTTPStreamMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=512)
    values: list[Any] = Field(min_length=1, max_length=32)

    @field_validator("values")
    @classmethod
    def _scalars(cls, values: list[Any]) -> list[Any]:
        if any(
            value is not None and type(value) not in {str, int, float, bool}
            for value in values
        ):
            raise ValueError("HTTP stream match values must be JSON scalars")
        return values


class HTTPStreamText(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["delta", "cumulative", "final"]
    path: str = Field(min_length=1, max_length=512)
    when: HTTPStreamMatch | None = None


class HTTPStreamSpec(BaseModel):
    """How a source-owned JSON SSE endpoint declares text and terminal events."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    completion: HTTPStreamMatch
    failure: HTTPStreamMatch | None = None
    text: list[HTTPStreamText] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def _unambiguous(self) -> "HTTPStreamSpec":
        modes = [rule.mode for rule in self.text]
        if len(modes) != len(set(modes)):
            raise ValueError("HTTP stream text modes must be unique")
        if self.failure == self.completion:
            raise ValueError("HTTP stream failure and completion must differ")
        return self


@dataclass(frozen=True)
class SSEFrame:
    event: str
    data: Any
    event_id: str | None


@dataclass(frozen=True)
class SSEComplete:
    payloads: tuple[Any, ...]
    text: str
    bytes_received: int
    events_received: int
    duplicate_events: int
    last_event_id: str | None


class SSEProtocolError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        partial_text: str = "",
        bytes_received: int = 0,
        events_received: int = 0,
        duplicate_events: int = 0,
        last_event_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.partial_text = partial_text
        self.bytes_received = bytes_received
        self.events_received = events_received
        self.duplicate_events = duplicate_events
        self.last_event_id = last_event_id


class SSEDecoder:
    """Incrementally frame bounded UTF-8 SSE input without buffering a response."""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self._buffer = ""
        self._event_lines: list[str] = []
        self._event_bytes = 0
        self.bytes_received = 0
        self._first_line = True

    def feed(self, chunk: bytes) -> list[SSEFrame]:
        self.bytes_received += len(chunk)
        if self.bytes_received > MAX_STREAM_BYTES:
            raise SSEProtocolError("sse_limit", "SSE response exceeds 1048576 bytes")
        try:
            self._buffer += self._decoder.decode(chunk)
        except UnicodeDecodeError as exc:
            raise SSEProtocolError(
                "sse_decode", "SSE response is not valid UTF-8"
            ) from exc
        return self._drain_lines(final=False)

    def finish(self) -> list[SSEFrame]:
        try:
            self._buffer += self._decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise SSEProtocolError(
                "sse_decode", "SSE response is not valid UTF-8"
            ) from exc
        frames = self._drain_lines(final=True)
        if self._event_lines:
            frame = self._dispatch()
            if frame is not None:
                frames.append(frame)
        return frames

    def _drain_lines(self, *, final: bool) -> list[SSEFrame]:
        frames: list[SSEFrame] = []
        while True:
            positions = [
                position
                for position in (self._buffer.find("\r"), self._buffer.find("\n"))
                if position >= 0
            ]
            if not positions:
                break
            position = min(positions)
            line = self._buffer[:position]
            consumed = 2 if self._buffer[position : position + 2] == "\r\n" else 1
            self._buffer = self._buffer[position + consumed :]
            frame = self._line(line)
            if frame is not None:
                frames.append(frame)
        if final and self._buffer:
            line, self._buffer = self._buffer, ""
            if line.endswith("\r"):
                line = line[:-1]
            frame = self._line(line)
            if frame is not None:
                frames.append(frame)
        elif len(self._buffer.encode("utf-8")) > MAX_STREAM_EVENT_BYTES:
            raise SSEProtocolError("sse_limit", "SSE event exceeds 65536 bytes")
        return frames

    def _line(self, line: str) -> SSEFrame | None:
        if self._first_line:
            line = line.removeprefix("\ufeff")
            self._first_line = False
        if not self._event_lines and not line and not self._event_bytes:
            return None
        self._event_bytes += len(line.encode("utf-8")) + 1
        if self._event_bytes > MAX_STREAM_EVENT_BYTES:
            raise SSEProtocolError("sse_limit", "SSE event exceeds 65536 bytes")
        if not line:
            return self._dispatch()
        self._event_lines.append(line)
        return None

    def _dispatch(self) -> SSEFrame | None:
        event = "message"
        event_id: str | None = None
        data_lines: list[str] = []
        for raw in self._event_lines:
            if raw.startswith(":"):
                continue
            field, separator, value = raw.partition(":")
            if separator and value.startswith(" "):
                value = value[1:]
            if field == "event":
                event = value or "message"
            elif field == "id" and "\x00" not in value:
                event_id = value or None
            elif field == "data":
                data_lines.append(value)
        self._event_lines = []
        self._event_bytes = 0
        if not data_lines:
            return None
        raw_data = "\n".join(data_lines)
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError as exc:
            if raw_data.startswith(("{", "[")) and raw_data != "[DONE]":
                raise SSEProtocolError(
                    "sse_decode", "SSE data is not valid JSON"
                ) from exc
            data = raw_data
        return SSEFrame(event=event, data=data, event_id=event_id)


_MISSING = object()


def _select(value: Any, path: str) -> Any:
    current = value
    for segment in [
        part for part in path.removeprefix("$").strip(".").split(".") if part
    ]:
        if isinstance(current, Mapping) and segment in current:
            current = current[segment]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
            try:
                current = current[int(segment)]
            except (ValueError, IndexError):
                return _MISSING
        else:
            return _MISSING
    return current


def _matches(envelope: Mapping[str, Any], match: HTTPStreamMatch | None) -> bool:
    if match is None:
        return False
    actual = _select(envelope, match.path)
    return actual is not _MISSING and any(
        type(actual) is type(expected) and actual == expected
        for expected in match.values
    )


class SSEReducer:
    def __init__(self, spec: HTTPStreamSpec) -> None:
        self.spec = spec
        self.payloads: list[Any] = []
        self.text = ""
        self.events_received = 0
        self.duplicate_events = 0
        self.last_event_id: str | None = None
        self._event_ids: dict[str, str] = {}

    def accept(self, frame: SSEFrame, *, bytes_received: int) -> SSEComplete | None:
        self.events_received += 1
        if self.events_received > MAX_STREAM_EVENTS:
            self._fail("sse_limit", "SSE response exceeds 4096 events", bytes_received)
        envelope = {"event": frame.event, "data": frame.data, "id": frame.event_id}
        if frame.event_id:
            digest = hashlib.sha256(
                json.dumps(
                    envelope, sort_keys=True, separators=(",", ":"), default=str
                ).encode()
            ).hexdigest()
            prior = self._event_ids.get(frame.event_id)
            if prior == digest:
                self.duplicate_events += 1
                return None
            if prior is not None:
                self._fail(
                    "sse_decode",
                    "SSE event ID was reused with different data",
                    bytes_received,
                )
            self._event_ids[frame.event_id] = digest
            self.last_event_id = frame.event_id
        if _matches(envelope, self.spec.failure):
            message = _select(envelope, "data.message")
            self._fail(
                "sse_application",
                message
                if isinstance(message, str) and message
                else "SSE endpoint declared failure",
                bytes_received,
            )
        matched: list[tuple[HTTPStreamText, str]] = []
        for rule in self.spec.text:
            if rule.when is not None and not _matches(envelope, rule.when):
                continue
            value = _select(envelope, rule.path)
            if value is _MISSING:
                continue
            if not isinstance(value, str):
                self._fail(
                    "sse_decode", "SSE text value must be a string", bytes_received
                )
            matched.append((rule, value))
        if len(matched) > 1:
            self._fail(
                "sse_decode", "SSE event matched multiple text rules", bytes_received
            )
        if matched:
            rule, value = matched[0]
            self.text = self.text + value if rule.mode == "delta" else value
        self.payloads.append(frame.data)
        if _matches(envelope, self.spec.completion):
            return SSEComplete(
                payloads=tuple(self.payloads),
                text=self.text,
                bytes_received=bytes_received,
                events_received=self.events_received,
                duplicate_events=self.duplicate_events,
                last_event_id=self.last_event_id,
            )
        return None

    def incomplete(self, *, bytes_received: int) -> SSEProtocolError:
        return SSEProtocolError(
            "sse_incomplete",
            "SSE response ended before declared completion",
            partial_text=self.text,
            bytes_received=bytes_received,
            events_received=self.events_received,
            duplicate_events=self.duplicate_events,
            last_event_id=self.last_event_id,
        )

    def _fail(self, code: str, message: str, bytes_received: int) -> None:
        raise SSEProtocolError(
            code,
            message,
            partial_text=self.text,
            bytes_received=bytes_received,
            events_received=self.events_received,
            duplicate_events=self.duplicate_events,
            last_event_id=self.last_event_id,
        )
