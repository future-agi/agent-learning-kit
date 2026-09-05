"""A mailbox speaks first and is never waited for."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("livekit")

from fi.simulate.simulation.engines import livekit


def test_the_watchdog_clears_a_slow_first_turn_without_being_generous():
    """It only sees a turn once the session commits it, and measured agent turn latency on a real
    run was 4292ms and 3947ms, so a bound near five seconds would talk over the greeting."""
    assert livekit._OPEN_INSTEAD_AFTER_SECONDS == 8.0
    assert livekit._OPEN_INSTEAD_AFTER_SECONDS < livekit._NO_CONVERSATION_TIMEOUT_SECONDS


def test_a_mailbox_is_recognised_from_the_calls_own_lane(monkeypatch):
    monkeypatch.delenv("HARNESS_ANSWERED_BY", raising=False)
    assert not livekit._answered_by_voicemail()

    monkeypatch.setenv("HARNESS_ANSWERED_BY", "voicemail")
    assert livekit._answered_by_voicemail()

    monkeypatch.setenv("HARNESS_ANSWERED_BY", " VoiceMail ")
    assert livekit._answered_by_voicemail()

    monkeypatch.setenv("HARNESS_ANSWERED_BY", "person")
    assert not livekit._answered_by_voicemail()


def test_the_person_still_opens_a_call_the_agent_never_starts():
    """The watchdog is what stops two voice agents waiting for each other, so it has to fire when
    nobody has spoken and stay quiet when somebody has."""
    opened: list[str] = []

    class Agent:
        def open_conversation(self) -> None:
            opened.append("opened")

    empty = type("S", (), {"history": type("H", (), {"items": []})()})()
    asyncio.run(
        livekit._open_if_nobody_speaks_first(empty, Agent(), timeout_seconds=0.05)
    )
    assert opened == ["opened"]

    spoken = type(
        "S",
        (),
        {
            "history": type(
                "H",
                (),
                {
                    "items": [
                        type(
                            "M",
                            (),
                            {
                                "type": "message",
                                "role": "assistant",
                                "text_content": "hello there",
                                "created_at": 1.0,
                                "interrupted": False,
                            },
                        )()
                    ]
                },
            )()
        },
    )()
    opened.clear()
    asyncio.run(
        livekit._open_if_nobody_speaks_first(spoken, Agent(), timeout_seconds=0.05)
    )
    assert opened == []
