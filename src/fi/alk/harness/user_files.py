"""Files in a folder a customer process's user owns, which the harness writes or reads back.

The harness can write and read almost anywhere. Under per-user isolation the folder that holds a
process's log or receipt belongs to that process's user, and every world shares it: a process of
that user can leave a symlink, a FIFO or a hard link to another file where the harness expects
its own file. So the harness never opens one through a link. A write replaces whatever is there
with a fresh file; a read of anything but a regular file of its own fails like a missing file.
Giving such a folder to that user walks it by descriptor, so it never enters a link either.
"""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, Protocol

# O_NONBLOCK: opening a FIFO blocks until the other end opens, and nobody ever would.
_NO_LINK = os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _own_file(fd: int) -> bool:
    info = os.fstat(fd)
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1


def open_log(path: Path) -> BinaryIO:
    """``path``, empty and open for writing, as a fresh file when anything else was there.

    It truncates only after the checks: truncating through a hard link empties the file it
    points to.
    """
    flags = os.O_WRONLY | os.O_CREAT | _NO_LINK
    try:
        fd = os.open(path, flags, 0o666)
    except OSError as exc:
        # ELOOP: a symlink. ENXIO: a FIFO or a socket.
        if exc.errno not in (errno.ELOOP, errno.ENXIO):
            raise
    else:
        if _own_file(fd):
            os.ftruncate(fd, 0)
            os.set_blocking(fd, True)
            return os.fdopen(fd, "wb")
        os.close(fd)
    path.unlink()
    fd = os.open(path, flags | os.O_EXCL, 0o666)
    os.set_blocking(fd, True)
    return os.fdopen(fd, "wb")


def _folder(root: Path, relative: Path, *, create: bool) -> int:
    """A descriptor of ``root / relative``. Every folder below ``root`` is opened from its parent's
    descriptor and never through a link; ``create`` makes one that is missing."""
    if relative.is_absolute() or ".." in relative.parts:
        raise OSError(errno.EPERM, "not a path below its folder", str(relative))
    fd = os.open(root, _DIRECTORY)
    try:
        for part in relative.parts:
            if create:
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, _DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
    except BaseException:
        os.close(fd)
        raise
    return fd


def _below(path: Path, beneath: Path) -> Path:
    try:
        return path.relative_to(beneath)
    except ValueError as exc:
        raise OSError(errno.EPERM, "not a path below its folder", str(path)) from exc


def make_folders(path: Path, *, beneath: Path) -> None:
    """Make ``path`` and the folders above it up to ``beneath``, never through a link."""
    os.close(_folder(beneath, _below(path, beneath), create=True))


def read_text(path: Path, *, beneath: Path | None = None, limit: int | None = None) -> str:
    """The text of ``path``, which must be a regular file of its own, else ``OSError``.

    With ``beneath``, a folder the harness trusts, every folder between it and the file is opened
    by descriptor, never through a link. With ``limit``, a longer file is refused.
    """
    folder = None
    if beneath is not None:
        folder = _folder(beneath, _below(path, beneath).parent, create=False)
    try:
        fd = os.open(path if folder is None else path.name, os.O_RDONLY | _NO_LINK, dir_fd=folder)
    finally:
        if folder is not None:
            os.close(folder)
    try:
        if not _own_file(fd):
            raise OSError(errno.EPERM, "not a regular file of its own", str(path))
        os.set_blocking(fd, True)
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read() if limit is None else handle.read(limit + 1)
    finally:
        os.close(fd)
    if limit is not None and len(data) > limit:
        raise OSError(errno.EFBIG, f"longer than {limit} bytes", str(path))
    return data.decode("utf-8", errors="replace")


class Chown(Protocol):
    """``os.chown``'s shape: ``path`` is relative to ``dir_fd`` when one is given."""

    def __call__(
        self, path: str | Path, uid: int, gid: int, *, dir_fd: int | None = None
    ) -> None: ...


def chown_no_follow(path: str | Path, uid: int, gid: int, *, dir_fd: int | None = None) -> None:
    """Re-own ``path``; a link is re-owned itself, never what it points at."""
    os.chown(path, uid, gid, dir_fd=dir_fd, follow_symlinks=False)


def _not_linked_elsewhere(info: os.stat_result) -> bool:
    # A file with another name may be one the harness owns elsewhere: never give it away.
    return not (stat.S_ISREG(info.st_mode) and info.st_nlink > 1)


def own_tree(
    root: Path,
    *,
    uid: int,
    gid: int,
    chown: Chown = chown_no_follow,
    check: Callable[[os.stat_result], bool] = _not_linked_elsewhere,
) -> None:
    """Give ``root`` and everything in it to ``uid``:``gid``, without ever following a link.

    A process of that user may already write in the tree while this runs. Every folder is
    therefore opened from its parent's descriptor, and one swapped for a symlink is skipped,
    never entered; each entry is re-owned by name in that descriptor. Symlinks keep their
    owner, and an entry removed meanwhile is passed over. ``check`` sees every other entry
    first: False leaves it as it is, and it may raise.
    """
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        chown(".", uid, gid, dir_fd=descriptor)
        for _, directories, files, current in os.fwalk(
            ".", dir_fd=descriptor, follow_symlinks=False
        ):
            for name in (*directories, *files):
                try:
                    info = os.stat(name, dir_fd=current, follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode) or not check(info):
                        continue
                    chown(name, uid, gid, dir_fd=current)
                except FileNotFoundError:
                    continue  # removed while the walk ran: nothing left to give
    finally:
        os.close(descriptor)
