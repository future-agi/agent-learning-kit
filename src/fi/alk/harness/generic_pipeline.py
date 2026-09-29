"""Deterministic core of the generic, bounded harness pipeline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from .certification import GenericHarnessArtifactStore
from .compile.base import BackendCompiler, CompilerRegistry, CompilerUnavailable
from .compile.no_state import NO_STATE_COMPILER_VERSION, compile_no_state
from .compile.postgres import (
    POSTGRES_COMPILER_VERSION,
    PostgresCompileError,
    compile_postgres,
)
from .compile.sqlite import SQLITE_COMPILER_VERSION, SQLiteCompileError, compile_sqlite
from .diagnostic_adapters.world_ir import diagnose_world_ir_error
from .diagnostics import HarnessDiagnostic
from .job import HarnessStage
from .repair_controller import (
    CandidateObservation,
    RepairController,
    RepairDecision,
    RepairOutcome,
    RepairPhase,
)
from .repair_patch import WorldIRRepairPatch, apply_world_ir_repair_patch
from .source_model import SourceModel
from .world_ir import WorldIR, WorldIRValidationError, validate_world_ir


class GenericCandidate(BaseModel):
    """An immutable source/world/compiler identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_hash: str
    source_model_hash: str
    world_ir_hash: str
    compiler_version: str

    @classmethod
    def create(
        cls,
        source: SourceModel,
        world: WorldIR,
        *,
        compiler_version: str = POSTGRES_COMPILER_VERSION,
    ) -> "GenericCandidate":
        payload = {
            "source_model_hash": source.fingerprint,
            "world_ir_hash": world.fingerprint,
            "compiler_version": compiler_version,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return cls(
            candidate_hash="sha256:" + hashlib.sha256(encoded).hexdigest(),
            **payload,
        )


class CandidateEvaluation(BaseModel):
    """One complete validation result; bound values stay only in compiled operations."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    candidate: GenericCandidate
    diagnostics: tuple[HarnessDiagnostic, ...]
    decision: RepairDecision
    compiled: Any | None = None


def _compile_diagnostic(
    error: PostgresCompileError | SQLiteCompileError | CompilerUnavailable,
) -> HarnessDiagnostic:
    return HarnessDiagnostic.create(
        stage=HarnessStage.VALIDATING_ENVIRONMENT,
        component=(
            "compiler_registry"
            if isinstance(error, CompilerUnavailable)
            else "backend_compiler"
        ),
        code=error.code,
        message=error.message,
        evidence_refs=("artifact://source-model", "artifact://world-ir"),
    )


class GenericHarnessPipeline:
    """Evaluate immutable candidates and persist every bounded policy decision."""

    def __init__(
        self,
        artifact_root: Path,
        *,
        controller: RepairController | None = None,
        compilers: CompilerRegistry | None = None,
    ) -> None:
        self.artifacts = GenericHarnessArtifactStore(artifact_root)
        self.controller = controller or RepairController()
        self.compilers = compilers or default_compiler_registry()

    def evaluate(
        self,
        source: SourceModel,
        world: WorldIR,
        *,
        phase: RepairPhase = RepairPhase.ENVIRONMENT,
    ) -> CandidateEvaluation:
        try:
            compiler = self.compilers.resolve(source.engine)
        except CompilerUnavailable as error:
            compiler = None
            compiler_version = "unavailable"
            compiler_diagnostic: tuple[HarnessDiagnostic, ...] = (
                _compile_diagnostic(error),
            )
        else:
            compiler_version = compiler.version
            compiler_diagnostic = ()
        candidate = GenericCandidate.create(
            source, world, compiler_version=compiler_version
        )
        diagnostics: tuple[HarnessDiagnostic, ...] = ()
        compiled: Any | None = None
        if source.unsupported:
            diagnostics = tuple(
                HarnessDiagnostic.create(
                    stage=HarnessStage.VALIDATING_ENVIRONMENT,
                    component=(
                        f"source_discovery:{construct.component}:{construct.code}:"
                        f"{construct.location or '<unknown>'}"
                    ),
                    code="unsupported_source_construct",
                    message=f"unsupported source construct: {construct.code}",
                    evidence_refs=(construct.location,) if construct.location else (),
                )
                for construct in source.unsupported
            )
        else:
            try:
                validate_world_ir(world, source)
            except WorldIRValidationError as error:
                diagnostics = diagnose_world_ir_error(error)
            else:
                diagnostics = compiler_diagnostic
        if not diagnostics and compiler is not None:
            try:
                compiled = compiler.compile(source, world)
            except (PostgresCompileError, SQLiteCompileError) as error:
                diagnostics = (_compile_diagnostic(error),)

        observation = CandidateObservation(
            candidate_hash=candidate.candidate_hash,
            phase=phase,
            diagnostics=diagnostics,
        )
        decision = self.controller.decide(observation)
        self.artifacts.write_contract_schemas()
        self.artifacts.write_source_model(source)
        self.artifacts.write_world_ir(world)
        self.artifacts.write_repair_history(self.controller.history)
        return CandidateEvaluation(
            candidate=candidate,
            diagnostics=diagnostics,
            decision=decision,
            compiled=compiled,
        )

    def record_result(
        self,
        decision_sequence: int,
        *,
        after_candidate_hash: str | None,
        outcome: RepairOutcome,
    ) -> None:
        self.controller.record_result(
            decision_sequence,
            after_candidate_hash=after_candidate_hash,
            outcome=outcome,
        )
        self.artifacts.write_repair_history(self.controller.history)

    def apply_patch(
        self,
        evaluation: CandidateEvaluation,
        patch: WorldIRRepairPatch,
        *,
        phase: RepairPhase = RepairPhase.ENVIRONMENT,
    ) -> CandidateEvaluation:
        """Apply exactly one policy-authorized typed patch and evaluate a new candidate."""

        from .repair_controller import RepairAction

        decision = evaluation.decision
        if decision.action is not RepairAction.PATCH_ENVIRONMENT:
            raise ValueError("repair_patch_not_authorized_for_decision")
        source = self.artifacts.read_source_model()
        world = self.artifacts.read_world_ir()
        allowed_reason_codes = {
            diagnostic.code
            for diagnostic in evaluation.diagnostics
            if diagnostic.repair_strategy
        }
        self.artifacts.write_repair_patch(patch, sequence=decision.sequence)
        try:
            repaired = apply_world_ir_repair_patch(
                world,
                source,
                patch,
                allowed_reason_codes=allowed_reason_codes,
            )
        except Exception:
            self.record_result(
                decision.sequence,
                after_candidate_hash=None,
                outcome=RepairOutcome.FAILED,
            )
            raise
        next_evaluation = self.evaluate(source, repaired, phase=phase)
        self.record_result(
            decision.sequence,
            after_candidate_hash=next_evaluation.candidate.candidate_hash,
            outcome=RepairOutcome.APPLIED,
        )
        return next_evaluation


__all__ = [
    "CandidateEvaluation",
    "GenericCandidate",
    "GenericHarnessPipeline",
    "default_compiler_registry",
]


def default_compiler_registry() -> CompilerRegistry:
    """Return the built-in state compilers without importing an agent integration."""

    return CompilerRegistry(
        (
            BackendCompiler(
                engine="none",
                version=NO_STATE_COMPILER_VERSION,
                compile=compile_no_state,
            ),
            BackendCompiler(
                engine="postgres",
                version=POSTGRES_COMPILER_VERSION,
                compile=compile_postgres,
            ),
            BackendCompiler(
                engine="sqlite",
                version=SQLITE_COMPILER_VERSION,
                compile=compile_sqlite,
            ),
        )
    )
