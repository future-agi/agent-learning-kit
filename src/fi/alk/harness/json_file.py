"""Writing a JSON file whole: a reader sees the old file or the new one, never a part-written one."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, value: Any) -> None:
    """Write ``value`` to ``path`` as sorted, indented JSON, readable by every user, by renaming a whole temporary file
    over it. The folder is made when missing; on any failure the temporary file is removed and the error raised."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(value, out, indent=2, sort_keys=True, ensure_ascii=False, default=str)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
