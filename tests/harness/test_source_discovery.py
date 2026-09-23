from __future__ import annotations

from pathlib import Path

import pytest

from fi.alk.harness.contract import (
    AgentContract,
    Runtime,
    RuntimeInterface,
    ToolEntry,
    ToolSpec,
)
from fi.alk.harness.source_discovery import (
    _action_schema,
    _json_type,
    compose_source_models,
    discover_code_source_model,
)
from fi.alk.harness.source_model import (
    LogicalType,
    SourceColumn,
    SourceModel,
    SourceTable,
)

DIGEST = "sha256:" + "a" * 64


def test_language_numeric_union_discovers_number_independent_of_order() -> None:
    assert _json_type("int | float") == {"type": "number"}
    assert _json_type("float | int") == {"type": "number"}


def test_allowed_values_override_conflicting_inferred_type() -> None:
    tool = ToolSpec(
        name="book_business_center",
        args=("service",),
        arg_types={"service": "integer"},
        arg_values={"service": ["meeting_room", "secretarial"]},
    )

    assert _action_schema(tool)["properties"]["service"] == {
        "enum": ["meeting_room", "secretarial"]
    }


def test_discovers_framework_neutral_process_interface_and_actions(
    tmp_path: Path,
) -> None:
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    contract = AgentContract(
        agent="graph-worker",
        tools=(
            ToolSpec(
                name="lookup",
                args=("account_id",),
                arg_types={"account_id": "str"},
            ),
        ),
        tool_entrypoints=(
            ToolEntry(
                tool="lookup",
                mode="import",
                module="agent.tools",
                callable="lookup",
            ),
        ),
        runtime=Runtime(
            language="python",
            command=("python", "-m", "agent"),
            interface=RuntimeInterface(
                kind="http", protocol="fi.alk", port=8080, path="/invoke"
            ),
        ),
    )

    model = discover_code_source_model(tmp_path, contract, source_digest=DIGEST)

    assert model.engine == "none"
    assert model.processes[0].command == ("python", "-m", "agent")
    assert model.interfaces[0].protocol == "fi.alk"
    assert model.interfaces[0].endpoint == "/invoke"
    assert model.actions[0].implementation_ref == "agent.tools:lookup"
    assert model.actions[0].input_schema["required"] == ["account_id"]
    rendered = model.model_dump_json().lower()
    assert "retell" not in rendered
    assert "livekit" not in rendered
    assert "voice" not in rendered
    assert "langgraph" not in rendered


def test_composes_code_and_state_discovery_without_losing_either() -> None:
    code = SourceModel.create(source_digest=DIGEST, engine="none")
    state = SourceModel.create(
        source_digest=DIGEST,
        engine="postgres",
        tables=(
            SourceTable(
                name="accounts",
                columns=(
                    SourceColumn(
                        name="id",
                        logical_type=LogicalType.INTEGER,
                        native_type="integer",
                        nullable=False,
                        has_default=False,
                    ),
                ),
                primary_key=("id",),
            ),
        ),
    )

    combined = compose_source_models(code, state)

    assert combined.engine == "postgres"
    assert combined.tables[0].name == "accounts"


def test_composition_rejects_different_source_revisions() -> None:
    code = SourceModel.create(source_digest=DIGEST, engine="none")
    state = SourceModel.create(source_digest="sha256:" + "b" * 64, engine="none")

    with pytest.raises(ValueError, match="source_model_digest_conflict"):
        compose_source_models(code, state)
