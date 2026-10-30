"""Sub-actors on a call: when each enters, the call's budget, and what happens to every line."""

from __future__ import annotations

import array
import asyncio
import itertools
import json

import pytest

from fi.simulate.simulation import sub_actors as registry
from fi.simulate.simulation.engines import sub_actor_voice as voice_engine
from fi.simulate.simulation.sub_actors import (
    Cast,
    Moment,
    due,
    line_brief,
    line_gain,
    limited,
    loudness_ratio,
    with_others,
)


def _actor(key: str, channel: str = "audio_handoff", **trigger) -> dict:
    return {
        "id": key,
        "name": key.title(),
        "role": "someone with the person",
        "channel": channel,
        "instruction": "You want something else and say so.",
        "trigger": trigger or {"kind": "always"},
    }


@pytest.mark.parametrize(
    "trigger, moment, expected",
    [
        ({"kind": "always"}, Moment(1), True),
        ({"kind": "always"}, Moment(0), False),
        ({"kind": "mid_journey", "turn": 3}, Moment(2), False),
        ({"kind": "mid_journey", "turn": 3}, Moment(3), True),
        (
            {"kind": "on_tool", "tool": "lookup", "on_call": 2},
            Moment(4, tools_called=["lookup"]),
            False,
        ),
        (
            {"kind": "on_tool", "tool": "lookup", "on_call": 2},
            Moment(4, tools_called=["lookup", "lookup"]),
            True,
        ),
        (
            {"kind": "on_keyword", "words": ["refund"]},
            Moment(2, last_text="Your total is ready."),
            False,
        ),
        (
            {"kind": "on_keyword", "words": ["TOTAL"]},
            Moment(2, last_text="Your total is ready."),
            True,
        ),
        (
            {"kind": "on_keyword", "words": ["合計"]},
            Moment(2, last_text="お会計の合計は千円です"),
            True,
        ),
        (
            {"kind": "on_keyword", "words": ["no"]},
            Moment(2, last_text="I know the way."),
            False,
        ),
        (
            {"kind": "on_keyword", "words": ["no"]},
            Moment(2, last_text="No, that is not right."),
            True,
        ),
        (
            {"kind": "on_keyword", "words": ["card on file"]},
            Moment(2, last_text="I will use the card on file."),
            True,
        ),
        ({"kind": "probability", "p": 1}, Moment(1), True),
    ],
)
def test_each_trigger_enters_at_its_moment(trigger, moment, expected):
    assert due(trigger, moment, seed="run", sub_actor_id="one") is expected


def test_a_probability_draw_is_fixed_by_the_run_seed():
    draws = {
        seed: due(
            {"kind": "probability", "p": 0.5}, Moment(1), seed=seed, sub_actor_id="one"
        )
        for seed in "abcdefgh"
    }
    assert set(draws.values()) == {True, False}
    assert draws == {
        seed: due(
            {"kind": "probability", "p": 0.5}, Moment(1), seed=seed, sub_actor_id="one"
        )
        for seed in "abcdefgh"
    }


def test_background_loudness_is_a_ratio_drawn_from_the_seed_unless_the_list_sets_one():
    low, high = registry.LOUDNESS_RANGE
    levels = [loudness_ratio(seed, "one", None) for seed in "abcdefgh"]
    assert all(low <= level <= high for level in levels)
    assert len(set(levels)) > 1 and levels == [
        loudness_ratio(seed, "one", None) for seed in "abcdefgh"
    ]
    assert loudness_ratio("a", "one", 0.5) == 0.5


def test_a_background_line_is_set_against_the_person_s_measured_level_and_never_clips():
    # A line twice as loud as the person, at a ratio of a quarter, is scaled to an eighth.
    assert line_gain(0.25, 2000.0, 4000.0) == 0.125
    # Before the person has spoken, or for a silent line, the ratio is used as it is.
    assert line_gain(0.25, None, 4000.0) == line_gain(0.25, 2000.0, 0.0) == 0.25
    assert limited(2.0, 1000) == 2.0
    ceiling = registry.PEAK_LIMIT * 32767
    assert limited(2.0, 30000) * 30000 == pytest.approx(ceiling)


def test_the_leveller_scales_each_frame_and_holds_its_peaks_under_the_limit():
    from livekit import rtc

    def frame(value, count=2400):
        data = array.array("h", [value, -value] * (count // 2))
        return rtc.AudioFrame(
            data=data.tobytes(),
            sample_rate=24000,
            num_channels=1,
            samples_per_channel=count,
        )

    async def frames():
        for value in (4000, 4000, 4000, 4000, 4000, 30000):
            yield frame(value)

    async def scenario():
        return [
            out
            async for out in voice_engine.at_level(frames(), 0.25, person_rms=2000.0)
        ]

    out = asyncio.run(scenario())
    peaks = [max(abs(s) for s in array.array("h", bytes(one.data))) for one in out]
    # Measured on its opening speech (RMS 4000): scaled to 0.25 x 2000 = 500.
    assert peaks[:5] == [500] * 5
    # A loud frame later in the line is scaled the same, and could never exceed the limit.
    assert peaks[5] == 3750 and peaks[5] <= registry.PEAK_LIMIT * 32767


def test_a_third_sub_actor_waits_for_room_and_enters_in_the_scenario_s_order():
    cast = Cast([_actor("one"), _actor("two"), _actor("three")], "run")
    assert [one["id"] for one in cast.entering(Moment(2))] == ["one", "two"]
    assert cast.entering(Moment(2)) == []
    cast.finish(cast.actors[0], "done")
    assert [one["id"] for one in cast.entering(Moment(3))] == ["three"]
    assert [
        event["kind"] for event in cast.events if event["sub_actor"] == "three"
    ] == ["triggered"]


def test_limits_and_the_call_wide_budget_hand_the_call_back_to_the_person():
    cast = Cast([_actor("one"), _actor("two")], "run")
    cast.entering(Moment(2))
    for turn in range(1, registry.LIMITS["audio_handoff"] + 1):
        assert cast.may_speak(cast.actors[0], turn)
        cast.delivered(cast.actors[0], f"line {turn}", turn, 0.0, 0.0)
    assert not cast.may_speak(cast.actors[0], 9)
    assert {"kind": "finished", "reason": "limit reached"}.items() <= cast.events[
        -1
    ].items()
    for _ in range(registry.TAKEOVER_AFTER_TURNS):
        cast.took_turn()
    assert not cast.may_speak(cast.actors[1], 10)


def test_a_background_voice_never_speaks_on_two_turns_in_a_row(monkeypatch):
    monkeypatch.setattr(registry, "SPEAKS_AGAIN", 1.0)
    cast = Cast([_actor("near", "audio_mix")], "run")
    cast.entering(Moment(2))
    assert cast.may_speak(cast.actors[0], 1)
    cast.delivered(cast.actors[0], "a line", 1, 0.0, 0.0)
    assert not cast.may_speak(cast.actors[0], 2)
    assert cast.may_speak(cast.actors[0], 3)


def test_the_summary_tells_triggered_delivered_and_dropped_apart():
    cast = Cast(
        [
            _actor("one"),
            _actor("two", "audio_mix"),
            _actor("three", **{"kind": "mid_journey", "turn": 9}),
        ],
        "run",
    )
    cast.entering(Moment(2))
    cast.delivered(cast.actors[0], "a line", 1, 0.0, 0.0)
    cast.dropped(cast.actors[1], "not ready in time")
    cast.hang_up()
    assert cast.summary() == [
        {
            "sub_actor": "one",
            "name": "One",
            "triggered": True,
            "delivered": 1,
            "dropped": [],
        },
        {
            "sub_actor": "two",
            "name": "Two",
            "triggered": True,
            "delivered": 0,
            "dropped": ["not ready in time"],
        },
        {
            "sub_actor": "three",
            "name": "Three",
            "triggered": False,
            "delivered": 0,
            "dropped": [],
        },
    ]


def test_a_sub_actor_s_brief_carries_the_situation_its_part_and_the_conversation_so_far():
    brief = line_brief(
        _actor("one"),
        situation="The person wants a refund.",
        said=["Hello"],
        conversation=[
            "The agent: Hi there",
            "The person: I want a refund",
            "The agent: How can I help?",
        ],
        language="French",
    )
    for part in (
        "You are One, someone with the person.",
        "The person wants a refund.",
        "You want something else",
        "Hello",
        "The person: I want a refund\nThe agent: How can I help?",
        "never repeat what you have already said",
        "Speak French.",
        "never confirm or choose",
    ):
        assert part in brief
    assert "Hi there" not in line_brief(
        _actor("one"),
        situation="s",
        said=[],
        conversation=["The agent: Hi there"] + ["The person: ok"] * 12,
        language="",
    )
    told = with_others("The person's situation.", [_actor("one"), _actor("two", "ivr")])
    assert (
        "One (someone with the person) may take the phone" in told
        and "Two (someone with the person) may answer your line before you do" in told
        and "if the agent speaks to you first, answer as yourself" in told
    )


class _Voice(voice_engine.SubActorVoice):
    def __init__(self, actor, lines, delay=0.0, tts="voice"):
        super().__init__(actor=actor, model=None, tts=tts, situation="s", language="")
        self.lines = list(lines)
        self.delay = delay
        self.heard = []

    async def line(self, conversation):
        self.heard.append(conversation)
        await asyncio.sleep(self.delay)
        text = self.lines.pop(0)
        if isinstance(text, Exception):
            raise text
        done = text.endswith("[DONE]")
        text = text.removesuffix("[DONE]").strip()
        return text, ([b"frame"] if text and text != "silent" else []), done


@pytest.fixture
def phone_from_first_turn(monkeypatch):
    """Engine tests below are about lines, not entry: let a phone-taker enter on the agent's first turn."""
    monkeypatch.setattr(registry, "TAKES_PHONE_FROM_TURN", 1)


def _on_call(voices, playing=0.0, **kwargs):
    said, played = [], []

    async def say(frames, text, name):
        said.append((name, text))

    async def play(frames, volume, text, name):
        await asyncio.sleep(playing)
        played.append(volume)

    kwargs.setdefault("clock", lambda: 1.0)
    call = voice_engine.SubActorsOnCall(
        voices, seed="run", play=play, say=say, **kwargs
    )
    return call, said, played


def test_every_line_is_written_from_the_conversation_so_far_and_records_how_long_it_took(
    phone_from_first_turn,
):
    async def scenario():
        phone = _Voice(_actor("one"), ["Hi, it's me [DONE]"])
        near = _Voice(_actor("near", "audio_mix"), ["over here"])
        call, _said, _played = _on_call(
            [phone, near],
            conversation=lambda: ["The agent: Hello", "The person: Hi"],
            clock=itertools.count().__next__,
        )
        await call.agent_turn("Which ride?")
        await asyncio.sleep(0.01)
        await call.close()
        return phone.heard, near.heard, call.events()

    phone, near, events = asyncio.run(scenario())
    expected = [["The agent: Hello", "The person: Hi", "The agent: Which ride?"]]
    assert phone == near == expected
    delivered = [one for one in events if one["kind"] == "delivered"]
    assert len(delivered) == 2 and all(one["asked_at"] < one["at"] for one in delivered)


class _Speech:
    """A streaming voice whose first frame is made at once and the rest slowly."""

    class capabilities:
        streaming = True

    def __init__(self, frames):
        self.frames = frames
        self.closed = False

    def stream(self):
        voice = self

        class _Stream:
            def push_text(self, text):
                pass

            def end_input(self):
                pass

            def __aiter__(self):
                return self._frames()

            async def _frames(self):
                for index, frame in enumerate(voice.frames):
                    if index:
                        await asyncio.sleep(0.5)
                    yield type("Event", (), {"frame": frame})()

            async def aclose(self):
                voice.closed = True

        return _Stream()


def test_a_line_starts_once_its_first_audio_is_made_not_once_all_of_it_is_voiced():
    async def scenario():
        speech = _Speech(["a", "b", "c"])
        frames = await asyncio.wait_for(voice_engine.voiced(speech, "hello"), 0.25)
        heard = [frame async for frame in frames]
        silent = await voice_engine.voiced(_Speech([]), "hello")
        return heard, speech.closed, silent

    heard, closed, silent = asyncio.run(scenario())
    assert heard == ["a", "b", "c"] and closed
    assert silent is None


@pytest.mark.parametrize(
    ("written", "text", "done"),
    [
        ("Here's Marcus back. [DONE]", "Here's Marcus back.", True),
        ('"Here\'s Marcus back. [DONE]."', "Here's Marcus back.", True),
        ("[DONE]", "", True),
        (
            "Could you use the north entrance?",
            "Could you use the north entrance?",
            False,
        ),
    ],
)
def test_the_hand_back_marker_ends_the_part_wherever_it_is_and_is_never_spoken(
    monkeypatch, written, text, done
):
    async def complete(model, context):
        return written

    async def voice(tts, said):
        return said or None

    monkeypatch.setattr(voice_engine, "complete_text", complete)
    monkeypatch.setattr(voice_engine, "voiced", voice)
    speaker = voice_engine.SubActorVoice(
        actor=_actor("one"), model=None, tts="voice", situation="s", language=""
    )
    line, _frames, finished = asyncio.run(speaker.line([]))
    assert (line, finished) == (text, done)


def test_agent_turns_that_overlap_never_let_someone_speak_after_handing_the_phone_back():
    async def scenario():
        phone = _Voice(
            _actor("one"), ["Here's Marcus back [DONE]", "Me again"], delay=0.05
        )
        call, said, _played = _on_call([phone])
        held = await asyncio.gather(call.agent_turn("Hello"), call.agent_turn("Hello?"))
        return held, said

    held, said = asyncio.run(scenario())
    assert said == [("One", "Here's Marcus back")]
    assert held == [False, False]


def test_someone_who_takes_the_phone_holds_it_until_done_and_only_one_holds_it_at_a_time(
    phone_from_first_turn,
):
    async def scenario():
        first = _Voice(_actor("one"), ["Hi, it's me", "Thanks [DONE]"])
        second = _Voice(_actor("two"), ["My turn [DONE]"])
        call, said, _played = _on_call([first, second])
        assert await call.agent_turn("Hello") is True
        assert said == [("One", "Hi, it's me")]
        assert await call.agent_turn("Sure") is False
        assert await call.agent_turn("Anything else?") is False
        await call.close()
        return said, call.summary()

    said, summary = asyncio.run(scenario())
    assert said == [("One", "Hi, it's me"), ("One", "Thanks"), ("Two", "My turn")]
    assert [one["delivered"] for one in summary] == [2, 1]


class _StatusError(Exception):
    status_code = 429


@pytest.mark.parametrize(
    "lines, delay, reason",
    [
        (["silent"], 0.0, "voice failed"),
        ([RuntimeError("model down")], 0.0, "line failed: RuntimeError"),
        ([_StatusError("quota")], 0.0, "line failed: _StatusError 429"),
        (["too late"], 0.05, "not ready in time"),
    ],
)
def test_a_line_that_cannot_be_heard_hands_the_phone_back_with_the_reason(
    monkeypatch, lines, delay, reason, phone_from_first_turn
):
    monkeypatch.setattr(voice_engine, "HANDOFF_LINE_SECONDS", 0.01)

    async def scenario():
        call, said, _played = _on_call([_Voice(_actor("one"), lines, delay)])
        holding = await call.agent_turn("Hello")
        await call.close()
        return holding, said, call.summary()

    holding, said, summary = asyncio.run(scenario())
    assert holding is False and said == []
    assert summary[0]["triggered"] and summary[0]["dropped"] == [reason]


def test_a_sub_actor_without_a_voice_of_its_own_is_dropped_with_the_reason_never_left_out(
    phone_from_first_turn,
):
    async def scenario():
        call, said, played = _on_call(
            [
                _Voice(_actor("one"), ["unused"], tts=None),
                _Voice(_actor("near", "audio_mix"), ["unused"], tts=None),
            ]
        )
        await call.agent_turn("Hello")
        await asyncio.sleep(0.01)
        await call.close()
        return said, played, call.summary()

    said, played, summary = asyncio.run(scenario())
    assert said == [] and played == []
    assert [(one["triggered"], one["dropped"]) for one in summary] == [
        (True, ["no voice of its own on this call"])
    ] * 2


def test_a_background_line_plays_whenever_it_is_ready_and_only_the_end_of_the_call_drops_it():
    async def scenario(delay, wait):
        near = _Voice(
            {**_actor("near", "audio_mix"), "loudness_ratio": 0.1}, ["over here"]
        )
        slow = _Voice(_actor("slow", "audio_mix"), ["a moment later"], delay=delay)
        call, said, played = _on_call([near, slow], playing=0.01)
        assert await call.agent_turn("Hello") is False
        await asyncio.sleep(wait)
        await call.close()
        return (
            said,
            played,
            [(one["delivered"], one["dropped"]) for one in call.summary()],
        )

    assert asyncio.run(scenario(delay=0.05, wait=0.1)) == (
        [],
        [pytest.approx(0.1), 1.0],
        [(1, []), (1, [])],
    )
    assert asyncio.run(scenario(delay=1.0, wait=0.05)) == (
        [],
        [pytest.approx(0.1)],
        [(1, []), (0, ["call ended before it was said"])],
    )


def test_the_person_s_menu_answers_first_or_never_enters_when_the_agent_spoke_first():
    async def answers():
        call, said, _played = _on_call(
            [_Voice(_actor("menu", "ivr"), ["Say accounts", "[DONE]"])]
        )
        await call.answer_first()
        holding = await call.agent_turn("Accounts")
        await call.close()
        return said, holding, call.summary()

    said, holding, summary = asyncio.run(answers())
    assert said == [("Menu", "Say accounts")] and holding is False
    assert summary[0]["delivered"] == 1

    call, _said, _played = _on_call([_Voice(_actor("menu", "ivr"), ["Say accounts"])])
    call.menu_cannot_answer("the agent spoke first")
    assert asyncio.run(call.agent_turn("Hello")) is False
    assert call.summary()[0] == {
        "sub_actor": "menu",
        "name": "Menu",
        "triggered": False,
        "delivered": 0,
        "dropped": [],
    }
    assert call.events()[0]["kind"] == "not_entered"


def test_delivered_lines_land_in_the_transcript_where_they_were_heard():
    messages = [
        {"role": "user", "content": "a", "started_speaking_at": 1.0},
        {"role": "assistant", "content": "b", "started_speaking_at": 3.0},
    ]
    events = [
        {
            "sub_actor": "one",
            "name": "One",
            "kind": "delivered",
            "text": "c",
            "at": 2.0,
            "ended_at": 2.5,
        },
        {
            "sub_actor": "two",
            "name": "Two",
            "kind": "dropped",
            "reason": "voice failed",
        },
    ]
    merged = voice_engine.with_sub_actor_turns(messages, events)
    assert [one["content"] for one in merged] == ["a", "c", "b"]
    assert merged[1]["role"] == "sub_actor" and merged[1]["sub_actor"] == "one"


def test_a_scenario_s_sub_actors_reach_the_call_resolved_in_voices_no_one_else_has(
    tmp_path, monkeypatch, caplog
):
    from fi.alk.harness.call_runner import _voiced_sub_actors
    from fi.alk.harness.sub_actor import SubActor, SubActorList, save_sub_actors

    save_sub_actors(
        tmp_path,
        SubActorList(
            sub_actors=[
                SubActor(
                    id="near",
                    name="Rowan",
                    role="someone with the person",
                    channel="audio_mix",
                    persona={"gender": "female"},
                ),
                SubActor(
                    id="writer",
                    name="Kai",
                    role="something on the page",
                    channel="page_content",
                ),
            ]
        ),
    )
    doc = {
        "instruction": "You want a refund.\n\nYour details:\n- Name: Sam\n- Verification code: 638204",
        "persona": {"name": "Sam", "gender": "female", "languages": ["English"]},
        "sub_actors": [
            {
                "id": "near",
                "instruction": "You want them to hurry.",
                "trigger": {"kind": "mid_journey", "turn": 2},
            },
            {"id": "writer", "instruction": "x"},
            {"id": "gone", "instruction": "x"},
        ],
    }
    monkeypatch.delenv("HARNESS_SUB_ACTORS", raising=False)
    assert _voiced_sub_actors(doc, tmp_path, "deepgram", "", "run") == []
    monkeypatch.setenv("HARNESS_SUB_ACTORS", "1")
    voiced = _voiced_sub_actors(doc, tmp_path, "deepgram", "", "run")
    assert [one["id"] for one in voiced] == ["near"]
    assert "sub-actor writer does not enter: not a voice channel" in caplog.text
    assert "sub-actor gone does not enter: not in the environment's list" in caplog.text
    near = voiced[0]
    from fi.alk.harness.simulator_voice import persona_voice

    assert near["voice"] and near["voice"] != persona_voice(doc["persona"], "deepgram")
    assert near["instruction"] == "You want them to hurry." and near["trigger"] == {
        "kind": "mid_journey",
        "turn": 2,
    }
    assert near["situation"] == "You want a refund." and near["language"] == "English"
    assert near["loudness_ratio"] == loudness_ratio("run", "near", None)
    json.dumps(voiced)


def test_a_voice_already_on_the_call_is_avoided_while_the_provider_has_another():
    from fi.alk.harness.simulator_voice import aura_voice_for, persona_voice

    person = {"name": "Sam", "gender": "female", "accent": "American"}
    own = aura_voice_for(person)
    assert persona_voice(person, "deepgram") == own
    assert persona_voice(person, "deepgram", frozenset({own})) not in ("", own)
    assert persona_voice(person, "elevenlabs") == ""


def test_a_tool_trace_that_cannot_be_read_never_holds_up_the_call(monkeypatch):
    from fi.alk.harness import call_runner

    def unreadable(_dsn, **_):
        raise RuntimeError("relation does not exist")

    monkeypatch.setattr(call_runner, "read_tool_trace", unreadable)
    assert voice_engine.world_access("postgresql://world")() == []


def test_the_tool_trace_is_read_only_for_a_sub_actor_that_enters_on_a_tool_call():
    reads = []

    def tools_called():
        reads.append(1)
        return ["lookup"]

    async def turn(trigger):
        call, _said, _played = _on_call(
            [_Voice(_actor("one", **trigger), ["Hi"])], tools_called=tools_called
        )
        await call.agent_turn("Hello")
        await call.close()

    asyncio.run(turn({"kind": "mid_journey", "turn": 5}))
    assert reads == []
    asyncio.run(turn({"kind": "on_tool", "tool": "lookup"}))
    assert reads == [1]

    async def two_turns():
        call, _said, _played = _on_call(
            [_Voice(_actor("one", kind="on_tool", tool="lookup"), ["Hi"])],
            tools_called=tools_called,
        )
        await call.agent_turn("Hello")
        await call.agent_turn("Anything else?")
        await call.close()

    reads.clear()
    asyncio.run(two_turns())
    assert reads == [1]


def test_the_menu_s_greeting_is_ready_by_the_time_the_call_is_answered():
    async def scenario():
        menu = _Voice(_actor("menu", "ivr"), ["Who are you calling for?"], delay=0.3)
        call, said, _played = _on_call([menu])
        call.prepare_menu()
        await asyncio.sleep(0.35)
        started = asyncio.get_running_loop().time()
        await call.answer_first()
        waited = asyncio.get_running_loop().time() - started
        await call.close()
        return said, waited, menu.heard

    said, waited, heard = asyncio.run(scenario())
    assert said == [("Menu", "Who are you calling for?")]
    assert waited < 0.1 and len(heard) == 1


def test_the_call_s_sub_actor_time_runs_out_and_every_sub_actor_stops_with_the_reason():
    now = [0.0]
    cast = Cast(
        [
            _actor("one"),
            _actor("two", "audio_mix"),
            _actor("three", kind="mid_journey", turn=2),
        ],
        "run",
        clock=lambda: now[0],
    )
    assert len(cast.entering(Moment(2))) == 2
    # Time passing in the agent's and the person's turns uses none of it.
    now[0] = 500.0
    assert cast.remaining() == registry.SUB_ACTOR_SECONDS
    # Only the sub-actors' own lines do: asked for at 500, ready and spoken by 530.
    cast.delivered(cast.actors[0], "a line", 2, 510.0, 530.0, 500.0)
    assert cast.remaining() == 30.0 and cast.line_limit(45.0) == 30.0
    assert cast.entering(Moment(2)) == []  # the third waits for room
    cast.delivered(cast.actors[1], "another", 3, 540.0, 560.0, 530.0)
    assert cast.out_of_time()
    assert cast.entering(Moment(3)) == []
    assert not cast.may_speak(cast.actors[0], 3)
    stopped = {
        (one["sub_actor"], one["kind"], one["reason"])
        for one in cast.events
        if one["kind"] in ("finished", "dropped")
    }
    assert stopped == {
        ("one", "finished", registry.TIME_UP),
        ("two", "finished", registry.TIME_UP),
        ("three", "dropped", registry.TIME_UP),
    }
    cast.hang_up()
    assert [one["dropped"] for one in cast.summary()] == [[], [], [registry.TIME_UP]]


def test_a_line_due_while_two_are_waiting_is_dropped_as_queue_full():
    cast = Cast([_actor("one"), _actor("two"), _actor("three")], "run")
    one, two, three = cast.actors
    assert cast.line_wanted(one) and cast.line_wanted(two)
    assert not cast.line_wanted(three)
    cast.line_settled()
    assert cast.line_wanted(three)
    assert [entry["dropped"] for entry in cast.summary()] == [[], [], ["queue full"]]


def test_a_stalled_line_is_dropped_and_the_person_takes_the_phone_back(
    monkeypatch, phone_from_first_turn
):
    monkeypatch.setattr(voice_engine, "HANDOFF_LINE_SECONDS", 0.05)

    async def scenario():
        phone = _Voice(_actor("one"), ["too late"], delay=1.0)
        near = _Voice(_actor("near", "audio_mix"), ["also late"], delay=1.0)
        call, said, played = _on_call([phone, near])
        holding = await call.agent_turn("Hello")
        await asyncio.sleep(0.2)
        await call.close()
        return holding, said, played, [one["dropped"] for one in call.summary()]

    holding, said, played, dropped = asyncio.run(scenario())
    assert holding is False and said == [] and played == []
    assert dropped == [["not ready in time"], ["not ready in time"]]


def test_lines_cut_off_by_the_call_s_sub_actor_time_say_so():
    import time

    offset = [0.0]

    async def scenario():
        phone = _Voice(_actor("one"), ["too late"], delay=1.0)
        call, said, _played = _on_call(
            [phone], clock=lambda: time.monotonic() + offset[0]
        )
        call.cast.entering(Moment(2))
        # Earlier lines used all but 50 ms of the call's sub-actor time.
        call.cast.delivered(
            call.cast.actors[0], "earlier", 1, 0.0, registry.SUB_ACTOR_SECONDS - 0.05
        )
        holding = await call.agent_turn("Hello")
        await call.close()
        return holding, said, call.summary()

    holding, said, summary = asyncio.run(scenario())
    assert holding is False and said == []
    assert summary[0]["dropped"] == [registry.TIME_UP]


def test_only_one_line_is_written_and_voiced_at_a_time():
    writing = {"now": 0, "most": 0}

    class _Counted(_Voice):
        async def line(self, conversation):
            writing["now"] += 1
            writing["most"] = max(writing["most"], writing["now"])
            try:
                return await super().line(conversation)
            finally:
                writing["now"] -= 1

    async def scenario():
        voices = [
            _Counted(_actor(key, "audio_mix"), ["a line"], delay=0.02)
            for key in ("one", "two")
        ]
        call, _said, played = _on_call(voices)
        await call.agent_turn("Hello")
        await asyncio.sleep(0.2)
        await call.close()
        return played

    assert len(asyncio.run(scenario())) == 2
    assert writing["most"] == 1


def test_a_background_line_reaches_the_mixer_at_its_rate_so_it_plays_at_its_own_speed():
    from livekit import rtc

    from fi.simulate.simulation.engines import livekit as engine

    def frame(count=240):
        return rtc.AudioFrame(
            data=array.array("h", [1000] * count).tobytes(),
            sample_rate=24000,
            num_channels=1,
            samples_per_channel=count,
        )

    async def frames():
        for _ in range(100):
            yield frame()

    async def scenario():
        return [out async for out in engine._stream_at_mixer_rate(frames())]

    out = asyncio.run(scenario())
    # One second of 24 kHz speech is still one second at the mixer's 48 kHz, not half of one.
    assert {one.sample_rate for one in out} == {engine._BACKGROUND_MIXER_RATE}
    total = sum(one.samples_per_channel for one in out)
    assert abs(total - engine._BACKGROUND_MIXER_RATE) <= 48


def test_someone_who_takes_the_phone_waits_until_the_person_has_spoken():
    cast = Cast(
        [
            _actor("phone", kind="on_keyword", words=["pickup"]),
            _actor("near", "audio_mix"),
        ],
        "run",
    )
    # The agent's greeting names the keyword before the person has said a word: only the
    # background voice comes in; the phone-taker waits for the agent's next turn.
    first = cast.entering(Moment(1, last_text="Where should we pickup?"))
    assert [one["id"] for one in first] == ["near"]
    assert [one["id"] for one in cast.entering(Moment(2, last_text="Got it."))] == [
        "phone"
    ]
    assert {"kind": "triggered", "turn": 2}.items() <= next(
        one for one in cast.events if one["sub_actor"] == "phone"
    ).items()


def test_a_sub_actor_is_never_told_it_may_answer_for_the_person():
    brief = line_brief(
        _actor("phone"), situation="Book a ride.", said=[], conversation=[], language=""
    )
    assert "unless" not in brief
    assert "what they came for is theirs to say" in brief


def test_only_the_per_turn_read_is_bounded_and_evidence_reads_are_not(monkeypatch):
    from types import SimpleNamespace

    import psycopg

    from fi.alk.harness import call_runner

    seen = []

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, _sql):
            return SimpleNamespace(description=[], fetchall=lambda: [])

    def connect(_dsn, **kwargs):
        seen.append(kwargs["options"])
        return _Connection()

    monkeypatch.setattr(psycopg, "connect", connect)
    call_runner.read_tool_trace("postgresql://world")
    voice_engine.world_access("postgresql://world")()
    assert "statement_timeout" not in seen[0]
    assert "statement_timeout=2000" in seen[1]


def test_a_line_late_on_its_own_limit_never_ends_the_call_s_sub_actor_time():
    now = [0.0]
    cast = Cast([_actor("one"), _actor("two")], "run", clock=lambda: now[0])
    cast.entering(Moment(2))
    first = cast.line_limit(registry.HANDOFF_LINE_SECONDS)
    cast.delivered(cast.actors[1], "a line", 2, 0.0, 50.0, 0.0)
    second = cast.line_limit(registry.HANDOFF_LINE_SECONDS)
    assert first == registry.HANDOFF_LINE_SECONDS and second < first
    cast.late(cast.actors[0], cut_by_call_time=first < registry.HANDOFF_LINE_SECONDS)
    assert not cast.out_of_time()
    cast.late(cast.actors[1], cut_by_call_time=second < registry.HANDOFF_LINE_SECONDS)
    assert cast.out_of_time()
