"""A turn waiting on a person's answer says nothing until they answer; however long they take is not a hang."""

import asyncio
from types import SimpleNamespace

import pytest

from fi.alk.harness import session as session_module
from fi.alk.harness.backends import ModelReply, Say
from fi.alk.harness.session import Stage, StageIdleTimeout, asking_a_person

BOUND = 0.05


class _Scripted:
    """A model session that says each line after its delay, then ends its turn."""

    def __init__(self, *lines: tuple[float, str]) -> None:
        self.lines = lines

    async def replies(self):
        for delay, text in self.lines:
            await asyncio.sleep(delay)
            yield ModelReply(parts=[Say(text)])


def _stage(*lines: tuple[float, str], bound: float = BOUND) -> Stage:
    stage = Stage.__new__(Stage)
    stage._spec = SimpleNamespace(idle_timeout_seconds=bound)
    stage._session = _Scripted(*lines)
    stage._overheard = False
    stage.history = []
    stage.name = "builder"
    return stage


async def _said(stage: Stage) -> list[str]:
    return [event.text async for event in stage._pending_events()]


def test_a_person_asked_past_the_bound_keeps_the_turn_waiting_for_their_answer() -> None:
    with asking_a_person():
        assert asyncio.run(_said(_stage((BOUND * 6, "answered")))) == ["answered"]
    assert session_module._ASKING == 0


def test_a_silent_turn_with_nobody_asked_is_still_stopped() -> None:
    with pytest.raises(StageIdleTimeout):
        asyncio.run(_said(_stage((BOUND * 6, "too late"))))


def test_a_reply_just_under_the_bound_is_delivered() -> None:
    assert asyncio.run(_said(_stage((0.1, "in time"), bound=0.4))) == ["in time"]


def test_a_stage_stepped_from_a_second_task_still_times_out() -> None:
    async def run() -> None:
        events = _stage((0, "first"), (BOUND * 6, "too late"))._pending_events()
        # The first reply is read by one task, the next by another.
        first = await asyncio.create_task(events.__anext__())
        assert first.text == "first"
        await asyncio.create_task(events.__anext__())

    with pytest.raises(StageIdleTimeout):
        asyncio.run(run())


def test_a_cancel_from_outside_on_the_same_wait_stays_a_cancel() -> None:
    async def run() -> None:
        loop = asyncio.get_running_loop()
        waiting = asyncio.create_task(_said(_stage((BOUND * 6, "too late"))))
        call_later = loop.call_later

        def with_outside_cancel(delay, callback, *args):
            # The watcher and an outside cancel land on the same wait, in the same turn of the loop.
            when = loop.time() + delay
            loop.call_later = call_later
            handle = loop.call_at(when, callback, *args)
            loop.call_at(when, waiting.cancel)
            return handle

        loop.call_later = with_outside_cancel
        await waiting

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run())
