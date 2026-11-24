from __future__ import annotations

import asyncio
import socket

import pytest

from fi.alk.harness.call_runner import _call_connected_server
from fi.alk.harness.call_worker import _call_announcer
from fi.alk.harness.isolated_process import (
    MAX_CHANNEL_MESSAGE_BYTES,
    encode_message,
    receive_message,
    send_message,
)
from fi.simulate.simulation.engines import livekit
from fi.simulate.simulation.engines.livekit import (
    CALL_CONNECTED,
    _announce_call,
    _announce_in_background,
    _settle_announcement,
)


def test_the_worker_sends_each_placed_call_to_the_harness_and_waits_for_its_answer() -> None:
    worker_end, harness_end = socket.socketpair()
    try:
        announce = _call_announcer(worker_end.detach())

        async def both() -> dict:
            sent = asyncio.create_task(announce("call-1"))
            request = await asyncio.to_thread(
                receive_message, harness_end, limit=MAX_CHANNEL_MESSAGE_BYTES
            )
            assert not sent.done()
            send_message(harness_end, encode_message({}))
            await sent
            return request

        assert asyncio.run(both()) == {"op": "call_connected", "provider_call_id": "call-1"}
    finally:
        harness_end.close()


def test_the_harness_tells_the_platform_the_call_under_its_scenario_before_answering() -> None:
    told: list[tuple[str, str]] = []

    async def call_connected(scenario_key: str, provider_call_id: str) -> None:
        told.append((scenario_key, provider_call_id))

    async def run() -> dict:
        serve = _call_connected_server(call_connected, "s1", asyncio.get_running_loop())
        return await asyncio.to_thread(
            serve, {"op": "call_connected", "provider_call_id": "call-1"}
        )

    assert asyncio.run(run()) == {}
    assert told == [("s1", "call-1")]


@pytest.mark.parametrize(
    "request_",
    [{"op": "other", "provider_call_id": "call-1"}, {"op": "call_connected", "provider_call_id": ""}],
)
def test_the_harness_ignores_anything_but_a_named_call(request_) -> None:
    told: list[str] = []

    async def call_connected(scenario_key: str, provider_call_id: str) -> None:
        told.append(provider_call_id)

    async def run() -> dict:
        serve = _call_connected_server(call_connected, "s1", asyncio.get_running_loop())
        return await asyncio.to_thread(serve, request_)

    assert asyncio.run(run()) == {}
    assert told == []


def test_a_platform_that_cannot_be_told_never_holds_the_call(monkeypatch) -> None:
    monkeypatch.setattr("fi.alk.harness.call_runner._CALL_CONNECTED_SEND_SECONDS", 0.05)

    async def call_connected(scenario_key: str, provider_call_id: str) -> None:
        await asyncio.sleep(5)

    async def run() -> dict:
        serve = _call_connected_server(call_connected, "s1", asyncio.get_running_loop())
        return await asyncio.to_thread(
            serve, {"op": "call_connected", "provider_call_id": "call-1"}
        )

    assert asyncio.run(run()) == {}


def test_no_hook_serves_no_channel() -> None:
    async def run() -> object:
        return _call_connected_server(None, "s1", asyncio.get_running_loop())

    assert asyncio.run(run()) is None


def test_the_engine_names_a_placed_call_to_whoever_listens() -> None:
    heard: list[str] = []

    async def listen(provider_call_id: str) -> None:
        heard.append(provider_call_id)

    async def run() -> None:
        CALL_CONNECTED.set(listen)
        await _announce_call("call-1")
        await _announce_call(None)

    asyncio.run(run())
    assert heard == ["call-1"]


def test_a_listener_that_fails_never_fails_the_call(caplog) -> None:
    async def broken(provider_call_id: str) -> None:
        raise RuntimeError("channel closed")

    async def run() -> None:
        CALL_CONNECTED.set(broken)
        await _announce_call("call-1")

    asyncio.run(run())
    assert "voice call connected callback failed" in caplog.text


def test_with_no_listener_the_engine_says_nothing() -> None:
    assert asyncio.run(_announce_call("call-1")) is None
    assert livekit.CALL_CONNECTED.get() is None


def test_a_slow_platform_never_holds_up_the_call() -> None:
    answered = asyncio.Event()
    heard: list[str] = []

    async def slow(provider_call_id: str) -> None:
        await answered.wait()
        heard.append(provider_call_id)

    async def run() -> tuple[bool, bool]:
        CALL_CONNECTED.set(slow)
        task = _announce_in_background("call-1")
        await asyncio.sleep(0)
        pending = not task.done()
        answered.set()
        await _settle_announcement(task, 1.0)
        return pending, task.done()

    assert asyncio.run(run()) == (True, True)
    assert heard == ["call-1"]


def test_an_announcement_still_waiting_at_cleanup_is_cancelled() -> None:
    async def never(provider_call_id: str) -> None:
        await asyncio.Event().wait()

    async def run() -> bool:
        CALL_CONNECTED.set(never)
        task = _announce_in_background("call-1")
        await _settle_announcement(task, 0.05)
        await asyncio.sleep(0)
        return task.cancelled()

    assert asyncio.run(run()) is True
