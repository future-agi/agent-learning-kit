"""Which environment variables the agent's own code gives a default (TH-10401).

A reading lists the variables the agent needs in ``runtime.required_environment``, and the
platform asks the person for each one. A variable the code reads only with a default, such as
``os.getenv("ANTHROPIC_MODEL", "vertex_ai/gemini-3.7-flash")`` or
``os.environ.get("PAGE_TURNER_MODEL") or "gpt-4o-mini"``, works without one, so it is not
required. Nothing is imported or run: the files are parsed, under the scans' parse budget.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

from .python_source import ParseBudget, parsed, read_module

_NOT_AGENT_CODE = {"tests", "test", "node_modules", "site-packages", "venv", "__pycache__"}
_GETS = {
    "os.getenv",
    "getenv",
    "os.environ.get",
    "environ.get",
    "os.environ.setdefault",
    "environ.setdefault",
}
_ENVIRON = {"os.environ", "environ"}


def defaulted_names(root: Path, files: Iterable[Path] = ()) -> set[str]:
    """The names every read of which, in ``root``'s Python files and ``files``, has a default.

    A name read anywhere without one is not in the set: ``os.environ["X"]``, or ``os.getenv("X")``
    with nothing after it, or with ``None`` or ``""``, which say "unset". Nor is a name written in
    a file that doesn't parse, since what that file does with it can't be read; and nothing is
    in it when the parse budget runs out.
    """
    root = root.resolve()
    paths = [
        path
        for path in sorted(root.rglob("*.py"))
        if not any(
            part.startswith(".") or part in _NOT_AGENT_CODE
            for part in path.relative_to(root).parts[:-1]
        )
    ]
    paths.extend(path for path in files if path.suffix == ".py" and path not in paths)
    budget = ParseBudget()
    defaulted: set[str] = set()
    plain: set[str] = set()
    unread: list[str] = []
    for path in paths:
        text = read_module(path)
        if text is None:
            continue
        if not budget.spend(text):
            return set()
        tree = parsed(text)
        if tree is None:
            unread.append(text)
            continue
        _reads(tree, defaulted, plain)
    return {
        name
        for name in defaulted - plain
        if not any(name in text for text in unread)
    }


def _reads(tree: ast.Module, defaulted: set[str], plain: set[str]) -> None:
    # A read followed by ``or <something>`` falls back to what follows it.
    or_left = {
        id(value): after
        for node in ast.walk(tree)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)
        for value, after in zip(node.values, node.values[1:])
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _dotted(node.func) in _GETS:
            name = _constant_name(node.args[0] if node.args else None)
            if name is None:
                continue
            default = (
                node.args[1]
                if len(node.args) > 1
                else next(
                    (one.value for one in node.keywords if one.arg in {"default", "value"}), None
                )
            )
            if default is None or _no_value(default):
                default = or_left.get(id(node), default)
            # Falling back to another variable is "one of these", not a default.
            if default is not None and not _no_value(default) and not _reads_environment(default):
                defaulted.add(name)
            else:
                plain.add(name)
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Load)
            and _dotted(node.value) in _ENVIRON
        ):
            name = _constant_name(node.slice)
            if name is not None:
                plain.add(name)


def _constant_name(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _reads_environment(node: ast.AST) -> bool:
    return any(
        (isinstance(one, ast.Call) and _dotted(one.func) in _GETS)
        or (isinstance(one, ast.Subscript) and _dotted(one.value) in _ENVIRON)
        for one in ast.walk(node)
    )


def _no_value(node: ast.AST) -> bool:
    # ``None`` and ``""`` are how code says "unset": ``os.getenv("API_KEY", "")`` still needs it.
    return isinstance(node, ast.Constant) and node.value in (None, "")


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else ""
    return ""
