"""ALK-owned ``sitecustomize`` hook for LiveKit function-tool executions.

The hosted simulator cannot observe an agent's private ``AgentSession`` events over the
LiveKit room. Repository agent interpreters therefore import this tiny hook when the bundle
declares ``HARNESS_TOOL_TRACE``. Using Python's ``sitecustomize`` mechanism is important:
LiveKit starts job executors in child Python processes, so a wrapper applied only to the
parent worker misses the ``AgentSession`` that actually executes tools. Tracing is strictly
best effort and must never alter agent behaviour.

Second responsibility: W>1 LiveKit workers sharing one sandbox each try to bind the same
worker health port and each believe themselves solely responsible for load, so a naive
multi-worker run collides on startup and mis-reports availability. Rather than modify the
agent under test, this hook also flips the worker into livekit-agents' own side-by-side
mode (the same mode the library's CLI uses for ``start --simulation``) whenever the
harness's own ``FI_WORKER_HEALTH_PORT`` env var is present; outside the harness (that var
absent), this isolation flip is a no-op and ``run()`` behaves exactly as the library defines
it. Simulation mode also disables the worker's own load shedding (it never reports itself
``WS_FULL``) -- intended, since the harness routes each world to exactly one worker, but an
operator reading worker logs should know that report is suppressed by design, not a bug.
"""

from __future__ import annotations

# The kit writes the mark module beside this file on every run, and its install() hands on to the
# next sitecustomize; its absence (a hand-copied hook) is silent. Any other failure is reported, and
# must never cost tool tracing or worker isolation.
try:
    import simulation_mark_bootstrap

    simulation_mark_bootstrap.install()
except ImportError as exc:
    if exc.name != "simulation_mark_bootstrap":
        import sys

        sys.stderr.write("simulation mark hook: failed\n")
except Exception:  # noqa: BLE001 - the mark must never cost tool tracing or worker isolation
    import sys

    sys.stderr.write("simulation mark hook: failed\n")

import functools
import json
import logging
import math
import os
from collections.abc import Mapping
import sys
import threading
import time
from pathlib import Path
from typing import Any

# A declared tool's trace line is kept under this size by cutting its values, never the line.
_MAX_RECORD_CHARS = 10000
_MAX_VALUE_CHARS = 2000


def _cut_text(value: Any) -> Any:
    """The value unchanged when its JSON fits a value's share, otherwise its text cut, with a
    note giving the full length so that a reader knows the cut happened."""
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, default=str, sort_keys=True)
    )
    if len(text) <= _MAX_VALUE_CHARS:
        return value
    return f"{text[:_MAX_VALUE_CHARS]} [cut from {len(text)} characters]"


def _bounded(value: Any) -> Any:
    """Like `_cut_text`, but a mapping keeps its keys and each value is bounded the same way,
    so a small value beside a large one keeps its JSON type."""
    if isinstance(value, Mapping):
        return {key: _bounded(item) for key, item in value.items()}
    return _cut_text(value)


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
                # Epoch seconds when the model issued the call; places it in the transcript.
                "at": getattr(call, "created_at", None),
                # An agent may also write its own line to this file for the same call; the
                # collector merges the two by this marker (call_runner._collect_file_tool_calls).
                "source": "livekit",
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


_WORKER_ISOLATION_LOGGER = logging.getLogger("fi.alk.harness.worker_isolation")


def _apply_worker_isolation(server: Any, env: Mapping[str, str]) -> str:
    """Pure, unit-testable core of the isolation shim: nudge one already-constructed
    ``AgentServer`` into livekit-agents' own side-by-side mode using only the
    harness's single ``FI_WORKER_HEALTH_PORT`` env knob (presence marks a
    harness-managed worker; there is nothing else to read, so both isolation
    modes below hardcode the rest). Never raises and never leaves ``server`` in a
    worse state than it started in.

    Two guards, not one, gate every attribute write:

    - `hasattr` first -- this shim does not own `server`'s class, so it must never
      CREATE an attribute that was never there. A plain, slot-free Python object
      accepts `server._simulation = True` unconditionally (that assignment just adds
      the attribute), so skipping this check on a wheel shape that genuinely has no
      `_simulation` would silently invent one, report "simulation", and never try the
      `_port` fallback that wheel actually needs -- wrong, and unobservable from the
      return value alone.
    - A post-write re-read second -- a wheel CAN carry `_simulation` yet reject
      assignment (a property with a raising or no-op setter), and stopping at `hasattr`
      alone would report "simulation" for a write that silently did nothing. So, once
      `hasattr` allows the attempt, the value is re-read to confirm it actually landed
      before committing to that mode -- otherwise execution falls through to the next,
      weaker path.
    """
    port_raw = env.get("FI_WORKER_HEALTH_PORT", "").strip()
    if not port_raw:
        return "noop"

    mode = "unsupported"

    # Primary path: the library's own CLI uses this for `start --simulation`, so it is
    # the best-supported way to disable the health server and force `_is_available()`
    # to always report true -- exactly what a harness-managed, log-driven-readiness
    # worker needs.
    if hasattr(server, "_simulation"):
        try:
            server._simulation = True
        except Exception:  # noqa: BLE001 - isolation must never break the worker
            pass
        if getattr(server, "_simulation", None) is True:
            mode = "simulation"

    if (
        mode == "unsupported"
        and hasattr(server, "_port")
        and hasattr(server, "_load_threshold")
    ):
        # Fallback for wheels where `_simulation` doesn't exist or didn't take: move the
        # health port to the harness-assigned one (so W>1 workers don't collide on the
        # default) and disable load-shedding, since this worker is the only one the
        # harness routes to.
        try:
            port = int(port_raw)
        except (TypeError, ValueError):
            port = None
        if port is not None:
            try:
                server._port = port
                server._load_threshold = math.inf
            except Exception:  # noqa: BLE001 - isolation must never break the worker
                pass
            if (
                getattr(server, "_port", None) == port
                and getattr(server, "_load_threshold", None) == math.inf
            ):
                mode = "port_override"

    if mode != "unsupported" and hasattr(server, "_num_idle_processes"):
        # Every harness-managed world runs exactly one worker process per world, so
        # this is a fixed fact of the harness's topology, not a per-world value --
        # there is nothing to read from env. No re-read needed: a rejected write here
        # only costs an idle-process count the library defaults for anyway.
        try:
            server._num_idle_processes = 1
        except Exception:  # noqa: BLE001 - isolation must never break the worker
            pass

    if mode == "unsupported":
        # Keys only, never values. An operator seeing repeated stray health-port binds at
        # W>1 needs to know this world's worker was never actually isolated.
        _WORKER_ISOLATION_LOGGER.warning(
            "livekit worker isolation could not be applied to this worker (unsupported "
            "livekit-agents version or worker shape); at parallelism>1 this world's worker "
            "will bind the default health port"
        )
    elif mode != "noop":
        _WORKER_ISOLATION_LOGGER.info("livekit worker isolation applied: mode=%s", mode)
    return mode


def _install_worker_isolation() -> None:
    if not os.environ.get("FI_WORKER_HEALTH_PORT", "").strip():
        # Local, unmanaged runs (no harness env at all) leave `run()` behaving exactly
        # as the library defines it -- no wrapper is installed for them.
        return
    try:
        from livekit.agents.worker import AgentServer
    except Exception:  # noqa: BLE001 - non-LiveKit / unpinned wheels continue unchanged
        return

    # `getattr` rather than a bare attribute access: a wheel shape without a `run` method
    # at all must not turn into a `sitecustomize` import error printed by every interpreter
    # this hook loads into, including every per-job child process it spawns.
    original_run = getattr(AgentServer, "run", None)
    if original_run is None or not callable(original_run):
        return
    if getattr(original_run, "__alk_worker_isolation__", False):
        return

    @functools.wraps(original_run)
    async def isolated_run(self: Any, *args: Any, **kwargs: Any) -> Any:
        # Re-derive from `os.environ` here (not a closed-over value) so the check is
        # live at call time too, not just at import time -- cheap, and keeps this
        # wrapper inert if the module is ever imported outside the harness.
        _apply_worker_isolation(self, os.environ)
        return await original_run(self, *args, **kwargs)

    isolated_run.__alk_worker_isolation__ = True  # type: ignore[attr-defined]
    try:
        AgentServer.run = isolated_run
    except Exception:  # noqa: BLE001 - a wheel that forbids the reassignment must not crash
        return


# A fixed value that is not a secret: with it set, a customer's own set_tracing_disabled(False)
# cannot send the gateway key to OpenAI's trace endpoint.
_TRACING_EXPORT_PLACEHOLDER = "alk-tracing-disabled"
# The build sets the SDK's own tracing switch on every gateway-routed process, so its export is
# off wherever this hook doesn't act. Once the hook has kept the export local, it turns tracing
# back on in this process (set_tracing_disabled, which takes precedence over the switch), so the
# kit's span observation (a processor of the SDK's tracing) runs.
_SDK_TRACING_SWITCH = "OPENAI_AGENTS_DISABLE_TRACING"
_SDK_TRACING_BY_KIT = "ALK_SDK_TRACING_OFF_BY_KIT"


def _guard_tracing_export_key(module: Any) -> None:
    """Keep the SDK's own trace export to OpenAI from sending anything on a gateway run.

    Its default exporter's export (both forms) does nothing, so spans and the key in its header never leave.
    The kit's span observation is a separate processor and still runs. As well, the gateway key
    becomes the placeholder whenever the agent hands it to the exporter: the SDK's
    set_default_openai_key and set_default_openai_client pass their key on unless told not to,
    and an empty key falls back to OPENAI_API_KEY. Getting the exporter builds it (with its HTTP
    client) during the agent's ``import agents``, where the SDK would build it on first use.
    """
    gateway_keys = {
        value
        for value in (os.environ.get("OPENAI_API_KEY"), os.environ.get("AGENTCC_API_KEY"))
        if value
    }
    exporter = sys.modules[f"{module.__name__}.tracing.processors"].default_exporter()

    def export_nothing(self: Any, items: Any, deadline: Any = None) -> None:
        return None

    # On the class, so an exporter the agent builds itself sends nothing either. The batch
    # processor calls the deadline form directly when it flushes at exit.
    exporter_class = type(exporter)
    if not all(
        callable(getattr(exporter_class, name, None))
        for name in ("export", "_export_with_deadline")
    ):
        # A version that sends through another method: the switch stays on.
        raise AttributeError("the SDK's exporter has no export or _export_with_deadline")
    exporter_class.export = export_nothing
    exporter_class._export_with_deadline = export_nothing
    if not gateway_keys:
        return
    set_api_key = exporter.set_api_key

    def guarded_set_api_key(api_key: str) -> None:
        if not api_key or api_key in gateway_keys:
            api_key = _TRACING_EXPORT_PLACEHOLDER
        set_api_key(api_key)

    exporter.set_api_key = guarded_set_api_key


def _set_openai_agents_chat_style(module: Any, reason: str = "") -> None:
    if not reason:
        # Each setting on its own: the trace key placeholder holds even if the chat style fails.
        try:
            module.set_tracing_export_api_key(_TRACING_EXPORT_PLACEHOLDER)
        except Exception as exc:  # noqa: BLE001 - a setting we could not make must not stop the agent
            sys.stderr.write(
                "alk: could not set the OpenAI Agents SDK trace export key: "
                f"{type(exc).__name__}\n"
            )
        try:
            _guard_tracing_export_key(module)
        except Exception as exc:  # noqa: BLE001 - a setting we could not make must not stop the agent
            sys.stderr.write(
                "alk: could not keep the OpenAI Agents SDK trace export local "
                f"({type(exc).__name__}), so its tracing stays off and its spans aren't "
                "observed\n"
            )
        else:
            # Only the kit's own switch, and for this process only: the environment keeps it, so
            # a child the hook never reaches (python -I) starts with its tracing off. That child's
            # exporter isn't made local, so if its own code turns tracing back on, it sends its
            # spans and OPENAI_API_KEY (the gateway key) to OpenAI: a stated limit. A customer
            # who turned their tracing off themselves keeps it off.
            if os.environ.get(_SDK_TRACING_BY_KIT) == "1":
                try:
                    module.set_tracing_disabled(False)
                except Exception as exc:  # noqa: BLE001 - must not stop the agent
                    sys.stderr.write(
                        "alk: could not turn the OpenAI Agents SDK's tracing back on "
                        f"({type(exc).__name__}), so its spans aren't observed\n"
                    )
        try:
            module.set_default_openai_api("chat_completions")
            return
        except Exception as exc:  # noqa: BLE001 - a setting we could not make must not stop the agent
            reason = type(exc).__name__
    sys.stderr.write(
        f"alk: could not set the OpenAI Agents SDK to chat completions: {reason}\n"
    )


class _ChatStyleLoader:
    """Stands in for the SDK's own loader on its spec only, so a loader shared with other
    modules (a zipimporter) is never changed."""

    def __init__(self, finder: "_OpenAIAgentsFinder", loader: Any) -> None:
        self._finder = finder
        self._loader = loader

    def create_module(self, spec: Any) -> Any:
        create = getattr(self._loader, "create_module", None)
        return create(spec) if create is not None else None

    def exec_module(self, module: Any) -> None:
        # The SDK's own errors reach the importer unchanged, and the hook stays armed.
        self._loader.exec_module(module)
        if self._finder.armed and module.__name__ == "agents":
            self._finder.armed = False
            _set_openai_agents_chat_style(module)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._loader, name)


class _OpenAIAgentsFinder:
    """First on ``sys.meta_path``; armed until the SDK has run once, so the setting is made
    after the SDK's own import but the SDK is never imported by this hook. It is never removed
    from the list, since removing it while the import system walks the list skips the next
    finder."""

    def __init__(self) -> None:
        self.armed = True

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if fullname != "agents" or not self.armed:
            return None
        spec = None
        for finder in list(sys.meta_path):
            if finder is self:
                continue
            find_spec = getattr(finder, "find_spec", None)
            if find_spec is None:
                continue
            try:
                spec = find_spec(fullname, path, target)
            except Exception:  # noqa: BLE001 - the import system reaches the same finder next
                # The SDK may then load without the hook: the build's switch keeps its export off.
                return None
            if spec is not None:
                break
        if spec is None:
            return None
        loader = getattr(spec, "loader", None)
        if getattr(loader, "exec_module", None) is None:
            # Not ours to load (a namespace package or an old-style loader): the SDK imports as
            # without the kit, and the log says it stays on its default style.
            self.armed = False
            _set_openai_agents_chat_style(
                None, "its loader cannot be wrapped (its tracing stays off)"
            )
            return spec
        spec.loader = _ChatStyleLoader(self, loader)
        return spec


def _install_openai_agents_chat_style() -> None:
    # The gateway serves the Responses request style only for OpenAI's own models, so a routed
    # model name needs the SDK on chat completions. The check proves the openai-agents
    # distribution is installed, not that the ``agents`` that gets imported came from it.
    if os.environ.get("ALK_OPENAI_AGENTS_CHAT_COMPLETIONS") != "1":
        return
    import importlib.metadata

    try:
        importlib.metadata.distribution("openai-agents")
    except importlib.metadata.PackageNotFoundError:
        return
    if "agents" in sys.modules:
        _set_openai_agents_chat_style(sys.modules["agents"])
        return
    sys.meta_path.insert(0, _OpenAIAgentsFinder())


try:
    _install_openai_agents_chat_style()
except Exception as exc:  # noqa: BLE001 - must not cost tool tracing or worker isolation
    sys.stderr.write(
        f"alk: could not prepare the OpenAI Agents SDK setting: {type(exc).__name__}\n"
    )
_install()
_install_worker_isolation()


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
        if not all(
            isinstance(value, str) and value for value in (name, module, callable_name)
        ):
            continue
        declared[(module, callable_name.rsplit(".", 1)[-1])] = name
    if not declared:
        return
    function_names = {function for _, function in declared}
    active = threading.local()
    import dis
    import inspect

    suspendable = (
        inspect.CO_COROUTINE | inspect.CO_GENERATOR | inspect.CO_ASYNC_GENERATOR
    )

    def matches_module(frame: Any, module: str) -> bool:
        loaded_as = frame.f_globals.get("__name__")
        if loaded_as == module:
            return True
        if loaded_as != "__main__":
            return False
        declared_path = Path(*module.split(".")).with_suffix(".py").resolve()
        return Path(frame.f_code.co_filename).resolve() == declared_path

    # frame id -> (frame, entry time). Holding the frame keeps its id from being
    # reused while an entry for it is still open.
    entries: dict[int, tuple[Any, float]] = {}

    def profile(frame: Any, event: str, value: Any) -> None:
        if getattr(active, "busy", False):
            return
        code = frame.f_code
        if code.co_name not in function_names:
            return
        if event == "call":
            # A coroutine re-enters its frame on every resume after an await; the
            # tool ran from its first entry, so keep that time.
            entry = entries.get(id(frame))
            if entry is None or entry[0] is not frame:
                entries[id(frame)] = (frame, time.time())
            return
        if event != "return":
            return
        # A coroutine also "returns" at every await; only a real return ends it.
        finished = not code.co_flags & suspendable or dis.opname[
            code.co_code[frame.f_lasti]
        ].startswith("RETURN")
        entry = (
            entries.pop(id(frame), None) if finished else entries.get(id(frame))
        )
        started = entry[1] if entry is not None else time.time()
        key = next(
            (
                name
                for (module, function), name in declared.items()
                if frame.f_code.co_name == function
                and matches_module(frame, module)
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
            record = {
                "name": key,
                "arguments": arguments,
                "output": value,
                "is_error": False,
                "at": started,
            }
            line = json.dumps(record, default=str, sort_keys=True)
            if len(line) > _MAX_RECORD_CHARS:
                # Cut the values, never the line: a cut line is not JSON, so the call would read
                # as no evidence at all. Only an oversize value is cut, so the rest keep their
                # types for a check, and each cut value says it was cut.
                record["arguments"] = _bounded(arguments)
                record["output"] = _bounded(value)
                record["truncated"] = True
                line = json.dumps(record, default=str, sort_keys=True)
            if len(line) > _MAX_RECORD_CHARS:
                # Many values that each fit: bound the record at the cost of their types.
                record["arguments"] = {
                    name: _cut_text(item) for name, item in arguments.items()
                }
                record["output"] = _cut_text(value)
                line = json.dumps(record, default=str, sort_keys=True)
            with path.open("a", encoding="utf-8") as trace:
                trace.write(line + "\n")
        except (OSError, KeyError, TypeError, ValueError):
            pass
        finally:
            active.busy = False

    sys.setprofile(profile)
    threading.setprofile(profile)


try:
    import crewai_runtime_observer_bootstrap

    _crewai_observer_installed = crewai_runtime_observer_bootstrap.install()
except Exception:  # noqa: BLE001 - optional observation must not affect customer execution
    _crewai_observer_installed = False

if not _crewai_observer_installed:
    _install_declared_python_tools()

# The world's door for tools the agent runs in its own process: beside this hook only when the bundle has one.
if os.environ.get("ALK_WORLD_DOOR_URL", "").strip():
    try:
        import in_process_door_bootstrap

        in_process_door_bootstrap.install()
    except Exception:  # noqa: BLE001 - the door must never cost the agent its tools
        sys.stderr.write("world door hook: failed\n")
