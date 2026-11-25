"""Run scenario code in a disposable interpreter against its assigned world."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import random
import sys
from pathlib import Path
from typing import Any


def tuples(value: Any) -> Any:
    return tuple(tuples(item) for item in value) if isinstance(value, list) else value


async def main() -> None:
    from .world.errors import (
        WorldError,
        WorldQueryRejected,
        WorldReadOnly,
        WorldReservedName,
        WorldStateTooLarge,
        WorldUnavailable,
        WorldUsageError,
    )
    from .checks import check_namespace
    from .isolated_process import CHANNEL_FD_ENV
    from .world.handle import HostedWorld
    from .world.runtime import Call
    from .world.stores.postgres import AttachedPostgresStore
    from .world.workspace_channel import WorkspaceClient

    payload = json.load(sys.stdin)
    state = payload["world"]
    rng = random.Random()
    rng.setstate(tuples(state["rng"]))
    http_client = None
    try:
        if state.get("kind") == "native_observation":
            from .world.observation import ObservationWorld
            world = ObservationWorld(
                state["world_index"], rng, state.get("endpoints", {}),
                observations=state.get("observations", {}),
            )
            http_client = world
        else:
            # The harness serves the workspace files: under per-user isolation this
            # process cannot open them.
            channel_fd = os.environ.get(CHANNEL_FD_ENV)
            workspaces = WorkspaceClient(int(channel_fd)) if channel_fd else None
            world = HostedWorld(
                AttachedPostgresStore(
                    state["dsn"],
                    admin_dsn=state.get("admin_dsn"),
                    restore_as_role=bool(state.get("restore_as_role")),
                    reader_dsn=state.get("reader_dsn"),
                ),
                state["world_index"],
                rng,
                state["baseline"],
                # The parent already validated this handle before scenario execution.
                _validate_baseline=False,
                workspace_channel=workspaces,
                endpoints=state.get("endpoints", {}),
                observations=state.get("observations", {}),
            )
            http_client = world._http_client
        http_client._restore_private_http_state(payload.get("private_http", {}))
        handle = world.read_only() if state["read_only"] else world
        # The same names a check is given while authoring (`same_id`), so a proved check runs the same here.
        namespace: dict[str, Any] = check_namespace()
        exec(compile(payload["source"], "<scenario-phase>", "exec"), namespace)  # noqa: S102 - isolated scenario execution
        args = [handle]
        if "calls" in payload:
            args.append(tuple(Call(**call) for call in payload["calls"]))
        if "reference" in payload:
            args.append(payload["reference"])
        value = namespace[payload["entry"]](*args)
        if inspect.isawaitable(value):
            value = await value
        valid_verdict = value is None or isinstance(value, (bool, str))
        result = {
            "value": value if valid_verdict else None,
            "invalid_verdict": not valid_verdict,
            "rng": rng.getstate(),
        }
    except BaseException as exc:  # noqa: BLE001 - scenario exit must become a reported phase fault
        result = {
            "error": type(exc).__name__,
            "world_error": next(
                (
                    kind.__name__
                    for kind in (
                        WorldUnavailable,
                        WorldStateTooLarge,
                        WorldReadOnly,
                        WorldReservedName,
                        WorldQueryRejected,
                        WorldUsageError,
                        WorldError,
                    )
                    if isinstance(exc, kind)
                ),
                None,
            ),
            "rng": rng.getstate(),
        }
    if http_client is not None:
        if isinstance(result.get("value"), str):
            result["value"] = http_client._redact_http_text(result["value"])
        try:
            result["private_http"] = http_client._private_http_state()
        except WorldUsageError:
            result = {
                "error": "WorldUsageError",
                "world_error": "WorldUsageError",
                "rng": rng.getstate(),
                "private_http": {},
            }
        result["http_events"] = http_client._drain_http_events()
    fd = os.open(Path(sys.argv[1]), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(result, stream)


if __name__ == "__main__":
    asyncio.run(main())
