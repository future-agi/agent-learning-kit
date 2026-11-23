"""Tests for torchembed-backed feedback metadata encoder."""
import pytest
from datetime import datetime, timezone

pytest.importorskip("torchembed", reason="torchembed not installed")
pytest.importorskip("torch", reason="torch not installed")

from fi.evals.feedback.torchembed_encoder import (
    FeedbackMetadataEncoder,
    is_available,
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


def test_full_vector_carries_correct_score_and_time():
    """All 68 coords matter: correct score (32-63) and time (64-67) change the vector."""
    enc = FeedbackMetadataEncoder()
    base = enc.encode(make_entry(0.5, 0.5))
    other_corr = enc.encode(make_entry(0.5, 0.95))
    assert base[32:64] != other_corr[32:64]
    e2 = make_entry(0.5, 0.5)
    e2.created_at = datetime(2026, 7, 5, 3, 0, tzinfo=timezone.utc)
    assert base[64:] != enc.encode(e2)[64:]
    assert any(abs(x) > 1e-3 for x in base[64:])  # time channel not swamped


def test_encoder_is_deterministic_across_instances():
    a = FeedbackMetadataEncoder().encode(make_entry())
    b = FeedbackMetadataEncoder().encode(make_entry())
    assert a == b


def test_encode_query_unspecified_channels_zero():
    enc = FeedbackMetadataEncoder()
    assert enc.encode_query() == [0.0] * enc.out_dim
    q = enc.encode_query(correct_score=0.9)
    assert all(v == 0.0 for v in q[:32]) and all(v == 0.0 for v in q[64:])
    assert any(v != 0.0 for v in q[32:64])


def test_blend_embeddings_weights():
    from fi.evals.feedback.torchembed_encoder import blend_embeddings
    v = blend_embeddings([3.0, 4.0], [1.0, 0.0], weight=0.25)
    assert len(v) == 4
    assert abs(sum(x * x for x in v) - 1.0) < 1e-9
    zero = blend_embeddings([1.0, 0.0], [0.0, 0.0], weight=0.5)
    assert abs(sum(x * x for x in zero) - 0.5) < 1e-9
