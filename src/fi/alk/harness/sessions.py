"""One conversation, one folder.

Everything about testing one agent lives in a single directory: what the agent is, the world
built for it, the scenarios written against that world, what happened when they ran, and the
conversation that produced all of it.

That is the whole state model. There is nothing held in memory that is not also on disk, so
closing the page, restarting the server or coming back tomorrow all resume the same way — by
reading the folder. A session that only existed in a process would be a session you could lose
by refreshing.

    artifacts/sessions/<id>/
        session.json          what this is: the agent, where its source lives, when it started
        chat.jsonl            the conversation, one message per line
        contract.json         stage 1
        world.sqlite          stage 2, with handlers/, simulator_prompt.md, sub_goals.json
        scenarios/<name>/     stage 3, one folder each
        runs.json             stage 4

The id is readable and unique: the agent's name with a short suffix, so two attempts at the same
agent are two sessions rather than one overwriting the other.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SESSIONS = Path("artifacts/sessions")
META = "session.json"
CHAT = "chat.jsonl"


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (text or "session").lower()).strip("-")
    return cleaned[:32] or "session"


def root(base: Path | None = None) -> Path:
    return Path(base) if base else SESSIONS


def new_id(agent: str = "", base: Path | None = None) -> str:
    """A readable, unique id. Two goes at the same agent are two sessions, not one clobbered."""
    stem = _slug(agent)
    while True:
        candidate = f"{stem}-{secrets.token_hex(3)}"
        if not (root(base) / candidate).exists():
            return candidate


@dataclass
class Session:
    """One conversation's folder, and what is in it."""

    id: str
    path: Path
    agent: str = ""
    source: str = ""
    kind: str = "repo"
    created: float = 0.0
    updated: float = 0.0
    stage: str = ""
    title: str = ""

    def meta(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent": self.agent,
            "source": self.source,
            "kind": self.kind,
            "created": self.created,
            "updated": self.updated,
            "stage": self.stage,
            "title": self.title,
        }

    def has(self) -> dict[str, Any]:
        """What this session has actually produced, read from the folder rather than remembered.

        Asking the folder means the answer survives a restart, and it cannot drift from what is
        really there — which is what makes reopening a session trustworthy.
        """
        from .catalogue import load_catalogue
        from .folder import read_all
        from .world.snapshot import saved as world_saved

        scenarios = read_all(self.path) if self.path.exists() else []
        runs = _runs(self.path)
        return {
            "contract": (self.path / "contract.json").exists(),
            "world": world_saved(self.path),
            "simulator_prompt": (self.path / "simulator_prompt.md").exists(),
            "sub_goals": len(load_catalogue(self.path).sub_goals) if self.path.exists() else 0,
            "scenarios": len(scenarios),
            "validated": None,  # filled in by whoever wants to pay for proving them
            "runs": len(runs),
            "runs_passed": sum(1 for one in runs if one.get("passed")),
            "messages": count_messages(self.path),
        }


def _runs(path: Path) -> list[dict[str, Any]]:
    found = path / "runs.json"
    if not found.exists():
        return []
    try:
        loaded = json.loads(found.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, list) else []
    except json.JSONDecodeError:
        return []


def create(agent: str = "", source: str = "", kind: str = "repo", base: Path | None = None) -> Session:
    """Start a new conversation, with its own folder."""
    identifier = new_id(agent, base)
    path = root(base) / identifier
    path.mkdir(parents=True, exist_ok=True)
    now = time.time()
    session = Session(
        id=identifier,
        path=path,
        agent=agent,
        source=source,
        kind=kind,
        created=now,
        updated=now,
        stage="reception",
        title=agent or "new session",
    )
    save(session)
    return session


def save(session: Session) -> None:
    session.updated = time.time()
    session.path.mkdir(parents=True, exist_ok=True)
    (session.path / META).write_text(
        json.dumps(session.meta(), indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load(identifier: str, base: Path | None = None) -> Session | None:
    path = root(base) / identifier
    if not path.is_dir():
        return None
    body: dict[str, Any] = {}
    found = path / META
    if found.exists():
        try:
            body = json.loads(found.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            body = {}
    return Session(
        id=identifier,
        path=path,
        agent=str(body.get("agent") or ""),
        source=str(body.get("source") or ""),
        kind=str(body.get("kind") or "repo"),
        created=float(body.get("created") or path.stat().st_ctime),
        updated=float(body.get("updated") or path.stat().st_mtime),
        stage=str(body.get("stage") or ""),
        title=str(body.get("title") or identifier),
    )


def every(base: Path | None = None) -> list[Session]:
    """Every session, newest first."""
    here = root(base)
    if not here.exists():
        return []
    found = [load(one.name, base) for one in here.iterdir() if one.is_dir()]
    return sorted((one for one in found if one), key=lambda s: s.updated, reverse=True)


def remove(identifier: str, base: Path | None = None) -> bool:
    """Delete a session and everything in it.

    Deliberately narrow: it will only remove a directory that sits directly inside the sessions
    root and holds a session file, so a mistyped id can never take anything else with it.
    """
    here = (root(base) / identifier).resolve()
    parent = root(base).resolve()
    if here.parent != parent or not here.is_dir():
        return False
    if not (here / META).exists():
        return False
    shutil.rmtree(here)
    return True


# -- the conversation itself --------------------------------------------------------


@dataclass
class Message:
    """One thing said, by either side."""

    role: str  # "you" or "harness"
    text: str = ""
    stage: str = ""
    at: float = 0.0
    # What the harness did while answering, so a reopened conversation shows the work and not
    # only the conclusion.
    tools: list[dict[str, Any]] = field(default_factory=list)

    def body(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "text": self.text,
            "stage": self.stage,
            "at": self.at or time.time(),
            "tools": self.tools,
        }


def remember(path: Path, message: Message) -> None:
    """Append one message to this session's conversation."""
    path.mkdir(parents=True, exist_ok=True)
    with (path / CHAT).open("a", encoding="utf-8") as file:
        file.write(json.dumps(message.body(), ensure_ascii=False) + "\n")


def history(path: Path) -> list[dict[str, Any]]:
    """The whole conversation, in order.

    A line that will not parse is skipped rather than taking the rest with it: a half-written
    line at the end is the ordinary result of a process being killed mid-write, and losing the
    conversation because of it would be absurd.
    """
    found = Path(path) / CHAT
    if not found.exists():
        return []
    messages: list[dict[str, Any]] = []
    for line in found.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return messages


def count_messages(path: Path) -> int:
    return len(history(path))
