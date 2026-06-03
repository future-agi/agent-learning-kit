"""
Score source contradictions and artifact grounding locally.

This catches two common RAG/multimodal failures:
- the answer says something contradicted by retrieved or cited source text
- the answer makes a claim that should be grounded in an artifact, such as OCR
  text, transcript metadata, screenshot text, or file metadata

No model, framework, or API key is required.
"""

from fi.evals.metrics.agents import evaluate_agent_report


report = {
    "results": [
        {
            "messages": [
                {"role": "user", "content": "Check refund policy and receipt total for order 123."},
                {
                    "role": "assistant",
                    "content": "Order 123 has a 30 day refund window. The receipt total is $42.00.",
                },
            ],
            "artifacts": [
                {
                    "type": "trace",
                    "metadata": {"kind": "retrieval_memory_trace"},
                    "data": {
                        "kind": "retrieval_memory_trace",
                        "documents": [
                            {
                                "id": "refund_policy_current",
                                "content": "Order 123 has a 30 day refund window and no restocking fee.",
                                "current": True,
                            }
                        ],
                        "document_reads": [{"id": "refund_policy_current"}],
                        "citations": [
                            {
                                "doc_ids": ["refund_policy_current"],
                                "claim": "Order 123 has a 30 day refund window.",
                            }
                        ],
                    },
                },
                {
                    "id": "receipt_123",
                    "type": "image",
                    "metadata": {"ocr_text": "Receipt order 123 total $42.00 paid by card."},
                    "data": {"description": "Receipt for order 123."},
                },
            ],
        }
    ]
}

result = evaluate_agent_report(
    report,
    config={
        "source_contradiction_checks": [
            {
                "id": "refund_window",
                "source_terms": ["30 day refund window"],
                "answer_terms": ["refund window"],
                "contradict_terms": ["90 day refund window", "non refundable"],
            }
        ],
        "artifact_grounding_checks": [
            {
                "id": "receipt_total",
                "artifact": {"type": "image", "id": "receipt_123"},
                "answer_terms": ["receipt total", "$42.00"],
                "support_terms": ["total $42.00"],
                "forbidden_answer_terms": ["$24.00"],
            }
        ],
        "metric_weights": {
            "source_contradiction": 4.0,
            "artifact_grounding_quality": 4.0,
        },
    },
    threshold=0.85,
)

metrics = result.summary["metric_averages"]

print("score:", result.score)
print("passed:", result.passed)
print("source_contradiction:", metrics.get("source_contradiction"))
print("artifact_grounding_quality:", metrics.get("artifact_grounding_quality"))
