from __future__ import annotations

import ast
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Iterable, Sequence

from .generated_runtime import NODE_LANGUAGES
from .import_lines import import_lines
from . import workspace_glob
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
# The planner's names for Node, and Bun.
_JAVASCRIPT_LANGUAGES = NODE_LANGUAGES | {"bun"}
# A workspace root's member patterns are read up to these bounds.
_WORKSPACE_PATTERNS = 256
_WORKSPACE_PATTERN_LENGTH = 256
_LEADING_SLASH = re.compile(r"^\.?/+")
_JAVASCRIPT_LAUNCHERS = frozenset({"node", "bun"})
# A package manager starts the package the planner builds; the planner names its language.
_PACKAGE_LAUNCHERS = frozenset({"npm", "pnpm", "yarn"})


def detect_instrumentation(
    source_root: Path | None, reached: Iterable[Path] = (), *, manifests: bool = True
) -> str:
    """How the agent is traced, from its imports: the files in ``source_root``, and the Python
    files in ``reached``, the code it imports from elsewhere in the repository, under one budget.
    With no Python file to read, and ``manifests``, from the packages the folder's package.json
    depends on: only while the runtime is unknown, since a Python runtime never runs them.
    """
    if source_root is None:
        return "none"
    imported, python_read = _python_imports(source_root.resolve(), reached)
    if not python_read:
        return _node_instrumentation(source_root.resolve()) if manifests else "none"
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


def detect_node_instrumentation(source_root: Path, workdir: str) -> str:
    """How a Node or Bun agent is traced, whatever other files its folder holds: from its package,
    the nearest package.json at ``runtime.workdir`` or above it, and from the workspace root just
    above that package when the root's ``workspaces`` names it, since a workspace hoists its
    members' packages. A manifest above that names no such member is another project's. Nothing
    above the repository is read.
    """
    root = source_root.resolve()
    folder = (root / (workdir.strip() or ".")).resolve()
    if not folder.is_relative_to(root):
        return "none"
    package = next(
        (candidate for candidate in (folder, *folder.parents)
         if candidate.is_relative_to(root) and _manifest_exists(candidate)),
        None,
    )
    if package is None:
        return "none"
    # An unread manifest (a link, or past the size limit) still marks the package.
    dependencies = _dependencies(_package_manifest(package / _PACKAGE_JSON))
    above = next(
        (candidate for candidate in package.parents
         if candidate.is_relative_to(root) and _manifest_exists(candidate)),
        None,
    )
    if above is not None:
        workspace = _package_manifest(above / _PACKAGE_JSON)
        if _names_member(workspace, package.relative_to(above).as_posix()):
            dependencies |= _dependencies(workspace)
    return _node_answer(dependencies)


def runs_javascript(language: str, command: Sequence[str]) -> bool | None:
    """Whether the selected runtime is Node or Bun: by its language, the planner's names for Node
    included, or with none by its command. None when it names neither, or when a package manager
    starts it: the package it starts decides."""
    language = language.strip().lower()
    if language:
        return language in _JAVASCRIPT_LANGUAGES
    if command:
        launcher = Path(str(command[0])).name.lower()
        return None if launcher in _PACKAGE_LAUNCHERS else launcher in _JAVASCRIPT_LAUNCHERS
    return None


def reads_python(source_root: Path, reached: Iterable[Path] = ()) -> bool:
    """Whether ``detect_instrumentation`` finds a Python file to read here; nothing is parsed."""
    root = source_root.resolve()
    return next(iter(_python_folders(root)), None) is not None or any(
        path.suffix == ".py" and not is_within(path, root) for path in reached
    )


def _manifest_exists(folder: Path) -> bool:
    try:
        (folder / _PACKAGE_JSON).lstat()
    except OSError:
        return False
    return True


def _names_member(workspace: dict[str, Any], member: str) -> bool:
    """Whether a workspace root's ``workspaces`` (a list, or Yarn's ``{"packages": [...]}``)
    names the member folder, written relative to the root, as npm's ``@npmcli/map-workspaces``
    maps it. A positive pattern takes back, whole, a preceding negation that its text matches; a
    negation still standing then drops each positive whose text it matches, and ignores the
    folders it matches. A field that costs more than the budget to read names no member."""
    patterns = workspace.get("workspaces")
    if isinstance(patterns, dict):
        patterns = patterns.get("packages")
    if not isinstance(patterns, list) or "node_modules" in member.split("/"):
        return False
    budget = workspace_glob.Budget()
    try:
        return _maps_member(patterns[:_WORKSPACE_PATTERNS], member, budget)
    except workspace_glob.TooCostly:
        return False


def _maps_member(patterns: list[Any], member: str, budget: workspace_glob.Budget) -> bool:
    def matches(pattern: str, path: str, *, folder: bool = False, dot: bool = False) -> bool:
        return workspace_glob.matches(pattern, path, budget, dot=dot, folder=folder)

    included: list[str] = []
    excluded: list[str] = []
    for pattern in patterns:
        if not isinstance(pattern, str) or len(pattern) > _WORKSPACE_PATTERN_LENGTH:
            continue
        bangs = len(pattern) - len(pattern.lstrip("!"))
        # An odd number of `!` negates; a leading `/` or `./` is dropped.
        spelled = _LEADING_SLASH.sub("", pattern[bangs:])
        if bangs % 2:
            excluded.append(spelled)
            continue
        index = 0
        while index < len(excluded):
            if matches(excluded[index], spelled):
                # npm removes it in place and steps on, so the negation after it is not tested.
                del excluded[index]
            index += 1
        included.append(spelled)
    included = [
        pattern.replace("\\", "/")
        for pattern in included
        if pattern and not any(matches(negation, pattern) for negation in excluded)
    ]
    return any(matches(pattern, member, folder=True) for pattern in included) and not any(
        matches(negation, member, folder=True, dot=True) for negation in excluded
    )


def _node_instrumentation(root: Path) -> str:
    """How a Node agent is traced, from the packages its package.json depends on."""
    return _node_answer(_dependencies(_package_manifest(root / _PACKAGE_JSON)))


def _node_answer(dependencies: set[str]) -> str:
    if dependencies & _TRACEAI_PACKAGES or any(
        name.startswith(_TRACEAI_SCOPE) for name in dependencies
    ):
        return "traceai"
    if dependencies & _NODE_OTEL_SDKS and _NODE_JSON_EXPORTER in dependencies:
        return "opentelemetry"
    return "none"


def _package_manifest(path: Path) -> dict[str, Any]:
    """A package.json's fields; none for a link, a non-file, or one too large to read."""
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return {}
        with path.open("rb") as handle:
            raw = handle.read(_LARGEST_PACKAGE_JSON + 1)
        if len(raw) > _LARGEST_PACKAGE_JSON:
            return {}
        manifest = json.loads(raw)
    except (OSError, ValueError, RecursionError):
        return {}
    return manifest if isinstance(manifest, dict) else {}


def _dependencies(manifest: dict[str, Any]) -> set[str]:
    """The package names a manifest depends on."""
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
    for directory, files in _python_folders(root, every=True):
        if directory.parent == root and "__init__.py" in files:
            # Only a package at the top of the repository sits ahead of the
            # installed one; app/opentelemetry is imported as app.opentelemetry.
            local.add(directory.name)
        for file_name in sorted(files):
            if not file_name.endswith(".py"):
                continue
            python_read = True
            if directory == root:
                # Only a module at the top of the repository sits ahead of the
                # installed package on the import path.
                local.add(file_name[: -len(".py")])
            _read_imports(directory / file_name, budget, escapes, imported)
    for path in sorted(reached):
        # The folder's own files were read above.
        if path.suffix == ".py" and not is_within(path, root):
            python_read = True
            _read_imports(path, budget, escapes, imported)
    return {name for name in imported if name.split(".")[0] not in local}, python_read


def _python_folders(root: Path, *, every: bool = False) -> Iterable[tuple[Path, list[str]]]:
    """The folders the scan reads, each with its files: those holding a ``.py`` file, or with
    ``every`` all of them. An installed package (a ``.dist-info`` beside it) and the skipped
    names are not entered."""
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
        if every or any(name.endswith(".py") for name in files):
            yield Path(directory), files


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
