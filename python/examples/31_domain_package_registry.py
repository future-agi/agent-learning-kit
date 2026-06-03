"""
Score a versioned, customer-configurable domain-package registry.

The registry extends the built-in claim preset with an enterprise alias,
required adjuster evidence, a custom claim status, a tolerance override, and an
extra audit document requirement.

Run:
    cd python
    uv run python -m examples.31_domain_package_registry
"""

from fi.evals.metrics.agents import evaluate_agent_report


DOMAIN_PACKAGE_REGISTRY = {
    "version": "futureagi.domain-packages.acme.v1",
    "presets": {
        "claim_file": {
            "version": "acme-claims-2026-06",
            "aliases": ["enterprise_claim"],
            "required_fields": ["adjuster.id"],
            "invariants": [
                {
                    "type": "collection_contains",
                    "items_path": "documents",
                    "field": "type",
                    "values_key": "claim_audit_documents",
                    "default_values": ["audit_trail"],
                }
            ],
        }
    },
}

report = {
    "results": [
        {
            "messages": [
                {"role": "user", "content": "Review the enterprise claim packet."},
                {"role": "assistant", "content": "Enterprise claim ECLM-9 is review complete."},
            ],
            "artifacts": [
                {
                    "type": "json",
                    "metadata": {
                        "id": "enterprise_claim_9",
                        "kind": "domain_package",
                        "domain": "insurance",
                        "package_type": "enterprise_claim",
                    },
                    "data": {
                        "claim_id": "ECLM-9",
                        "status": "review_complete",
                        "claimant": {"id": "cust_9"},
                        "adjuster": {"id": "adj_1"},
                        "loss": {"date": "2026-06-01"},
                        "coverage": {"limit": 1000.0},
                        "amount": 1020.0,
                        "documents": [
                            {"type": "loss_notice"},
                            {"type": "policy"},
                            {"type": "audit_trail"},
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
        "domain_package_registry": DOMAIN_PACKAGE_REGISTRY,
        "domain_package_checks": [
            {
                "id": "enterprise_claim_preset",
                "package_id": "enterprise_claim_9",
                "package_type": "enterprise_claim",
                "allowed_statuses": ["review_complete"],
                "amount_tolerance": 25.0,
                "claim_audit_documents": ["audit_trail"],
            }
        ],
        "metric_weights": {"domain_package_quality": 6.0, "artifact_coverage": 1.0},
    },
    threshold=0.85,
)

metrics = result.summary["metric_averages"]
domain_metric = next(metric for metric in result.cases[0].metrics if metric.name == "domain_package_quality")
registry = domain_metric.details["checks"][0]["registry"]

print("score:", result.score)
print("passed:", result.passed)
print("artifact_coverage:", metrics.get("artifact_coverage"))
print("domain_package_quality:", metrics.get("domain_package_quality"))
print("registry_version:", registry.get("version"))
print("preset_versions:", registry.get("preset_versions"))
