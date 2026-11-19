"""Writing a JSON file whole: a reader sees the old file or the new one, never a part-written one."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, value: Any, *, sort_keys: bool = True) -> None:
    """Write ``value`` to ``path`` as sorted, indented JSON, with the mode a plain write gets under the process umask
    (0644 by default; the harness's alone under per-user isolation, whose umask is 077, so a proof is never readable
    by the agent's user), by renaming a whole temporary file over it. The folder is made when missing; on any failure
    the temporary file is removed and the error raised. ``sort_keys`` off keeps the value's own key order."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.stem}-{secrets.token_hex(8)}.json"
    # Created the way a plain write creates a file, so the umask alone sets its mode.
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o666)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(value, out, indent=2, sort_keys=sort_keys, ensure_ascii=False, default=str)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
