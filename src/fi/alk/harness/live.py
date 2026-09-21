"""The live channel between a running harness and whoever is watching it.

A stage is already a conversation: it holds a session open, takes a message, and emits typed
events. What it has never had is a way for that conversation to reach anybody while it is
running on somebody else's machine. The hosted path could only hand it corrections between
stages, and a stage lasts twenty minutes, so a correction sent during one arrived after it.

Two files under one directory, both append-only JSONL:

``inbox.jsonl``
    What the person said. Written by whoever is driving from outside, read here.

``outbox.jsonl``
    Every event the stage emitted, in the order it emitted them. Written here, tailed by
    whoever is driving from outside.

Files rather than a socket because that is the channel a sandbox actually has: the provider
refuses public ingress, and uploading a file to a running sandbox is already how corrections and
cancellation travel. Nothing in this module knows what is on the other end.

**Inert unless ``ALK_HARNESS_CHAT_DIR`` is set.** An unattended run sets nothing and behaves
exactly as it did before, which is the only acceptable price for putting this on the path every
stage takes.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
import uuid
import uuid
from pathlib import Path
from typing import Any

DIRECTORY = "ALK_HARNESS_CHAT_DIR"
INBOX = "inbox.jsonl"
OUTBOX = "outbox.jsonl"

# A message is handed over on the back of whatever the harness does next. Said plainly, because
# it arrives out of nowhere and the alternative is that the model treats it as its own idea.
# How many tool results a message rides in on before it is let go. Enough to survive a burst of
# calls, few enough that a stage which never speaks is not nagged into a loop.
HANDOVERS = 4

# How long a question waits for its answer before the run carries on without one, and how often it
# looks. A person who closed the tab must not hold a sandbox open to its TTL.
ANSWER_TIMEOUT_SECONDS = float(os.environ.get("ALK_HARNESS_ANSWER_TIMEOUT", "900") or 900)
ANSWER_POLL_SECONDS = 1.0

# How long a question waits for its answer before the run carries on without one. A person who
# closed the tab must not hold a sandbox open to its TTL, and an unanswered question is recoverable
# where a stuck job is not.
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
        # What has been handed over but not yet answered. A message rides in on a tool result,
        # and a model part way through a burst of calls reads one result and keeps going, so
        # handing it over once is handing it over to nobody. Repeated until the stage actually
        # says something, and bounded so a stage that never speaks is not nagged for ever.
        self._pending: list[str] = []
        self._handed = 0

    @property
    def live(self) -> bool:
        return self.root is not None

    def _path(self, name: str) -> Path | None:
        return self.root / name if self.root is not None else None

    def waiting(self) -> list[str]:
        """Everything said since the last read, and nothing twice.

        A malformed line is skipped rather than raised on: the writer is another process, and a
        half-flushed line must not take the stage down with it.
        """
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
        """Put a question to whoever is watching and wait for their answer.

        Blocking on purpose: the model asked because it cannot sensibly go on, and going on anyway
        with a guess is the behaviour this exists to replace. Bounded on purpose too, because a
        sandbox held open by somebody who closed the tab is a worse failure than a question nobody
        answered.

        Returns the answer, or ``answered: False``. What an unanswered question means is the
        caller's to decide; this reports the fact and nothing else.
        """
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
        """Every answer sitting in the inbox, read whole each time.

        Answers are not consumed the way messages are. A question asked just after its answer was
        written must still find it, and the inbox is small enough that re-reading costs nothing.
        """
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
    """The same tool servers, each result carrying anything the person has said since.

    A stage is one long turn: the model is sent an opening message and then runs for twenty
    minutes driving tools, so there is no second message for an inbox check to ride in on. Tool
    results are the thing it reads constantly, every few seconds, whichever backend is running
    it. Putting the handover there is what makes this a conversation rather than a queue, and it
    needs nothing from the provider's protocol.

    Only the stage's own servers, never a worker's. A writer told to stop and answer a question
    addressed to the stage that briefed it would answer for everybody.
    """
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
