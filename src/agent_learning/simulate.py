from __future__ import annotations

from typing import Any

from ._facade import proxy_dir, proxy_getattr

_MODULE = "fi.simulate"
_EXTRA = "simulate"


def __getattr__(name: str) -> Any:
    return proxy_getattr(_MODULE, _EXTRA, name)


def __dir__() -> list[str]:
    return proxy_dir(_MODULE, _EXTRA)

