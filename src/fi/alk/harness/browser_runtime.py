"""The environment's browser in a world's life: whether a job and its agent call for one, starting it with each
spawn of the world for the agent's process alone, stopping it before a reset, and closing it with the world.

The browser itself, its proxy and its DevTools relay are in `kit_browser.py`; this module is how a world's processes
meet it. It is kept apart from `process_runtime.py`, which calls it, and imports from there only inside functions.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .bundle_v2 import ProcessUser
from .job import FailureDomain
from .world_door import PostgresLog

if TYPE_CHECKING:
    from .process_runtime import ProcessRuntimeError, RuntimeEndpoint, SpawnContext

logger = logging.getLogger(__name__)


def read_browser_allowed_sites(work_directory: Path) -> tuple[str, ...] | None:
    """The sites the environment's browser may open, from `/work/job.json` `metadata.browser_allowed_sites`; None
    when the environment has no browser. A list that is present and wrong fails the spawn, as the build's start
    would have."""
    from .browser_environment import SitesProblem, allowed_sites

    from .process_runtime import read_job_json

    raw = read_job_json(work_directory, _browser_unknown)
    if raw is None:
        return None
    metadata = raw.get("metadata") if isinstance(raw, dict) else None
    try:
        return allowed_sites(metadata if isinstance(metadata, dict) else None)
    except SitesProblem as problem:
        raise _browser_unknown(problem.failure.message) from problem


def _browser_unknown(reason: str) -> ProcessRuntimeError:
    from .process_runtime import ProcessRuntimeError

    return ProcessRuntimeError("browser", "spawn_failed", reason, domain=FailureDomain.INFRASTRUCTURE)


def contract_needs_browser(bundle_dir: Path | None) -> bool:
    """Whether the bundle's contract says its agent needs a browser from the environment; False for none, or one
    that can't be read (the build's own steps report that)."""
    from .contract import AgentContract, how_it_runs

    if bundle_dir is None:
        return False
    path = Path(bundle_dir) / "contract.json"
    try:
        contract = AgentContract.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        # Taken for no browser, so the trial will say none was started; the cause is here.
        logger.warning("browser: the agent's contract at %s could not be read", path, exc_info=True)
        return False
    return "browser" in how_it_runs(contract).needs


def browser_started(context: SpawnContext) -> bool:
    return context.browser_allowed_sites is not None and context.needs_browser


def world_browser_environment(
    context: SpawnContext, world_index: int, endpoints: dict[str, RuntimeEndpoint]
) -> dict[str, str]:
    """The browser's address for the agent's process: the world's browser, started anew on an empty profile at each
    spawn of the world; nothing where the environment gives the agent no browser."""
    if not browser_started(context):
        return {}
    browser = context.browsers.get(world_index)
    if browser is None:
        from .browser_record import BlockedFileLog

        database = next((one for one in endpoints.values() if one.protocol == "postgres"), None)
        door = any(name in endpoints for name in ("world_door", "tool_proxy"))
        # With a door and a database the door's log is the one source of refusals, read by the trial as the agent's
        # other calls are; with no door a trial never reads that log, so the refusals go to a kit-owned file.
        log: Any = (
            PostgresLog(database.address)
            if door and database is not None
            else BlockedFileLog(context.work_directory / "browser-state" / f"w{world_index}-blocked.jsonl")
        )
        make = context.browser_factory or kit_browser_factory(context)
        try:
            browser = make(world_index, log)
        except OSError as exc:
            from .process_runtime import ProcessRuntimeError

            raise ProcessRuntimeError(
                "browser", "spawn_failed", f"the environment's browser could not be made: {exc}",
                domain=FailureDomain.INFRASTRUCTURE,
            ) from exc
        context.browsers[world_index] = browser
    try:
        browser.start()
    except (OSError, RuntimeError) as exc:
        from .process_runtime import ProcessRuntimeError

        raise ProcessRuntimeError(
            "browser", "spawn_failed", f"the environment's browser did not start: {exc}",
            domain=FailureDomain.INFRASTRUCTURE,
        ) from exc
    return browser.environment()


def kit_browser_factory(context: SpawnContext) -> Callable[[int, Any], Any]:
    """Makes a world's `KitBrowser`: Chromium from the image, run as the agent's user through the context's runner,
    with its profile in the world's scratch and its state where only the kit writes."""
    from .browser_record import BrowserRecorder
    from .kit_browser import KitBrowser, chromium_binary
    from .process_runtime import _resolve_process_user, _terminate_and_wait, world_scratch_dir

    binary = chromium_binary()
    if binary is None:
        raise _browser_unknown("this image has no browser (alk-chromium) for a browser environment")
    user = _resolve_process_user(
        ProcessUser.SVC_AGENT,
        resolver=context.user_resolver,
        require=context.require_declared_user,
        process_name="browser",
        stage="browser",
        domain=FailureDomain.INFRASTRUCTURE,
    )
    uid = user.pw_uid if user is not None else None
    gid = user.pw_gid if user is not None else None

    def make(world_index: int, log: Any) -> Any:
        state = context.work_directory / "browser-state" / f"w{world_index}.json"
        state.parent.mkdir(parents=True, exist_ok=True)

        def launch(argv: list[str], cwd: Path, env: dict[str, str], log_path: Path) -> Any:
            return context.runner(argv, cwd=cwd, env=env, log_path=log_path, user=uid, group=gid)

        def prepare(profile: Path) -> None:
            # The profile is the agent's, and so is the browser's log, which the runner hands to the user it starts
            # the browser as. The folder around them, and the browser's state file, stay the kit's.
            if uid is not None:
                context.chown(profile, uid, gid if gid is not None else -1)

        return KitBrowser(
            binary=binary,
            sites=context.browser_allowed_sites or (),
            log=log,
            directory=world_scratch_dir(context.work_directory, world_index, "browser"),
            state_path=state,
            launch=launch,
            stop=lambda handle: _terminate_and_wait(handle),
            prepare=prepare,
            retain_log=context.retain_process_log,
            recorder=BrowserRecorder(
                context.browser_allowed_sites or (),
                state.with_name(f"w{world_index}-record.json"),
                # A secret of the job's the agent types into a page is masked before the record is written.
                secret_values=tuple(context.secret_values.values()),
            ),
        )

    return make


def browser_metadata(context: SpawnContext, world_index: int) -> dict[str, Any]:
    """What the world's runtime says of its browser, for the trial's notices."""
    if context.browser_allowed_sites is None:
        return {}
    if not context.needs_browser:
        return {"browser": {"state": "not_started"}}
    browser = context.browsers.get(world_index)
    return {"browser": browser.metadata()} if browser is not None else {}


def stop_browser(context: SpawnContext, world_index: int) -> None:
    """Stop one world's browser, if it has one. Logged, never raised."""
    browser = context.browsers.get(world_index)
    if browser is None:
        return
    try:
        browser.stop()
    except Exception:  # noqa: BLE001 - the world is reset whatever its browser does
        logger.warning("reset: the browser of world %s did not stop cleanly", world_index, exc_info=True)


def close_browsers(context: SpawnContext, world_index: int | None = None) -> None:
    """Stop one world's browser, or every world's, and close its proxy and relay. Logged, never raised."""
    for index in [world_index] if world_index is not None else list(context.browsers):
        browser = context.browsers.pop(index, None)
        if browser is None:
            continue
        try:
            browser.close()
        except Exception:  # noqa: BLE001 - teardown reaches the end whatever one browser does
            logger.warning("teardown: the browser of world %s did not close cleanly", index, exc_info=True)
