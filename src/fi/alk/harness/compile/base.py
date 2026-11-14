"""Capability-neutral registry for deterministic environment compilers.

The understanding and repair layers select a compiler by discovered backend capability. They do
not import provider, framework, modality, or agent-specific code. New stores register an adapter
without changing the generic pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..source_model import SourceModel
from ..world_ir import WorldIR


class CompilerUnavailable(ValueError):
    def __init__(self, engine: str) -> None:
        self.engine = engine
        self.code = "backend_compiler_unavailable"
        self.message = f"no deterministic compiler is registered for backend {engine!r}"
        super().__init__(f"{self.code}: {self.message}")


@dataclass(frozen=True, slots=True)
class BackendCompiler:
    engine: str
    version: str
    compile: Callable[[SourceModel, WorldIR], Any]

    def __post_init__(self) -> None:
        normalized = self.engine.strip().lower()
        if not normalized or normalized != self.engine:
            raise ValueError("backend_compiler_engine_not_canonical")
        if not self.version.strip():
            raise ValueError("backend_compiler_version_missing")


class CompilerRegistry:
    """Closed-at-runtime, extensible-at-composition compiler selection."""

    def __init__(self, compilers: tuple[BackendCompiler, ...] = ()) -> None:
        self._compilers: dict[str, BackendCompiler] = {}
        for compiler in compilers:
            self.register(compiler)

    def register(self, compiler: BackendCompiler) -> None:
        if compiler.engine in self._compilers:
            raise ValueError(f"backend_compiler_duplicate: {compiler.engine}")
        self._compilers[compiler.engine] = compiler

    def resolve(self, engine: str) -> BackendCompiler:
        normalized = engine.strip().lower()
        try:
            return self._compilers[normalized]
        except KeyError as exc:
            raise CompilerUnavailable(normalized) from exc


__all__ = ["BackendCompiler", "CompilerRegistry", "CompilerUnavailable"]
