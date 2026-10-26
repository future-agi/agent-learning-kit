"""Resolve source-backed Python callables for runtime tool tracing."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contract import ToolEntry


class TraceBindingError(RuntimeError):
    """A declared Python tool cannot produce trustworthy runtime evidence."""


_LANGCHAIN_TOOL_METHODS = frozenset({"invoke", "ainvoke"})


def _is_langchain_module(name: str) -> bool:
    return name in {"langchain", "langchain_core"} or name.startswith(
        ("langchain.", "langchain_core.")
    )


def _module_statements(tree: ast.Module) -> list[ast.stmt]:
    statements: list[ast.stmt] = []
    pending = list(reversed(tree.body))
    while pending:
        node = pending.pop()
        statements.append(node)
        nested: list[ast.stmt] = []
        if isinstance(node, ast.If):
            nested = [*node.body, *node.orelse]
        elif isinstance(node, ast.Try):
            nested = [
                *node.body,
                *(item for handler in node.handlers for item in handler.body),
                *node.orelse,
                *node.finalbody,
            ]
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            nested = list(node.body)
        pending.extend(reversed(nested))
    return statements


def _is_langchain_tool_reference(
    target: ast.expr, decorator_names: set[str], module_names: set[str]
) -> bool:
    while isinstance(target, ast.Call):
        target = target.func
    name = ast.unparse(target)
    return name in decorator_names or (
        name.endswith(".tool") and name.split(".", 1)[0] in module_names
    )


def _last_binding(
    tree: ast.Module, name: str, before: ast.stmt | None = None
) -> ast.stmt | None:
    result = None
    for node in _module_statements(tree):
        if node is before:
            break
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [
                item.id
                for target in targets
                for item in ast.walk(target)
                if isinstance(item, ast.Name)
            ]
        elif isinstance(node, (ast.ImportFrom, ast.Import)):
            names = [alias.asname or alias.name.split(".")[0] for alias in node.names]
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "*" for alias in node.names
            ):
                names.append(name)
        elif isinstance(node, ast.Delete):
            names = [
                item.id
                for target in node.targets
                for item in ast.walk(target)
                if isinstance(item, ast.Name)
            ]
        else:
            names = []
        if name in names:
            result = node
    return result


def _langchain_tool_targets(tree: ast.Module, method: str) -> dict[str, str]:
    statements = _module_statements(tree)
    decorator_names: set[str] = set()
    structured_tool_names: set[str] = set()
    module_names = {"langchain", "langchain_core"}
    for node in statements:
        if isinstance(node, ast.ImportFrom) and _is_langchain_module(node.module or ""):
            for alias in node.names:
                if alias.name == "*":
                    decorator_names.add("tool")
                    structured_tool_names.add("StructuredTool")
                elif alias.name == "tool":
                    decorator_names.add(alias.asname or alias.name)
                elif alias.name == "tools":
                    module_names.add(alias.asname or alias.name)
                elif alias.name == "StructuredTool":
                    structured_tool_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _is_langchain_module(alias.name):
                    module_names.add(alias.asname or alias.name.split(".", 1)[0])

    def imported_reference(target: ast.expr, before: ast.stmt) -> bool:
        while isinstance(target, ast.Call):
            target = target.func
        root = ast.unparse(target).split(".", 1)[0]
        binding = _last_binding(tree, root, before)
        if (
            isinstance(binding, (ast.Assign, ast.AnnAssign))
            and isinstance(binding.value, ast.Constant)
            and binding.value.value is None
            and any(
                isinstance(handler, ast.ExceptHandler)
                and isinstance(handler.type, ast.Name)
                and handler.type.id == "ImportError"
                and binding in handler.body
                for handler in ast.walk(tree)
            )
        ):
            binding = _last_binding(tree, root, binding)
        return (
            isinstance(binding, ast.Import)
            and any(
                _is_langchain_module(alias.name)
                and (alias.asname or alias.name.split(".")[0]) == root
                for alias in binding.names
            )
        ) or (
            isinstance(binding, ast.ImportFrom)
            and _is_langchain_module(binding.module or "")
        )

    tools: dict[str, str] = {}
    for node in statements:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if _last_binding(tree, node.name) is not node:
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if _is_langchain_tool_reference(
                target, decorator_names, module_names
            ) and imported_reference(target, node):
                tools[node.name] = node.name
                break

    for node in statements:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        factory = ast.unparse(value.func)
        wraps_function = _is_langchain_tool_reference(
            value.func, decorator_names, module_names
        ) or (
            factory.endswith(".from_function")
            and (
                factory.removesuffix(".from_function") in structured_tool_names
                or (
                    ".StructuredTool.from_function" in factory
                    and factory.split(".", 1)[0] in module_names
                )
            )
        )
        if not wraps_function or not imported_reference(value.func, node):
            continue
        assigned = [
            target.id
            for target in targets
            if isinstance(target, ast.Name) and _last_binding(tree, target.id) is node
        ]
        keywords = {item.arg: item.value for item in value.keywords}
        if factory.endswith(".from_function"):
            sync = keywords.get("func", value.args[0] if value.args else None)
            candidate = (
                keywords.get(
                    "coroutine", value.args[1] if len(value.args) > 1 else None
                )
                if method == "ainvoke"
                else sync
            )
            if (
                candidate is None
                or isinstance(candidate, ast.Constant)
                and candidate.value is None
            ):
                candidate = sync
            candidates = [candidate]
        else:
            candidates = value.args
        wrapped = next(
            (
                candidate.id
                for candidate in candidates
                if isinstance(candidate, ast.Name)
                and isinstance(
                    _last_binding(tree, candidate.id, node),
                    (ast.FunctionDef, ast.AsyncFunctionDef),
                )
            ),
            None,
        )
        if wrapped is not None:
            tools.update({name: wrapped for name in assigned})
    return tools


def _trace_source_roots(source: Path, component: Path) -> tuple[Path, ...]:
    component = component.resolve()
    if not component.is_relative_to(source) or not component.is_dir():
        raise TraceBindingError("runtime_component_invalid")
    roots = (component, component / "src", source, source / "src")
    return tuple(dict.fromkeys(root.resolve() for root in roots if root.is_dir()))


@dataclass(frozen=True)
class _TraceModule:
    tree: ast.Module
    trace_name: str
    is_package: bool


def _module_tree(roots: tuple[Path, ...], module_name: str) -> _TraceModule | None:
    module = Path(*module_name.split("."))
    for root in roots:
        candidates = (
            (root / module.with_suffix(".py"), False),
            (root / module / "__init__.py", True),
        )
        for path, is_package in candidates:
            if not path.is_file() or not path.resolve().is_relative_to(root):
                continue
            try:
                return _TraceModule(
                    tree=ast.parse(path.read_text(encoding="utf-8")),
                    trace_name=(
                        f"{module_name}.__init__" if is_package else module_name
                    ),
                    is_package=is_package,
                )
            except (OSError, UnicodeError, SyntaxError):
                return None
    return None


def _relative_module(
    current: str, imported: str | None, level: int, *, is_package: bool
) -> str:
    if level == 0:
        return imported or ""
    package = current.split(".") if is_package else current.split(".")[:-1]
    keep = len(package) - level + 1
    if keep < 0:
        return ""
    parts = [*package[:keep], *((imported or "").split(".") if imported else [])]
    return ".".join(part for part in parts if part)


def _reexported_name(
    tree: ast.Module, module_name: str, name: str, *, is_package: bool
) -> tuple[str, str] | None:
    for node in _module_statements(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if _last_binding(tree, name) is not node:
            continue
        for alias in node.names:
            if (alias.asname or alias.name) != name or alias.name == "*":
                continue
            imported_module = _relative_module(
                module_name, node.module, node.level, is_package=is_package
            )
            if imported_module:
                return imported_module, alias.name
    return None


def _owned_tool_method(tree: ast.Module, name: str, method: str) -> str | None:
    statements = _module_statements(tree)
    classes = {node.name: node for node in statements if isinstance(node, ast.ClassDef)}
    binding = _last_binding(tree, name)
    owner = name if isinstance(binding, ast.ClassDef) else None
    for node in statements:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if node is not binding:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == name for target in targets
        ):
            continue
        value = node.value
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in classes
        ):
            owner = value.func.id
            break
    if owner is None:
        return None
    method_node = next(
        (
            item
            for item in classes[owner].body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == method
        ),
        None,
    )
    if method_node is not None:
        decorators = {
            decorator.id
            for decorator in method_node.decorator_list
            if isinstance(decorator, ast.Name)
        }
        if not method_node.decorator_list or (
            len(method_node.decorator_list) == 1 and decorators == {"classmethod"}
        ):
            return f"{owner}.{method}"
    return None


def _unobservable_trace_binding(entry: ToolEntry) -> TraceBindingError:
    return TraceBindingError(
        f"contract_tool_trace_unobservable: {entry.tool}: callable "
        f"{entry.callable!r} does not identify a source function or source-owned method; "
        "bind the underlying module-level source function"
    )


def _observable_trace_binding(
    roots: tuple[Path, ...], entry: ToolEntry
) -> tuple[str, str]:
    if entry.mode != "import":
        found = _module_tree(roots, entry.module)
        return (found.trace_name if found else entry.module), entry.callable
    parts = entry.callable.rsplit(".", 1)
    if len(parts) != 2:
        found = _module_tree(roots, entry.module)
        return (found.trace_name if found else entry.module), entry.callable
    bound_name, method = parts
    if not bound_name.isidentifier() or not all(
        part.isidentifier() for part in entry.module.split(".")
    ):
        raise _unobservable_trace_binding(entry)

    module_name, object_name = entry.module, bound_name
    seen: set[tuple[str, str]] = set()
    while (module_name, object_name) not in seen:
        seen.add((module_name, object_name))
        found = _module_tree(roots, module_name)
        if found is None:
            break
        if method in _LANGCHAIN_TOOL_METHODS:
            target = _langchain_tool_targets(found.tree, method).get(object_name)
            if target is not None:
                return found.trace_name, target
        owned_method = _owned_tool_method(found.tree, object_name, method)
        if owned_method is not None:
            return found.trace_name, owned_method
        reexport = _reexported_name(
            found.tree,
            module_name,
            object_name,
            is_package=found.is_package,
        )
        if reexport is None:
            break
        module_name, object_name = reexport
    raise _unobservable_trace_binding(entry)


def trace_bindings(
    contract: dict[str, Any], source: Path, component: Path
) -> list[dict[str, str]]:
    roots = _trace_source_roots(source, component)
    bindings: list[dict[str, str]] = []
    for raw in contract.get("tool_entrypoints", []):
        if not isinstance(raw, dict):
            continue
        entry = ToolEntry.model_validate(raw)
        if (
            entry.mode not in {"import", "construct"}
            or not entry.module
            or not entry.callable
        ):
            continue
        module, callable_name = _observable_trace_binding(roots, entry)
        bindings.append(
            {"name": entry.tool, "module": module, "callable": callable_name}
        )
    return bindings
