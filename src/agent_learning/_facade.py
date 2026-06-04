from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any


def optional_module(module_name: str, extra: str) -> ModuleType:
    try:
        return importlib.import_module(module_name)
    except Exception as exc:
        raise RuntimeError(
            f"`{module_name}` is not available. Install "
            f"`agent-learning-kit[{extra}]` or `agent-learning-kit[trinity]`."
        ) from exc


def proxy_getattr(module_name: str, extra: str, name: str) -> Any:
    module = optional_module(module_name, extra)
    try:
        return getattr(module, name)
    except AttributeError as exc:
        raise AttributeError(f"module `{module_name}` has no attribute `{name}`") from exc


def proxy_dir(module_name: str, extra: str) -> list[str]:
    module = optional_module(module_name, extra)
    return sorted(set(dir(module)))

