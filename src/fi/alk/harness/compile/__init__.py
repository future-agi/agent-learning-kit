"""Deterministic compilers from World IR to backend-native operations."""

from .base import BackendCompiler, CompilerRegistry, CompilerUnavailable
from .no_state import NO_STATE_COMPILER_VERSION, NoStateCompileResult, compile_no_state
from .postgres import (
    POSTGRES_COMPILER_VERSION,
    CompileDecision,
    PostgresCompileError,
    PostgresCompileResult,
    PostgresInsert,
    apply_postgres,
    compile_postgres,
)
from .sqlite import (
    SQLITE_COMPILER_VERSION,
    SQLiteCompileError,
    SQLiteCompileResult,
    SQLiteInsert,
    apply_sqlite,
    compile_sqlite,
)

__all__ = [
    "BackendCompiler",
    "CompilerRegistry",
    "CompilerUnavailable",
    "NO_STATE_COMPILER_VERSION",
    "NoStateCompileResult",
    "compile_no_state",
    "POSTGRES_COMPILER_VERSION",
    "CompileDecision",
    "PostgresCompileError",
    "PostgresCompileResult",
    "PostgresInsert",
    "apply_postgres",
    "compile_postgres",
    "SQLITE_COMPILER_VERSION",
    "SQLiteCompileError",
    "SQLiteCompileResult",
    "SQLiteInsert",
    "apply_sqlite",
    "compile_sqlite",
]
