"""Serve the conversation for as long as the sandbox is up.

The pipeline runs its stages and ends. This does not: it waits on the inbox and answers whatever is
asked, whether authoring is mid-flight, finished, or failed an hour ago. That is the difference
between a chat and a message folded into whichever stage happened to be open.

Run as its own process beside the pipeline:

    python -m fi.alk.harness.converse_service /work/authoring

It is idle until somebody says something, so the cost of having it running is a poll of one small
file. With no channel configured it exits at once rather than spinning.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from .contract import AgentContract
from .converse import open_conversation
from .live import Channel

# How often the inbox is read, and how long to wait for the run to produce something to talk about.
POLL_SECONDS = 2.0
WAIT_FOR_READY_SECONDS = 2400.0

# The conversation holds the writers' own tools, and those restore the world, so it cannot open
# before the environment stage has written one. Waiting on the contract alone opened it too early
# and the service died on a missing manifest with the question still sitting in the inbox.
NEEDED = ("contract.json", "manifest.json")


def _ready(destination: Path) -> bool:
    return all((destination / name).is_file() for name in NEEDED)


def _contract(destination: Path) -> AgentContract | None:
    path = destination / "contract.json"
    if not path.is_file():
        return None
    try:
        return AgentContract.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001 - a half-written contract is read again on the next poll
        return None


async def _wait_until_ready(destination: Path, channel: Channel, *, announced: bool) -> bool:
    """Hold until the run has a contract and a world, saying so once rather than every poll."""
    waited = 0.0
    while not _ready(destination):
        if not announced and waited > POLL_SECONDS * 10:
            channel.record(
                "text",
                "The run is rebuilding its environment, so I cannot look at it just now. Ask "
                "away: I will answer as soon as it settles.",
                stage="converse",
            )
            announced = True
        if waited >= WAIT_FOR_READY_SECONDS:
            return False
        await asyncio.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
    return True


async def serve(destination: Path, channel: Channel) -> int:
    if not await _wait_until_ready(destination, channel, announced=True):
        channel.record(
            "text",
            "I cannot answer: this run never produced a contract and a world.",
            stage="converse",
        )
        return 1

    greeted = False
    while True:
        # Re-checked on every attempt, not once. `/work/authoring` is rewritten as the run works,
        # so the manifest a session needs can be there when it is checked and gone a second later,
        # and a retry that does not look again simply spins on the same missing file.
        if not await _wait_until_ready(destination, channel, announced=False):
            return 1
        contract = _contract(destination)
        if contract is None:
            await asyncio.sleep(POLL_SECONDS)
            continue
        if not greeted:
            channel.record(
                "text",
                f"Ready. Ask me anything about {contract.agent!r}: this run, its world, its "
                "scenarios, what the suite does not cover, or ask me to change something.",
                stage="converse",
            )
            greeted = True
        try:
            stage = open_conversation(contract, out=destination, channel=channel)
            async with stage:
                while True:
                    said = channel.waiting()
                    if not said:
                        await asyncio.sleep(POLL_SECONDS)
                        continue
                    for message in said:
                        # Recorded before answering, so a question is visible even when the answer
                        # fails. A question that vanished is worse than a visible failure.
                        channel.record("said", message, stage="converse")
                        try:
                            await stage.say(message)
                        except Exception as failed:  # noqa: BLE001 - one turn, not the chat
                            channel.record(
                                "text",
                                f"That turn failed: {type(failed).__name__}: {failed}. Ask again, "
                                "or ask something narrower.",
                                stage="converse",
                            )
        except Exception:  # noqa: BLE001 - the session died; wait for the world and open another
            # Silent on purpose. An earlier version announced every reopen and wrote the same
            # sentence into the transcript six times a minute while the world was being rebuilt.
            await asyncio.sleep(POLL_SECONDS * 5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="the authoring directory to talk about")
    args = parser.parse_args(argv)

    channel = Channel()
    if not channel.live:
        print("no ALK_HARNESS_CHAT_DIR, so nobody can talk to this run", file=sys.stderr)
        return 0
    try:
        return asyncio.run(serve(Path(args.destination), channel))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
