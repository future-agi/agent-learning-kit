"""Before/after retrieval demo for torchembed metadata enrichment.

Run (CPU only):  python examples/feedback_torchembed_retrieval_demo.py
Requires: pip install "ai-evaluation[feedback-torch]"

Scenario: several feedback entries have near-identical text but differ in the
developer-corrected score and in when they were created. The query asks for
examples whose corrected score is HIGH (0.95) and created on a Tuesday ~14:00.

  BEFORE  enrichment off -> ranking depends on text only.
  AFTER   enrichment on  -> same text embeddings blended with the torchembed
                            score/time vector; the query carries the targets.

NOTE (honest scope): the text embedding here is a deterministic hashed
bag-of-words STUB, not a real model, so the demo is offline, seeded and
reproducible. It proves the *mechanism* (metadata in the vector changes the
ranking, deterministically). It does NOT show how much a real
sentence-transformers embedding benefits; that is unverified.
"""
from __future__ import annotations

import hashlib
import math
import sys
import tempfile
from datetime import datetime, timezone

from fi.evals.feedback import FeedbackEntry
from fi.evals.feedback.store import ChromaFeedbackStore
from fi.evals.feedback.torchembed_encoder import is_available

DIM = 256
WEIGHT = 0.5
TARGET_SCORE = 0.95
TARGET_TIME = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)  # Tuesday 14:00
QUERY = "response: The capital of France is Paris. context: France capital city"


def stub_embed(texts):
    """Deterministic hashed bag-of-words embedding (unit norm). NOT a real model."""
    out = []
    for t in texts:
        v = [0.0] * DIM
        for tok in t.lower().replace(".", " ").replace(":", " ").split():
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            v[h % DIM] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


def _entry(eid, response, corr, when):
    return FeedbackEntry(
        id=eid, eval_name="faithfulness",
        inputs={"response": response, "context": "France capital city"},
        original_score=0.5, correct_score=corr, created_at=when,
    )


def dataset():
    sun3 = datetime(2026, 7, 5, 3, 0, tzinfo=timezone.utc)
    mon22 = datetime(2026, 7, 6, 22, 0, tzinfo=timezone.utc)
    tue14 = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)
    tue13 = datetime(2026, 7, 7, 13, 0, tzinfo=timezone.utc)
    return [
        # decoys: text closest to the query, low corrected score / other time
        _entry("decoy-A", "The capital of France is Paris.", 0.10, sun3),
        _entry("decoy-B", "The capital of France is Paris indeed", 0.20, mon22),
        _entry("decoy-C", "The capital of France is Paris today now", 0.30, sun3),
        # high corrected score, recent Tuesday afternoon, text slightly farther
        _entry("TARGET", "The capital of France is Paris, a large city.", 0.95, tue14),
        # same text as the target but wrong score/time -> metadata must discriminate
        _entry("near-miss", "The capital of France is Paris, a large town.", 0.15, tue13),
    ]


def run(enrich: bool):
    with tempfile.TemporaryDirectory() as d:
        store = ChromaFeedbackStore(
            persist_directory=d, collection_prefix=f"demo_{int(enrich)}",
            enrich_with_metadata=enrich, metadata_weight=WEIGHT,
            text_embedding_fn=stub_embed,
        )
        for e in dataset():
            store.add(e)
        kw = dict(correct_score=TARGET_SCORE, created_at=TARGET_TIME) if enrich else {}
        return store.query_similar("faithfulness", QUERY, n_results=5, **kw)


def table(title, ranked):
    print(f"\n{title}")
    print(f"  {'rank':<5}{'id':<11}{'correct_score':<15}")
    for i, e in enumerate(ranked, 1):
        print(f"  {i:<5}{e.id:<11}{e.correct_score!s:<15}")


def main() -> int:
    if not is_available():
        print("torch/torchembed not installed: pip install 'ai-evaluation[feedback-torch]'")
        return 2
    print("Embedding: deterministic hashed bag-of-words STUB (not a real model); "
          f"metadata weight={WEIGHT}")
    print(f"Query text : {QUERY!r}")
    print(f"Query targets (AFTER only): correct_score={TARGET_SCORE}, created_at={TARGET_TIME:%a %H:%M}")
    before, after = run(False), run(True)
    table("BEFORE (enrichment off, text-only):", before)
    table("AFTER  (enrichment on, score/time targeted):", after)
    ok = before[0].id != "TARGET" and after[0].id == "TARGET"
    print(f"\nTARGET rank before={[e.id for e in before].index('TARGET') + 1}, "
          f"after={[e.id for e in after].index('TARGET') + 1}")
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
