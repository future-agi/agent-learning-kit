"""Controller-owned reviewed inputs for connected environment authoring."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from fi.alk.studio import EnvironmentInputError
from fi.alk.studio._environment_input_types import _canonical_json
from fi.alk.studio._source_scenarios import _sealed_source_case

from .backends.files import secret_path
from .releases import ReleaseError
from .scenario import SOURCE_FIXTURE, Scenario

SNAPSHOT_PATH = Path("/run/futureagi/reviewed-source-snapshot.json")
PIN_FILE = "reviewed-source.json"
_RECEIPT = "source_review_receipt"
_DOCUMENTS = "source_review_documents"
_SOURCE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def load_review_pin(workspace: Path) -> dict[str, str] | None:
    path = workspace / PIN_FILE
    if not path.exists() and not path.is_symlink():
        return None
    try:
        pin = json.loads(path.read_bytes()) if not path.is_symlink() else None
    except (OSError, ValueError):
        pin = None
    if (
        not isinstance(pin, dict)
        or pin.keys() != {"id", "digest", "schema_digest"}
        or not all(isinstance(value, str) for value in pin.values())
        or not all(
            _SOURCE_DIGEST.fullmatch(pin[key]) for key in ("digest", "schema_digest")
        )
        or pin["id"] != "source_snapshot_" + pin["digest"][7:23]
    ):
        raise ReleaseError(
            "source_snapshot_changed", "The workspace's approved source pin is invalid."
        )
    return pin


@dataclass(frozen=True)
class ReviewedBuild:
    pin: dict[str, str]
    evidence: dict[str, Any]

    @property
    def cases(self) -> list[dict]:
        return self.evidence["cases"]

    def tool_servers(self):
        from .design.reviewed_sources import source_server

        server = source_server(self.evidence)
        return {server.name: server}

    def brief(self) -> str:
        return (
            f"\n\n## Approved source cases\nUse the reviewed_sources read_source tool "
            f"for exactly {len(self.cases)} assigned cases in its cases section. "
            "Ground each scenario's task, starting state and checks in one assigned case. "
            "Send its ID in fixture.reviewed_source when submitting; each case is used once. "
            "The tool also exposes approved documents and records for context. "
            "Never reproduce an evaluator-only check or an incorrect candidate answer."
        )

    def subset(self, cases: list[dict]) -> ReviewedBuild:
        return replace(self, evidence={**self.evidence, "cases": cases})

    def saved_case(self, scenario: Scenario) -> dict | None:
        value = scenario.fixture.get(SOURCE_FIXTURE)
        return value if isinstance(value, dict) else None

    def remaining(self, kept: list[Scenario]) -> list[dict]:
        saved = {
            case["id"]
            for one in kept
            if (case := self.saved_case(one)) and "id" in case
        }
        return [case for case in self.cases if case["id"] not in saved]

    def bind(self, args: dict[str, Any], kept: list[Scenario]) -> dict[str, Any]:
        fixture = args.get("fixture") or {}
        if not isinstance(fixture, dict):
            raise ReleaseError(
                "source_case_invalid", "Give the scenario a reviewed case ID."
            )
        supplied = fixture.get(SOURCE_FIXTURE)
        requested = supplied.get("id") if isinstance(supplied, dict) else supplied
        key = str(args.get("scenario_key") or "").strip()
        name = str(args.get("name") or "").strip()
        prior = next((one for one in kept if key and one.scenario_key == key), None)
        if prior is None:
            prior = next((one for one in kept if one.name == name), None)
        assigned = self.saved_case(prior) if prior else None
        if assigned and requested not in (None, assigned.get("id")):
            raise ReleaseError(
                "source_case_invalid", "An edited scenario keeps its approved case."
            )
        available = self.remaining([one for one in kept if one is not prior])
        if assigned:
            requested = assigned.get("id")
        case = next((item for item in available if item["id"] == requested), None)
        if case is None:
            raise ReleaseError(
                "source_case_invalid",
                "Choose an unused approved case ID from read_source before submitting.",
            )
        # Proof and scenario content are keyed to the approved public case, including its facts and policy.
        approved = _sealed_source_case(case)
        return {**args, "fixture": {**fixture, SOURCE_FIXTURE: approved}}

    def check_complete(self, scenarios: list[Scenario], workspace: Path) -> None:
        from .proofs import load_proofs

        self.check_pin(workspace)
        proofs = load_proofs(workspace)
        approved = {one["id"]: _sealed_source_case(one) for one in self.cases}
        seen: set[str] = set()
        for scenario in scenarios:
            case = self.saved_case(scenario)
            key = case.get("id") if case else None
            if not isinstance(key, str) or key in seen or case != approved.get(key):
                raise ReleaseError(
                    "source_suite_invalid",
                    "Every scenario needs a distinct approved source case.",
                )
            if (
                proofs.get(scenario.scenario_key, {}).get("reviewed_source_digest")
                != case["proof_digest"]
            ):
                raise ReleaseError(
                    "source_suite_unproved",
                    "Prove each scenario against its current approved case.",
                )
            seen.add(key)
        if seen != approved.keys():
            raise ReleaseError(
                "source_suite_incomplete",
                "Write and prove every assigned approved source case.",
            )

    def persist_pin(self, workspace: Path) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        path = workspace / PIN_FILE
        try:
            with path.open("x", encoding="utf-8") as output:
                output.write(_canonical_json(self.pin) + "\n")
        except FileExistsError:
            pass
        self.check_pin(workspace)

    def check_pin(self, workspace: Path) -> None:
        if load_review_pin(workspace) != self.pin:
            raise ReleaseError(
                "source_snapshot_changed",
                "The workspace's approved source pin is missing or changed.",
            )


def _private_path(path: Path, workspace: Path, source: Path) -> bool:
    resolved = path.resolve()
    return (
        secret_path(str(resolved))
        and not resolved.is_relative_to(workspace.resolve())
        and not resolved.is_relative_to(source.resolve())
    )


def prepare_reviewed_build(
    raw_job: dict[str, Any],
    *,
    workspace: Path,
    source: Path,
    snapshot: Path | None = None,
    job_path: Path | None = None,
) -> tuple[dict[str, Any], ReviewedBuild | None]:
    """Validate a sourced launch and keep only its public projection in the builder's process."""
    metadata = raw_job.get("metadata") or {}
    if not isinstance(metadata, dict) or any(
        metadata.get(kind) is not None
        for kind in ("design", "adaptation", "release_composition")
    ):
        return raw_job, None
    path = snapshot or SNAPSHOT_PATH
    receipt = metadata.get(_RECEIPT)
    has_pin = (workspace / PIN_FILE).exists()
    if not (
        has_pin
        or snapshot is not None
        or path.exists()
        or _RECEIPT in metadata
        or _DOCUMENTS in metadata
    ):
        return raw_job, None
    if job_path is not None and not _private_path(job_path, workspace, source):
        raise ReleaseError(
            "source_job_not_private",
            "The approved source launch needs a private controller job.",
        )
    if not path.is_file():
        raise ReleaseError(
            "source_snapshot_required", "Supply the approved private source snapshot."
        )
    if not _private_path(path, workspace, source):
        raise ReleaseError(
            "source_snapshot_invalid",
            "The approved snapshot needs a private controller path.",
        )
    from .design.reviewed_sources import (
        _validate_receipt,
        project_evidence,
        read_reviewed_snapshot,
    )
    from .design.workspace import DocumentRecord

    try:
        rows = metadata.get(_DOCUMENTS, [])
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError("invalid manifest")
        documents = [
            DocumentRecord(
                id=doc["id"],
                name=doc["name"],
                file=doc["name"],
                sha256=str(doc["sha256"]).removeprefix("sha256:"),
                size=doc["size"],
            )
            for doc in rows
        ]
    except (KeyError, TypeError, ValueError, ValidationError):
        raise ReleaseError(
            "source_review_receipt_invalid", "The selected source manifest is invalid."
        ) from None
    try:
        reviewed = read_reviewed_snapshot(path)
        _validate_receipt(receipt, reviewed, documents)
        digest = reviewed.simulation_schema_digest
        if not digest:
            raise ReleaseError(
                "source_schema_required",
                "The approved snapshot must identify its simulation schema.",
            )
        pin = {
            "id": reviewed.snapshot_id,
            "digest": reviewed.snapshot_digest,
            "schema_digest": digest,
        }
        evidence = project_evidence(
            reviewed,
            task=f"Test the behavior of {metadata.get('agent_name') or 'the connected agent'}",
            count=int(raw_job.get("scenario_count") or 10),
        )
    except EnvironmentInputError as exc:
        raise ReleaseError(
            exc.code, "The approved sources could not be used for this build."
        ) from None
    clean = {
        **raw_job,
        "metadata": {
            k: v for k, v in metadata.items() if k not in {_RECEIPT, _DOCUMENTS}
        },
    }
    approved = ReviewedBuild(pin=pin, evidence=evidence)
    if has_pin:
        approved.check_pin(workspace)
    return clean, approved
