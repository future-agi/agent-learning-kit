"""Deterministic near-duplicate detection. No model calls, no context limits.

Two planned scenarios are near-duplicates when the words describing their situation and outcome
mostly overlap. Token-set similarity is crude next to embeddings, but it is free, deterministic,
dependency-free, and catches the rewording-of-the-same-situation failure that matters at scale;
an embedding backend can replace ``similarity`` later without touching callers.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping

_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be but by for if in into is it no not of on or such that the "
    "their then there these they this to was will with without user agent".split()
)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(
        token for token in _WORD.findall(str(text).lower()) if token not in _STOPWORDS
    )


def _signature(row: Mapping) -> frozenset[str]:
    return _tokens(
        " ".join(
            str(row.get(key, ""))
            for key in ("situation", "unique_end_state", "target_failure")
        )
    )


def similarity(a: Mapping, b: Mapping) -> float:
    """Jaccard similarity of the rows' descriptive token sets, 0..1."""
    ta, tb = _signature(a), _signature(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def near_duplicate(
    row: Mapping, existing: Iterable[Mapping], *, threshold: float = 0.6
) -> Mapping | None:
    """Return the first existing row this one nearly duplicates, or None."""
    for other in existing:
        if similarity(row, other) >= threshold:
            return other
    return None
