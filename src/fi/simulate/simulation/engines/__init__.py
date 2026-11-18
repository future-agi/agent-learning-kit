from typing import Any

from fi.simulate.simulation.engines.base import BaseEngine
from fi.simulate.simulation.engines.cloud import CloudEngine
from fi.simulate.simulation.engines.local_text import LocalTextEngine

__all__ = ["BaseEngine", "CloudEngine", "LiveKitEngine", "LocalTextEngine"]


def __getattr__(name: str) -> Any:
    # LiveKit is optional, and only a voice run needs it. Importing it takes about a second, and
    # the harness starts fresh interpreters for every chat conversation, so load it on first use.
    if name != "LiveKitEngine":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:  # pragma: no cover
        from fi.simulate.simulation.engines.livekit import LiveKitEngine
    except ImportError:  # pragma: no cover
        LiveKitEngine = None  # type: ignore
    globals()[name] = LiveKitEngine
    return LiveKitEngine
