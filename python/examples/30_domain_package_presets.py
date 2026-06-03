"""
Score domain-package preset families without a model judge.

The checks only name a package id and package type. The evaluator expands those
types into deterministic invariants for claims, contracts, CRM account plans,
procurement, clinical intake, and incident response packages.

Run:
    cd python
    uv run python -m examples.30_domain_package_presets
"""

from fi.evals.metrics.agents import evaluate_agent_report


report = {
    "results": [
        {
            "messages": [
                {"role": "user", "content": "Review the pending workflow packages."},
                {
                    "role": "assistant",
                    "content": (
                        "Claim CLM-9, contract CTR-7, account ACME, purchase order PO-8, "
                        "clinical intake INT-4, and incident INC-5 all satisfy their package rules."
                    ),
                },
            ],
            "artifacts": [
                {
                    "type": "json",
                    "metadata": {
                        "id": "claim_9",
                        "kind": "domain_package",
                        "domain": "insurance",
                        "package_type": "insurance_claim",
                    },
                    "data": {
                        "claim_id": "CLM-9",
                        "status": "approved",
                        "claimant": {"id": "cust_9"},
                        "loss": {"date": "2026-06-01"},
                        "coverage": {"limit": 1000.0},
                        "amount": 875.0,
                        "documents": [{"type": "loss_notice"}, {"type": "policy"}],
                    },
                },
                {
                    "type": "json",
                    "metadata": {
                        "id": "contract_7",
                        "kind": "domain_package",
                        "domain": "legal",
                        "package_type": "contract_review",
                    },
                    "data": {
                        "contract_id": "CTR-7",
                        "effective_date": "2026-06-01",
                        "expiration_date": "2027-06-01",
                        "parties": [{"id": "acme"}, {"id": "futureagi"}],
                        "signatures": [
                            {"party_id": "acme", "status": "signed"},
                            {"party_id": "futureagi", "status": "executed"},
                        ],
                    },
                },
                {
                    "type": "json",
                    "metadata": {
                        "id": "account_acme",
                        "kind": "domain_package",
                        "domain": "crm",
                        "package_type": "crm_account_plan",
                    },
                    "data": {
                        "account_id": "ACME",
                        "owner": {"id": "owner_1"},
                        "last_touch_at": "2026-06-01T09:00:00",
                        "next_step": {
                            "action": "security review",
                            "due_at": "2026-06-05T09:00:00",
                        },
                        "contacts": [{"id": "c1", "role": "economic_buyer"}],
                    },
                },
                {
                    "type": "json",
                    "metadata": {
                        "id": "po_8",
                        "kind": "domain_package",
                        "domain": "procurement",
                        "package_type": "purchase_order",
                    },
                    "data": {
                        "po_id": "PO-8",
                        "status": "approved",
                        "vendor": {"id": "vendor_1"},
                        "line_items": [
                            {"sku": "A", "quantity": 2, "unit_price": 50.0},
                            {"sku": "B", "quantity": 1, "unit_price": 140.0},
                        ],
                        "total": 240.0,
                        "approvals": [
                            {"role": "requester", "status": "approved"},
                            {"role": "finance", "status": "approved"},
                        ],
                    },
                },
                {
                    "type": "json",
                    "metadata": {
                        "id": "clinical_4",
                        "kind": "domain_package",
                        "domain": "clinical",
                        "package_type": "clinical_intake",
                    },
                    "data": {
                        "patient": {"id": "pat_4"},
                        "encounter": {"reason": "knee pain"},
                        "consent": {"signed_at": "2026-06-03T08:00:00"},
                        "triage": {"level": "urgent"},
                        "sections": [
                            {"name": "allergies"},
                            {"name": "medications"},
                            {"name": "consent"},
                        ],
                    },
                },
                {
                    "type": "json",
                    "metadata": {
                        "id": "incident_5",
                        "kind": "domain_package",
                        "domain": "security",
                        "package_type": "incident_response",
                    },
                    "data": {
                        "incident_id": "INC-5",
                        "severity": "high",
                        "status": "contained",
                        "detected_at": "2026-06-03T10:00:00",
                        "contained_at": "2026-06-03T10:45:00",
                        "owner": {"id": "sec_1"},
                        "actions": [{"type": "containment"}, {"type": "customer_update"}],
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
            {"id": "claim_preset", "package_id": "claim_9", "package_type": "insurance_claim"},
            {"id": "contract_preset", "package_id": "contract_7", "package_type": "contract_review"},
            {"id": "crm_preset", "package_id": "account_acme", "package_type": "crm_account_plan"},
            {"id": "po_preset", "package_id": "po_8", "package_type": "purchase_order"},
            {"id": "clinical_preset", "package_id": "clinical_4", "package_type": "clinical_intake"},
            {"id": "incident_preset", "package_id": "incident_5", "package_type": "incident_response"},
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
