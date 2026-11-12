"""The folder of a repository that holds one agent, for repositories that hold several."""

from __future__ import annotations

import errno
import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path, PurePath, PurePosixPath
from typing import Any

from .failure_file import write_failure_file
from .job import FailureDomain, HarnessFailure, HarnessStage

SUBFOLDER_NOT_FOUND = "subfolder_not_found"

# A link loop or an over-long name is a fault in the path the customer named, not in our disk.
_PATH_ERRNOS = {errno.ELOOP, errno.ENAMETOOLONG}
# The platform's own rule. ALK also runs from jobs the platform never checked, and the value is
# compared exactly and written into prompts, so it is refused rather than normalised.
_FOLDER_FORM = re.compile(r"(?!.*(^|/)\.{1,2}(/|$))[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*")
_FOLDER_MAX = 512


class SubfolderNotFound(Exception):
    """The named folder is not at the commit, is not a folder, is empty, or leaves the repository."""

    def __init__(self, subfolder: str) -> None:
        super().__init__(f"subfolder_not_found: {subfolder}")
        self.subfolder = subfolder


def is_within(path: PurePath, folder: PurePath) -> bool:
    """Whether ``path`` is ``folder`` or inside it.

    ``is_relative_to`` builds every parent of the path on 3.12, so a crafted path thousands of
    parts long costs it seconds; comparing the parts costs their number.
    """
    prefix = folder.parts
    return path.parts[: len(prefix)] == prefix


def _inside(path: Path, folder: Path) -> bool:
    return is_within(path, folder)


def contains_or_is_above(place: PurePath, folder: PurePath) -> bool:
    """Whether ``place`` is ``folder``, a folder inside it, or a folder above it."""
    return is_within(place, folder) or is_within(folder, place)


def contract_folder(contract: Any) -> str:
    """The folder a contract records as its agent, or ``""``."""
    return contract.runtime.source_subfolder if contract.runtime else ""


def in_agent_scope(relative: PurePosixPath, folder: PurePosixPath) -> bool:
    """Whether a file is in the agent's folder or in one of the folders above it."""
    return contains_or_is_above(relative.parent, folder)


def agent_scope(root: Path, folder: str) -> PurePosixPath | None:
    """The folder as the walk below reports it: a link inside the repository is spelled by its
    target there. The literal value stands when the folder can't be resolved."""
    if not folder:
        return None
    resolved = Path(root).resolve()
    try:
        return PurePosixPath(agent_folder(resolved, folder).relative_to(resolved).as_posix())
    except SubfolderNotFound:
        return PurePosixPath(folder)


def agent_folder(root: Path, subfolder: str | None) -> Path:
    """The resolved folder the agent lives in: ``root`` itself when no subfolder is named."""
    resolved_root = Path(root).resolve()
    if not subfolder:
        return resolved_root
    if len(subfolder) > _FOLDER_MAX or not _FOLDER_FORM.fullmatch(subfolder):
        raise SubfolderNotFound(subfolder)
    try:
        folder = (resolved_root / subfolder).resolve()
        if not _inside(folder, resolved_root):
            raise SubfolderNotFound(subfolder)
        # A committed link to the root, or to its own folder or above (`agents/up -> ..`,
        # `self -> .`, or out of the repository and back), would make every sibling this
        # agent's own while the build is still recorded as a folder build.
        if folder == resolved_root or _inside((resolved_root / subfolder).parent.resolve(), folder):
            raise SubfolderNotFound(subfolder)
        # Git stores no empty folder, so an empty one is an uninitialised submodule.
        with os.scandir(folder) as entries:
            if next(entries, None) is None:
                raise SubfolderNotFound(subfolder)
    except (FileNotFoundError, NotADirectoryError, RuntimeError) as error:
        raise SubfolderNotFound(subfolder) from error
    except OSError as error:
        if error.errno in _PATH_ERRNOS:
            raise SubfolderNotFound(subfolder) from error
        raise
    return folder


def subfolder_not_found(
    subfolder: str,
    *,
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> HarnessFailure:
    # The platform refuses a failure message over 2,000 characters.
    subfolder = subfolder[:_FOLDER_MAX]
    return HarnessFailure(
        domain=FailureDomain.AGENT,
        stage=HarnessStage.ACQUIRING_SOURCE,
        code=SUBFOLDER_NOT_FOUND,
        message=message
        or (
            f"The folder '{subfolder}' is not in this repository at the commit being built, "
            "is not a folder, is empty, or is not a valid path. Check the path and the branch."
        ),
        retryable=False,
        details={"subfolder": subfolder, **(details or {})},
    )


def _named(folder: str) -> str:
    return f"'{folder}'" if folder else "the repository root"


def folder_failure(
    root: Path, subfolder: str | None, runtime: Mapping[str, Any] | None = None
) -> HarnessFailure | None:
    """The failure for a job whose folder can't be used, or ``None`` when all is well.

    Given a saved contract's runtime, a contract built from another folder is refused too.
    """
    try:
        agent_folder(root, subfolder)
    except SubfolderNotFound:
        return subfolder_not_found(subfolder or "")
    if runtime is None:
        return None
    built_from = str(runtime.get("source_subfolder") or "")
    wanted = subfolder or ""
    if built_from == wanted:
        return None
    return subfolder_not_found(
        wanted,
        message=(
            f"This agent was built from {_named(built_from)}, but this job names "
            f"{_named(wanted)}."
        ),
        details={"built_from": built_from},
    )


def saved_runtime(authoring: Path) -> Mapping[str, Any] | None:
    """The runtime of the contract saved in ``authoring``, as written.

    ``None`` means there is no contract. A contract with no runtime gives ``{}``, so it counts
    as built from the repository root rather than escaping the folder comparison.
    """
    try:
        saved = json.loads((Path(authoring) / "contract.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict):
        return None
    runtime = saved.get("runtime")
    return runtime if isinstance(runtime, dict) else {}


def refuse_unusable_folder(
    root: Path,
    subfolder: str | None,
    output: Path,
    runtime: Mapping[str, Any] | None = None,
) -> bool:
    """Leave the failure for the platform and say so; ``True`` when the build must stop."""
    failure = folder_failure(root, subfolder, runtime)
    if failure is None:
        return False
    write_failure_file(failure, output)
    print(f"{failure.code}: {failure.message}", file=sys.stderr, flush=True)
    return True


def recorded_folder(runtime: Mapping[str, Any] | None) -> str:
    """The folder a runtime records as its agent, or ``""`` when it records none."""
    if not isinstance(runtime, Mapping):
        return ""
    return str(runtime.get("source_subfolder") or "")


def saved_folder(authoring: Path) -> str:
    """The folder the contract saved in ``authoring`` records, or ``""``."""
    return recorded_folder(saved_runtime(authoring))


def recorded_root(root: Path, runtime: Mapping[str, Any] | None) -> Path:
    """The folder a runtime records as its agent, or ``root`` when it records none."""
    folder = recorded_folder(runtime)
    return Path(root) / folder if folder else Path(root)


def _climbs_out(root: Path, workdir: Path, folder: Path, item: str) -> bool:
    from .runtime_selection import charged_path

    try:
        path = Path(item)
        target = charged_path(path if path.is_absolute() else workdir / path)
        if not target.exists() or _inside(target, folder):
            return False
        # An absolute path outside the repository is a system program, not repository content.
        return not path.is_absolute() or _inside(target, root)
    except (OSError, RuntimeError, ValueError):
        return False


def bind_runtime(
    runtime: Mapping[str, Any] | None, root: Path, subfolder: str | None
) -> Mapping[str, Any] | None:
    """Record the agent folder on a runtime and keep the runtime inside it.

    Without a subfolder the runtime comes back untouched, except that a folder the model
    wrote is removed. ``workdir``, ``compose_file`` and the folder are repository-relative.
    """
    if not subfolder:
        if runtime is not None and "source_subfolder" in runtime:
            return {key: value for key, value in runtime.items() if key != "source_subfolder"}
        return runtime
    # Each path the runtime names is resolved charged: through links, one can cost a lookup for
    # each part of every target it follows. Past the save's budget, PathLookupsSpent says so.
    from .runtime_selection import charged_path

    runtime = dict(runtime or {})
    folder = agent_folder(root, subfolder)
    resolved_root = Path(root).resolve()
    workdir = str(runtime.get("workdir") or "")
    if workdir in ("", "."):
        workdir = subfolder
    try:
        start = charged_path(resolved_root / workdir)
        inside = _inside(start, folder)
    except (OSError, RuntimeError, ValueError):
        inside = False
    if not inside:
        raise ValueError(
            f"runtime_workdir_outside_subfolder: {workdir}; expected {subfolder} or a "
            "folder inside it, relative to the repository root"
        )
    for item in (str(one) for one in runtime.get("command") or []):
        # `--directory=../b` is the same command as `--directory ../b`.
        if any(
            part and _climbs_out(resolved_root, start, folder, part)
            for part in (item, item.partition("=")[2])
        ):
            raise ValueError(f"runtime_command_outside_subfolder: {item}")
    compose_file = str(runtime.get("compose_file") or "")
    if compose_file:
        try:
            parent = charged_path(resolved_root / compose_file).parent
            reachable = contains_or_is_above(parent, folder)
        except (OSError, RuntimeError, ValueError):
            reachable = False
        if not reachable:
            raise ValueError(f"runtime_compose_file_outside_subfolder: {compose_file}")
    return {**runtime, "source_subfolder": subfolder, "workdir": workdir}
