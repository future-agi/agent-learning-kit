from __future__ import annotations

import sqlite3
from pathlib import Path

from fi.alk.harness.generic_pipeline import GenericCandidate, GenericHarnessPipeline
from fi.alk.harness.repair_controller import RepairAction, RepairOutcome
from fi.alk.harness.repair_patch import (
    RepairPatchOp,
    RepairPatchOperation,
    WorldIRRepairPatch,
)
from fi.alk.harness.source_model import (
    LogicalType,
    SourceColumn,
    SourceModel,
    SourceTable,
    UnsupportedSourceConstruct,
)
from fi.alk.harness.world_import.sqlite import import_sqlite_world
from fi.alk.harness.world_ir import WorldIR, WorldRow, WorldTable, WorldValue


def _source() -> SourceModel:
    return SourceModel.create(
        source_digest="sha256:" + "a" * 64,
        engine="postgres",
        tables=(
            SourceTable(
                name="users",
                columns=(
                    SourceColumn(
                        name="id",
                        logical_type=LogicalType.STRING,
                        native_type="text",
                        nullable=False,
                        has_default=False,
                    ),
                    SourceColumn(
                        name="tags",
                        logical_type=LogicalType.ARRAY,
                        native_type="text[]",
                        nullable=False,
                        has_default=False,
                        element_type=LogicalType.STRING,
                    ),
                ),
                primary_key=("id",),
            ),
        ),
    )


def test_pipeline_imports_validates_compiles_and_certifies(tmp_path: Path) -> None:
    source = _source()
    database = sqlite3.connect(":memory:")
    database.execute("CREATE TABLE users (id TEXT, tags TEXT)")
    database.execute("INSERT INTO users VALUES ('user-1', '[\"priority\"]')")
    imported = import_sqlite_world(database, source)
    pipeline = GenericHarnessPipeline(tmp_path / "generic")

    evaluation = pipeline.evaluate(source, imported.world)

    assert evaluation.decision.action is RepairAction.CERTIFY
    assert evaluation.diagnostics == ()
    assert evaluation.compiled is not None
    assert evaluation.compiled.operations[0].params == ("user-1", ["priority"])
    assert pipeline.artifacts.read_source_model() == source
    assert pipeline.artifacts.read_world_ir() == imported.world
    assert pipeline.artifacts.read_repair_history().decisions == (evaluation.decision,)


def test_pipeline_certifies_an_agent_with_no_harness_owned_state(
    tmp_path: Path,
) -> None:
    source = SourceModel.create(
        source_digest="sha256:" + "b" * 64,
        engine="none",
    )
    world = WorldIR.create(source_model_fingerprint=source.fingerprint, tables=())

    evaluation = GenericHarnessPipeline(tmp_path / "generic").evaluate(source, world)

    assert evaluation.decision.action is RepairAction.CERTIFY
    assert evaluation.compiled.compiler_version == "futureagi.no-state-compiler.v1"


def test_pipeline_rejects_explicitly_unsupported_source_before_compilation(
    tmp_path: Path,
) -> None:
    source = SourceModel.create(
        source_digest="sha256:" + "c" * 64,
        engine="postgres",
        unsupported=(
            UnsupportedSourceConstruct(
                code="postgres_native_type_unsupported",
                component="schema",
                location="events.position",
            ),
        ),
    )
    world = WorldIR.create(source_model_fingerprint=source.fingerprint, tables=())

    evaluation = GenericHarnessPipeline(tmp_path / "generic").evaluate(source, world)

    assert evaluation.compiled is None
    assert evaluation.decision.action is RepairAction.REJECT
    assert evaluation.diagnostics[0].code == "unsupported_source_construct"
    assert evaluation.diagnostics[0].owner.value == "unsupported"
    assert evaluation.diagnostics[0].evidence_refs == ("events.position",)


def test_pipeline_routes_semantic_failure_to_bounded_repair(tmp_path: Path) -> None:
    source = _source()
    world = WorldIR.create(
        source_model_fingerprint=source.fingerprint,
        tables=(
            WorldTable(
                source_name="users",
                rows=(
                    WorldRow(
                        identity="row-1",
                        values={"id": WorldValue.present(LogicalType.STRING, "user-1")},
                    ),
                ),
            ),
        ),
    )
    pipeline = GenericHarnessPipeline(tmp_path / "generic")

    first = pipeline.evaluate(source, world)
    repeated = pipeline.evaluate(source, world)

    assert first.decision.action is RepairAction.PATCH_ENVIRONMENT
    assert first.diagnostics[0].code == "required_value_missing"
    assert repeated.decision.action is RepairAction.REJECT
    assert "without a materially changed candidate" in repeated.decision.reason


def test_candidate_hash_changes_only_with_material_inputs() -> None:
    source = _source()
    empty = WorldIR.create(source_model_fingerprint=source.fingerprint, tables=())
    same = WorldIR.model_validate_json(empty.model_dump_json())
    changed = WorldIR.create(
        source_model_fingerprint=source.fingerprint,
        tables=(WorldTable(source_name="users", rows=()),),
    )

    assert GenericCandidate.create(source, empty) == GenericCandidate.create(
        source, same
    )
    assert GenericCandidate.create(source, empty) != GenericCandidate.create(
        source, changed
    )


def test_pipeline_persists_repair_outcome(tmp_path: Path) -> None:
    source = _source()
    world = WorldIR.create(
        source_model_fingerprint=source.fingerprint,
        tables=(
            WorldTable(
                source_name="users",
                rows=(
                    WorldRow(
                        identity="row-1",
                        values={"id": WorldValue.present(LogicalType.STRING, "user-1")},
                    ),
                ),
            ),
        ),
    )
    pipeline = GenericHarnessPipeline(tmp_path / "generic")
    evaluation = pipeline.evaluate(source, world)

    pipeline.record_result(
        evaluation.decision.sequence,
        after_candidate_hash=evaluation.candidate.candidate_hash,
        outcome=RepairOutcome.APPLIED,
    )

    result = pipeline.artifacts.read_repair_history().results[0]
    assert result.outcome is RepairOutcome.NO_MATERIAL_CHANGE


def test_pipeline_applies_only_typed_policy_authorized_patch(tmp_path: Path) -> None:
    source = _source()
    world = WorldIR.create(
        source_model_fingerprint=source.fingerprint,
        tables=(
            WorldTable(
                source_name="users",
                rows=(
                    WorldRow(
                        identity="row-1",
                        values={"id": WorldValue.present(LogicalType.STRING, "user-1")},
                    ),
                ),
            ),
        ),
    )
    pipeline = GenericHarnessPipeline(tmp_path / "generic")
    evaluation = pipeline.evaluate(source, world)
    patch = WorldIRRepairPatch.create(
        base_world_ir_hash=world.fingerprint,
        operations=(
            RepairPatchOperation(
                op=RepairPatchOp.SET_VALUE,
                table="users",
                row_identity="row-1",
                column="tags",
                value=WorldValue.present(LogicalType.ARRAY, ["priority"]),
                reason="required_value_missing",
                evidence_refs=("source-model:users.tags",),
            ),
        ),
    )

    repaired = pipeline.apply_patch(evaluation, patch)

    assert repaired.decision.action is RepairAction.CERTIFY
    assert repaired.diagnostics == ()
    assert (
        pipeline.artifacts.read_repair_history().results[0].outcome
        is RepairOutcome.APPLIED
    )
    assert (tmp_path / "generic" / "repair-patch-0001.json").is_file()
