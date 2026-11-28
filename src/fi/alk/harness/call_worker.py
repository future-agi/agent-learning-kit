"""Execute one simulation with the environment supplied at process creation."""

from __future__ import annotations

import asyncio
import faulthandler
import logging
import os
import socket
import sys
import threading
from pathlib import Path

from .isolated_process import (
    CHANNEL_FD_ENV,
    MAX_CHANNEL_MESSAGE_BYTES,
    encode_message,
    receive_message,
    run_worker,
    send_message,
    stop_requested,
)


def _call_announcer(fd: int):
    """Sends the provider's id for each call placed to the harness, which tells the platform."""
    channel = socket.socket(fileno=fd)
    lock = threading.Lock()

    def ask(provider_call_id: str) -> None:
        with lock:
            send_message(
                channel,
                encode_message({"op": "call_connected", "provider_call_id": provider_call_id}),
            )
            receive_message(channel, limit=MAX_CHANNEL_MESSAGE_BYTES)

    async def announce(provider_call_id: str) -> None:
        await asyncio.to_thread(ask, provider_call_id)

    return announce


async def main() -> None:
    faulthandler.enable()
    os.environ.setdefault("RUST_BACKTRACE", "1")
    from fi.simulate.runtime import SimulationSpec

    from .call_runner import _default_place_call

    spec = SimulationSpec.model_validate_json(sys.stdin.buffer.read())
    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s %(levelname)s voice_run={spec.run_id} pid=%(process)d %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger(__name__).info("voice worker started")
    if channel_fd := os.environ.get(CHANNEL_FD_ENV):
        from fi.simulate.simulation.engines.livekit import CALL_CONNECTED

        CALL_CONNECTED.set(_call_announcer(int(channel_fd)))
    # On the person's stop the engine hangs up at the next turn boundary and still reports the call it kept.
    report = await _default_place_call(spec, stop_when=lambda _messages, _persona: stop_requested())
    output = Path(sys.argv[1])
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(report.model_dump_json())
    logging.getLogger(__name__).info("voice worker result written")


if __name__ == "__main__":
    run_worker(main)
