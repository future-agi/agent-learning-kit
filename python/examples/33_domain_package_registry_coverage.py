"""
Measure domain-package registry replay coverage before using a judge.

This cookbook generates deterministic fixtures from a customer registry, scores
those fixtures locally, and then diagnoses which preset invariant families are
missing from a partial replay set.

Run:
    cd python
    uv run python -m examples.33_domain_package_registry_coverage
"""

import json

from fi.evals.metrics.agents import (
    analyze_domain_package_registry_coverage,
    evaluate_agent_report,
    generate_domain_package_registry_fixtures,
)


DOMAIN_PACKAGE_REGISTRY = {
    "version": "futureagi.domain-packages.acme.v1",
    "presets": {
        "claim_file": {
            "version": "acme-claims-2026-06",
            "aliases": ["enterprise_claim"],
            "required_fields": ["adjuster.id"],
        },
        "contract_review": {
            "version": "acme-contracts-2026-06",
            "aliases": ["enterprise_contract"],
            "required_fields": ["counterparty.id"],
        },
    },
}

fixture_pack = generate_domain_package_registry_fixtures(
    DOMAIN_PACKAGE_REGISTRY,
    preset_names=["claim_file", "contract_review"],
)

fixture_result = evaluate_agent_report(
    fixture_pack["report"],
    config=fixture_pack["config"],
    threshold=1.0,
)

claim_fixture = next(item for item in fixture_pack["fixtures"] if item["preset"] == "claim_file")
claim_replay_case = {
    "id": "claim_replay_only",
    "input": {
        "observability": {
            "raw": {
                "agent_report": {
                    "results": [
                        {
                            "messages": [
                                {"role": "user", "content": "Review the claim packet."},
                                {"role": "assistant", "content": "The claim packet is complete."},
                            ],
                            "artifacts": [claim_fixture["package"]],
                        }
                    ]
                },
                "agent_report_config": {
                    "domain_package_checks": [claim_fixture["check"]],
                },
            }
        }
    },
    "expected": {"required_metrics": {"domain_package_quality": 1.0}},
}

coverage = analyze_domain_package_registry_coverage(
    DOMAIN_PACKAGE_REGISTRY,
    [claim_replay_case],
    preset_names=["claim_file", "contract_review"],
)
first_recommendation = coverage["recommendations"][0]

print("generated_fixture_count:", fixture_pack["preset_count"])
print("generated_fixture_score:", fixture_result.summary["metric_averages"]["domain_package_quality"])
print("coverage_passed:", coverage["passed"])
print("coverage_score:", coverage["coverage_score"])
print("missing:", json.dumps(coverage["missing"], sort_keys=True))
print("first_recommendation_preset:", first_recommendation["preset"])
print("first_recommendation_family:", first_recommendation["invariant_family"])
print(
    "first_recommendation_fixture_families:",
    first_recommendation["suggested_fixture"]["invariant_families"],
)
