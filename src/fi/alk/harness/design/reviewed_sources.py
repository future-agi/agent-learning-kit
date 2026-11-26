"""The design builder's projection of a reviewed Studio snapshot."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fi.alk.studio import (
    EnvironmentInputError,
    EnvironmentRequirements,
    ReviewedEnvironmentInputs,
    compile_environment_inputs,
)
from fi.alk.studio._environment_input_snapshots import (
    MAX_SNAPSHOT_BYTES,
    deserialize_environment_snapshot,
)
from fi.alk.studio._source_scenarios import _distinct_source_drafts, _public_source_case, _reviewable_scenarios

from ..backends import ToolServer, tool, tool_server
from ..folder import read_all
from ..releases import ReleaseError
from ..scenario import SOURCE_FIXTURE
from ..source_grounding import SOURCE_OUTCOME_GUIDANCE, action_code_guidance, bind_source_case, validate_bound_scenario
from ..tools import schema
from .workspace import Design, DocumentRecord, ReviewedSourcePin

EVIDENCE = "reviewed-source-evidence.json"
REVIEW_REQUIRED = "Approve the environment documents before building this design."


def document_manifest(
    directory: Path, *, workspace: Path | None = None
) -> list[DocumentRecord]:
    if not directory.is_dir():
        return []
    if workspace is not None and directory.resolve().is_relative_to(
        workspace.resolve()
    ):
        raise ReleaseError(
            "source_snapshot_invalid",
            "Keep uploaded documents outside the design workspace.",
        )
    documents = []
    for path in sorted(directory.iterdir()):
        if path.is_symlink() or not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        documents.append(
            DocumentRecord(
                id=path.name,
                name=path.name,
                file=path.name,
                sha256=digest.hexdigest(),
                size=path.stat().st_size,
            )
        )
    return documents


class _ReceiptModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _UploadOrigin(_ReceiptModel):
    kind: Literal["upload"]
    document_id: str = Field(min_length=1)
    original_sha256: str = Field(pattern=r"^(sha256:)?[a-f0-9]{64}$")


class _KnowledgeBaseOrigin(_ReceiptModel):
    kind: Literal["knowledge_base"]
    kb_id: str = Field(min_length=1)
    file_id: str = Field(min_length=1)
    original_sha256: str = Field(pattern=r"^(sha256:)?[a-f0-9]{64}$")


class _Binding(_ReceiptModel):
    source_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    parser_revision: str = Field(min_length=1)
    role: Literal["seed_records", "policy", "reference"]
    origin: _UploadOrigin | _KnowledgeBaseOrigin = Field(discriminator="kind")


class _Receipt(_ReceiptModel):
    review_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    snapshot_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    bindings: list[_Binding] = Field(min_length=1)


def _validate_receipt(
    value: object | None,
    reviewed: ReviewedEnvironmentInputs,
    documents: list[DocumentRecord],
) -> None:
    if value is None:
        raise ReleaseError(
            "source_review_receipt_required",
            "Supply the approved source review receipt.",
        )
    try:
        receipt = _Receipt.model_validate(value)
    except ValidationError:
        raise ReleaseError(
            "source_review_receipt_invalid", "The source review receipt is invalid."
        ) from None
    expected = {
        binding.source_id: (
            binding.source_sha256,
            binding.parser_revision,
            binding.role,
        )
        for binding in receipt.bindings
    }
    actual = {
        source.pin.source_id: (
            source.pin.source_sha256,
            source.pin.parser_revision,
            source.pin.role,
        )
        for source in reviewed.sources
    }
    upload_bindings = [
        (
            binding.origin.document_id,
            binding.origin.original_sha256.removeprefix("sha256:"),
        )
        for binding in receipt.bindings
        if isinstance(binding.origin, _UploadOrigin)
    ]
    knowledge_base_files = {
        (binding.origin.kb_id, binding.origin.file_id)
        for binding in receipt.bindings
        if isinstance(binding.origin, _KnowledgeBaseOrigin)
    }
    if (
        receipt.snapshot_id != reviewed.snapshot_id
        or receipt.snapshot_digest != reviewed.snapshot_digest
        or expected != actual
        or len(expected) != len(receipt.bindings)
        or len(actual) != len(reviewed.sources)
        or set(upload_bindings) != {(doc.id, doc.sha256) for doc in documents}
        or len({doc.id for doc in documents}) != len(documents)
        or len(upload_bindings) != len(documents)
        or len(knowledge_base_files) + len(upload_bindings) != len(receipt.bindings)
    ):
        raise ReleaseError(
            "source_review_receipt_mismatch",
            "The approved source review does not match the selected sources.",
        )


def read_reviewed_snapshot(path: Path) -> ReviewedEnvironmentInputs:
    try:
        with path.open("rb") as source:
            return deserialize_environment_snapshot(source.read(MAX_SNAPSHOT_BYTES + 1))
    except OSError:
        raise ReleaseError(
            "source_snapshot_invalid", "Cannot read the approved source snapshot."
        ) from None


def project_evidence(
    reviewed: ReviewedEnvironmentInputs,
    *,
    task: str,
    count: int,
    assigned_cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    compiled = compile_environment_inputs(reviewed)
    if assigned_cases is None:
        generated = _reviewable_scenarios(
            reviewed,
            environment_requirements=EnvironmentRequirements(task=task),
            requested_count=count,
        )
        assigned_cases = [
            _public_source_case(draft) for draft in _distinct_source_drafts(generated)
        ]
    evidence: dict[str, Any] = {
        "documents": [],
        "records": [],
        "cases": assigned_cases,
        "scenario_count": count,
    }
    for environment in compiled.agent_environments:
        data = environment.as_dict()["data"]
        evidence["documents"].extend(data.get("documents", []))
        evidence["records"].extend(data.get("artifacts", {}).values())
    return evidence


def bind_reviewed_sources(
    workspace: Path, design: Design, path: Path | None, receipt: object | None
) -> Design:
    if path is None:
        if design.reviewed_source is not None or receipt is not None:
            raise ReleaseError(
                "source_snapshot_required",
                "Supply the private approved source snapshot for this launch.",
            )
        return design
    if path.resolve().is_relative_to(workspace.resolve()):
        raise ReleaseError(
            "source_snapshot_invalid",
            "Keep the private snapshot outside the design workspace.",
        )
    try:
        reviewed = read_reviewed_snapshot(path)
        pinned = design.reviewed_source
        if pinned is not None and (
            pinned.id != reviewed.snapshot_id
            or pinned.digest != reviewed.snapshot_digest
        ):
            raise ReleaseError(
                "source_snapshot_changed",
                "The resumed design has a different approved snapshot.",
            )
        if design.reviewed_source is None and (
            design.provenance or (design.documents and design.stage != "interview")
        ):
            raise ReleaseError(
                "source_snapshot_changed",
                "Start a new design to replace unreviewed source changes.",
            )
        _validate_receipt(receipt, reviewed, design.documents)
        evidence = project_evidence(
            reviewed,
            task=design.requirements.strip() or f"Design {design.name}",
            count=design.scenario_count,
        )
    except OSError as exc:
        raise ReleaseError(
            "source_snapshot_invalid", "Cannot read the approved source snapshot."
        ) from exc
    except EnvironmentInputError as exc:
        raise ReleaseError(exc.code, str(exc)) from exc

    target = workspace / EVIDENCE
    body = json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()
    pin = ReviewedSourcePin(
        id=reviewed.snapshot_id,
        digest=reviewed.snapshot_digest,
        evidence_digest=hashlib.sha256(body).hexdigest(),
    )
    if (
        design.reviewed_source != pin
        or not target.is_file()
        or target.read_bytes() != body
    ):
        with tempfile.NamedTemporaryFile(dir=workspace, delete=False) as file:
            temporary = Path(file.name)
            try:
                file.write(body)
                file.close()
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
    return design.model_copy(update={"reviewed_source": pin})


def load_evidence(workspace: Path, design: Design) -> dict[str, Any]:
    if design.reviewed_source is None:
        if design.documents:
            raise ReleaseError("source_review_required", REVIEW_REQUIRED)
        return {}
    try:
        body = (workspace / EVIDENCE).read_bytes()
        if hashlib.sha256(body).hexdigest() == design.reviewed_source.evidence_digest:
            return json.loads(body)
    except (OSError, ValueError):
        pass
    raise ReleaseError(
        "source_snapshot_invalid",
        "The approved source projection is missing or changed.",
    )


def proposal_rule_sources(rules: list[str], evidence: dict[str, Any]) -> list[dict]:
    """Link an unchanged approved clause to its document page, never a derived plan item."""
    def wording(text: str) -> str:
        text = re.sub(r"^\s*\[(?:agent_policy|system_invariant|configurable_environment_policy)\]\s*", "", text)
        text = re.sub(r"^\s*\d+[.)]\s+", "", text)
        return " ".join(text.casefold().split()).rstrip(".")

    pages: dict[str, set[tuple[str, int]]] = {}
    for case in evidence.get("cases", []):
        clause = wording(case.get("policy_reference") or "")
        for cite in case.get("citations", []):
            if clause and cite.get("source_id") and isinstance(cite.get("page"), int):
                pages.setdefault(clause, set()).add((cite["source_id"], cite["page"]))
    return [
        {"rule_index": index, "source_id": source, "page": page}
        for index, rule in enumerate(rules)
        for source, page in sorted(pages.get(wording(rule), ()))
    ]


def source_server(evidence: dict[str, Any]) -> ToolServer:
    @tool(
        "read_source",
        "Read approved documents, mapped records or scenario cases. Use the counts in the brief. "
        "Sections: documents, records, cases. Index is one-based. Paginate long entries with offset.",
        schema({"section": str, "index": int, "offset": int}, ["section", "index"]),
    )
    async def read_source(args: dict[str, Any]) -> dict[str, Any]:
        section = args.get("section")
        index = int(args["index"]) - 1
        offset = max(0, int(args.get("offset") or 0))
        if section not in ("documents", "records", "cases") or not 0 <= index < len(
            evidence[section]
        ):
            return {
                "content": [{"type": "text", "text": "No such source entry."}],
                "is_error": True,
            }
        text = json.dumps(evidence[section][index], ensure_ascii=False)
        limit = offset + 12_000
        page = text[offset:limit]
        if limit < len(text):
            page += f"\n[Read again with offset {limit}.]"
        return {"content": [{"type": "text", "text": page}]}

    return tool_server(name="reviewed_sources", tools=[read_source])


def bind_scenario(evidence: dict[str, Any], args: dict[str, Any]) -> str:
    fixture = args.get("fixture")
    source = fixture.get(SOURCE_FIXTURE) if isinstance(fixture, dict) else None
    if source is None:
        return ""
    selected = source.get("id") if isinstance(source, dict) else source
    case = next((case for case in evidence["cases"] if case["id"] == selected), None)
    if case is None:
        return "Select a reviewed case ID from the cases section of read_source."
    try:
        args.update(bind_source_case(args, case))
    except ValueError as exc:
        if str(exc) == "source_case_answer_in_situation":
            return "Keep the expected code in the test and solution. Remove it from the name, heading and caller situation."
        hint = SOURCE_OUTCOME_GUIDANCE + action_code_guidance([case])
        return "The scenario is unrelated to its approved source case. Choose an input that triggers the cited rule and assert the required outcome. Keep record codes in their supplied roles. Only name queues, priorities and deadlines supported by the evidence." + (" " + hint if hint else "")
    return ""


def check_grounded_suite(
    workspace: Path, evidence: dict[str, Any], design: Design
) -> None:
    if not evidence:
        return
    cases = {case["id"]: case for case in evidence["cases"]}
    seen: set[str] = set()
    for scenario in read_all(workspace):
        source = scenario.fixture.get(SOURCE_FIXTURE)
        if source is None:
            continue
        case_id = source.get("id") if isinstance(source, dict) else None
        if not isinstance(case_id, str) or source != cases.get(case_id):
            raise ReleaseError(
                "design_source_invalid",
                f"{scenario.name} cites an unapproved source case.",
            )
        if not validate_bound_scenario(scenario, source):
            raise ReleaseError(
                "design_source_invalid",
                f"{scenario.name} changed its source-grounded semantics.",
            )
        if case_id in seen:
            raise ReleaseError(
                "design_source_duplicate",
                f"{scenario.name} reuses an approved source case.",
            )
        seen.add(case_id)
    if missing := len(cases.keys() - seen):
        raise ReleaseError(
            "design_source_required",
            f"Prove the {missing} missing approved source cases.",
        )
