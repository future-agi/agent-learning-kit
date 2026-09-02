"""Whether two scenarios are the same test, when they share no words.

The lexical check catches rewordings and near-copies, which is most of what a suite accumulates,
and it has a limit that is easy to state and impossible to fix with a cleverer string metric:
"caller cannot find their booking" and "the booking cannot be found by the caller" share two
content words out of four and score 0.5, which is under any threshold that does not also fire on
genuinely different lines. Shorter lines make it worse, and a plan is made of short lines.

Embeddings settle it. Measured on that exact pair with Vertex `text-embedding-005`: the rewording
scores 0.95 and a genuinely different angle on the same cell scores 0.42. The gap is wide enough
that a threshold in between is not a judgement call.

This is optional by construction. No credentials, no network, no library, or an API that refuses:
every one of those returns nothing and the caller keeps its lexical answer. A duplicate check is
worth having and never worth stopping a run over.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Vertex's general-purpose text embedding. Named here rather than taken from the harness model
# setting: the model that writes scenarios and the model that measures them are different
# choices, and pinning this one keeps a similarity score comparable between runs.
MODEL = "text-embedding-005"

# Above this, two lines are the same test. From the measured gap: rewordings land at 0.93 to 0.96
# and genuinely different angles on one cell sit near 0.4, so anywhere in the middle works and
# 0.88 leaves room for a rewording that is longer than its original.
SAME_TEST = 0.88

# One request per batch of this many. Vertex refuses very large batches, and a suite of a thousand
# should not become a thousand requests.
PER_REQUEST = 100


@dataclass
class Pair:
    """Two things that read as one test, and how alike they are."""

    one: str
    two: str
    score: float


def _client():
    """A Vertex client, or None. Never raises: this whole module is optional."""
    try:
        from google import genai
    except Exception:  # pragma: no cover - depends on the machine
        return None

    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project:
        path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        if path:
            try:
                import json

                with open(path, encoding="utf-8") as handle:
                    project = str(json.load(handle).get("project_id") or "")
            except Exception:
                project = ""
    if not project:
        return None
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1").strip() or "us-central1"
    try:
        return genai.Client(vertexai=True, project=project, location=location)
    except Exception as why:  # pragma: no cover - depends on credentials
        logger.info("no embedding client, falling back to the lexical check: %s", why)
        return None


def vectors(lines: list[str]) -> list[list[float]] | None:
    """Embed each line, or None if embedding is not available here."""
    if not lines:
        return []
    client = _client()
    if client is None:
        return None
    found: list[list[float]] = []
    try:
        for start in range(0, len(lines), PER_REQUEST):
            batch = lines[start : start + PER_REQUEST]
            answer = client.models.embed_content(model=MODEL, contents=batch)
            found.extend(list(one.values) for one in answer.embeddings)
    except Exception as why:
        logger.info("embedding failed, falling back to the lexical check: %s", why)
        return None
    return found


def _normalised(rows: list[list[float]]):
    import math

    out = []
    for row in rows:
        size = math.sqrt(sum(value * value for value in row)) or 1.0
        out.append([value / size for value in row])
    return out


def duplicates(
    named: list[tuple[str, str]], *, within: dict[str, str] | None = None, threshold: float = SAME_TEST
) -> list[Pair] | None:
    """Pairs that read as one test. ``named`` is (id, text); None means embedding was unavailable.

    ``within`` optionally scopes comparison, keyed by id: only ids sharing a group are compared.
    Two cells legitimately share a situation, and comparing across them would push a plan toward
    making its cells artificially unlike each other.
    """
    if len(named) < 2:
        return []
    rows = vectors([text for _, text in named])
    if rows is None:
        return None
    rows = _normalised(rows)

    found: list[Pair] = []
    for index, (left, _) in enumerate(named):
        for other in range(index + 1, len(named)):
            right = named[other][0]
            if within is not None and within.get(left) != within.get(right):
                continue
            score = sum(a * b for a, b in zip(rows[index], rows[other]))
            if score >= threshold:
                found.append(Pair(left, right, round(score, 3)))
    return sorted(found, key=lambda one: -one.score)


def spread(named: list[tuple[str, str]]) -> tuple[float, list[tuple[str, float, float]]] | None:
    """How varied a set is, and where each item sits on two axes.

    The number is mean pairwise similarity: lower is more varied. The coordinates are the first
    two principal components, which is what makes a suite plottable rather than merely scored.
    """
    if len(named) < 3:
        return None
    rows = vectors([text for _, text in named])
    if rows is None:
        return None
    rows = _normalised(rows)

    total = 0.0
    pairs = 0
    for index in range(len(rows)):
        for other in range(index + 1, len(rows)):
            total += sum(a * b for a, b in zip(rows[index], rows[other]))
            pairs += 1
    mean = total / pairs if pairs else 0.0

    try:
        import numpy as np

        matrix = np.array(rows)
        centred = matrix - matrix.mean(axis=0)
        # SVD rather than a covariance eigendecomposition: same answer, and it does not need
        # scikit-learn, which is not a dependency here.
        _, _, right = np.linalg.svd(centred, full_matrices=False)
        flat = centred @ right[:2].T
        placed = [
            (named[index][0], float(flat[index][0]), float(flat[index][1]))
            for index in range(len(named))
        ]
    except Exception as why:  # pragma: no cover - numpy is present, but never fail on this
        logger.info("no projection: %s", why)
        placed = []
    return round(mean, 3), placed
