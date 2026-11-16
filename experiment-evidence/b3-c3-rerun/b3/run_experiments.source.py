"""Test-only B3/C3 rerun. Imports only public ALK studio APIs, plus PDF fixture tools."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from fi.alk.studio import (
    AgentEnvironmentSpec, DestinationField, DestinationSchema,
    EnvironmentInputError, EnvironmentInputReview, EnvironmentRequirements,
    EnvironmentSource, FieldMapping, PolicyReview, PolicyRuleProposal,
    PolicyRuleReview, RecordRelationship, RecordReview, ScenarioDiagnostic,
    SourceRecordReference, compile_environment_inputs, coverage_report,
    deserialize_environment_snapshot, generate_source_scenarios,
    prepare_environment_inputs, review_environment_inputs,
    serialize_environment_snapshot,
)

PRODUCT_SHA = "023abdf3655435a4b63177b3e24ee4c14df874ca"
LABEL = "ALK sandbox experiment using public APIs — not the production Future AGI platform."
POLICY = 'Refund responses must contain "approved".\n\nThe response must not contain "forbidden".'


def asdict(value):
    """Evidence serialization without deepcopy of immutable mappingproxy values."""
    if is_dataclass(value):
        return {field.name: asdict(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: asdict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [asdict(item) for item in value]
    return value


def pdf(text):
    out = io.BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(out)
    return out.getvalue()


def fixture(*, hidden=False, unsupported=False):
    text = "Explain the policy courteously." if unsupported else POLICY
    sources = [EnvironmentSource("public-policy", "refund-policy.pdf", "application/pdf", pdf(text), "policy")]
    schema = DestinationSchema(tuple(DestinationField(x, "string") for x in ("customer_id", "name", "status")))
    if not hidden and not unsupported:
        sources += [EnvironmentSource("customers", "customers.csv", "text/csv", b"customer_id,name\nC17,Alice\n", "seed_records"), EnvironmentSource("orders", "orders.json", "application/json", b'[{"buyer_id":"C17","status":"eligible"}]', "seed_records")]
    draft = prepare_environment_inputs(tuple(sources), destination_schema=schema)
    rules = tuple(PolicyRuleReview(p, "accepted" if p.status == "supported" else "reference_only") for p in draft.proposals.policy_rules)
    records = () if hidden or unsupported else (
        RecordReview("customers", "agent_visible", "synthetic_replace", (FieldMapping("customer_id", "customer_id", "string", True, True), FieldMapping("name", "name", "string", personal_data=True))),
        RecordReview("orders", "agent_visible", "synthetic_replace", (FieldMapping("buyer_id", "customer_id", "string", True, True), FieldMapping("status", "status", "string"))),
    )
    relationships = () if hidden or unsupported else (RecordRelationship("orders", "customer_id", "customers", "customer_id"),)
    review = EnvironmentInputReview(draft.draft_digest, records, (PolicyReview("public-policy", "evaluator_only" if hidden else "agent_visible", rules),), relationships=relationships)
    reviewed = review_environment_inputs(draft, review=review, reviewer_id="rerun-reviewer")
    inputs = {"policy_text": text, "policy_visibility": "evaluator_only" if hidden else "agent_visible", "customers.csv": None if not records else "customer_id,name\nC17,Alice\n", "orders.json": None if not records else [{"buyer_id": "C17", "status": "eligible"}], "review": asdict(review), "snapshot_id": reviewed.snapshot_id}
    return reviewed, inputs


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


class Experiment:
    def __init__(self, identifier):
        self.result = {"id": identifier, "label": LABEL, "commit_sha": PRODUCT_SHA, "status": "inconclusive", "assertions": [], "fixtures": {}, "api_calls": [], "observations": {}, "limitations": []}

    def check(self, identifier, expression, condition, actual):
        row = {"id": identifier, "expression": expression, "status": "pass" if condition else "fail", "actual": actual}
        self.result["assertions"].append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    def typed_error(self, identifier, call, expected_code):
        try:
            value = call()
        except Exception as exc:
            self.check(identifier, f"isinstance(exc, EnvironmentInputError) and exc.code == {expected_code!r}; construction and compile both inside try", isinstance(exc, EnvironmentInputError) and exc.code == expected_code, {"exception": type(exc).__name__, "code": getattr(exc, "code", None), "message": str(exc)})
        else:
            self.check(identifier, f"raises EnvironmentInputError({expected_code})", False, {"unexpected_return": type(value).__name__})


def b3(e):
    reviewed, inputs = fixture(hidden=True)
    e.result["fixtures"]["reviewed"] = inputs
    malformed = [({}, "missing-id"), ({"id": None}, "null-id"), ({"id": ""}, "blank-id"), ({"id": "  "}, "whitespace-id"), ({"id": 1}, "integer-id"), ({"id": []}, "list-id")]
    cases = [(name, [entry]) for entry, name in malformed] + [("null-member", [None]), ("scalar-string", "document-id"), ("scalar-null", None), ("scalar-integer", 1), ("blank-map-key", {"": {}}), ("whitespace-map-key", {" ": {}}), ("integer-map-key", {1: {}})]
    e.result["fixtures"]["malformed_entries"] = [{"name": name, "entries": entries} for name, entries in cases]
    e.result["api_calls"] += ["try:\n    existing = AgentEnvironmentSpec(adapter, {field: entries})\n    compile_environment_inputs(reviewed, existing_environments=(existing,))\nexcept Exception as exc:\n    assert isinstance(exc, EnvironmentInputError)\n    assert exc.code == 'source_mapping_invalid'", "serialize_environment_snapshot(reviewed)", "deserialize_environment_snapshot(wire)"]
    for field, adapter in (("documents", "retrieval_memory"), ("artifacts", "structured_artifacts")):
        for name, entries in cases:
            def invoke(entries=entries, field=field, adapter=adapter):
                existing = AgentEnvironmentSpec(adapter, {field: entries})
                return compile_environment_inputs(reviewed, existing_environments=(existing,))
            e.typed_error(f"{field}/{name}", invoke, "source_mapping_invalid")
        for name, entries in (("mapping", {"valid-id": {}}), ("list", [{"id": "valid-id"}]), ("empty-list", []), ("empty-map", {})):
            try:
                existing = AgentEnvironmentSpec(adapter, {field: entries})
                compiled = compile_environment_inputs(reviewed, existing_environments=(existing,))
                e.check(f"{field}/valid-{name}", "valid mapping/list compiles and preserves existing environment", compiled.agent_environments[0] == existing, existing.as_dict())
            except Exception as exc:
                e.check(f"{field}/valid-{name}", "valid mapping/list compiles", False, repr(exc))
    def duplicate_list():
        existing = AgentEnvironmentSpec("retrieval_memory", {"documents": [{"id": "same"}, {"id": "same"}]})
        return compile_environment_inputs(reviewed, existing_environments=(existing,))
    e.typed_error("collision/duplicate-list", duplicate_list, "source_mapping_invalid")
    def cross_adapter():
        first = AgentEnvironmentSpec("retrieval_memory", {"documents": [{"id": "same"}]})
        second = AgentEnvironmentSpec("structured_artifacts", {"artifacts": {"same": {}}})
        return compile_environment_inputs(reviewed, existing_environments=(first, second))
    e.typed_error("collision/cross-adapter", cross_adapter, "source_mapping_invalid")
    wire = serialize_environment_snapshot(reviewed)
    e.check("snapshot/valid-roundtrip", "deserialize_environment_snapshot(serialize_environment_snapshot(reviewed)) == reviewed", deserialize_environment_snapshot(wire) == reviewed, {"wire_sha256": hashlib.sha256(wire).hexdigest()})
    envelope = json.loads(wire)
    envelope["data"]["reviewer_id"] = "attacker-rewritten-reviewer"
    e.typed_error("snapshot/ordinary-envelope-tamper", lambda: deserialize_environment_snapshot(canonical(envelope)), "source_snapshot_invalid")
    envelope["digest"] = digest(envelope["data"])
    restored = deserialize_environment_snapshot(canonical(envelope))
    e.check("snapshot/recomputed-unkeyed-digest-classification", "rewritten reviewer accepted after outer checksum recomputation; classify as authenticity limitation", restored.reviewer_id == "attacker-rewritten-reviewer", {"accepted_reviewer_id": restored.reviewer_id, "classification": "authenticity limitation of caller-owned trusted storage and unkeyed envelope checksum", "source_content_modified": False})
    content_envelope = json.loads(wire)
    content_envelope["data"]["sources"][0]["pages"][0]["text"] = "altered source text"
    e.typed_error("snapshot/source-content-tamper-without-rehash", lambda: deserialize_environment_snapshot(canonical(content_envelope)), "source_snapshot_invalid")
    content_envelope["digest"] = digest(content_envelope["data"])
    e.typed_error("snapshot/source-content-outer-rehash-only", lambda: deserialize_environment_snapshot(canonical(content_envelope)), "source_snapshot_invalid")
    e.result["limitations"] = ["Unkeyed checksums detect modification relative to trusted digests; they do not authenticate an attacker-writable envelope. Recomputed reviewer metadata checksum acceptance is an authenticity limitation, not a failed source-content integrity assertion.", "Source text tampering remains rejected if only the outer digest is recomputed; no keyed signature or trusted external digest is supplied.", "Controlled local public SDK APIs only; no production platform E2E claim."]


def c3(e):
    reviewed, inputs = fixture()
    missing = SourceRecordReference("customers", "not-approved")
    requirements = EnvironmentRequirements(task="Handle a refund", required_records=(missing, SourceRecordReference("customers", "synthetic_0001")))
    generated = generate_source_scenarios(reviewed, environment_requirements=requirements, requested_count=3)
    e.result["fixtures"] = {"reviewed": inputs, "missing_relationship_target_requirements": asdict(requirements), "requested_count": 3}
    e.result["api_calls"] = ["generate_source_scenarios(reviewed, environment_requirements=requirements, requested_count=3)", "coverage_report(generated)  # complete GeneratedScenarioDraftSet: completion gate", "coverage_report(generated.public_scenarios)  # structural only, not source readiness", "coverage_report(all_blocked)  # default axes; no declared tools", "replace(draft, evaluator=replace(draft.evaluator, diagnostics=(ScenarioDiagnostic('private_evidence_missing', 'blocking', 'Missing evidence'),)))", "coverage_report(evaluator_blocked)"]
    states = [d.status for d in generated.drafts]
    e.check("mixed/statuses", "statuses == ['blocked', 'reviewable', 'reviewable']", states == ["blocked", "reviewable", "reviewable"], states)
    e.check("mixed/missing-target", "first referenced_records == (missing,) and diagnostic.code == 'source_relationship_missing'", generated.drafts[0].referenced_records == (missing,) and generated.drafts[0].diagnostics[0].code == "source_relationship_missing", {"reference": asdict(generated.drafts[0].referenced_records[0]), "diagnostics": [asdict(d) for d in generated.drafts[0].diagnostics]})
    e.check("mixed/preservation", "len(drafts) == len(public_scenarios) == len(scenario_payloads) == len(evaluator_specs) == 3", len(generated.drafts) == len(generated.public_scenarios) == len(generated.scenario_payloads) == len(generated.evaluator_specs) == 3, {"drafts": len(generated.drafts), "public_scenarios": len(generated.public_scenarios), "payloads": len(generated.scenario_payloads), "evaluators": len(generated.evaluator_specs)})
    for i, d in enumerate(generated.drafts):
        e.check(f"mixed/alignment-{i}", "public_scenarios[i] == drafts[i].scenario and payloads[i] == drafts[i].scenario_payload and evaluator_specs[i].scenario_id == drafts[i].scenario_id", generated.public_scenarios[i] == d.scenario and generated.scenario_payloads[i] == d.scenario_payload and generated.evaluator_specs[i].scenario_id == d.scenario_id, {"index": i, "scenario_id": d.scenario_id, "status": d.status, "public_name": generated.public_scenarios[i].name})
    trusted = coverage_report(generated)
    structural = coverage_report(generated.public_scenarios)
    source_cases = trusted["obligation_coverage"]["per_axis"]["source_cases"]
    e.check("mixed/source-cases", "source_cases == {'declared': 3, 'covered': 2, 'uncovered': [blocked_id]}", source_cases == {"declared": 3, "covered": 2, "uncovered": [generated.drafts[0].scenario_id]}, source_cases)
    e.check("mixed/completion-gate", "coverage_report(generated).obligation_coverage.rate < 1", trusted["obligation_coverage"]["rate"] < 1, trusted)
    e.check("structural/not-source-readiness", "public-only rate == 1 and no source_cases axis; full draft-set rate < 1", structural["obligation_coverage"]["rate"] == 1 and "source_cases" not in structural["obligation_coverage"]["per_axis"] and trusted["obligation_coverage"]["rate"] < 1, structural)
    # Keep reference-only prose evaluator-only so generation adds no public
    # retrieval tools. Public policy prose would implicitly declare read tools.
    unsupported, unsupported_inputs = fixture(hidden=True, unsupported=True)
    all_blocked = generate_source_scenarios(unsupported, environment_requirements=EnvironmentRequirements(task="Handle a request"), requested_count=3)
    all_report = coverage_report(all_blocked)
    e.result["fixtures"]["all_blocked_no_tool"] = unsupported_inputs
    e.check("all-blocked/no-tool", "all statuses blocked and no declared tool obligations", all(d.status == "blocked" for d in all_blocked.drafts) and all_report["obligation_coverage"]["per_axis"]["tool_obligations"]["declared"] == 0, {"statuses": [d.status for d in all_blocked.drafts], "coverage": all_report})
    cov = all_report["obligation_coverage"]
    e.check("all-blocked/zero-readiness", "declared > 0 and covered == 0 and rate == 0", cov["declared"] > 0 and cov["covered"] == 0 and cov["rate"] == 0, cov)
    ready = generate_source_scenarios(reviewed, environment_requirements=EnvironmentRequirements(task="Handle a refund", declared_tools=("issue_refund",)), requested_count=3)
    evaluator_blocked = replace(ready, drafts=tuple(replace(d, evaluator=replace(d.evaluator, diagnostics=(ScenarioDiagnostic("private_evidence_missing", "blocking", "Missing evidence"),))) for d in ready.drafts))
    before, after = coverage_report(ready), coverage_report(evaluator_blocked)
    e.check("private-blocking/starting-state", "original statuses all reviewable and original coverage rate == 1", all(d.status == "reviewable" for d in ready.drafts) and before["obligation_coverage"]["rate"] == 1, before)
    e.check("private-blocking/public-payloads-identical", "ready.scenario_payloads == evaluator_blocked.scenario_payloads and ready.public_scenarios == evaluator_blocked.public_scenarios", ready.scenario_payloads == evaluator_blocked.scenario_payloads and ready.public_scenarios == evaluator_blocked.public_scenarios, {"before_sha256": [hashlib.sha256(p.encode()).hexdigest() for p in ready.scenario_payloads], "after_sha256": [hashlib.sha256(p.encode()).hexdigest() for p in evaluator_blocked.scenario_payloads]})
    e.check("private-blocking/trusted-readiness", "evaluator-only blocking changes trusted coverage from 1 to 0 and source covered from 3 to 0", before["obligation_coverage"]["rate"] == 1 and after["obligation_coverage"]["rate"] == 0 and before["obligation_coverage"]["per_axis"]["source_cases"]["covered"] == 3 and after["obligation_coverage"]["per_axis"]["source_cases"]["covered"] == 0, after)
    e.check("private-blocking/structural-unchanged", "coverage_report(ready.public_scenarios) == coverage_report(evaluator_blocked.public_scenarios)", coverage_report(ready.public_scenarios) == coverage_report(evaluator_blocked.public_scenarios), coverage_report(evaluator_blocked.public_scenarios))
    e.result["observations"] = {"mixed_trusted": trusted, "mixed_structural": structural, "all_blocked": all_report, "private_before": before, "private_after": after, "mixed_public_payloads": list(generated.scenario_payloads), "private_before_payloads": list(ready.scenario_payloads), "private_after_payloads": list(evaluator_blocked.scenario_payloads)}
    e.result["limitations"] = ["coverage_report(generated.public_scenarios) intentionally measures structure only and must not be interpreted as source readiness; use the complete GeneratedScenarioDraftSet for the completion gate.", "Draft readiness and structural coverage do not measure executed agent success.", "Evaluator blocking is injected into immutable test fixtures via dataclasses.replace; no product code is changed.", "Controlled local public SDK APIs only; no production platform E2E claim."]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("experiment", choices=("b3", "c3"))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    observed = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    assert observed == PRODUCT_SHA, ("Experiments require exact product HEAD", observed)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    e = Experiment(args.experiment.upper())
    try:
        (b3 if args.experiment == "b3" else c3)(e)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        e.check("experiment/unhandled-exception", "experiment completes", False, repr(exc))
    e.result["status"] = "pass" if e.result["assertions"] and all(a["status"] == "pass" for a in e.result["assertions"]) else "fail"
    e.result["summary"] = f"{sum(a['status'] == 'pass' for a in e.result['assertions'])}/{len(e.result['assertions'])} exact assertions passed"
    (args.output_dir / "result.json").write_text(json.dumps(e.result, indent=2, ensure_ascii=False) + "\n")
    print(e.result["status"], e.result["summary"], flush=True)
    raise SystemExit(0 if e.result["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
