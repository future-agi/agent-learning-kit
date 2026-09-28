import asyncio

from fi.simulate.simulation.engines.opening import PromptOpeningGate, opening_hint


def test_prompt_extraction_and_safe_fallback():
    assert (
        opening_hint("Greeting:\nSay 'Welcome to Acme.'\nInstructions: Help people.")
        == "Say 'Welcome to Acme.'"
    )
    assert "Greet the customer" in opening_hint(
        "You help customers. Greet the customer and ask what they need."
    )
    assert opening_hint("Help people with their accounts.") == ""
    assert PromptOpeningGate("Help people.").accepts("This call is being recorded.")


def test_mixed_disclosure_and_greeting_is_interactive():
    gate = PromptOpeningGate("Greeting: Ask what they need.")
    assert gate.accepts("This call is being recorded. Welcome, how can I help you?")
    assert not gate.pending


def test_prompt_can_prescribe_recording_disclosure():
    gate = PromptOpeningGate('Greeting: Say "This call is being recorded."')
    assert gate.accepts("This call is being recorded.")


def test_silent_agent_fallback_opens_once():
    async def run():
        gate = PromptOpeningGate("Greeting: Say hello.", timeout=0.02)
        opened = []
        await gate.wait_then_open(lambda: opened.append(True))
        assert opened == [True]
        assert not gate.pending

    asyncio.run(run())


def test_speech_onset_cancels_deadline_even_for_long_greeting():
    async def run():
        gate = PromptOpeningGate("Greeting: Say hello.", timeout=0.03)
        opened = []
        task = asyncio.create_task(gate.wait_then_open(lambda: opened.append(True)))
        await asyncio.sleep(0.01)
        gate.speech_started()
        await asyncio.sleep(0.05)
        assert not opened
        gate.speech_ended()
        assert gate.accepts("Good morning, what do you need?")
        await asyncio.wait_for(task, 0.1)
        assert not opened

    asyncio.run(run())


def test_disclosure_then_silence_rearms_fallback():
    async def run():
        gate = PromptOpeningGate("Greeting: Ask how to help.", timeout=0.02)
        gate.speech_started()
        gate.speech_ended()
        assert not gate.accepts("This call is being recorded.")
        opened = []
        await gate.wait_then_open(lambda: opened.append(True))
        assert opened == [True]

    asyncio.run(run())


def test_disclosure_then_delayed_long_greeting_never_opens_over_it():
    async def run():
        gate = PromptOpeningGate("Greeting: Ask how to help.", timeout=0.03)
        gate.speech_started()
        gate.speech_ended()
        assert not gate.accepts("This call is being recorded.")
        opened = []
        task = asyncio.create_task(gate.wait_then_open(lambda: opened.append(True)))
        await asyncio.sleep(0.01)
        gate.speech_started()
        await asyncio.sleep(0.05)
        gate.speech_ended()
        assert not opened
        assert gate.accepts("Welcome back, can I take your name?")
        await asyncio.wait_for(task, 0.1)
        assert not opened

    asyncio.run(run())


def test_buffered_authoritative_stream_blocks_timer_until_completion():
    async def run():
        gate = PromptOpeningGate("Greeting: Welcome the caller.", timeout=0.02)
        gate.stream_started()
        opened = []
        task = asyncio.create_task(gate.wait_then_open(lambda: opened.append(True)))
        await asyncio.sleep(0.04)
        assert not opened
        assert not gate.accepts("This call is being recorded.")
        gate.stream_ended()
        gate.stream_started()
        await asyncio.sleep(0.04)
        assert not opened
        assert gate.accepts("Hello there!")
        gate.stream_ended()
        await asyncio.wait_for(task, 0.1)
        assert not opened

    asyncio.run(run())
