"""The simulated caller should not deliberate before answering."""

from fi.simulate.simulation.livekit_models import _simulator_thinking


def test_caller_does_not_think_by_default():
    assert _simulator_thinking("gemini-3.7-flash") == {"thinking_budget": 0}


def test_a_run_can_ask_for_thinking(monkeypatch):
    monkeypatch.setenv("SIMULATOR_LLM_THINKING", "low")
    assert _simulator_thinking("gemini-3.7-flash") == {"thinking_level": "low"}


def test_a_numeric_budget_is_passed_through(monkeypatch):
    monkeypatch.setenv("SIMULATOR_LLM_THINKING", "512")
    assert _simulator_thinking("gemini-3.7-flash") == {"thinking_budget": 512}


def test_non_gemini_models_are_left_alone():
    assert _simulator_thinking("gpt-4o") is None
