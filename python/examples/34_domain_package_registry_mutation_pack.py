"""
Generate negative domain-package registry mutation packs.

This cookbook starts from a customer registry, creates passing fixtures, mutates
one required field or invariant family at a time, and evaluates each mutant
locally so missing boundary regression rows are obvious before promotion.

Run:
    cd python
    uv run python -m examples.34_domain_package_registry_mutation_pack
"""

import json

from fi.evals.metrics.agents import (
    evaluate_agent_report,
    generate_domain_package_registry_mutation_pack,
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

mutation_pack = generate_domain_package_registry_mutation_pack(
    DOMAIN_PACKAGE_REGISTRY,
    preset_names=["claim_file", "contract_review", "procurement"],
)

failures = []
for mutant in mutation_pack["mutants"]:
    result = evaluate_agent_report(
        mutant["report"],
        config=mutant["config"],
        threshold=1.0,
    )
    metric = next(metric for metric in result.cases[0].metrics if metric.name == "domain_package_quality")
    finding_types = sorted({finding["type"] for finding in metric.details["findings"]})
    failures.append(
        {
            "id": mutant["id"],
            "preset": mutant["preset"],
            "family": mutant["invariant_family"],
            "score": metric.score,
            "findings": finding_types,
        }
    )

print("fixture_count:", mutation_pack["fixture_count"])
print("mutant_count:", mutation_pack["mutant_count"])
print("all_mutants_failed:", all(item["score"] < 1.0 for item in failures))
print("families:", json.dumps(sorted({(item["preset"], item["family"]) for item in failures})))
print("first_failure:", json.dumps(failures[0], sort_keys=True))
