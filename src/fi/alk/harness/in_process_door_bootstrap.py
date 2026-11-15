"""The world's door, inside an agent that runs its tools in its own process.

Imported by the ALK `sitecustomize` hook when the bundle sets `ALK_WORLD_DOOR_URL`. Each contract tool the bundle
binds (`ALK_TOOL_TRACE_BINDINGS`: tool name, module, callable) is wrapped so every call asks the world's door first
(`in_process_door.py`): the scenario's fault can hold it before it runs, or lose its answer after; the door logs what
came of it in the world's action log, beside every other surface's calls.

The wrapper goes on as the function is defined, not after: the tool's module is compiled with one more decorator on
the tool's `def`, the innermost, so a framework decorator above it (`@tool`, `@function_tool`) receives the wrapped
function and keeps it. A module whose source cannot be read or changed is patched by attribute after it runs, which
a framework that already copied the function escapes; those calls then run outside the door, and the run says the
faults set on them were not applied. A tool defined in the script started as `__main__` is not wrapped either.

Never alters a tool otherwise: its signature, name and documentation are kept (`functools.wraps`), its answer and
its own errors are passed through as they are, and a door that cannot be reached lets every call run as without it.
Standard library only: the customer's interpreter has nothing of ALK's.
"""

from __future__ import annotations

import ast
import asyncio
import functools
import importlib.abc
import importlib.machinery
import inspect
import json
import os
import sys
import threading
import time
from typing import Any
from urllib import request

URL = os.environ.get("ALK_WORLD_DOOR_URL", "").rstrip("/")
DECORATOR = "__alk_world_door__"
_TIMEOUT_SECONDS = 35.0


class ToolTimedOut(TimeoutError):
    """What the agent's tool raises when the scenario's fault times its call out."""


class ToolUnavailable(ConnectionError):
    """What the agent's tool raises when the scenario's fault says its service is down."""


class ToolFailed(RuntimeError):
    """What the agent's tool raises when the scenario's fault answers with an error status."""


def _post(path: str, body: dict[str, Any]) -> dict[str, Any] | None:
    try:
        outgoing = request.Request(
            URL + path,
            data=json.dumps(body, default=str).encode(),
            method="POST",
            headers={"content-type": "application/json"},
        )
        with request.urlopen(outgoing, timeout=_TIMEOUT_SECONDS) as response:
            answer = json.loads(response.read() or b"{}")
        return answer if isinstance(answer, dict) else None
    except Exception:  # noqa: BLE001 - a door out of reach lets the call run as without it
        return None


def _arguments(function: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        bound = inspect.signature(function).bind_partial(*args, **kwargs)
        named = dict(bound.arguments)
    except (TypeError, ValueError):
        named = {**{f"arg{index}": value for index, value in enumerate(args)}, **kwargs}
    named.pop("self", None)
    named.pop("cls", None)
    return json.loads(json.dumps(named, default=str))


def _held(decision: dict[str, Any]) -> BaseException:
    mode = str(decision.get("mode") or "")
    detail = str(decision.get("detail") or "")
    if mode == "unavailable":
        return ToolUnavailable("the service is unavailable (503)")
    if mode == "error":
        return ToolFailed(f"error {decision.get('status')}: {detail}")
    return ToolTimedOut("the request timed out")


def _delay(answer: dict[str, Any]) -> float:
    try:
        return min(max(float(answer.get("delay_seconds") or 0.0), 0.0), 30.0)
    except (TypeError, ValueError):
        return 0.0


def _report_in_background(call_id: str, exc: BaseException) -> None:
    threading.Thread(
        target=_post, args=("/end", {"id": call_id, "ok": False, "error": repr(exc)}), daemon=True
    ).start()


def door(tool: str) -> Any:
    """The decorator that puts the world's door in front of one tool."""

    def wrap(function: Any) -> Any:
        if getattr(function, "__alk_world_door__", False) or not callable(function):
            return function
        if inspect.isgeneratorfunction(function) or inspect.isasyncgenfunction(function):
            # A tool that streams has no one answer to hold or lose; it runs as without a door.
            return function
        if inspect.iscoroutinefunction(function):

            @functools.wraps(function)
            async def held_async(*args: Any, **kwargs: Any) -> Any:
                decision = await asyncio.to_thread(
                    _post, "/begin", {"name": tool, "arguments": _arguments(function, args, kwargs)}
                )
                if decision is not None and decision.get("run") is False:
                    if decision.get("mode") == "timeout_before_commit":
                        await asyncio.sleep(_delay(decision))
                    raise _held(decision)
                call_id = str((decision or {}).get("id") or "")
                try:
                    result = await function(*args, **kwargs)
                except Exception as exc:
                    if call_id:
                        await asyncio.to_thread(_post, "/end", {"id": call_id, "ok": False, "error": repr(exc)})
                    raise
                except BaseException as exc:
                    # Cancelled (or interrupted): told without awaiting, or the door waits out the call for minutes.
                    if call_id:
                        _report_in_background(call_id, exc)
                    raise
                if call_id:
                    answer = await asyncio.to_thread(_post, "/end", {"id": call_id, "ok": True, "result": result})
                    if answer is not None and answer.get("lost"):
                        await asyncio.sleep(_delay(answer))
                        raise ToolTimedOut("the request timed out")
                return result

            held_async.__alk_world_door__ = True  # type: ignore[attr-defined]
            return held_async

        @functools.wraps(function)
        def held(*args: Any, **kwargs: Any) -> Any:
            decision = _post("/begin", {"name": tool, "arguments": _arguments(function, args, kwargs)})
            if decision is not None and decision.get("run") is False:
                if decision.get("mode") == "timeout_before_commit":
                    time.sleep(_delay(decision))
                raise _held(decision)
            call_id = str((decision or {}).get("id") or "")
            try:
                result = function(*args, **kwargs)
            except BaseException as exc:
                if call_id:
                    _post("/end", {"id": call_id, "ok": False, "error": repr(exc)})
                raise
            if call_id:
                answer = _post("/end", {"id": call_id, "ok": True, "result": result})
                if answer is not None and answer.get("lost"):
                    time.sleep(_delay(answer))
                    raise ToolTimedOut("the request timed out")
            return result

        held.__alk_world_door__ = True  # type: ignore[attr-defined]
        return held

    return wrap


def decorate_source(source: str, targets: dict[str, str]) -> ast.Module | None:
    """The module's code with the door as the innermost decorator of each target: `targets` maps a callable's path
    in the module (`issue_refund`, `Tools.issue_refund`) to its tool name. None when no target is found."""
    return _decorate(source, targets)[0]


def _decorate(source: str, targets: dict[str, str]) -> tuple[ast.Module | None, set[str]]:
    """`decorate_source`, and which target paths got the decorator."""
    tree = ast.parse(source)
    decorated: set[str] = set()

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                visit(node.body, f"{prefix}{node.name}.")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                tool = targets.get(f"{prefix}{node.name}")
                if tool is None:
                    continue
                call = ast.Call(func=ast.Name(id=DECORATOR, ctx=ast.Load()), args=[ast.Constant(value=tool)], keywords=[])
                # Last in the list is applied first: the framework's decorators receive the wrapped function.
                node.decorator_list.append(ast.copy_location(call, node))
                decorated.add(f"{prefix}{node.name}")

    visit(tree.body, "")
    if not decorated:
        return None, decorated
    return ast.fix_missing_locations(tree), decorated


def _patch_attributes(module: Any, targets: dict[str, str]) -> None:
    """After the module ran: wrap each target found by attribute (a framework holding the original escapes)."""
    for path, tool in targets.items():
        owner: Any = module
        parts = path.split(".")
        try:
            for part in parts[:-1]:
                owner = getattr(owner, part)
            current = getattr(owner, parts[-1])
        except AttributeError:
            continue
        if getattr(current, "__alk_world_door__", False):
            continue
        try:
            setattr(owner, parts[-1], door(tool)(current))
        except (AttributeError, TypeError):
            continue


class _DoorLoader(importlib.abc.Loader):
    def __init__(self, original: Any, targets: dict[str, str]) -> None:
        self.original = original
        self.targets = targets

    def create_module(self, spec: Any) -> Any:
        return self.original.create_module(spec) if hasattr(self.original, "create_module") else None

    def exec_module(self, module: Any) -> None:
        name = module.__spec__.name if module.__spec__ else module.__name__
        try:
            source = self.original.get_source(name)
            tree, decorated = _decorate(source, self.targets) if source else (None, set())
        except Exception:  # noqa: BLE001 - unreadable or unparseable: patched by attribute instead
            tree, decorated = None, set()
        if tree is None:
            self.original.exec_module(module)
            _patch_attributes(module, self.targets)
            return
        module.__dict__[DECORATOR] = door
        filename = getattr(module, "__file__", None) or name
        exec(compile(tree, filename, "exec"), module.__dict__)  # noqa: S102 - the module's own code, one decorator more
        # A tool not written as a plain `def` (made by a factory, defined under `try`) is wrapped by attribute.
        _patch_attributes(module, {path: tool for path, tool in self.targets.items() if path not in decorated})

    def get_source(self, name: str) -> Any:
        return self.original.get_source(name)


class _DoorFinder(importlib.abc.MetaPathFinder):
    """Finds the bound tools' modules as Python would, and loads them with the door on their tools."""

    def __init__(self, modules: dict[str, dict[str, str]]) -> None:
        self.modules = modules

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        targets = self.modules.get(fullname)
        if not targets:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None or not hasattr(spec.loader, "get_source"):
            return spec
        spec.loader = _DoorLoader(spec.loader, targets)
        return spec


def bindings() -> dict[str, dict[str, str]]:
    """The bound tools by module: `{module: {callable path: tool name}}`."""
    try:
        listed = json.loads(os.environ.get("ALK_TOOL_TRACE_BINDINGS", "[]"))
    except ValueError:
        return {}
    modules: dict[str, dict[str, str]] = {}
    for item in listed if isinstance(listed, list) else []:
        if not isinstance(item, dict):
            continue
        name, module, path = item.get("name"), item.get("module"), item.get("callable")
        if all(isinstance(value, str) and value for value in (name, module, path)):
            modules.setdefault(module, {})[path] = name
    return modules


def install() -> bool:
    """Put the door in front of the bound tools; False when there is no door or nothing to bind."""
    if not URL:
        return False
    modules = bindings()
    if not modules:
        return False
    for name, targets in modules.items():
        loaded = sys.modules.get(name)
        if loaded is not None:
            _patch_attributes(loaded, targets)
    sys.meta_path.insert(0, _DoorFinder(modules))
    return True
