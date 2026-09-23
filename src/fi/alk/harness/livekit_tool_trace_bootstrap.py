"""ALK-owned ``sitecustomize`` hook for LiveKit function-tool executions.

The hosted simulator cannot observe an agent's private ``AgentSession`` events over the
LiveKit room. Repository agent interpreters therefore import this tiny hook when the bundle
declares ``HARNESS_TOOL_TRACE``. Using Python's ``sitecustomize`` mechanism is important:
LiveKit starts job executors in child Python processes, so a wrapper applied only to the
parent worker misses the ``AgentSession`` that actually executes tools. Tracing is strictly
best effort and must never alter agent behaviour.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any


def _record(event: Any) -> None:
    destination = os.environ.get("HARNESS_TOOL_TRACE", "").strip()
    if not destination:
        return
    records: list[dict[str, Any]] = []
    try:
        pairs = event.zipped()
    except Exception:  # noqa: BLE001 - observability must never affect the target
        return
    for call, output in pairs:
        records.append(
            {
                "name": str(getattr(call, "name", "")),
                "arguments": getattr(call, "arguments", {}) or {},
                "output": getattr(output, "output", "") if output is not None else "",
                "is_error": bool(
                    output is not None and getattr(output, "is_error", False)
                ),
            }
        )
    if not records:
        return
    try:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as trace:
            for record in records:
                trace.write(json.dumps(record, default=str, sort_keys=True) + "\n")
    except OSError:
        return


def _install() -> None:
    try:
        from livekit.agents import AgentSession
    except Exception:  # noqa: BLE001 - non-LiveKit targets continue unchanged
        return
    original = AgentSession.__init__
    if getattr(original, "__alk_tool_trace__", False):
        return

    def traced_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        try:
            self.on("function_tools_executed", _record)
        except Exception:  # noqa: BLE001 - observability must never affect the target
            return

    traced_init.__alk_tool_trace__ = True  # type: ignore[attr-defined]
    AgentSession.__init__ = traced_init


_install()


def _install_declared_python_tools() -> None:
    """Observe source-declared Python tool functions across agent frameworks.

    The profiler matches both the declared module path and callable name, so unrelated
    functions in dependencies cannot be misreported as a target tool. It records completed
    invocations only and never executes or alters a tool itself.
    """

    try:
        bindings = json.loads(os.environ.get("ALK_TOOL_TRACE_BINDINGS", "[]"))
    except ValueError:
        return
    if not isinstance(bindings, list):
        return
    declared: dict[tuple[str, str], str] = {}
    for item in bindings:
        if not isinstance(item, dict):
            continue
        name, module, callable_name = (
            item.get("name"),
            item.get("module"),
            item.get("callable"),
        )
        if not all(isinstance(value, str) and value for value in (name, module, callable_name)):
            continue
        declared[(module.replace(".", "/") + ".py", callable_name.rsplit(".", 1)[-1])] = name
    if not declared:
        return
    function_names = {function for _, function in declared}
    active = threading.local()

    def profile(frame: Any, event: str, value: Any) -> None:
        if getattr(active, "busy", False):
            return
        if event != "return" or frame.f_code.co_name not in function_names:
            return
        filename = frame.f_code.co_filename.replace("\\", "/")
        key = next(
            (
                name
                for (module_path, function), name in declared.items()
                if frame.f_code.co_name == function and filename.endswith(module_path)
            ),
            None,
        )
        if key is None:
            return
        active.busy = True
        try:
            parameter_count = frame.f_code.co_argcount + frame.f_code.co_kwonlyargcount
            arguments = {
                name: frame.f_locals[name]
                for name in frame.f_code.co_varnames[:parameter_count]
                if name in frame.f_locals and name not in {"self", "cls"}
            }
            path = Path(os.environ["HARNESS_TOOL_TRACE"])
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as trace:
                trace.write(
                    json.dumps(
                        {
                            "name": key,
                            "arguments": arguments,
                            "output": value,
                            "is_error": False,
                        },
                        default=str,
                        sort_keys=True,
                    )[:10000]
                    + "\n"
                )
        except (OSError, KeyError, TypeError, ValueError):
            pass
        finally:
            active.busy = False

    sys.setprofile(profile)
    threading.setprofile(profile)


_install_declared_python_tools()
