from __future__ import annotations

import ast
import json
import os
import stat
from pathlib import Path
from typing import Iterable

from .import_lines import import_lines
from .python_source import EscapeBudget, ParseBudget, parsed_or_syntax_error, read_module
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
# A Node agent names its tracing in its package.json. Only a folder with no Python file is read
# so, so a Python agent beside a JavaScript frontend keeps the answer its imports give.
_PACKAGE_JSON = "package.json"
# An honest manifest is a few KB; a larger one is read as no manifest.
_LARGEST_PACKAGE_JSON = 64 * 1024
_DEPENDENCY_FIELDS = ("dependencies", "devDependencies", "optionalDependencies")
_TRACEAI_SCOPE = "@traceai/"
# Runtimes whose built-in Future AGI sink emits traceAI's spans (`fi.span.kind`, the model, the
# token counts), named one by one.
_TRACEAI_PACKAGES = frozenset({"@future-agi/omega-runtime"})
# The Node SDKs whose spans an exporter sends; the web SDK traces a browser, not the agent.
_NODE_OTEL_SDKS = frozenset(
    {
        "@opentelemetry/sdk-trace-base",
        "@opentelemetry/sdk-trace-node",
        "@opentelemetry/sdk-node",
    }
)
# The OTLP exporter whose body the sandbox's Node hook can mark: JSON over HTTP. The protobuf and
# gRPC exporters would send spans no call is stamped on, so they read as untraced, as gRPC does
# for Python.
_NODE_JSON_EXPORTER = "@opentelemetry/exporter-trace-otlp-http"


def detect_instrumentation(source_root: Path | None, reached: Iterable[Path] = ()) -> str:
    """How the agent is traced, from its imports: the files in ``source_root``, and the Python
    files in ``reached``, the code it imports from elsewhere in the repository, under one budget.
    With no Python file to read, from the packages the folder's package.json depends on.
    """
    if source_root is None:
        return "none"
    imported, python_read = _python_imports(source_root.resolve(), reached)
    if not python_read:
        return _node_instrumentation(source_root.resolve())
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


def _node_instrumentation(root: Path) -> str:
    """How a Node agent is traced, from the packages its package.json depends on."""
    dependencies = _package_dependencies(root / _PACKAGE_JSON)
    if dependencies & _TRACEAI_PACKAGES or any(
        name.startswith(_TRACEAI_SCOPE) for name in dependencies
    ):
        return "traceai"
    if dependencies & _NODE_OTEL_SDKS and _NODE_JSON_EXPORTER in dependencies:
        return "opentelemetry"
    return "none"


def _package_dependencies(path: Path) -> set[str]:
    """The package names a manifest depends on; none for a link, a non-file, or one too large to read."""
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return set()
        with path.open("rb") as handle:
            raw = handle.read(_LARGEST_PACKAGE_JSON + 1)
        if len(raw) > _LARGEST_PACKAGE_JSON:
            return set()
        manifest = json.loads(raw)
    except (OSError, ValueError, RecursionError):
        return set()
    if not isinstance(manifest, dict):
        return set()
    names: set[str] = set()
    for field in _DEPENDENCY_FIELDS:
        declared = manifest.get(field)
        if isinstance(declared, dict):
            names.update(name for name in declared if isinstance(name, str))
    return names


def _python_imports(root: Path, reached: Iterable[Path] = ()) -> tuple[set[str], bool]:
    """The modules the Python files import, and whether there was any Python file to read."""
    python_read = False
    imported: set[str] = set()
    local: set[str] = set()
    budget = ParseBudget()
    escapes = EscapeBudget(reader="instrumentation scan")
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
            python_read = True
            if Path(directory) == root:
                # Only a module at the top of the repository sits ahead of the
                # installed package on the import path.
                local.add(file_name[: -len(".py")])
            _read_imports(Path(directory) / file_name, budget, escapes, imported)
    for path in sorted(reached):
        # The folder's own files were read above.
        if path.suffix == ".py" and not is_within(path, root):
            python_read = True
            _read_imports(path, budget, escapes, imported)
    return {name for name in imported if name.split(".")[0] not in local}, python_read


def _read_imports(
    path: Path, budget: ParseBudget, escapes: EscapeBudget, imported: set[str]
) -> None:
    text = read_module(path, needles=_NAMES, escapes=escapes, also=(also := []))
    if text is None:
        return
    # An import and `python file.py` can read a crafted file two ways: each reading on its own.
    for reading in (text, *also):
        if not budget.spend(reading):
            continue
        tree = parsed_or_syntax_error(reading)
        if isinstance(tree, ast.Module):
            imported.update(_imports(tree))
        elif isinstance(tree, SyntaxError):
            # A file the kit's own Python can't parse still imports what it imports: the line
            # search reads its absolute imports, so tracing there is not missed.
            imported.update(name for level, name in import_lines(reading) if not level)


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
