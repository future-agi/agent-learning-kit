from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from fi.alk.harness import repair_authoring
from fi.alk.harness.backends import FILE_TOOLS
from fi.alk.harness.diagnostics import HarnessDiagnostic
from fi.alk.harness.job import HarnessStage
from fi.alk.harness.repair_patch import RepairPatchOp
from fi.alk.harness.source_model import (
    LogicalType,
    SourceColumn,
    SourceModel,
    SourceTable,
)
from fi.alk.harness.world_ir import WorldIR, WorldRow, WorldTable, WorldValue


def _source_and_world() -> tuple[SourceModel, WorldIR]:
    source = SourceModel.create(
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
                ),
                primary_key=("id",),
            ),
        ),
    )
    world = WorldIR.create(
        source_model_fingerprint=source.fingerprint,
        tables=(
            WorldTable(
                source_name="users",
                rows=(
                    WorldRow(
                        identity="row-1",
                        values={
                            "id": WorldValue.present(LogicalType.STRING, "old")
                        },
                    ),
                ),
            ),
        ),
    )
    return source, world


def _diagnostic() -> HarnessDiagnostic:
    return HarnessDiagnostic.create(
        stage=HarnessStage.VALIDATING_ENVIRONMENT,
        component="world",
        code="enum_value_invalid",
        message="generated value violates source semantics",
        evidence_refs=("source.py:1",),
    )


def test_world_repair_uses_fresh_evidence_pass_after_constrained_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source, world = _source_and_world()
    specs = []
    monkeypatch.setattr(repair_authoring, "chosen_model", lambda: "routed-model")

    class FakeStage:
        def __init__(self, spec, *, name: str):  # noqa: ANN001
            del name
            self.spec = spec
            self.calls = 0
            specs.append(spec)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def say(self, _message: str):
            self.calls += 1
            if self.spec.builtins == FILE_TOOLS and self.calls == 1:
                submit = self.spec.servers["repair"].tools[0].handler
                await submit(
                    {
                        "operations": [
                            {
                                "op": "set_value",
                                "table": "users",
                                "row_identity": "row-1",
                                "column": "id",
                                "value": {
                                    "state": "present",
                                    "logical_type": "string",
                                    "value": "new",
                                },
                                "reason": "enum_value_invalid",
                                "evidence_refs": ["source.py:1"],
                            }
                        ]
                    }
                )

    monkeypatch.setattr(repair_authoring, "Stage", FakeStage)

    patch = asyncio.run(
        repair_authoring.request_world_ir_patch(
            tmp_path, source, world, (_diagnostic(),)
        )
    )

    assert [spec.builtins for spec in specs] == [FILE_TOOLS]
    assert [spec.model for spec in specs] == ["routed-model"]
    assert patch.operations[0].op is RepairPatchOp.SET_VALUE


def test_world_repair_reports_both_exhausted_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source, world = _source_and_world()
    specs = []
    monkeypatch.setattr(repair_authoring, "chosen_model", lambda: "routed-model")

    class FakeStage:
        def __init__(self, spec, *, name: str):  # noqa: ANN001
            del name
            self.spec = spec
            specs.append(spec)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def say(self, _message: str):
            return None

    monkeypatch.setattr(repair_authoring, "Stage", FakeStage)

    with pytest.raises(
        RuntimeError,
        match="typed_world_ir_patch_not_submitted_after_constrained_and_evidence_passes",
    ):
        asyncio.run(
            repair_authoring.request_world_ir_patch(
                tmp_path, source, world, (_diagnostic(),)
            )
        )

    assert [spec.builtins for spec in specs] == [FILE_TOOLS, FILE_TOOLS]
    assert [spec.model for spec in specs] == ["routed-model", "routed-model"]


def test_world_repair_can_see_the_rows_a_diagnostic_is_about(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source, world = _source_and_world()
    seen: list[str] = []
    monkeypatch.setattr(repair_authoring, "chosen_model", lambda: "routed-model")

    class FakeStage:
        def __init__(self, spec, *, name: str):  # noqa: ANN001
            del name
            self.spec = spec

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def say(self, _message: str):
            tools = {tool.name: tool.handler for tool in self.spec.servers["repair"].tools}
            listed = await tools["inspect_world"]({})
            rows = await tools["query_world"]({"sql": "SELECT id FROM users"})
            refused = await tools["query_world"]({"sql": "DELETE FROM users"})
            seen.extend(
                [listed["content"][0]["text"], rows["content"][0]["text"], str(refused)]
            )

    monkeypatch.setattr(repair_authoring, "Stage", FakeStage)
    with pytest.raises(RuntimeError):
        asyncio.run(
            repair_authoring.request_world_ir_patch(
                tmp_path, source, world, (_diagnostic(),)
            )
        )
    assert '"users": 1' in seen[0]
    assert '"old"' in seen[1]
    assert "query failed" in seen[2]
