"""torchembed-backed metadata encoder for feedback entries.

Encodes numerical and temporal fields from FeedbackEntry into dense vectors
using torchembed primitives (GaussianFourierProjection for scores,
CyclicEmbedding for time-of-day / day-of-week). These vectors are appended
to the text embedding stored in ChromaDB, so retrieval is informed by *both*
semantic content and score / time context.

Requires: pip install ai-evaluation[feedback-torch]
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from fi.evals.feedback.types import FeedbackEntry

_TORCHEMBED_AVAILABLE = False
try:
    import torch
    import torchembed  # noqa: F401 — presence check
    _TORCHEMBED_AVAILABLE = True
except ImportError:
    pass


def is_available() -> bool:
    """Return True when torchembed + torch are installed."""
    return _TORCHEMBED_AVAILABLE


class FeedbackMetadataEncoder:
    """Encode numerical and temporal metadata from a FeedbackEntry into a
    fixed-length float vector using torchembed primitives.

    The output vector has shape ``(out_dim,)`` and is ready to append to a
    text embedding produced by sentence-transformers or ChromaDB's default
    embedding function, giving the retrieval index joint signal over both
    semantic content and score / time proximity.

    Args:
        score_dim: Output dimension for each score channel (original + correct).
        time_dim: Output dimension for each cyclic time channel (hour + weekday).
        score_sigma: Bandwidth for the Gaussian Fourier projection on scores.
    """

    def __init__(
        self,
        score_dim: int = 32,
        time_dim: int = 16,
        score_sigma: float = 1.0,
    ) -> None:
        if not _TORCHEMBED_AVAILABLE:
            raise ImportError(
                "torchembed is required for FeedbackMetadataEncoder. "
                "Install it with: pip install ai-evaluation[feedback-torch]"
            )

        import torch
        from torchembed import GaussianFourierProjection, CyclicEmbedding

        # One projector shared across both score channels (original + correct).
        # GaussianFourierProjection(embed_dim) maps a scalar → (1, embed_dim).
        self._score_proj = GaussianFourierProjection(embed_dim=score_dim, scale=score_sigma)
        self._score_proj.eval()

        # CyclicEmbedding always outputs 2 dims (sin, cos) per channel.
        self._hour_enc = CyclicEmbedding(period=24)
        self._weekday_enc = CyclicEmbedding(period=7)
        for mod in (self._hour_enc, self._weekday_enc):
            mod.eval()

        # Total: 2 score channels × score_dim  +  2 time channels × 2 (sin/cos)
        self.out_dim = 2 * score_dim + 4

    @torch.no_grad()
    def encode(self, entry: "FeedbackEntry") -> List[float]:
        """Return a flat list of floats encoding the entry's metadata.

        Missing scores are replaced with 0.0 (neutral) before projection so
        the vector length is always ``self.out_dim``.
        """
        import torch

        # --- score channels --------------------------------------------------
        orig = float(entry.original_score) if entry.original_score is not None else 0.0
        corr = float(entry.correct_score) if entry.correct_score is not None else orig

        orig_t = torch.tensor([orig])             # (1,)
        corr_t = torch.tensor([corr])             # (1,)
        # GaussianFourierProjection returns (1, score_dim); squeeze to (score_dim,)
        score_vec = torch.cat(
            [self._score_proj(orig_t).squeeze(0), self._score_proj(corr_t).squeeze(0)],
            dim=-1,
        )                                         # (2 * score_dim,)

        # --- temporal channels -----------------------------------------------
        ts = entry.created_at
        hour_t = torch.tensor([float(ts.hour)])
        wday_t = torch.tensor([float(ts.weekday())])
        # CyclicEmbedding returns (1, 2); squeeze to (2,)
        time_vec = torch.cat(
            [self._hour_enc(hour_t).squeeze(0), self._weekday_enc(wday_t).squeeze(0)],
            dim=-1,
        )                                         # (4,)

        full = torch.cat([score_vec, time_vec], dim=-1)   # (out_dim,)
        # L2-normalise so scale matches unit-norm text embeddings
        full = full / (full.norm() + 1e-8)
        return full.tolist()

    def encode_batch(self, entries: List["FeedbackEntry"]) -> List[List[float]]:
        """Encode a list of entries, returning one vector per entry."""
        return [self.encode(e) for e in entries]


def make_enriched_document(
    text: str,
    meta_vector: Optional[List[float]],
    weight: float = 0.3,
) -> str:
    """Append a compact numerical signature to a text document.

    ChromaDB stores documents as strings.  We embed the metadata vector as a
    whitespace-separated suffix (``|meta|<floats>``).  The ChromaDB embedding
    function ignores it (it doesn't know the separator), but the vector signal
    leaks weakly into the token-level representation of some embedding models.

    For a cleaner approach, use a custom ChromaDB EmbeddingFunction that reads
    the ``|meta|`` section and blends it with the text embedding at query time.
    The ``weight`` parameter controls the blending ratio in that custom function.

    Args:
        text: Original document text.
        meta_vector: Output of ``FeedbackMetadataEncoder.encode()``.
        weight: Intended blending weight (stored as metadata hint only).

    Returns:
        Enriched document string.
    """
    if not meta_vector:
        return text
    sig = " ".join(f"{v:.4f}" for v in meta_vector[:16])  # keep suffix short
    return f"{text}\n|meta|{sig}|w={weight:.2f}|"
