"""
Lint, diff, and replay-gate domain-package registries.

This cookbook treats a registry update like a migration: validate the new
schema, diff it against the prior registry, and replay a preserved regression
case to prove historical rows still pass before deployment.

Run:
    cd python
    uv run python -m examples.32_domain_package_registry_gate
"""

from fi.evals.metrics.agents import (
    diff_domain_package_registries,
    replay_domain_package_registry,
    validate_domain_package_registry,
)


BASE_REGISTRY = {
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

MIGRATED_REGISTRY = {
    "version": "futureagi.domain-packages.acme.v2",
    "presets": {
        "claim_file": {
            "version": "acme-claims-2026-07",
            "aliases": ["enterprise_claim"],
            "required_fields": ["adjuster.id", "supervisor.id"],
        }
    },
}

REGRESSION_CASE = {
    "id": "enterprise_claim_9",
    "input": {
        "observability": {
            "raw": {
                "agent_report": {
                    "results": [
                        {
                            "messages": [
                                {"role": "user", "content": "Review the enterprise claim packet."},
                                {
                                    "role": "assistant",
                                    "content": "Enterprise claim ECLM-9 is review complete.",
                                },
                            ],
                            "artifacts": [
                                {
                                    "type": "json",
                                    "metadata": {
                                        "id": "enterprise_claim_9",
                                        "kind": "domain_package",
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
                },
                "agent_report_config": {
                    "domain_package_checks": [
                        {
                            "id": "enterprise_claim_preset",
                            "package_id": "enterprise_claim_9",
                            "package_type": "enterprise_claim",
                            "allowed_statuses": ["review_complete"],
                            "amount_tolerance": 25.0,
                            "claim_audit_documents": ["audit_trail"],
                        }
                    ]
                },
            }
        }
    },
    "expected": {"required_metrics": {"domain_package_quality": 1.0}},
}

validation = validate_domain_package_registry(MIGRATED_REGISTRY)
diff = diff_domain_package_registries(BASE_REGISTRY, MIGRATED_REGISTRY)
base_replay = replay_domain_package_registry(BASE_REGISTRY, [REGRESSION_CASE], threshold=1.0)
migrated_replay = replay_domain_package_registry(MIGRATED_REGISTRY, [REGRESSION_CASE], threshold=1.0)

print("Validation valid:", validation["valid"])
print("Breaking changes:", diff["breaking_changes"])
print("Base replay passed:", base_replay["passed"])
print("Migrated replay passed:", migrated_replay["passed"])
print("Migrated replay score:", migrated_replay["cases"][0]["score"])
