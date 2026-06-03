"""
Score domain-package workflow quality without a model judge.

The report contains workflow packages from support, finance, calendar, and
email domains. The evaluator checks package-level invariants: ticket status,
assignee/SLA capture, balanced ledger entries, calendar conflicts,
chronological thread order, and required participants.

Run:
    cd python
    uv run python -m examples.29_domain_package_quality
"""

from fi.evals.metrics.agents import evaluate_agent_report


report = {
    "results": [
        {
            "messages": [
                {"role": "user", "content": "Close the refund support package."},
                {
                    "role": "assistant",
                    "content": (
                        "Ticket TCK-123 is resolved by Priya. Ledger LED-9 is balanced, "
                        "QA calendar has no conflict, and the email thread includes Avery and Priya."
                    ),
                },
            ],
            "artifacts": [
                {
                    "type": "json",
                    "metadata": {
                        "id": "ticket_123",
                        "kind": "domain_package",
                        "domain": "support",
                        "package_type": "support_ticket",
                    },
                    "data": {
                        "ticket_id": "TCK-123",
                        "status": "resolved",
                        "assignee": {"id": "agent_priya", "name": "Priya"},
                        "sla": {"met": True},
                    },
                },
                {
                    "type": "json",
                    "metadata": {
                        "id": "ledger_9",
                        "kind": "domain_package",
                        "domain": "finance",
                        "package_type": "ledger",
                    },
                    "data": {
                        "ledger_id": "LED-9",
                        "entries": [
                            {"account": "refunds", "debit": 42.0, "credit": 0.0},
                            {"account": "cash", "debit": 0.0, "credit": 42.0},
                        ],
                    },
                },
                {
                    "type": "json",
                    "metadata": {
                        "id": "qa_calendar",
                        "kind": "domain_package",
                        "domain": "calendar",
                        "package_type": "calendar",
                    },
                    "data": {
                        "events": [
                            {
                                "id": "handoff",
                                "start": "10:00",
                                "end": "10:30",
                                "participants": ["agent_priya"],
                            },
                            {
                                "id": "qa",
                                "start": "10:30",
                                "end": "11:00",
                                "participants": ["agent_priya"],
                            },
                        ]
                    },
                },
                {
                    "type": "json",
                    "metadata": {
                        "id": "thread_refund",
                        "kind": "domain_package",
                        "domain": "email",
                        "package_type": "email_thread",
                    },
                    "data": {
                        "messages": [
                            {
                                "sent_at": "2026-06-03T09:00:00",
                                "from": "avery@example.com",
                                "to": ["priya@example.com"],
                            },
                            {
                                "sent_at": "2026-06-03T09:05:00",
                                "from": "priya@example.com",
                                "to": ["avery@example.com"],
                            },
                        ]
                    },
                },
            ],
        }
    ]
}


result = evaluate_agent_report(
    report,
    config={
        "domain_package_checks": [
            {
                "id": "support_ticket_package",
                "package_id": "ticket_123",
                "domain": "support",
                "package_type": "support_ticket",
                "expected_fields": {"ticket_id": "TCK-123", "status": "resolved", "sla.met": True},
                "answer_fields": {"ticket_id": ["TCK-123"], "assignee.name": ["Priya"]},
                "invariants": [
                    {"type": "field_present", "path": "assignee.id"},
                    {"type": "status_in", "path": "status", "allowed": ["resolved", "closed"]},
                    {"type": "field_equals", "path": "sla.met", "value": True},
                ],
            },
            {
                "id": "ledger_package",
                "package_id": "ledger_9",
                "domain": "finance",
                "package_type": "ledger",
                "invariants": [{"type": "ledger_balanced", "entries_path": "entries"}],
            },
            {
                "id": "calendar_package",
                "package_id": "qa_calendar",
                "domain": "calendar",
                "package_type": "calendar",
                "invariants": [{"type": "calendar_no_overlap", "events_path": "events"}],
            },
            {
                "id": "thread_package",
                "package_id": "thread_refund",
                "domain": "email",
                "package_type": "email_thread",
                "invariants": [
                    {"type": "chronological", "items_path": "messages", "time_field": "sent_at"},
                    {
                        "type": "required_participants",
                        "items_path": "messages",
                        "participants": ["avery@example.com", "priya@example.com"],
                        "item_participant_paths": ["from", "to"],
                    },
                ],
            },
        ],
        "metric_weights": {"domain_package_quality": 6.0, "artifact_coverage": 1.0},
    },
    threshold=0.85,
)

metrics = result.summary["metric_averages"]

print("score:", result.score)
print("passed:", result.passed)
print("artifact_coverage:", metrics.get("artifact_coverage"))
print("domain_package_quality:", metrics.get("domain_package_quality"))
