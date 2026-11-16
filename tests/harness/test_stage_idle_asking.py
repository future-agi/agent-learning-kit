"""A turn waiting on a person's answer says nothing until they answer; however long they take is not a hang."""

import asyncio
from types import SimpleNamespace

import pytest

from fi.alk.harness import session as session_module
from fi.alk.harness.session import Stage, StageIdleTimeout, asking_a_person


class _Quiet:
    """A model session that says nothing for a while, then ends its turn."""

    def __init__(self, quiet_for: float) -> None:
        self.quiet_for = quiet_for

    async def replies(self):
        await asyncio.sleep(self.quiet_for)
        return
        yield


def _stage(quiet_for: float) -> Stage:
    stage = Stage.__new__(Stage)
    stage._spec = SimpleNamespace(idle_timeout_seconds=0.05)
    stage._session = _Quiet(quiet_for)
    stage._overheard = False
    stage.history = []
    stage.name = "builder"
    return stage


async def _drain(stage: Stage) -> list:
    return [event async for event in stage._pending_events()]


def test_a_turn_waiting_on_a_person_outlasts_the_idle_bound() -> None:
    with asking_a_person():
        assert asyncio.run(_drain(_stage(quiet_for=0.3))) == []
    assert session_module._ASKING == 0


def test_a_silent_turn_with_nobody_asked_is_still_stopped() -> None:
    with pytest.raises(StageIdleTimeout):
        asyncio.run(_drain(_stage(quiet_for=0.3)))
