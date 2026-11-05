from __future__ import annotations

import ast
import io
import os
import stat
import tokenize
from pathlib import Path

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
_READ_LIMIT = 64 * 1024 * 1024
_FILE_LIMIT = 5_000
_LINE_LIMIT = 64 * 1024
_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
_OTEL_SDK_TRACE = "opentelemetry.sdk.trace"
_OTEL_OTLP_EXPORTER = "opentelemetry.exporter.otlp"
_OTEL_GRPC_EXPORTER = "opentelemetry.exporter.otlp.proto.grpc"


def detect_instrumentation(source_root: Path | None) -> str:
    if source_root is None:
        return "none"
    imported = _python_imports(source_root.resolve())
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


def _python_imports(root: Path) -> set[str]:
    imported: set[str] = set()
    local: set[str] = set()
    files_left = _FILE_LIMIT
    bytes_left = _READ_LIMIT
    for directory, subdirectories, files in os.walk(root):
        if Path(directory) != root and any(
            name.endswith(".dist-info") for name in subdirectories
        ):
            subdirectories[:] = []
            continue
        subdirectories[:] = [
            name for name in subdirectories if name not in _SKIPPED_DIRECTORIES
        ]
        if Path(directory).parent == root and "__init__.py" in files:
            # Only a package at the top of the repository sits ahead of the
            # installed one; app/opentelemetry is imported as app.opentelemetry.
            local.add(Path(directory).name)
        for file_name in files:
            if not file_name.endswith(".py"):
                continue
            if Path(directory) == root:
                # Only a module at the top of the repository sits ahead of the
                # installed package on the import path.
                local.add(file_name[: -len(".py")])
            if files_left <= 0:
                continue
            files_left -= 1
            data = _read_source(Path(directory) / file_name, bytes_left)
            if data is None:
                continue
            bytes_left -= len(data)
            if max(map(len, data.splitlines()), default=0) > _LINE_LIMIT:
                continue
            try:
                encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
                tree = ast.parse(data.decode(encoding))
                imported.update(_imports(tree))
            except (
                LookupError,
                MemoryError,
                RecursionError,
                SyntaxError,
                UnicodeError,
                ValueError,
            ):
                continue
    return {name for name in imported if name.split(".")[0] not in local}


def _read_source(path: Path, limit: int) -> bytes | None:
    try:
        descriptor = os.open(path, _OPEN_FLAGS)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        chunks: list[bytes] = []
        size = 0
        while size <= limit:
            chunk = os.read(descriptor, min(1 << 20, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        return None if size > limit else b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(descriptor)


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
