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
