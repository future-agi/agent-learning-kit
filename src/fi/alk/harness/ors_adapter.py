"""The Open Reward Standard, mapped onto an episode.

PR 4's ALK half. `Episode` already drives a world one action at a time; this turns the ORS
wire protocol into those calls, as pure functions over request data. There is no web framework
here on purpose: Platform owns transport, authentication and tenancy, and mounting this on
whatever it already serves should not drag a second framework into the harness. It also means
the protocol can be tested without a server.

**Provisional until the reference client is pinned.** The architecture section requires
capturing a real ORS client's request and response fixtures before the API is frozen, and
warns that a FutureAGI-only demo cannot establish compatibility. This module is written from
the published specification, so treat its shapes as a first pass to check against that client,
not as a settled contract. Anything the spec leaves undefined is marked below.

What the spec gives us, and what it does not:

* **A session is an episode.** `/create_session` only mints an id; `/create` instantiates.
  Creating twice on one id is a 400 and reusing one after `/delete` is refused, so the
  one-episode-per-session shape this adapter enforces is the spec's own.
* **Splits are first class**, typed `train`, `validation` or `test`, which maps directly onto
  what T28 needs.
* **Actions are tool calls and nothing else**, streamed back over SSE.
* **Reward is an optional number whose null the spec never defines**, and the only error
  channel is the one an invalid action already uses. So an ungraded trial is reported as a null
  reward plus `futureagi_grading`, our own field (decision 6). A generic client sees only null,
  which is why the meaning has to live in our SDK and our docs.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, Mapping

from .episode_driver import (
    Episode,
    EpisodeClosed,
    PublicObservation,
    ToolNotPermitted,
)
from .terminal_verdict import Decision, reward_for

# The spec's own limits, carried here so a caller does not have to rediscover them.
SESSION_IDLE_SECONDS = 15 * 60
# A completed action's result stays replayable for this long, for a dropped connection.
RESULT_LINGER_SECONDS = 60
# Responses above this are split across `chunk` events before `end`.
CHUNK_BYTES = 4096

# Our extension, named so it is obviously not the standard's.
GRADING_FIELD = "futureagi_grading"


class SplitType(str, Enum):
    """ORS split types. A custom split name defaults to `validation`."""

    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"


class OrsError(RuntimeError):
    """A protocol-level refusal, carrying the status the spec assigns it."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Split:
    name: str
    type: SplitType = SplitType.VALIDATION


@dataclass
class Session:
    """One allocated session and the single episode it may hold.

    Separate from `Episode` because a session exists before an episode does: `/create_session`
    mints the id, and `/create` is what instantiates the world.
    """

    sid: str
    episode: Episode | None = None
    deleted: bool = False
    # Completed action results, keyed by task_id, for reconnect replay within the linger window.
    results: dict[str, dict[str, Any]] = field(default_factory=dict)


class SessionStore:
    """In-memory sessions, for a single process.

    Deliberately not durable. Durable ownership, leases and expiry are Platform's (PR 5), and a
    store that looked durable here would invite someone to rely on it across workers.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self) -> Session:
        sid = uuid.uuid4().hex
        self._sessions[sid] = Session(sid=sid)
        return self._sessions[sid]

    def require(self, sid: str | None) -> Session:
        """The session, or the refusal the spec assigns.

        A deleted session is `410 Gone` rather than `404`, so a client can tell "this never
        existed" from "you already finished with it" — the difference between a bug and a
        late retry.
        """
        if not sid:
            raise OrsError(400, "missing X-Session-ID header")
        session = self._sessions.get(sid)
        if session is None:
            raise OrsError(404, f"unknown session {sid}")
        if session.deleted:
            raise OrsError(410, f"session {sid} was deleted; sessions are not reusable")
        return session

    def forget(self, sid: str) -> None:
        self._sessions.pop(sid, None)


# --- discovery -------------------------------------------------------------------------


def health() -> dict[str, Any]:
    return {"status": "ok"}


def list_environments(names: list[str]) -> list[str]:
    return list(names)


def splits_response(splits: list[Split]) -> list[dict[str, Any]]:
    return [{"name": one.name, "type": one.type.value} for one in splits]


def tasks_response(env_name: str, handles: list[str]) -> dict[str, Any]:
    """Opaque task handles for one split.

    Handles only: the full task, its reference answer and its checks resolve server-side on
    `/create`, so nothing a caller holds can be substituted for them.
    """
    return {"env_name": env_name, "tasks": list(handles)}


def task_range(handles: list[str], start: int | None, stop: int | None) -> list[str]:
    """A slice of a split's handles, with Python slice semantics, as the spec specifies."""
    return handles[start:stop]


def prompt_blocks(observation: PublicObservation) -> list[dict[str, Any]]:
    """The prompt as ORS Blocks. Text only here; the spec also allows base64 images."""
    blocks: list[dict[str, Any]] = [{"type": "text", "text": observation.prompt}]
    blocks.extend({"type": "text", "text": message} for message in observation.messages)
    return blocks


def tools_response(observation: PublicObservation) -> list[dict[str, Any]]:
    """The published tools, in the shape a tool-calling client expects.

    Argument types are not carried: the catalogue publishes names, and a type the environment
    does not actually enforce would be a promise we have not made.
    """
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": {
                "type": "object",
                "properties": {name: {} for name in tool.arguments},
            },
        }
        for tool in observation.tools
    ]


# --- episode lifecycle -----------------------------------------------------------------


def create_session(store: SessionStore) -> dict[str, Any]:
    """`/create_session`. Mints an id and nothing else; `/create` builds the world."""
    return {"sid": store.create().sid}


def create_episode(session: Session, episode: Episode) -> dict[str, Any]:
    """`/create`. Instantiates the one episode this session may hold.

    A session already holding an episode is a 400, matching the spec's "Session already
    exists". That is what makes one-episode-per-session the standard's shape and not a
    limitation of ours.
    """
    if session.episode is not None:
        raise OrsError(400, f"session {session.sid} already has an episode")
    session.episode = episode
    return {"sid": session.sid}


def require_episode(session: Session) -> Episode:
    if session.episode is None:
        raise OrsError(400, f"session {session.sid} has no episode; call /create first")
    return session.episode


def delete_episode(session: Session, *, decision: Decision | None = None) -> dict[str, Any]:
    """`/delete`. Tears the episode down and reports the terminal grade if there is one.

    Closing an episode that already finished preserves its grade rather than regrading it.
    """
    episode = require_episode(session)
    episode.freeze()
    session.deleted = True
    body: dict[str, Any] = {"ok": True}
    if decision is not None:
        body["reward"] = reward_for(decision)
        body[GRADING_FIELD] = grading_status(decision)
    return body


def grading_status(decision: Decision) -> dict[str, Any]:
    """Our extension: what the reward means, since ORS leaves null undefined.

    `graded` is the field a trainer must branch on. When it is false the reward is null and the
    sample carries no label — never a zero, which would train against behaviour nobody judged.
    """
    return {
        "graded": decision.graded,
        "status": decision.status.value,
        "code": decision.failure_code,
        "detail": decision.failure_detail,
    }


# --- actions ---------------------------------------------------------------------------


def _event(name: str, data: Any) -> str:
    """One SSE event, framed as the spec requires."""
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


def call_events(
    session: Session,
    *,
    name: str,
    arguments: Mapping[str, Any] | None = None,
    task_id: str | None = None,
) -> Iterator[str]:
    """`/{env}/call` as an SSE stream.

    The spec's order: `task_id` always first, then `chunk` events for a large payload, then
    `end` carrying the final JSON. A raised action sends `error` and **no** `end`.

    `task_id` is the spec's reconnection key, not an idempotency key: a reconnect with a known
    id replays the stored result instead of acting again, and results linger only 60 seconds.
    It is also the episode's action id here, so a replayed action is refused by the door rather
    than committed twice — which is what makes a lost response safe. The spec does not define
    retry semantics beyond reconnection, so a client must not retry an ambiguous write blindly.
    """
    action_id = task_id or uuid.uuid4().hex
    yield _event("task_id", {"task_id": action_id})

    stored = session.results.get(action_id)
    if stored is not None:
        # A reconnect: replay, do not act again.
        yield from _emit_end(stored)
        return

    try:
        episode = require_episode(session)
        receipt = episode.step(action_id=action_id, tool=name, arguments=arguments or {})
    except ToolNotPermitted as exc:
        yield _event("end", {"ok": False, "error": str(exc)})
        return
    except EpisodeClosed as exc:
        yield _event("end", {"ok": False, "error": str(exc)})
        return
    except Exception as exc:  # noqa: BLE001 - the spec's `error` event is for a raised action
        yield _event("error", {"error": f"{type(exc).__name__}: {exc}"})
        return

    public = receipt.public
    body: dict[str, Any] = (
        {"ok": True, "output": public.output}
        if public.ok
        else {"ok": False, "error": public.error}
    )
    session.results[action_id] = body
    yield from _emit_end(body)


def _emit_end(body: Mapping[str, Any]) -> Iterator[str]:
    """`end`, preceded by `chunk` events when the payload is large.

    The spec has the client concatenate chunk data with the final `end` data, so the chunks
    carry the oversized field and `end` carries the rest.
    """
    encoded = json.dumps(body)
    if len(encoded.encode()) <= CHUNK_BYTES:
        yield _event("end", dict(body))
        return
    for index in range(0, len(encoded), CHUNK_BYTES):
        yield _event("chunk", {"data": encoded[index : index + CHUNK_BYTES]})
    yield _event("end", {"chunked": True})


def terminal_output(decision: Decision, output: Any = None) -> dict[str, Any]:
    """A final `ToolOutput`: the reward, `finished`, and what the reward means.

    `reward` is null for an ungraded trial. A client reading only the standard's fields sees
    that null and nothing more, so `futureagi_grading` is what tells it whether to keep the
    sample.
    """
    return {
        "output": output,
        "reward": reward_for(decision),
        "finished": True,
        GRADING_FIELD: grading_status(decision),
    }
