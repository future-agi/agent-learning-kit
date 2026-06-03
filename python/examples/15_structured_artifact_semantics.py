"""
Score structured artifact semantics locally.

This catches domain-specific failures in receipts, forms, tables, logs, or other
JSON-like artifacts: exact fields, table rows, event sequence, answer claims,
and forbidden answer terms. No model, framework, or API key is required.
"""

from fi.evals.metrics.agents import evaluate_agent_report


report = {
    "results": [
        {
            "messages": [
                {"role": "user", "content": "Check receipt semantics for order 123."},
                {
                    "role": "assistant",
                    "content": "Receipt rcpt_123 from Northwind has total $42.00 and SKU-1 quantity 2.",
                },
            ],
            "artifacts": [
                {
                    "type": "json",
                    "metadata": {
                        "id": "receipt_123",
                        "kind": "structured_artifact",
                        "domain": "receipt",
                        "schema": "receipt_v1",
                    },
                    "data": {
                        "receipt_id": "rcpt_123",
                        "merchant": "Northwind",
                        "order": {"id": "123"},
                        "total": {"amount": 42.0, "currency": "USD"},
                        "line_items": [
                            {"sku": "SKU-1", "description": "Widget", "quantity": 2, "amount": 20.0},
                            {"sku": "TAX", "description": "Tax", "quantity": 1, "amount": 2.0},
                        ],
                        "events": [
                            {"event": "created"},
                            {"event": "paid"},
                            {"event": "captured"},
                        ],
                    },
                }
            ],
        }
    ]
}

result = evaluate_agent_report(
    report,
    config={
        "artifact_semantic_checks": [
            {
                "id": "receipt_semantics",
                "artifact": {
                    "type": "json",
                    "id": "receipt_123",
                    "metadata": {"domain": "receipt", "schema": "receipt_v1"},
                },
                "expected_fields": {
                    "receipt_id": "rcpt_123",
                    "merchant": "Northwind",
                    "order.id": "123",
                    "total.amount": 42.0,
                    "total.currency": "USD",
                },
                "answer_fields": {
                    "receipt_id": ["rcpt_123"],
                    "merchant": ["Northwind"],
                    "total.amount": ["$42.00"],
                },
                "required_rows": [
                    {
                        "path": "line_items",
                        "where": {"sku": "SKU-1"},
                        "fields": {"quantity": 2, "amount": 20.0},
                    }
                ],
                "event_sequence": {
                    "path": "events",
                    "field": "event",
                    "expected": ["created", "paid", "captured"],
                },
                "forbidden_answer_terms": ["$24.00", "SKU-9"],
            }
        ],
        "metric_weights": {"artifact_semantics_quality": 5.0},
    },
    threshold=0.85,
)

metrics = result.summary["metric_averages"]

print("score:", result.score)
print("passed:", result.passed)
print("artifact_semantics_quality:", metrics.get("artifact_semantics_quality"))
