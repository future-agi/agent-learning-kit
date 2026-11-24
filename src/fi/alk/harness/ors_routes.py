"""The ORS endpoints, served from inside the sandbox.

The missing transport. `ors_adapter` maps the protocol onto an episode as pure functions;
this exposes them over HTTP so something can actually call them. It lives here rather than in
Platform because Platform does not import `fi.alk` at all -- ALK runs behind a sandbox
boundary -- so the environment's own process is the only place that can serve a world. Platform
authenticates the trainer, resolves which release and split it may touch, and proxies.

Mounted as a router on the existing sandbox app, with that app's own authorization dependency.
A second FastAPI app would mean a second port to open and a second auth story.

Deliberately not implemented: `/num_tasks`, `/task` and `/task_range`, which are read-only
conveniences a client can get from `/tasks`, and `/ping`, which matters only once Platform owns
session expiry. Add them when the pinned reference client is shown to need them.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator, Protocol

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from . import ors_adapter
from .episode_driver import Episode
from .ors_adapter import OrsError, SessionStore, Split
from .terminal_verdict import Decision


class Releases(Protocol):
    """What the sandbox knows about the environments it is holding.

    Injected rather than reached for, so the routes can be tested without a world and so the
    sandbox stays the only thing that decides what exists.
    """

    def environments(self) -> list[str]: ...

    def splits(self, environment: str) -> list[Split]: ...

    def task_handles(self, environment: str, split: str) -> list[str]: ...

    def open_episode(
        self, environment: str, *, split: str | None, index: int | None, task: str | None
    ) -> Episode:
        """Resolve the task server-side and build its episode.

        Resolution happens here so a caller cannot substitute a task, a grader or a seed: it
        passes a split and index, or an opaque handle, and nothing else.
        """
        ...

    def grade(self, episode: Episode) -> Decision | None:
        """The terminal decision for a finished episode, or None if it cannot be graded yet."""
        ...


def _refuse(exc: OrsError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


def ors_router(
    releases: Releases,
    *,
    store: SessionStore | None = None,
    authorize: Callable[..., Any] | None = None,
) -> APIRouter:
    """The ORS routes for one sandbox.

    `authorize` is the host app's dependency; passing None leaves the routes open, which is
    only right when something in front of them has already authenticated the caller.
    """
    sessions = store or SessionStore()
    router = APIRouter(dependencies=[authorize] if authorize is not None else [])

    def _session(sid: str | None):
        try:
            return sessions.require(sid)
        except OrsError as exc:
            raise _refuse(exc) from exc

    def _episode(sid: str | None) -> Episode:
        session = _session(sid)
        try:
            return ors_adapter.require_episode(session)
        except OrsError as exc:
            raise _refuse(exc) from exc

    # --- discovery ---------------------------------------------------------------------

    @router.get("/list_environments")
    async def list_environments() -> list[str]:
        return ors_adapter.list_environments(releases.environments())

    @router.get("/{environment}/splits")
    async def splits(environment: str) -> list[dict[str, Any]]:
        return ors_adapter.splits_response(releases.splits(environment))

    @router.post("/{environment}/tasks")
    async def tasks(environment: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        split = str((body or {}).get("split") or "train")
        return ors_adapter.tasks_response(
            environment, releases.task_handles(environment, split)
        )

    # --- lifecycle ---------------------------------------------------------------------

    @router.post("/create_session")
    async def create_session() -> dict[str, Any]:
        return ors_adapter.create_session(sessions)

    @router.post("/create")
    async def create(
        body: dict[str, Any] | None = None,
        x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
    ) -> dict[str, Any]:
        session = _session(x_session_id)
        request = body or {}
        task = request.get("task_spec")
        split = request.get("split")
        index = request.get("index")
        # The spec allows exactly one of the two forms; both or neither is a 400.
        if (task is None) == (split is None and index is None):
            raise HTTPException(
                status_code=400, detail="give exactly one of task_spec, or split with index"
            )
        try:
            episode = releases.open_episode(
                str(request.get("env_name") or ""),
                split=str(split) if split is not None else None,
                index=int(index) if index is not None else None,
                task=str(task) if task is not None else None,
            )
        except (KeyError, ValueError, IndexError) as exc:
            # A handle or index that resolves to nothing is the caller's error, not a fault.
            raise HTTPException(status_code=400, detail=f"unknown task: {exc}") from exc
        try:
            return ors_adapter.create_episode(session, episode)
        except OrsError as exc:
            raise _refuse(exc) from exc

    @router.get("/{environment}/prompt")
    async def prompt(
        environment: str,
        x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
    ) -> list[dict[str, Any]]:
        return ors_adapter.prompt_blocks(_episode(x_session_id).observe())

    @router.get("/{environment}/task_tools")
    async def task_tools(
        environment: str,
        x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
    ) -> list[dict[str, Any]]:
        return ors_adapter.tools_response(_episode(x_session_id).observe())

    @router.get("/{environment}/tools")
    async def tools(
        environment: str,
        x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
    ) -> list[dict[str, Any]]:
        """The spec separates shared tools from task tools. This environment has one set."""
        return await task_tools(environment, x_session_id)

    # --- acting ------------------------------------------------------------------------

    @router.post("/{environment}/call")
    async def call(
        environment: str,
        request: Request,
        x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
    ) -> StreamingResponse:
        session = _session(x_session_id)
        body = await request.json() if await request.body() else {}

        def stream() -> Iterator[str]:
            yield from ors_adapter.call_events(
                session,
                name=str(body.get("name") or ""),
                arguments=body.get("input") or {},
                task_id=body.get("task_id"),
            )

        # The spec says a server returns SSE regardless of the Accept header.
        return StreamingResponse(stream(), media_type="text/event-stream")

    # --- finishing ---------------------------------------------------------------------

    @router.post("/delete")
    async def delete(
        x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
    ) -> dict[str, Any]:
        session = _session(x_session_id)
        try:
            decision = releases.grade(ors_adapter.require_episode(session))
            return ors_adapter.delete_episode(session, decision=decision)
        except OrsError as exc:
            raise _refuse(exc) from exc

    @router.post("/delete_session")
    async def delete_session(
        x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
    ) -> dict[str, Any]:
        """Optional in the spec. Forgets the id; `/delete` is what releases the world."""
        if x_session_id:
            sessions.forget(x_session_id)
        return {"ok": True}

    return router
