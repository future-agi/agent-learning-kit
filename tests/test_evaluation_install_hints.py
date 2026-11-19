"""The kit's optional-feature hints must not reinstall the older evaluator."""

import sys

import pytest


def test_chroma_hint_names_kit_extra(monkeypatch):
    from fi.evals.feedback.store import ChromaFeedbackStore

    monkeypatch.setitem(sys.modules, "chromadb", None)
    with pytest.raises(ImportError) as raised:
        ChromaFeedbackStore()
    assert 'python -m pip install "agent-learning-kit[feedback]"' in str(raised.value)


def test_nli_hints_name_kit_extra_without_loading_weights(monkeypatch):
    from fi.evals.metrics.hallucination import nli

    def unavailable(*args, **kwargs):
        raise OSError("fixture weights unavailable")

    monkeypatch.setattr(nli, "_NLI_AVAILABLE", True)
    monkeypatch.setattr(nli, "_nli_pipeline", None)
    monkeypatch.setattr(nli, "_hf_pipeline", unavailable, raising=False)
    monkeypatch.setattr(nli, "_heuristic_warning_issued", False)
    for warn in (nli._get_nli_pipeline, nli._warn_heuristic_fallback):
        with pytest.warns(RuntimeWarning, match=r'agent-learning-kit\[nli\]'):
            warn()


def test_simulation_evaluator_missing_engine_hints(monkeypatch):
    from fi.opt.simulation import SimulationEvaluator
    from fi.opt.targets import AgentCandidate

    for name in ("fi.simulate", "fi.simulate.evaluation", "fi.evals.metrics.agents"):
        monkeypatch.setitem(sys.modules, name, None)
    evaluator = SimulationEvaluator()
    report = object()
    checks = [
        (evaluator._get_runner, "runner or runner_cls"),
        (lambda: evaluator._evaluate_report(report), "evaluate_report_fn"),
        (lambda: evaluator._evaluate_agent_report(report, AgentCandidate.from_config({})),
         "fi.evals.metrics.agents"),
    ]
    for call, hint in checks:
        with pytest.raises(RuntimeError) as raised:
            call()
        message = str(raised.value)
        assert hint in message and "kit" in message
        assert not any(name in message for name in ("ai-evaluation", "agent-simulate", "simulate-sdk"))
        assert isinstance(raised.value.__cause__, ImportError)

    # The injection paths remain usable even when the engine cannot be imported.
    provided = SimulationEvaluator(runner=report, evaluate_report_fn=lambda value: value)
    assert provided._get_runner() is report
    assert provided._evaluate_report(report) is report
