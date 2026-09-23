"""Deterministic compiler for agents that declare no harness-owned state."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ..source_model import SourceModel
from ..world_ir import WorldIR, validate_world_ir

NO_STATE_COMPILER_VERSION = "futureagi.no-state-compiler.v1"


class NoStateCompileResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_schema_hash: str
    world_ir_hash: str
    compiler_version: str = NO_STATE_COMPILER_VERSION
    operations: tuple[()] = ()
    decisions: tuple[()] = ()
    warnings: tuple[str, ...] = ()


def compile_no_state(source: SourceModel, world: WorldIR) -> NoStateCompileResult:
    if source.engine != "none":
        raise ValueError(
            f"schema_type_mismatch: expected no state engine, got {source.engine}"
        )
    validate_world_ir(world, source)
    return NoStateCompileResult(
        source_schema_hash=source.fingerprint,
        world_ir_hash=world.fingerprint,
    )


__all__ = ["NO_STATE_COMPILER_VERSION", "NoStateCompileResult", "compile_no_state"]
