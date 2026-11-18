"""The world's door, inside an agent that runs its tools in its own process.

Standard library only: the customer's interpreter has nothing of ALK's.

A stated framework exception: `WORKER_RUNS` names framework internals, because a worker can only be stopped at its
deadline where its framework runs it as a task of its own. A worker those entries miss runs on past its deadline, is
not listed as stopped, and its deadline is recorded as not enforced."""

from __future__ import annotations

import ast
import asyncio
import builtins
import functools
import importlib.abc
import importlib.machinery
import inspect
import json
import os
import sys
import threading
import time
import typing
from typing import Any
from urllib import request

URL = os.environ.get("ALK_WORLD_DOOR_URL", "").rstrip("/")
DECORATOR = "__alk_world_door__"
_TIMEOUT_SECONDS = 35.0
# Longest a worker's run is given to end once cancelled; one still running is not stopped.
_CANCEL_SECONDS = 5.0


class ToolTimedOut(TimeoutError):
    """What the agent's tool raises when the scenario's fault times its call out."""


class ToolUnavailable(ConnectionError):
    """What the agent's tool raises when the scenario's fault says its service is down."""


class ToolFailed(RuntimeError):
    """What the agent's tool raises when the scenario's fault answers with an error status."""


class WorkerStopped(TimeoutError):
    """What a worker's run raises to its coordinator when the door stopped it at its deadline."""


class ToolRefused(PermissionError):
    """What the agent's tool raises when the world refuses the call on purpose, such as an approval not given, with
    the world's reason."""


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


# Each tool's calls that ran while the door was out of reach, told to the door on the tool's next call, so the trial's
# evidence says they went unrecorded.
_UNRECORDED: dict[str, int] = {}
_UNRECORDED_LOCK = threading.Lock()


def _begin(tool: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    with _UNRECORDED_LOCK:
        missed = _UNRECORDED.pop(tool, 0)
    decision = _post("/begin", {"name": tool, "arguments": arguments, **({"unrecorded": missed} if missed else {})})
    if decision is None:
        with _UNRECORDED_LOCK:
            _UNRECORDED[tool] = _UNRECORDED.get(tool, 0) + missed + 1
    return decision


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
    # The scenario's timeout is what the agent sees, even for a call the world refused behind it.
    if mode.startswith("timeout"):
        return ToolTimedOut("the request timed out")
    if decision.get("outcome") == "refused":
        return ToolRefused(f"refused ({decision.get('status')}): {detail}")
    if mode == "unavailable":
        return ToolUnavailable("the service is unavailable (503)")
    # An error fault, or a call held for a reason the door could not settle (an approval it could not read).
    return ToolFailed(f"error {decision.get('status')}: {detail}")


def _delay(answer: dict[str, Any]) -> float:
    try:
        return min(max(float(answer.get("delay_seconds") or 0.0), 0.0), 30.0)
    except (TypeError, ValueError):
        return 0.0


def _report_in_background(call_id: str, exc: BaseException) -> None:
    threading.Thread(
        target=_post, args=("/end", {"id": call_id, "ok": False, "error": repr(exc)}), daemon=True
    ).start()


def _resolved_annotations(function: Any) -> dict[str, Any]:
    """The tool's annotations as objects, read where the tool is defined. A framework that copies the tool to describe
    it (ADK copies it with the wrapper's own globals) then never evaluates a string annotation, such as a
    `ToolContext` the tool's module imports, against this module, where the name is unknown."""
    try:
        return typing.get_type_hints(function, include_extras=True)
    except Exception:  # noqa: BLE001 - unresolvable hints stay as written, as before
        return dict(getattr(function, "__annotations__", None) or {})


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
                decision = await asyncio.to_thread(_begin, tool, _arguments(function, args, kwargs))
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

            held_async.__annotations__ = _resolved_annotations(function)
            held_async.__alk_world_door__ = True  # type: ignore[attr-defined]
            return held_async

        @functools.wraps(function)
        def held(*args: Any, **kwargs: Any) -> Any:
            decision = _begin(tool, _arguments(function, args, kwargs))
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

        held.__annotations__ = _resolved_annotations(function)
        held.__alk_world_door__ = True  # type: ignore[attr-defined]
        return held

    return wrap


def decorate_source(source: str, targets: dict[str, str]) -> ast.Module | None:
    """The module's code with the door as the innermost decorator of each target: `targets` maps a callable's path
    in the module (`update_record`, `Tools.update_record`) to its tool name. None when no target is found."""
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

    def get_code(self, name: str) -> Any:
        """The module's code with the door on its tools, for `python -m <module>`: runpy runs the code itself, in
        `__main__`, so the decorator is found among the builtins."""
        try:
            source = self.original.get_source(name)
            tree = decorate_source(source, self.targets) if source else None
        except Exception:  # noqa: BLE001 - unreadable or unparseable: the module runs as without a door
            tree = None
        if tree is None:
            return self.original.get_code(name)
        setattr(builtins, DECORATOR, door)
        return compile(tree, self.original.get_filename(name), "exec")


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
        listed = json.loads(os.environ.get("ALK_WORLD_DOOR_BINDINGS", "[]"))
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


# Where a framework runs an agent as an asyncio task of its own: module, class, method, the argument holding the
# agent, and the name the framework gives an unnamed agent. Only these awaited runs are stopped at a deadline.
WORKER_RUNS = (
    # OpenAI Agents SDK: a worker held as a tool runs as `await Runner.run(worker, ...)`. A hand-off changes the agent
    # inside one run, and `run_streamed` and `run_sync` are not awaited runs, so neither is stopped.
    ("agents.run", "Runner", "run", "starting_agent", ""),
    # LangGraph: a worker compiled as a graph of its own and awaited with `ainvoke`. A graph without a name, one run
    # with `invoke`, or one added as a node of its parent's graph (a swarm member) is not stopped.
    ("langgraph.pregel", "Pregel", "ainvoke", "self", "LangGraph"),
)
_STOPS = "__alk_worker_stop__"


def workers() -> dict[str, str]:
    """The app's workers that run in its own process, by the name its framework gives each: `{name: worker id}`."""
    try:
        named = json.loads(os.environ.get("ALK_WORLD_DOOR_WORKERS", "{}"))
    except ValueError:
        return {}
    if not isinstance(named, dict):
        return {}
    return {str(name): str(worker) for name, worker in named.items() if name and worker}


def stop_at_deadline(run: Any, agent_argument: str, named: dict[str, str], unnamed: str = "") -> Any:
    """`run` with a worker's run cancelled once the worker passes its deadline; any other agent's run, and one under
    the framework's default name `unnamed`, is unchanged."""
    signature = inspect.signature(run)

    @functools.wraps(run)
    async def stopping(*args: Any, **kwargs: Any) -> Any:
        try:
            agent = signature.bind_partial(*args, **kwargs).arguments.get(agent_argument)
        except TypeError:
            agent = None
        name = str(getattr(agent, "name", "") or "")
        worker = named.get(name) if name != unnamed else None
        answer = await asyncio.to_thread(_post, "/worker_deadline", {"worker": worker}) if worker else None
        remaining = (answer or {}).get("remaining")
        if isinstance(remaining, bool) or not isinstance(remaining, (int, float)):
            return await run(*args, **kwargs)
        if remaining <= 0:
            # Already past its deadline: the stop is claimed before the run is scheduled, so a run with no await in it
            # never finishes first. Nothing ran, so it is stopped.
            claim = await asyncio.to_thread(_post, "/worker_stop", {"worker": worker}) or {}
            if claim.get("stop") is not True:
                return await run(*args, **kwargs)
            await asyncio.to_thread(_post, "/worker_stop_end", {"id": claim.get("id"), "stopped": True})
            raise WorkerStopped(f"{worker} was stopped at its deadline")
        task = asyncio.ensure_future(run(*args, **kwargs))
        try:
            done, _ = await asyncio.wait({task}, timeout=remaining)
            if task in done:
                return task.result()
            # Claimed before the cancel: once the call has sealed its stops, the worker runs on.
            claim = await asyncio.to_thread(_post, "/worker_stop", {"worker": worker}) or {}
            if claim.get("stop") is not True:
                return await task
            task.cancel()
            await asyncio.wait({task}, timeout=_CANCEL_SECONDS)
            stopped = task.cancelled()
            await asyncio.to_thread(_post, "/worker_stop_end", {"id": claim.get("id"), "stopped": stopped})
        except asyncio.CancelledError:
            task.cancel()
            raise
        if not stopped:
            # The run did not end at its cancel: it is not stopped, and what it returns stands.
            return await task
        raise WorkerStopped(f"{worker} was stopped at its deadline")

    setattr(stopping, _STOPS, True)
    return stopping


def _stop_workers_in(module: Any, named: dict[str, str]) -> None:
    for module_name, owner_name, method, agent_argument, unnamed in WORKER_RUNS:
        if module.__name__ != module_name:
            continue
        owner = getattr(module, owner_name, None)
        raw = inspect.getattr_static(owner, method, None) if owner is not None else None
        bound = isinstance(raw, (classmethod, staticmethod))
        function = raw.__func__ if isinstance(raw, (classmethod, staticmethod)) else raw
        if not inspect.iscoroutinefunction(function) or getattr(function, _STOPS, False):
            continue
        stopping = stop_at_deadline(function, agent_argument, named, unnamed)
        setattr(owner, method, type(raw)(stopping) if bound else stopping)


class _AfterLoad(importlib.abc.Loader):
    """Loads a module as Python would, then puts the worker stop on the run it holds."""

    def __init__(self, original: Any, named: dict[str, str]) -> None:
        self.original = original
        self.named = named

    def create_module(self, spec: Any) -> Any:
        return self.original.create_module(spec) if hasattr(self.original, "create_module") else None

    def exec_module(self, module: Any) -> None:
        self.original.exec_module(module)
        _stop_workers_in(module, self.named)


class _WorkerRunFinder(importlib.abc.MetaPathFinder):
    """Finds the modules that run a framework's agents through `sys.path`, and loads them with the worker stop on that
    run; a module only another finder on `sys.meta_path` can load (an editable install's own) runs without it."""

    def __init__(self, named: dict[str, str]) -> None:
        self.named = named

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if fullname not in {one[0] for one in WORKER_RUNS}:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return spec
        spec.loader = _AfterLoad(spec.loader, self.named)
        return spec


def stop_workers(named: dict[str, str]) -> None:
    """Stop each named worker's run at its deadline, in modules already loaded and in those loaded later."""
    if not named:
        return
    for module_name, *_ in WORKER_RUNS:
        loaded = sys.modules.get(module_name)
        if loaded is not None:
            _stop_workers_in(loaded, named)
    sys.meta_path.insert(0, _WorkerRunFinder(named))


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
    stop_workers(workers())
    return True
