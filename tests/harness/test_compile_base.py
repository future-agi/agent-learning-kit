from __future__ import annotations

import pytest

from fi.alk.harness.compile.base import (
    BackendCompiler,
    CompilerRegistry,
    CompilerUnavailable,
)


def test_registry_selects_backend_without_agent_or_modality_branches() -> None:
    sentinel = object()

    def compile_world(_source, _world):
        return sentinel

    registry = CompilerRegistry(
        (BackendCompiler(engine="future_store", version="v1", compile=compile_world),)
    )

    selected = registry.resolve(" FUTURE_STORE ")

    assert selected.version == "v1"
    assert selected.compile(None, None) is sentinel


def test_registry_rejects_unknown_backend_with_typed_error() -> None:
    with pytest.raises(CompilerUnavailable) as captured:
        CompilerRegistry().resolve("new_store")

    assert captured.value.code == "backend_compiler_unavailable"


def test_registry_rejects_ambiguous_backend_registration() -> None:
    adapter = BackendCompiler(engine="store", version="v1", compile=lambda *_: None)
    registry = CompilerRegistry((adapter,))

    with pytest.raises(ValueError, match="backend_compiler_duplicate"):
        registry.register(adapter)
