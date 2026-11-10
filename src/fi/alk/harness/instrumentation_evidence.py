from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Iterable

from .python_source import EscapeBudget, ParseBudget, parsed, read_module
from .source_folder import is_within

_SKIPPED_DIRECTORIES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "env",
    "node_modules",
    "site-packages",
    "venv",
}
# A file that names none of these can't import them, so it isn't parsed.
_NAMES = (b"fi_instrumentation", b"traceai_", b"opentelemetry")
_OTEL_SDK_TRACE = "opentelemetry.sdk.trace"
_OTEL_OTLP_EXPORTER = "opentelemetry.exporter.otlp"
_OTEL_GRPC_EXPORTER = "opentelemetry.exporter.otlp.proto.grpc"


def detect_instrumentation(source_root: Path | None, reached: Iterable[Path] = ()) -> str:
    """How the agent is traced, from its imports: the files in ``source_root``, and the Python
    files in ``reached``, the code it imports from elsewhere in the repository, under one budget.
    """
    if source_root is None:
        return "none"
    imported = _python_imports(source_root.resolve(), reached)
    if any(
        name.split(".")[0] == "fi_instrumentation"
        or name.split(".")[0].startswith("traceai_")
        for name in imported
    ):
        return "traceai"
    if not any(_within(name, _OTEL_SDK_TRACE) for name in imported):
        return "none"
    # Only OTLP exporters decide the protocol; a console, Zipkin or Prometheus
    # exporter next to the gRPC one still leaves the gRPC exporter as the only OTLP one.
    exporters = {name for name in imported if _within(name, _OTEL_OTLP_EXPORTER)}
    leaves = {
        name
        for name in exporters
        if not any(other.startswith(name + ".") for other in exporters)
    }
    if leaves and all(_within(name, _OTEL_GRPC_EXPORTER) for name in leaves):
        return "none"
    return "opentelemetry"


def _python_imports(root: Path, reached: Iterable[Path] = ()) -> set[str]:
    imported: set[str] = set()
    local: set[str] = set()
    budget = ParseBudget()
    escapes = EscapeBudget()
    for directory, subdirectories, files in os.walk(root):
        if Path(directory) != root and any(
            name.endswith(".dist-info") for name in subdirectories
        ):
            subdirectories[:] = []
            continue
        # In name order: past the total, what's read mustn't depend on how the disk lists it.
        subdirectories[:] = sorted(
            name for name in subdirectories if name not in _SKIPPED_DIRECTORIES
        )
        if Path(directory).parent == root and "__init__.py" in files:
            # Only a package at the top of the repository sits ahead of the
            # installed one; app/opentelemetry is imported as app.opentelemetry.
            local.add(Path(directory).name)
        for file_name in sorted(files):
            if not file_name.endswith(".py"):
                continue
            if Path(directory) == root:
                # Only a module at the top of the repository sits ahead of the
                # installed package on the import path.
                local.add(file_name[: -len(".py")])
            _read_imports(Path(directory) / file_name, budget, escapes, imported)
    for path in sorted(reached):
        # The folder's own files were read above.
        if path.suffix == ".py" and not is_within(path, root):
            _read_imports(path, budget, escapes, imported)
    return {name for name in imported if name.split(".")[0] not in local}


def _read_imports(
    path: Path, budget: ParseBudget, escapes: EscapeBudget, imported: set[str]
) -> None:
    text = read_module(path, needles=_NAMES, escapes=escapes)
    if text is None or not budget.spend(text):
        return
    if (tree := parsed(text)) is not None:
        imported.update(_imports(tree))


def _imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    # An explicit stack, because a generated file can nest deeper than the
    # interpreter's recursion limit.
    pending = [tree]
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If) and _is_type_checking(child.test):
                pending.extend(child.orelse)
                continue
            pending.append(child)
    return found


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _within(name: str, package: str) -> bool:
    return name == package or name.startswith(package + ".")
