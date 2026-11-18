from typing import TYPE_CHECKING, Any

from fi.simulate.simulation.bridge.livekit import LiveKitAudioBridge
from fi.simulate.simulation.bridge.vapi import VapiWebSocketConnector

if TYPE_CHECKING:
    from fi.simulate.simulation.bridge.retell import RetellWebCallConnector

__all__ = [
    "LiveKitAudioBridge",
    "RetellWebCallConnector",
    "VapiWebSocketConnector",
]


def __getattr__(name: str) -> Any:
    # The Retell connector needs aiortc, so it loads on first use: a Vapi, SIP
    # or native LiveKit run never imports it.
    if name == "RetellWebCallConnector":
        from fi.simulate.simulation.bridge.retell import RetellWebCallConnector

        return RetellWebCallConnector
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
