"""
Select a compact domain-package registry replay pack.

This cookbook combines existing replay rows, generated passing fixtures, and
negative mutation packs into the smallest representative pack that covers
required preset families, customer aliases, and one boundary failure per family.

Run:
    cd python
    uv run python -m examples.35_domain_package_registry_replay_pack_selection
"""

import json

from fi.evals.metrics.agents import (
    generate_domain_package_registry_fixtures,
    select_domain_package_registry_replay_pack,
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

claim_fixture = generate_domain_package_registry_fixtures(
    DOMAIN_PACKAGE_REGISTRY,
    preset_names=["claim_file"],
)["fixtures"][0]
claim_package = claim_fixture["package"]
claim_package["metadata"]["package_type"] = "enterprise_claim"
existing_claim_case = {
    "id": "existing_enterprise_claim",
    "input": {
        "observability": {
            "raw": {
                "agent_report": {
                    "results": [
                        {
                            "messages": [{"role": "assistant", "content": "Claim is ready."}],
                            "artifacts": [claim_package],
                        }
                    ]
                },
                "agent_report_config": {
                    "domain_package_checks": [
                        {
                            **claim_fixture["check"],
                            "package_type": "enterprise_claim",
                        }
                    ]
                },
            }
        }
    },
    "expected": {"required_metrics": {"domain_package_quality": 1.0}},
}

selection = select_domain_package_registry_replay_pack(
    DOMAIN_PACKAGE_REGISTRY,
    [existing_claim_case],
    preset_names=["claim_file", "contract_review", "procurement"],
)

negative_families = sorted(
    {
        (item["preset"], item["invariant_family"])
        for item in selection["selected"]
        if item["kind"] == "negative_mutation"
    }
)

print("selection_complete:", selection["selection_complete"])
print("selected_case_count:", selection["selected_case_count"])
print("selected_positive_count:", selection["selected_positive_count"])
print("selected_negative_count:", selection["selected_negative_count"])
print("generated_mutant_count:", selection["generated_mutant_count"])
print("coverage_score:", selection["selected_coverage"]["coverage_score"])
print("alias_covered_presets:", selection["alias_covered_presets"])
print("negative_families:", json.dumps(negative_families))
