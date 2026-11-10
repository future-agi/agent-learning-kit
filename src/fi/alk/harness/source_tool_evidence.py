"""Reject demonstrably non-executable Python tool examples without importing source.

This is a negative-evidence gate, not a general static registration detector. Dynamic tools,
external packages and non-Python implementations still require runtime proof.
"""

from __future__ import annotations

import ast
import re
import tokenize
from pathlib import Path

from .contract import AgentContract
from .python_source import ParseBudget, TokenBudget, parsed, read_module
from .runtime_selection import charged_path
from .source_folder import SubfolderNotFound, agent_folder, is_within

_NOT_AGENT_CODE = {"tests", "test", "node_modules", "site-packages", "venv", "__pycache__"}
# Certification runs in the job, not while a conversation waits, so it reads what bundling reads.
_LARGEST_TOOL_MODULE = 1024 * 1024


def _file_in(candidate: Path, root: Path) -> bool:
    """A file, resolving inside ``root``. The resolve is charged to the save's path budget: a
    module's name can be dozens of links. A name past the system's limit can't be a file."""
    try:
        return candidate.is_file() and is_within(charged_path(candidate), root)
    except (OSError, RuntimeError, ValueError):
        return False


def tool_evidence_problems(
    contract: AgentContract, source: Path, subfolder: str | None = None
) -> list[str]:
    root = source.resolve()
    bases = [root, root / "src"]
    if subfolder:
        # The run imports from the agent's folder first, so the check reads what will run.
        try:
            folder = agent_folder(root, subfolder)
        except SubfolderNotFound:
            pass  # The folder check reports this.
        else:
            bases = [folder, folder / "src", *bases]
    problems = []
    # Many entries can name one module; each is read once, and all of them under one budget.
    read: dict[Path, tuple[ast.Module, str] | None] = {}
    commented: dict[Path, list[str] | None] = {}
    budget = ParseBudget()
    tokens = TokenBudget()
    for entry in contract.tool_entrypoints:
        if entry.mode not in {"import", "construct"} or not entry.module:
            continue
        parts = entry.module.split(".")
        if not all(part.isidentifier() for part in parts):
            continue
        module = Path(*parts)
        candidates = [
            base / relative
            for base in bases
            for relative in (module.with_suffix(".py"), module / "__init__.py")
        ]
        path = next((p for p in candidates if _file_in(p, root)), None)
        if path is None:
            continue
        if path not in read:
            text = read_module(path)
            tree = parsed(text) if text is not None and budget.spend(text) else None
            read[path] = None if tree is None else (tree, text)
        if read[path] is None:
            continue  # The actual source interpreter/package validator owns these failures.
        tree, text = read[path]
        name = entry.callable.rsplit(".", 1)[-1]
        if not name.isidentifier():
            continue
        # Assignments/imports may legitimately export a callable without a function definition.
        bindings = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        bindings.update(
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
        )
        bindings.update(
            node.asname or node.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.alias)
        )
        written = re.compile(r"\b(?:async\s+)?def\s+" + re.escape(name) + r"\s*\(")
        # A comment is in the text as written, so only a file with the line is tokenized.
        if name in bindings or not written.search(text):
            continue
        if path not in commented:
            commented[path] = _comments(text, tokens)
        if commented[path] is not None and any(written.search(line) for line in commented[path]):
            problems.append(
                f"tool[{entry.tool}]:commented-only-entrypoint:{entry.module}.{entry.callable} — "
                "the named callable exists only in comments, not executable Python. Remove "
                "the example tool or supply its actual runtime binding. An agent with no "
                "custom tools must use tools=[]; do not invent a tool or backing data."
            )
    return problems


def _comments(text: str, tokens: TokenBudget) -> list[str] | None:
    """The file's comments, or None past the total, which leaves the check unproven."""
    if not tokens.spend(text):
        return None
    try:
        found = tokens.tokens(text)
    except (SyntaxError, tokenize.TokenError):
        return None
    if found is None:
        return None
    return [token.string for token in found if token.type == tokenize.COMMENT]


def defined_tool_names(source: Path) -> set[str]:
    """Names the source's own Python code defines a callable or an explicit tool name for."""
    root = source.resolve()
    names: set[str] = set()
    for path in root.rglob("*.py"):
        parts = path.relative_to(root).parts[:-1]
        if any(part.startswith(".") or part in _NOT_AGENT_CODE for part in parts):
            continue
        text = read_module(path, _LARGEST_TOOL_MODULE)
        if text is None or (tree := parsed(text)) is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name)
            elif (
                isinstance(node, ast.keyword)
                and node.arg == "name"
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                names.add(node.value.value)
    return names
