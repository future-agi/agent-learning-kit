"""Feedback storage backends.

Provides abstract FeedbackStore and two implementations:
- InMemoryFeedbackStore: for testing and small-scale usage
- ChromaFeedbackStore: for production with semantic vector search

ChromaFeedbackStore can optionally blend a torchembed-encoded score/time vector
into the text embedding (``enrich_with_metadata=True``), so retrieval can also
target a corrected score or time of day. Off by default; torch/torchembed are
only imported when it is enabled. Install with: pip install ai-evaluation[feedback-torch]
"""

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .types import FeedbackEntry

logger = logging.getLogger(__name__)


class FeedbackStore(ABC):
    """Abstract base class for feedback persistence."""

    @abstractmethod
    def add(self, entry: FeedbackEntry) -> str:
        """Store a feedback entry. Returns the entry ID."""
        ...

    @abstractmethod
    def query_similar(
        self,
        metric_name: str,
        text: str,
        n_results: int = 5,
    ) -> List[FeedbackEntry]:
        """Find feedback entries semantically similar to the given text,
        filtered by metric_name."""
        ...

    @abstractmethod
    def get_by_metric(self, metric_name: str, limit: int = 100) -> List[FeedbackEntry]:
        """Get all feedback entries for a specific metric."""
        ...

    @abstractmethod
    def count(self, metric_name: Optional[str] = None) -> int:
        """Count entries, optionally filtered by metric_name."""
        ...

    @abstractmethod
    def delete(self, entry_id: str) -> bool:
        """Delete a feedback entry by ID."""
        ...


class InMemoryFeedbackStore(FeedbackStore):
    """In-memory feedback store for testing and small-scale usage.

    No vector search -- falls back to recency-based retrieval.
    Suitable for unit tests and quick experimentation.
    """

    def __init__(self):
        self._entries: Dict[str, FeedbackEntry] = {}

    def add(self, entry: FeedbackEntry) -> str:
        self._entries[entry.id] = entry
        return entry.id

    def query_similar(
        self,
        metric_name: str,
        text: str,
        n_results: int = 5,
    ) -> List[FeedbackEntry]:
        # No semantic search -- return most recent entries for this metric
        filtered = [
            e for e in self._entries.values()
            if e.eval_name == metric_name
        ]
        filtered.sort(key=lambda e: e.created_at, reverse=True)
        return filtered[:n_results]

    def get_by_metric(self, metric_name: str, limit: int = 100) -> List[FeedbackEntry]:
        return [
            e for e in self._entries.values()
            if e.eval_name == metric_name
        ][:limit]

    def count(self, metric_name: Optional[str] = None) -> int:
        if metric_name:
            return sum(1 for e in self._entries.values() if e.eval_name == metric_name)
        return len(self._entries)

    def delete(self, entry_id: str) -> bool:
        return self._entries.pop(entry_id, None) is not None


class ChromaFeedbackStore(FeedbackStore):
    """ChromaDB-backed feedback store with semantic vector search.

    Supports two modes:
    - Local: in-process persistent ChromaDB (default)
    - Service: connects to a remote ChromaDB server (e.g. Docker container)

    Embedding is handled by ChromaDB's built-in default embedding function
    (all-MiniLM-L6-v2 via sentence-transformers) OR via a LiteLLM embedding
    function for API-based embeddings.

    When ``enrich_with_metadata=True`` (requires the ``feedback-torch`` extra) the
    store computes the text embedding itself, concatenates it with a torchembed
    vector encoding original/corrected score and hour/weekday (see
    ``torchembed_encoder``), and passes the blend to Chroma via ``embeddings=``.
    Queries apply the same blend using a target given to ``query_similar``
    (``correct_score=``, ``created_at=``); without a target, ranking is
    text-only. Enriched entries live in a separate ``..._meta`` collection
    because their vectors have a different dimension. Default behaviour
    (enrichment off) is unchanged.

    Args:
        host: ChromaDB server host. None = local persistent mode.
        port: ChromaDB server port. Default 8000.
        persist_directory: Local storage path (local mode only).
            Default "~/.fi/feedback/chroma".
        collection_prefix: Prefix for ChromaDB collection names.
        embedding_model: LiteLLM model string for embeddings.
            None = use ChromaDB's default (sentence-transformers).
        enrich_with_metadata: Blend torchembed score/time vectors into the
            embeddings. If requested but torch/torchembed are missing, a warning
            is logged and the store behaves as if it were off.
        metadata_weight: Share (0–1, default 0.3) of the similarity that comes
            from metadata rather than text.
        text_embedding_fn: Optional ``List[str] -> List[List[float]]`` used for
            the text embedding (also when enrichment is off). Default: the LiteLLM
            function if ``embedding_model`` is set, else Chroma's default MiniLM. Mainly for
            tests / offline use.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: int = 8000,
        persist_directory: Optional[str] = None,
        collection_prefix: str = "fi_feedback",
        embedding_model: Optional[str] = None,
        enrich_with_metadata: bool = False,
        metadata_weight: float = 0.3,
        text_embedding_fn: Optional[Callable[[List[str]], List[List[float]]]] = None,
    ):
        try:
            import chromadb
        except ImportError:
            raise ImportError(
                "chromadb is required for ChromaFeedbackStore. "
                "Install it with: pip install ai-evaluation[feedback]"
            )

        self._collection_prefix = collection_prefix
        self._embedding_model = embedding_model
        self._metadata_weight = metadata_weight

        self._text_embedding_fn = text_embedding_fn

        # torchembed metadata encoder (optional, imported lazily)
        self._meta_encoder: Optional[Any] = None
        if enrich_with_metadata:
            try:
                from .torchembed_encoder import FeedbackMetadataEncoder, is_available

                if is_available():
                    self._meta_encoder = FeedbackMetadataEncoder()
                    logger.info("torchembed metadata enrichment enabled for ChromaFeedbackStore")
                else:
                    raise ImportError("torch/torchembed not installed")
            except Exception as exc:
                logger.warning(
                    "enrich_with_metadata=True but metadata enrichment is unavailable "
                    f"({exc}); falling back to text-only retrieval. "
                    "Install with: pip install ai-evaluation[feedback-torch]"
                )

        # Initialize ChromaDB client
        if host:
            self._client = chromadb.HttpClient(host=host, port=port)
            logger.info(f"Connected to ChromaDB server at {host}:{port}")
        else:
            import os
            path = persist_directory or os.path.expanduser("~/.fi/feedback/chroma")
            os.makedirs(path, exist_ok=True)
            self._client = chromadb.PersistentClient(path=path)
            logger.info(f"Using local ChromaDB at {path}")

        # Set up embedding function
        self._embedding_fn = None
        if embedding_model:
            self._embedding_fn = self._make_litellm_embedding_fn(embedding_model)

    @staticmethod
    def _make_litellm_embedding_fn(model: str):
        """Create a ChromaDB-compatible embedding function using LiteLLM."""
        from chromadb.api.types import EmbeddingFunction, Documents, Embeddings
        import litellm

        class LiteLLMEmbedding(EmbeddingFunction):
            def __call__(self, input: Documents) -> Embeddings:
                response = litellm.embedding(model=model, input=input)
                return [item["embedding"] for item in response.data]

        return LiteLLMEmbedding()

    def _get_collection(self, metric_name: str):
        """Get or create a ChromaDB collection for a specific metric."""
        name = f"{self._collection_prefix}_{metric_name}".replace(".", "_")
        if self._meta_encoder is not None:
            name += "_meta"  # blended vectors have a different dimension
        kwargs: Dict[str, Any] = {"name": name}
        if self._embedding_fn:
            kwargs["embedding_function"] = self._embedding_fn
        return self._client.get_or_create_collection(**kwargs)

    def _embed_text(self, text: str) -> List[float]:
        fn = self._text_embedding_fn or self._embedding_fn
        if fn is None:
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

            fn = self._text_embedding_fn = DefaultEmbeddingFunction()
        return [float(x) for x in fn([text])[0]]

    def add(self, entry: FeedbackEntry) -> str:
        collection = self._get_collection(entry.eval_name)
        document = entry.to_embedding_text()
        extra: Dict[str, Any] = {}
        if self._meta_encoder is not None:
            from .torchembed_encoder import blend_embeddings

            extra["embeddings"] = [blend_embeddings(
                self._embed_text(document),
                self._meta_encoder.encode(entry),
                self._metadata_weight,
            )]
        elif self._text_embedding_fn is not None:
            extra["embeddings"] = [self._embed_text(document)]
        collection.add(
            ids=[entry.id],
            documents=[document],
            **extra,
            metadatas=[{
                "metric_name": entry.eval_name,
                "original_score": entry.original_score or 0.0,
                "correct_score": entry.correct_score if entry.correct_score is not None else -1.0,
                "correct_reason": entry.correct_reason[:1000],
                "inputs_json": json.dumps(entry.inputs, default=str)[:4000],
                "original_reason": entry.original_reason[:1000],
                "created_at": entry.created_at.isoformat(),
            }],
        )
        return entry.id

    def query_similar(
        self,
        metric_name: str,
        text: str,
        n_results: int = 5,
        correct_score: Optional[float] = None,
        created_at: Optional[datetime] = None,
        original_score: Optional[float] = None,
    ) -> List[FeedbackEntry]:
        """Return the entries most similar to ``text``.

        With metadata enrichment on, ``correct_score`` / ``original_score`` /
        ``created_at`` (hour-of-day + weekday) are *targets*: entries whose
        metadata is close to them are pulled up the ranking (by ``metadata_weight``).
        Omitted targets are ignored. Ignored entirely when enrichment is off.
        """
        collection = self._get_collection(metric_name)

        if collection.count() == 0:
            return []

        actual_n = min(n_results, collection.count())

        if self._meta_encoder is not None:
            from .torchembed_encoder import blend_embeddings

            q = blend_embeddings(
                self._embed_text(text),
                self._meta_encoder.encode_query(
                    correct_score=correct_score,
                    created_at=created_at,
                    original_score=original_score,
                ),
                self._metadata_weight,
            )
            results = collection.query(query_embeddings=[q], n_results=actual_n)
        elif self._text_embedding_fn is not None:
            results = collection.query(
                query_embeddings=[self._embed_text(text)], n_results=actual_n
            )
        else:
            results = collection.query(query_texts=[text], n_results=actual_n)

        entries = []
        for i, meta in enumerate(results["metadatas"][0]):
            inputs = {}
            try:
                inputs = json.loads(meta.get("inputs_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                pass

            correct_score_val = meta.get("correct_score", -1.0)
            entry = FeedbackEntry(
                id=results["ids"][0][i],
                eval_name=meta.get("metric_name", metric_name),
                inputs=inputs,
                original_score=meta.get("original_score"),
                original_reason=meta.get("original_reason", ""),
                correct_score=correct_score_val if correct_score_val >= 0 else None,
                correct_reason=meta.get("correct_reason", ""),
            )
            entries.append(entry)

        return entries

    def get_by_metric(self, metric_name: str, limit: int = 100) -> List[FeedbackEntry]:
        collection = self._get_collection(metric_name)
        if collection.count() == 0:
            return []
        results = collection.get(limit=limit)
        entries = []
        for i, meta in enumerate(results["metadatas"]):
            inputs = {}
            try:
                inputs = json.loads(meta.get("inputs_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                pass
            correct_score_val = meta.get("correct_score", -1.0)
            entry = FeedbackEntry(
                id=results["ids"][i],
                eval_name=meta.get("metric_name", metric_name),
                inputs=inputs,
                original_score=meta.get("original_score"),
                original_reason=meta.get("original_reason", ""),
                correct_score=correct_score_val if correct_score_val >= 0 else None,
                correct_reason=meta.get("correct_reason", ""),
            )
            entries.append(entry)
        return entries

    def count(self, metric_name: Optional[str] = None) -> int:
        if metric_name:
            return self._get_collection(metric_name).count()
        total = 0
        for col in self._client.list_collections():
            if col.name.startswith(self._collection_prefix):
                total += col.count()
        return total

    def delete(self, entry_id: str) -> bool:
        for col in self._client.list_collections():
            if col.name.startswith(self._collection_prefix):
                try:
                    col.delete(ids=[entry_id])
                    return True
                except Exception:
                    continue
        return False
