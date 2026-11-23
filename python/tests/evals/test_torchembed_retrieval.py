"""Before/after retrieval test + base-install import check for metadata enrichment.

The text embedding is a deterministic stub (see examples/feedback_torchembed_retrieval_demo.py);
this verifies the mechanism, not the quality with a real embedding model.
"""
import subprocess
import sys
import textwrap

import pytest


def test_base_import_without_torch_or_torchembed():
    """store + encoder + package import with torch/torchembed blocked; enrichment is a no-op."""
    code = textwrap.dedent(
        """
        import sys
        for m in ("torch", "torchembed"):
            sys.modules[m] = None  # any import of these raises ImportError
        import fi.evals.feedback
        import fi.evals.feedback.store
        from fi.evals.feedback import torchembed_encoder as te
        assert te.is_available() is False
        assert not any(m in sys.modules and sys.modules[m] for m in ("torch", "torchembed"))
        try:
            te.FeedbackMetadataEncoder()
        except ImportError:
            pass
        else:
            raise SystemExit("encoder should raise ImportError")
        print("OK")
        """
    )
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout + r.stderr


import importlib.util
import os

sys.path.insert(0, os.path.dirname(__file__))
from examples_path import load_demo  # noqa: E402

requires_enrichment_env = pytest.mark.skipif(
    not all(importlib.util.find_spec(m) for m in ("chromadb", "torch", "torchembed")),
    reason="needs chromadb + torch + torchembed (pip install ai-evaluation[feedback-torch])",
)


@requires_enrichment_env
def test_enrichment_requested_without_torch_warns_and_falls_back(tmp_path, caplog, monkeypatch):
    from fi.evals.feedback import torchembed_encoder
    from fi.evals.feedback.store import ChromaFeedbackStore

    monkeypatch.setattr(torchembed_encoder, "is_available", lambda: False)
    with caplog.at_level("WARNING"):
        store = ChromaFeedbackStore(persist_directory=str(tmp_path), enrich_with_metadata=True)
    assert store._meta_encoder is None
    assert "enrichment is unavailable" in caplog.text


@requires_enrichment_env
def test_corrected_score_and_time_change_ranking():
    demo = load_demo()
    before = [e.id for e in demo.run(False)]
    after = [e.id for e in demo.run(True)]
    assert before[0] != "TARGET"  # text-only: target is NOT first
    assert before.index("TARGET") >= 3
    assert after[0] == "TARGET"   # with score/time targets: target is first
    assert [e.id for e in demo.run(True)] == after  # deterministic


@requires_enrichment_env
def test_enrichment_off_without_targets_is_text_only():
    """With enrichment on but no query targets, ranking equals the text-only ranking."""
    import tempfile
    demo = load_demo()
    from fi.evals.feedback.store import ChromaFeedbackStore

    with tempfile.TemporaryDirectory() as d:
        s = ChromaFeedbackStore(persist_directory=d, collection_prefix="t", enrich_with_metadata=True,
                                metadata_weight=demo.WEIGHT, text_embedding_fn=demo.stub_embed)
        for e in demo.dataset():
            s.add(e)
        ids = [e.id for e in s.query_similar("faithfulness", demo.QUERY, n_results=5)]
    assert ids == [e.id for e in demo.run(False)]
