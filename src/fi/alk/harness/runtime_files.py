"""Relocate build-local launchers after copying a world runtime tree."""

from __future__ import annotations

import os
from pathlib import Path


def prepare_runtime_environment(root: Path) -> dict[str, str]:
    """Keep build-downloaded assets inside the tree copied into each world."""
    state = root / ".alk-runtime"
    home = state / "home"
    cache = state / "cache"
    huggingface = cache / "huggingface"
    env = {
        "HOME": str(home),
        "XDG_CACHE_HOME": str(cache),
        "HF_HOME": str(huggingface),
        "HF_HUB_CACHE": str(huggingface / "hub"),
        "HUGGINGFACE_HUB_CACHE": str(huggingface / "hub"),
    }
    for value in env.values():
        directory = Path(value)
        if not directory.resolve().is_relative_to(root.resolve()):
            raise OSError("runtime cache directory escapes the runtime tree")
        directory.mkdir(parents=True, exist_ok=True)
    return env


def relocate_runtime_files(build: Path, runtime: Path) -> None:
    """Redirect internal absolute links and virtualenv launchers to the private copy.

    System interpreter links stay shared. This is path isolation for cooperative
    processes, not a filesystem security boundary between processes sharing a UID.
    """
    old = str(build.resolve())
    new = str(runtime.resolve())
    for path in runtime.rglob("*"):
        if path.is_symlink():
            target = os.readlink(path)
            if target == old or target.startswith(old + os.sep):
                path.unlink()
                path.symlink_to(new + target[len(old) :])
        elif path.is_file() and (
            path.parent.name == "bin"
            or path.suffix == ".pth"
            or (
                path.name.startswith("__editable__")
                and path.name.endswith("_finder.py")
            )
        ):
            if path.stat().st_size > 1024 * 1024:
                continue
            data = path.read_bytes()
            if b"\0" not in data and old.encode() in data:
                path.write_bytes(data.replace(old.encode(), new.encode()))
