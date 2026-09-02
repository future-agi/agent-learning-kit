"""A provider's per-minute ceiling must not end a run.

Measured: a five-hundred scenario suite died in its fourteenth turn, still planning, because one
call came back 429 and nothing tried again. Hours of proved work sat one transient error away
from being abandoned every time.
"""

from __future__ import annotations

import asyncio

from fi.alk.harness import session as stage_module
from fi.alk.harness.session import Turn, _rate_limited


class TestRecognisingACeiling:
    def test_a_429_turn_is_a_wait(self):
        assert _rate_limited(Turn(outcome="failed", error="the model call failed (429): busy"))

    def test_resource_exhausted_is_a_wait(self):
        assert _rate_limited(
            Turn(outcome="failed", error="RESOURCE_EXHAUSTED. try again later")
        )

    def test_any_other_failure_is_not(self):
        assert not _rate_limited(
            Turn(outcome="failed", error="the provider rejected the credentials")
        )

    def test_a_turn_that_worked_is_not(self):
        assert not _rate_limited(Turn(outcome="ok", error=""))


class TestWaitingItOut:
    def test_the_stage_asks_again_after_a_ceiling(self, monkeypatch):
        """The session is kept, so the conversation and everything already proved survive."""
        waited: list[float] = []
        real_sleep = asyncio.sleep

        async def note(seconds):
            waited.append(seconds)
            await real_sleep(0)

        monkeypatch.setattr(stage_module.asyncio, "sleep", note)

        replies = [
            Turn(outcome="failed", error="the model call failed (429): busy"),
            Turn(outcome="ok", text="done"),
        ]

        class Fake:
            history: list[Turn] = []
            trace = type("T", (), {"record": lambda self, e: None})()

            async def stream(self, message):
                self.history.append(replies.pop(0))
                return
                yield  # pragma: no cover - makes this an async generator

            async def _said_once(self, message, *, on_event=None):
                self.history.append(replies.pop(0))
                return self.history[-1]

        fake = Fake()
        got = asyncio.run(stage_module.Stage.say(fake, "go"))

        assert got.outcome == "ok"
        assert waited, "it should have waited before asking again"

    def test_it_gives_up_rather_than_waiting_for_ever(self, monkeypatch):
        monkeypatch.setattr(stage_module, "RATE_LIMIT_RETRIES", 2)
        real_sleep = asyncio.sleep
        monkeypatch.setattr(stage_module.asyncio, "sleep", lambda _s: real_sleep(0))

        class Fake:
            history: list[Turn] = []
            trace = type("T", (), {"record": lambda self, e: None})()

            async def _said_once(self, message, *, on_event=None):
                turn = Turn(outcome="failed", error="the model call failed (429): busy")
                self.history.append(turn)
                return turn

        got = asyncio.run(stage_module.Stage.say(Fake(), "go"))
        assert got.outcome == "failed"
