"""The browser's refused attempts in a world with no door, kept in a kit-owned file the agent's user cannot write,
and the kit's reads and writes of the files it keeps beside the browser.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .outbound import redact_outbound_text
from .world_door import MemoryLog

logger = logging.getLogger(__name__)

# As many refusals as the browser record keeps rows.
BLOCKED_ROWS = 500
# The most of a kit-written file the platform's side reads: more than a capped record can be, so more is not one.
KIT_FILE_BYTES = 64 * 1024 * 1024


def read_kit_file(path: Path) -> str:
    """A kit-written file's text; ValueError when it is larger than any the kit writes."""
    with path.open("rb") as handle:
        data = handle.read(KIT_FILE_BYTES + 1)
    if len(data) > KIT_FILE_BYTES:
        raise ValueError(f"{path.name} is larger than {KIT_FILE_BYTES} bytes")
    return data.decode("utf-8")


def write_text(path: Path, text: str) -> None:
    fresh = path.with_name(f"{path.name}.{uuid.uuid4().hex}.new")
    fresh.write_text(text, encoding="utf-8")
    os.replace(fresh, path)


class BlockedFileLog(MemoryLog):
    """The browser's refused attempts in a world with no door: a kit-owned file the agent's user cannot write, cut
    at each start of the browser, capped, and cleaned as other rows are."""

    def __init__(self, path: Path, *, cap: int = BLOCKED_ROWS) -> None:
        super().__init__()
        self.path = path
        self.cap = cap
        self._written = 0
        self._file_lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.reset()

    def reset(self) -> None:
        with self._file_lock:
            self._written = 0
            try:
                write_text(self.path, "")
            except OSError:
                # Last start's rows must never be read as this one's: with no file the trial reads them unreadable.
                logger.warning("browser record: the blocked log %s could not be cut", self.path)
                try:
                    self.path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("browser record: the blocked log %s could not be removed either", self.path)
        with self._lock:
            self._rows.clear()

    def write(self, row: dict[str, Any]) -> None:
        arguments = row.get("arguments") if isinstance(row.get("arguments"), dict) else {}
        kept = {
            **{key: row[key] for key in ("actor_id", "outcome", "refusal_code", "method") if key in row},
            "arguments": {
                key: redact_outbound_text(str(arguments[key]))[:253]
                for key in ("host", "method", "via")
                if arguments.get(key)
            },
            "at": time.time(),
        }
        with self._file_lock:
            if self._written > self.cap:
                return
            if self._written < self.cap:
                super().write(row)
            self._written += 1
            line = {"truncated": True} if self._written > self.cap else kept
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(line, default=str) + "\n")
            except OSError:
                logger.warning("browser record: the blocked log %s could not be written", self.path)


def blocked_from_file(path: Path) -> list[dict[str, Any]] | None:
    """The browser's refusals from its kit-owned file, grouped as a trial's blocked list; None when the file cannot be
    read, never an empty list in its place."""
    from .call_runner import trial_state_changes

    try:
        lines = read_kit_file(path).splitlines()
        rows = [json.loads(line) for line in lines if line.strip()]
    except (OSError, ValueError):
        return None
    if not all(isinstance(row, dict) for row in rows):
        return None
    truncated = any(row.get("truncated") is True for row in rows)
    changes = trial_state_changes([row for row in rows if "truncated" not in row], {})
    blocked = next((list(entry.get("blocked") or []) for entry in changes if entry.get("who") == "agent"), [])
    return blocked + ([{"truncated": True}] if truncated else [])
