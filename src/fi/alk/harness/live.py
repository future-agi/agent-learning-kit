"""The live channel between a running harness and whoever is watching it."""

from __future__ import annotations

import dataclasses
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

DIRECTORY = "ALK_HARNESS_CHAT_DIR"
INBOX = "inbox.jsonl"
OUTBOX = "outbox.jsonl"

# How many tool results a message rides in on before it is let go.
HANDOVERS = 4

# How long a question waits for its answer before the run carries on without one.
ANSWER_TIMEOUT_SECONDS = float(os.environ.get("ALK_HARNESS_ANSWER_TIMEOUT", "900") or 900)
ANSWER_POLL_SECONDS = 1.0

HANDOVER = (
    "The person watching you said this just now. Answer it or act on it before carrying on "
    "with what you were doing, and say which you did. Then carry on: answering is not "
    "finishing, and a stage that stops here loses everything the spent turns bought. If what "
    "they asked for genuinely ends the work, say so and why rather than going quiet."
)


class Channel:
    """One run's live channel. Cheap to construct and safe to construct when there is none."""

    def __init__(self, directory: str | Path | None = None) -> None:
        raw = directory if directory is not None else os.environ.get(DIRECTORY, "")
        self.root = Path(raw) if raw else None
        self._read = 0
        # Repeated on each tool result until the stage speaks, bounded by HANDOVERS.
        self._pending: list[str] = []
        self._handed = 0

    @property
    def live(self) -> bool:
        return self.root is not None

    def _path(self, name: str) -> Path | None:
        return self.root / name if self.root is not None else None

    def waiting(self) -> list[str]:
        """Everything said since the last read, and nothing twice; malformed lines are skipped."""
        path = self._path(INBOX)
        if path is None or not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        said: list[str] = []
        for line in lines[self._read :]:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and not record.get("answer_to"):
                text = str(record.get("text") or "").strip()
                if text:
                    said.append(text)
        self._read = len(lines)
        if said:
            self._pending.extend(said)
            self._handed = 0
        return said

    def unanswered(self) -> list[str]:
        """What has been said and not yet answered, while it is still worth repeating."""
        if self._handed >= HANDOVERS:
            self._pending = []
        return list(self._pending)

    def handed(self) -> None:
        self._handed += 1

    def ask(self, question: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        """Put a question to whoever is watching and wait, bounded, for their answer."""
        if not self.live:
            return {"answered": False, "reason": "no one is watching this run"}
        asked = str(uuid.uuid4())
        self.record("ask", str(question.get("prompt") or ""), ask_id=asked, question=question)
        limit = ANSWER_TIMEOUT_SECONDS if timeout is None else timeout
        waited = 0.0
        while waited < limit:
            for answer in self._answers():
                if str(answer.get("answer_to") or "") == asked:
                    return {"answered": True, **answer}
            time.sleep(ANSWER_POLL_SECONDS)
            waited += ANSWER_POLL_SECONDS
        return {"answered": False, "reason": f"nobody answered within {limit:g}s"}

    def _answers(self) -> list[dict[str, Any]]:
        """Every answer sitting in the inbox, read whole each time."""
        path = self._path(INBOX)
        if path is None or not path.is_file():
            return []
        found: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("answer_to"):
                found.append(record)
        return found

    def record(self, kind: str, text: str = "", **detail: Any) -> None:
        """Append one event. Never raises: losing the transcript must not lose the run."""
        if kind == "text" and text.strip():
            # It spoke, so it read what it was given.
            self._pending = []
        path = self._path(OUTBOX)
        if path is None:
            return
        record = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "kind": kind,
            "text": text,
            **({"detail": detail} if detail else {}),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, default=str) + "\n")
        except OSError:
            return


def folded_in(message: str, said: list[str]) -> str:
    """The message a stage is about to send, with anything the person said folded into it."""
    if not said:
        return message
    spoken = "\n\n".join(said)
    return f"{message}\n\n---\n\n{HANDOVER}\n\n{spoken}" if message else f"{HANDOVER}\n\n{spoken}"


def carrying(channel: "Channel", servers: dict[str, Any]) -> dict[str, Any]:
    """The stage's own tool servers, each result carrying anything the person has said since."""
    if not channel.live:
        return servers

    def wrap(handler):
        async def carried(arguments: dict[str, Any]) -> dict[str, Any]:
            answered = await handler(arguments)
            for one in channel.waiting():
                channel.record("said", one)
            said = channel.unanswered()
            if not said:
                return answered
            channel.handed()
            content = list(answered.get("content") or [])
            content.append(
                {"type": "text", "text": HANDOVER + "\n\n" + "\n\n".join(said)}
            )
            return {**answered, "content": content}

        return carried

    return {
        name: dataclasses.replace(
            server,
            tools=[
                dataclasses.replace(spec, handler=wrap(spec.handler))
                for spec in server.tools
            ],
        )
        for name, server in servers.items()
    }
