"""Tests for torchembed-backed feedback metadata encoder."""
import pytest
from datetime import datetime, timezone

pytest.importorskip("torchembed", reason="torchembed not installed")
pytest.importorskip("torch", reason="torch not installed")

from fi.evals.feedback.torchembed_encoder import (
    FeedbackMetadataEncoder,
    is_available,
    make_enriched_document,
)
from fi.evals.feedback.types import FeedbackEntry


def make_entry(orig: float = 0.8, corr: float = 0.9) -> FeedbackEntry:
    return FeedbackEntry(
        eval_name="faithfulness",
        inputs={"response": "The sky is blue.", "context": "Sky color varies."},
        original_score=orig,
        correct_score=corr,
        created_at=datetime(2026, 7, 8, 14, 30, tzinfo=timezone.utc),  # Tuesday, 14:00
    )


def test_is_available():
    assert is_available() is True


def test_encode_returns_correct_length():
    enc = FeedbackMetadataEncoder(score_dim=32, time_dim=16)
    vec = enc.encode(make_entry())
    assert len(vec) == enc.out_dim  # 2*32 + 4 = 68
    assert all(isinstance(v, float) for v in vec)


def test_encode_normalised():
    import math
    enc = FeedbackMetadataEncoder()
    vec = enc.encode(make_entry())
    norm = math.sqrt(sum(v * v for v in vec))
    assert abs(norm - 1.0) < 1e-4


def test_different_scores_produce_different_vectors():
    enc = FeedbackMetadataEncoder()
    v1 = enc.encode(make_entry(orig=0.2, corr=0.3))
    v2 = enc.encode(make_entry(orig=0.9, corr=0.95))
    assert v1 != v2


def test_missing_scores_handled():
    enc = FeedbackMetadataEncoder()
    entry = make_entry()
    entry.original_score = None
    entry.correct_score = None
    vec = enc.encode(entry)
    assert len(vec) == enc.out_dim


def test_encode_batch():
    enc = FeedbackMetadataEncoder()
    entries = [make_entry(0.1 * i, 0.1 * i + 0.05) for i in range(5)]
    vecs = enc.encode_batch(entries)
    assert len(vecs) == 5
    assert all(len(v) == enc.out_dim for v in vecs)


def test_make_enriched_document_contains_meta_marker():
    enc = FeedbackMetadataEncoder()
    vec = enc.encode(make_entry())
    doc = make_enriched_document("hello world", vec)
    assert "|meta|" in doc
    assert "hello world" in doc


def test_make_enriched_document_no_vector():
    doc = make_enriched_document("hello world", None)
    assert doc == "hello world"
