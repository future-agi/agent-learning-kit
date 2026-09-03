"""Reading a scenario's ``setup(world)``, which is how it stands its own state up.

The folder writer always leaves a ``setup.py`` behind, and when a scenario changes nothing that
file holds a docstring saying so. Two separate places needed to tell that placeholder apart from
real setup, and both grew their own copy of the same walk. One copy decided whether a scenario
seeds anything; the other decided whether two scenarios seed the *same* thing. They are the same
question asked twice, so they share an answer here.
"""

from __future__ import annotations

import ast


def _real_statements(source: str) -> list[ast.stmt] | None:
    """The statements of the first function that are not a docstring or ``pass``.

    ``None`` when the source will not parse or holds no function at all, which the callers treat
    differently: unparseable code is somebody's real attempt and the proof gates report it
    properly, while a signature falls back to comparing the text.
    """
    text = (source or "").strip()
    if not text:
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    function = next(
        (node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))),
        None,
    )
    if function is None:
        return None
    return [
        node
        for node in function.body
        if not isinstance(node, ast.Pass)
        and not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
    ]


def changes_the_world(source: str) -> bool:
    """Whether this setup does something, rather than merely existing."""
    statements = _real_statements(source)
    if statements is None:
        # Unparseable is a real attempt, and the proof gates will say what is wrong with it.
        return bool((source or "").strip())
    return bool(statements)


def fingerprint(source: str) -> str:
    """What this setup does, comparably, so two scenarios seeding the same state can be spotted.

    Empty when the setup does nothing, so placeholders do not all look like each other.
    """
    if not (source or "").strip():
        return ""
    statements = _real_statements(source)
    if statements is None:
        return " ".join(source.split())
    if not statements:
        return ""
    return ast.dump(ast.Module(body=statements, type_ignores=[]))
