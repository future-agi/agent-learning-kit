from __future__ import annotations

import importlib
from typing import Any

from .config import AgentLearningConfig, configure, current_config, get_api_key

_SUBMODULES = {
    "actions",
    "evals",
    "optimize",
    "redteam",
    "simulate",
    "suite",
}


def __getattr__(name: str) -> Any:
    if name in _SUBMODULES:
        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), *_SUBMODULES})


__all__ = [
    "AgentLearningConfig",
    "actions",
    "configure",
    "current_config",
    "evals",
    "get_api_key",
    "optimize",
    "redteam",
    "simulate",
    "suite",
]
