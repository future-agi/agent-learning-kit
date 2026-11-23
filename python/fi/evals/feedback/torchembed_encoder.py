"""torchembed-backed metadata encoder for feedback entries (optional extra).

Encodes numerical and temporal fields of a ``FeedbackEntry`` into a dense
vector using torchembed primitives (``GaussianFourierProjection`` for scores,
``CyclicEmbedding`` for hour-of-day / day-of-week).

How the vector is used (design choice)
--------------------------------------
``ChromaFeedbackStore(enrich_with_metadata=True)`` does **not** append floats to
the document text. Instead it computes the text embedding itself, blends it with
this metadata vector with :func:`blend_embeddings`, and hands the result to
Chroma through its ``embeddings=`` / ``query_embeddings=`` path. Because the
*same* blend is applied to stored entries and to queries (queries pass a target
``correct_score`` / ``created_at``), the corrected score and time-of-day really
change the ranking. The blend is a concatenation
``[sqrt(1-w) * text_unit, sqrt(w) * meta_unit]`` so that, for unit vectors,
``cos(blend_a, blend_b) = (1-w) * cos(text) + w * cos(meta)``: ``w`` is exactly
the share of the similarity that comes from metadata.

Caveats: the time channels are *cyclic* (hour of day, weekday), not absolute
recency; the Fourier score features are a smooth kernel over score distance.

This module imports cleanly **without** torch/torchembed; they are only
imported when a :class:`FeedbackMetadataEncoder` is constructed.

Requires: pip install ai-evaluation[feedback-torch]
"""

from __future__ import annotations

import importlib.util
import math
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional, Sequence

if TYPE_CHECKING:
    from fi.evals.feedback.types import FeedbackEntry


def is_available() -> bool:
    """Return True when both torch and torchembed are installed.

    Uses ``find_spec`` so that merely asking does not import (heavy) torch.
    """
    try:
        return (
            importlib.util.find_spec("torch") is not None
            and importlib.util.find_spec("torchembed") is not None
        )
    except (ImportError, ValueError):
        return False


class FeedbackMetadataEncoder:
    """Encode score / time metadata of a FeedbackEntry into a fixed-length vector.

    Layout of the ``out_dim = 2 * score_dim + 4`` vector (default 68):
    ``[0, score_dim)`` original score, ``[score_dim, 2*score_dim)`` corrected
    score, then ``(sin, cos)`` of hour-of-day and ``(sin, cos)`` of weekday.
    Each of the four channels is L2-normalised separately (so the 2-d time
    channels are not swamped by the wide score channels) and the whole vector is
    then L2-normalised.

    ``GaussianFourierProjection`` draws its frequencies at random; they are
    seeded here (``seed``) so vectors stored in one process are comparable with
    query vectors built in another. Keep ``seed``/``score_dim``/``score_sigma``
    fixed for the lifetime of a collection.

    Args:
        score_dim: Output dimension for each score channel (must be even).
        time_dim: Kept for backwards compatibility; unused (cyclic channels are 2-d).
        score_sigma: Bandwidth of the Gaussian Fourier projection on scores.
        seed: Seed for the Fourier frequencies.
    """

    def __init__(
        self,
        score_dim: int = 32,
        time_dim: int = 16,
        score_sigma: float = 1.0,
        seed: int = 0,
    ) -> None:
        if not is_available():
            raise ImportError(
                "torchembed is required for FeedbackMetadataEncoder. "
                "Install it with: pip install ai-evaluation[feedback-torch]"
            )
        import torch
        from torchembed import CyclicEmbedding, GaussianFourierProjection

        self.score_dim = score_dim
        self._score_proj = GaussianFourierProjection(embed_dim=score_dim, scale=score_sigma)
        gen = torch.Generator().manual_seed(seed)
        with torch.no_grad():
            self._score_proj.W.copy_(
                torch.randn(score_dim // 2, generator=gen) * score_sigma
            )
        self._hour_enc = CyclicEmbedding(period=24)
        self._weekday_enc = CyclicEmbedding(period=7)
        for mod in (self._score_proj, self._hour_enc, self._weekday_enc):
            mod.eval()

        self.out_dim = 2 * score_dim + 4

    # -- internals ----------------------------------------------------------
    def _channels(
        self,
        orig: Optional[float],
        corr: Optional[float],
        ts: Optional[datetime],
    ) -> List[float]:
        """Concatenate the four unit-norm channels; ``None`` inputs give zeros."""
        import torch

        sd = self.score_dim
        zeros = lambda n: [0.0] * n  # noqa: E731

        def unit(t: "torch.Tensor") -> List[float]:
            return (t / (t.norm() + 1e-8)).tolist()

        with torch.no_grad():
            o = unit(self._score_proj(torch.tensor([float(orig)])).squeeze(0)) if orig is not None else zeros(sd)
            c = unit(self._score_proj(torch.tensor([float(corr)])).squeeze(0)) if corr is not None else zeros(sd)
            if ts is not None:
                h = self._hour_enc(torch.tensor([ts.hour + ts.minute / 60.0])).squeeze(0).tolist()
                w = self._weekday_enc(torch.tensor([float(ts.weekday())])).squeeze(0).tolist()
            else:
                h, w = zeros(2), zeros(2)
        return _unit(o + c + h + w)

    # -- public API ---------------------------------------------------------
    def encode(self, entry: "FeedbackEntry") -> List[float]:
        """Metadata vector for a stored entry (length ``out_dim``, unit norm).

        A missing original score is encoded as 0.0; a missing corrected score
        falls back to the original score.
        """
        orig = float(entry.original_score) if entry.original_score is not None else 0.0
        corr = float(entry.correct_score) if entry.correct_score is not None else orig
        return self._channels(orig, corr, entry.created_at)

    def encode_query(
        self,
        correct_score: Optional[float] = None,
        created_at: Optional[datetime] = None,
        original_score: Optional[float] = None,
    ) -> List[float]:
        """Metadata vector for a *query*: only the targets you give are encoded.

        Unspecified channels are zero, so they contribute nothing to similarity.
        With no targets at all the result is the zero vector (text-only ranking).
        """
        return self._channels(original_score, correct_score, created_at)

    def encode_batch(self, entries: List["FeedbackEntry"]) -> List[List[float]]:
        """Encode a list of entries, returning one vector per entry."""
        return [self.encode(e) for e in entries]


def _unit(v: Sequence[float]) -> List[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 1e-12 else [0.0] * len(v)


def blend_embeddings(
    text_embedding: Sequence[float],
    meta_vector: Sequence[float],
    weight: float = 0.3,
) -> List[float]:
    """Concatenate ``[sqrt(1-w) * unit(text), sqrt(w) * meta]``.

    ``meta_vector`` is expected to be unit-norm or all-zero (a query with no
    metadata target). For two blends of unit vectors the dot product equals
    ``(1-w) * text_similarity + w * meta_similarity``.
    """
    if not 0.0 <= weight <= 1.0:
        raise ValueError(f"weight must be in [0, 1], got {weight}")
    a, b = math.sqrt(1.0 - weight), math.sqrt(weight)
    return [a * x for x in _unit(text_embedding)] + [b * x for x in meta_vector]
