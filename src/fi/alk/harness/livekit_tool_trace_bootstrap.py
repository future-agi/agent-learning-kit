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

# Which mechanism observes the tools in this process: "monitoring" (sys.monitoring, Python 3.12+)
# or "profile" (the profile function, the fallback). Set once the install has chosen.
_TOOL_TRACE_MECHANISM: str | None = None
# Whether the monitoring tracer needs a generator's owner to see it closed at a bare `yield`:
# True where closing one fires no event on this interpreter (or the probe could not tell).
_TOOL_TRACE_NEEDS_OWNER: bool | None = None

# A declared tool's trace line is kept under this size by cutting its values, never the line.
_MAX_RECORD_CHARS = 10000
_MAX_VALUE_CHARS = 2000


def _text(value: Any) -> str:
    """A value's text for a trace line; one that cannot be printed gets a placeholder.

    ``BaseException`` is caught around ``str()`` alone: a ``SystemExit`` or ``KeyboardInterrupt``
    raised there exists only because the tracer asked for the text, and must not end the agent."""
    try:
        return str(value)
    except BaseException:  # noqa: BLE001 - observation must never reach the agent's call
        try:
            return f"<unprintable {type(value).__name__}>"
        except BaseException:  # noqa: BLE001
            return "<unprintable value>"


class _Cut(str):
    """A value's text already cut to its share, with the note. A second cut would nest the note,
    so `_cut_text` and `_bounded` leave one as it is."""


def _cut_text(value: Any) -> Any:
    """The value unchanged when its JSON fits a value's share, otherwise its text cut, with a
    note giving the full length so that a reader knows the cut happened."""
    if isinstance(value, _Cut):
        return value
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, default=_text, sort_keys=True)
    )
    if len(text) <= _MAX_VALUE_CHARS:
        return value
    return _cut_note(text)


def _cut_note(text: str) -> _Cut:
    return _Cut(f"{text[:_MAX_VALUE_CHARS]} [cut from {len(text)} characters]")


def _entry_value(value: Any) -> Any:
    """What a monitoring-path call records for one argument, taken when the call starts: the value
    as it was passed (a JSON round trip, so a list the tool mutates is recorded as passed), except
    that one too large to be written whole is cut there and then, as `write_record` would cut it,
    so only what is written is copied. A mapping is kept whole: its cut is per key."""
    if isinstance(value, str):
        text = str.__str__(value)
    elif isinstance(value, (dict, list, tuple, int, float, bool, type(None))):
        try:
            text = json.dumps(value, default=_text, sort_keys=True)
        except TypeError:  # keys that do not sort together: as a round trip would have them
            return json.loads(json.dumps(value, default=_text))
        if len(text) <= _MAX_RECORD_CHARS or isinstance(value, dict):
            return json.loads(text)
        return _cut_note(text)
    else:
        text = _text(value)  # what `json.dumps(default=_text)` makes of it
    return _cut_text(text) if len(text) > _MAX_RECORD_CHARS else text


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
                trace.write(json.dumps(record, default=_text, sort_keys=True) + "\n")
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
        # Only reached for a name the wrapper lacks. Its own fields are absent while copy or
        # pickle rebuilds it, and asking the loader for them would call this again without end.
        if name in {"_loader", "_finder"}:
            raise AttributeError(name)
        return getattr(self._loader, name)

    @property  # type: ignore[misc]
    def __class__(self) -> type:
        # isinstance(agents.__loader__, SourceFileLoader) holds as it does without the kit, so a
        # tool that picks its handling by the loader's class (pkg_resources) is unchanged.
        return type(self._loader)


class _OpenAIAgentsFinder:
    """First on ``sys.meta_path``; armed until the SDK has run once, so the setting is made
    after the SDK's own import but the SDK is never imported by this hook. It is never removed
    from the list, since removing it while the import system walks the list skips the next
    finder."""

    def __init__(self) -> None:
        self.armed = True
        self._walking = threading.local()

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if fullname != "agents" or not self.armed:
            return None
        # A finder ahead of this one that walks the list again (or a second copy of the hook)
        # must not call back into this walk without end.
        if getattr(self._walking, "active", False):
            return None
        self._walking.active = True
        try:
            return self._find_spec(fullname, path, target)
        finally:
            self._walking.active = False

    def _find_spec(self, fullname: str, path: Any, target: Any) -> Any:
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
    # A binding that carries its full callable (`qualname`) matches only that callable.
    qualified: dict[tuple[str, str, str], str] = {}
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
        leaf = callable_name.rsplit(".", 1)[-1]
        full_name = item.get("qualname")
        if isinstance(full_name, str) and full_name:
            qualified[(module, leaf, full_name)] = name
        else:
            declared[(module, leaf)] = name
    if not declared and not qualified:
        return
    function_names = {function for _, function in declared} | {
        function for _, function, _ in qualified
    }
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
        try:
            declared_path = Path(*module.split(".")).with_suffix(".py").resolve()
            return Path(frame.f_code.co_filename).resolve() == declared_path
        except Exception:  # noqa: BLE001 - an odd module name is no match, never an error
            return False

    def resolve_key(frame: Any) -> str | None:
        """The bound tool's name this frame is a call of, or None."""
        code = frame.f_code
        key = next(
            (
                name
                for (module, function), name in declared.items()
                if code.co_name == function and matches_module(frame, module)
            ),
            None,
        )
        if key is not None:
            return key
        # co_qualname exists from Python 3.11; before it, the last name alone decides.
        frame_qualname = getattr(code, "co_qualname", None)
        # A method inherited from a base class carries the base's qualname; the receiver's
        # own class names it as the reading wrote it.
        receiver_qualname = None
        try:
            receiver = frame.f_locals.get(code.co_varnames[0]) if (
                code.co_argcount and code.co_varnames[0] in {"self", "cls"}
            ) else None
            if receiver is not None:
                owner = receiver if code.co_varnames[0] == "cls" else type(receiver)
                receiver_qualname = f"{owner.__qualname__}.{code.co_name}"
        except Exception:  # noqa: BLE001 - observation must never reach the agent's call
            receiver_qualname = None
        return next(
            (
                name
                for (module, function, full_name), name in qualified.items()
                if code.co_name == function
                and (
                    frame_qualname is None
                    # A tool defined inside a function is written by its own name.
                    or frame_qualname.rsplit("<locals>.", 1)[-1] == full_name
                    or receiver_qualname == full_name
                )
                and matches_module(frame, module)
            ),
            None,
        )

    def collect_arguments(frame: Any) -> dict[str, Any]:
        code = frame.f_code
        parameter_count = code.co_argcount + code.co_kwonlyargcount
        return {
            name: frame.f_locals[name]
            for name in code.co_varnames[:parameter_count]
            if name in frame.f_locals and name not in {"self", "cls"}
        }

    def write_record(
        key: str, get_arguments: Any, value: Any, raised: bool, started: float
    ) -> None:
        """Append one call's line. ``get_arguments`` is called inside the guard, so a failure
        to read the arguments still leaves the call on record."""
        try:
            arguments = get_arguments()
            path = Path(os.environ["HARNESS_TOOL_TRACE"])
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "name": key,
                "arguments": arguments,
                "output": value,
                "is_error": raised,
                "at": started,
            }
            line = json.dumps(record, default=_text, sort_keys=True)
            # A value already cut when the call started means the whole line would have been too
            # long: it is bounded the same way, and marked.
            cut_early = isinstance(arguments, dict) and any(
                isinstance(item, _Cut) for item in arguments.values()
            )
            if len(line) > _MAX_RECORD_CHARS or cut_early:
                # Cut the values, never the line: a cut line is not JSON, so the call would read
                # as no evidence at all. Only an oversize value is cut, so the rest keep their
                # types for a check, and each cut value says it was cut.
                record["arguments"] = _bounded(arguments)
                record["output"] = _bounded(value)
                record["truncated"] = True
                line = json.dumps(record, default=_text, sort_keys=True)
            if len(line) > _MAX_RECORD_CHARS:
                # Many values that each fit: bound the record at the cost of their types.
                record["arguments"] = {
                    name: _cut_text(item) for name, item in arguments.items()
                }
                record["output"] = _cut_text(value)
                line = json.dumps(record, default=_text, sort_keys=True)
            with path.open("a", encoding="utf-8") as trace:
                trace.write(line + "\n")
        except Exception:  # noqa: BLE001 - observation must never reach the agent's call
            # A value json cannot encode (a very deep one, tuple keys, mixed key types) still
            # leaves the call on record, with its values replaced by text.
            try:
                minimal = {
                    "name": key,
                    "arguments": "the tracer could not encode this value",
                    "output": "the tracer could not encode this value",
                    "is_error": raised,
                    "at": started,
                    "truncated": True,
                }
                with Path(os.environ["HARNESS_TOOL_TRACE"]).open("a", encoding="utf-8") as trace:
                    trace.write(json.dumps(minimal, sort_keys=True) + "\n")
            except Exception:  # noqa: BLE001 - observation must never reach the agent's call
                pass

    def install_profile() -> None:
        # frame id -> (frame, entry time). Holding the frame keeps its id from being
        # reused while an entry for it is still open.
        entries: dict[int, tuple[Any, float]] = {}
        # frame id -> (how it was last thrown into, the instruction it was thrown at). A frame
        # that `close()` or a task's `cancel()` ended is not a tool that raised.
        thrown: dict[int, tuple[str, int]] = {}

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
                    thrown.pop(id(frame), None)
                # On a throw into the frame the call event's value is the thrown exception.
                if isinstance(value, GeneratorExit):
                    thrown[id(frame)] = ("closed", frame.f_lasti)
                elif isinstance(value, BaseException) and type(value).__name__ == "CancelledError":
                    thrown[id(frame)] = ("cancelled", frame.f_lasti)
                return
            if event != "return":
                return
            # The opcode the frame ended on tells a return from a raise. A coroutine or
            # generator also "returns" at every await or yield; only a real return, or a raise,
            # ends it.
            try:
                opname = dis.opname[code.co_code[frame.f_lasti]]
            except (IndexError, TypeError):
                opname = "RETURN_VALUE"
            # Python 3.13 reports a suspension on the RESUME after the yield (a non-zero
            # oparg), where 3.12 and 3.14 report the YIELD_VALUE itself; on 3.14 a closed
            # generator also ends on that RESUME, so the rule is 3.13's alone.
            try:
                resumes_after_yield = (
                    sys.version_info[:2] == (3, 13)
                    and opname == "RESUME"
                    and code.co_code[frame.f_lasti + 1] != 0
                )
            except IndexError:
                resumes_after_yield = False
            ended_by = None
            mark = thrown.get(id(frame))
            if mark is not None:
                kind, thrown_at = mark
                try:
                    bare_raise = opname == "RAISE_VARARGS" and code.co_code[frame.f_lasti + 1] == 0
                except IndexError:
                    bare_raise = False
                # The thrown exception passed through: a re-raise, an unwinding `finally`, an
                # awaited callee ending, or (a generator) a frame closed at its `yield` without
                # running.
                if (
                    opname in {"CLEANUP_THROW", "RERAISE"}
                    or bare_raise
                    or (
                        kind == "closed"
                        and opname.startswith("YIELD")
                        and frame.f_lasti == thrown_at
                    )
                ):
                    ended_by = kind
            if ended_by is None and (
                opname == "RETURN_GENERATOR"
                or (
                    code.co_flags & suspendable
                    and (opname.startswith("YIELD") or resumes_after_yield)
                )
            ):
                return
            thrown.pop(id(frame), None)
            raised = ended_by is None and not opname.startswith("RETURN")
            entry = entries.pop(id(frame), None)
            if raised:
                value = "the tool raised an exception"
            elif ended_by == "cancelled":
                value = "the tool was cancelled before it finished"
            elif ended_by == "closed":
                value = "the generator was closed before it finished"
            started = entry[1] if entry is not None else time.time()
            key = resolve_key(frame)
            if key is None:
                return
            active.busy = True
            try:
                write_record(key, lambda: collect_arguments(frame), value, raised, started)
            finally:
                active.busy = False

        sys.setprofile(profile)
        threading.setprofile(profile)

        rearming = threading.local()

        def rearm(event: str, args: Any) -> None:
            # CPython drops a profile function whose call raised, even one the tracer caught
            # inside a deep recursion; put it back on the next audited event of the thread.
            try:
                if getattr(rearming, "now", False) or sys.getprofile() is not None:
                    return
                rearming.now = True
                try:
                    sys.setprofile(profile)
                finally:
                    rearming.now = False
            except Exception:  # noqa: BLE001 - observation must never reach the agent's call
                pass

        sys.addaudithook(rearm)

    def install_monitoring() -> bool:
        """Trace through ``sys.monitoring`` (Python 3.12+). False when it cannot be claimed.

        Only a function whose name a tool is bound to is ever reported: every other code object
        is switched off at its first start, so the agent's own code runs at full speed. No
        audit-hook re-arm is needed here: a monitoring tool is not a per-thread hook that
        CPython drops when a call under it raises."""
        global _TOOL_TRACE_NEEDS_OWNER
        monitoring = getattr(sys, "monitoring", None)
        if monitoring is None:
            return False
        import collections
        import gc
        import types
        import weakref

        tool = None
        for candidate in (4, 3):  # unassigned by PEP 669; 0-2 and 5 are reserved
            try:
                if monitoring.get_tool(candidate) is not None:
                    continue
                monitoring.use_tool_id(candidate, "alk-tool-trace")
            except Exception:  # noqa: BLE001 - a taken id means the profile path
                continue
            tool = candidate
            break
        if tool is None:
            return False
        events = monitoring.events
        disable = monitoring.DISABLE
        # frame id -> (frame, entry time, tool name, arguments or None); holding the frame
        # keeps its id from being reused while an entry is open.
        calls: dict[int, tuple[Any, float, str, Any]] = {}
        # frame id -> how it was last thrown into ("closed" by close(), "cancelled" by cancel()).
        thrown: dict[int, str] = {}
        armed: set[Any] = set()
        # PY_UNWIND and PY_THROW can only be set process-wide, so they are on only while a
        # tool call is open (a paused generator or an awaiting coroutine is open); PY_RETURN
        # is the one event set per code object.
        start_only = events.PY_START
        while_open = events.PY_START | events.PY_UNWIND | events.PY_THROW
        guard = threading.Lock()
        # frame id -> weak reference to the generator that owns the frame, kept only for an
        # open generator call. Early 3.12 and 3.13 releases fire no event when a generator
        # paused at a bare `yield` is closed, so such a call is settled by a callback on the
        # reference, which runs when the generator is let go.
        owners: dict[int, Any] = {}
        # Calls settled by a callback that could not take `guard` (it may run inside the tracer's
        # own code, on a thread that holds it): the next callback, start or finish turns the
        # exception events off for them. A deque, so appending needs no lock.
        unsettled: collections.deque[int] = collections.deque()
        generator_flag = 0x20  # inspect.CO_GENERATOR; coroutines and async generators differ

        def arguments_of(call: tuple[Any, float, str, Any]) -> Any:
            def get() -> Any:
                if call[3] is None:
                    raise ValueError("the arguments could not be read")
                return call[3]

            return get

        def turn_events_off_when_idle() -> None:
            """Called with ``guard`` held: drop the exception events once no call is open."""
            while unsettled:
                unsettled.popleft()
            if not calls:
                monitoring.set_events(tool, start_only)

        def dropped(frame_id: int) -> None:
            """The generator that owned this open call was let go, so it ended without an event:
            write its record, as a close. The callback can run anywhere a generator is freed,
            including a collection inside the tracer's own code on a thread that holds ``guard``,
            so it never waits for it: the call is taken off by an atomic ``dict.pop``, the record
            written here, and only turning the exception events off waits for a free ``guard``."""
            try:
                owners.pop(frame_id, None)
                thrown.pop(frame_id, None)
                call = calls.pop(frame_id, None)
                if call is None:
                    return
                if guard.acquire(blocking=False):
                    try:
                        turn_events_off_when_idle()
                    finally:
                        guard.release()
                else:
                    unsettled.append(frame_id)
                write_record(
                    call[2],
                    arguments_of(call),
                    "the generator was closed before it finished",
                    False,
                    call[1],
                )
            except Exception:  # noqa: BLE001 - observation must never reach the agent's call
                pass

        def drain_unsettled() -> None:
            if unsettled:
                with guard:
                    turn_events_off_when_idle()

        def on_start(code: Any, offset: int) -> Any:
            if unsettled:
                drain_unsettled()
            if code.co_name not in function_names:
                return disable
            try:
                frame = sys._getframe(1)
                key = resolve_key(frame)
                if key is None:
                    # Only a method's answer can differ between calls of one code object (it
                    # follows the receiver's class); any other code is settled for good.
                    if code.co_argcount and code.co_varnames[0] in {"self", "cls"}:
                        return None
                    return disable
                if code not in armed:
                    monitoring.set_local_events(tool, code, events.PY_RETURN)
                    armed.add(code)
                try:
                    # Encoded now, so a list or dict the tool mutates is recorded as passed.
                    arguments = {
                        name: _entry_value(value)
                        for name, value in collect_arguments(frame).items()
                    }
                except Exception:  # noqa: BLE001
                    arguments = None
                owner = None
                if needs_owner and code.co_flags & generator_flag:
                    # The frame names its generator on 3.14; elsewhere `gc.get_referrers` walks
                    # every tracked object, which is why it runs only where a close is not seen.
                    generator = getattr(frame, "f_generator", None)
                    if generator is None:
                        generator = next(
                            (
                                o
                                for o in gc.get_referrers(frame)
                                if isinstance(o, types.GeneratorType)
                            ),
                            None,
                        )
                    if generator is not None:
                        owner = weakref.ref(
                            generator, lambda _ref, frame_id=id(frame): dropped(frame_id)
                        )
                with guard:
                    calls[id(frame)] = (frame, time.time(), key, arguments)
                    if owner is not None:
                        owners[id(frame)] = owner
                    thrown.pop(id(frame), None)
                    if len(calls) == 1:
                        monitoring.set_events(tool, while_open)
            except Exception:  # noqa: BLE001 - observation must never reach the agent's call
                pass
            return None

        def on_throw(code: Any, offset: int, exception: BaseException) -> None:
            if code.co_name not in function_names:
                return
            try:
                frame = sys._getframe(1)
                if id(frame) not in calls:
                    return
                if isinstance(exception, GeneratorExit):
                    thrown[id(frame)] = "closed"
                elif type(exception).__name__ == "CancelledError":
                    thrown[id(frame)] = "cancelled"
            except Exception:  # noqa: BLE001 - observation must never reach the agent's call
                pass

        def finish(frame: Any, value: Any, raised: bool) -> None:
            with guard:
                # `pop`, not `get` then `del`: a callback may take the call off at the same time.
                call = calls.pop(id(frame), None)
                if call is None:
                    return
                if call[0] is not frame:
                    calls[id(frame)] = call
                    return
                owners.pop(id(frame), None)
                thrown.pop(id(frame), None)
                turn_events_off_when_idle()
            write_record(call[2], arguments_of(call), value, raised, call[1])

        def on_return(code: Any, offset: int, retval: Any) -> None:
            try:
                finish(sys._getframe(1), retval, False)
            except Exception:  # noqa: BLE001 - observation must never reach the agent's call
                pass

        def on_unwind(code: Any, offset: int, exception: BaseException) -> None:
            if code.co_name not in function_names:
                return
            try:
                frame = sys._getframe(1)
                kind = thrown.get(id(frame))
                # The exception ending the frame is known, so only the thrown one itself is not
                # the tool's own error: an error raised later in a `finally` is.
                if kind == "closed" and isinstance(exception, GeneratorExit):
                    finish(frame, "the generator was closed before it finished", False)
                elif kind == "cancelled" and type(exception).__name__ == "CancelledError":
                    finish(frame, "the tool was cancelled before it finished", False)
                else:
                    finish(frame, "the tool raised an exception", True)
            except Exception:  # noqa: BLE001 - observation must never reach the agent's call
                pass

        # Whether a generator paused at a bare `yield` that is closed fires PY_UNWIND or PY_THROW
        # depends on the release (3.12.3 and 3.13.3 fire nothing), so ask this interpreter once,
        # on this tool id before it is armed. Where it fires nothing the owner is needed; any
        # doubt, a failed probe included, means the same.
        needs_owner = True
        try:
            probe_fired: list[bool] = []

            def probe() -> Any:
                yield

            probe_code = probe.__code__

            def probe_event(code: Any, offset: int, exception: BaseException) -> None:
                if code is probe_code:
                    probe_fired.append(True)

            monitoring.register_callback(tool, events.PY_UNWIND, probe_event)
            monitoring.register_callback(tool, events.PY_THROW, probe_event)
            monitoring.set_events(tool, events.PY_UNWIND | events.PY_THROW)
            try:
                g = probe()
                next(g)
                g.close()
            finally:
                monitoring.set_events(tool, 0)
            needs_owner = not probe_fired
        except Exception:  # noqa: BLE001 - a failed probe means the owner is needed
            needs_owner = True
        _TOOL_TRACE_NEEDS_OWNER = needs_owner

        try:
            monitoring.register_callback(tool, events.PY_START, on_start)
            monitoring.register_callback(tool, events.PY_RETURN, on_return)
            monitoring.register_callback(tool, events.PY_UNWIND, on_unwind)
            monitoring.register_callback(tool, events.PY_THROW, on_throw)
            monitoring.set_events(tool, events.PY_START)
        except Exception:  # noqa: BLE001 - give the id back and use the profile path
            try:
                monitoring.set_events(tool, 0)
                monitoring.free_tool_id(tool)
            except Exception:  # noqa: BLE001
                pass
            return False
        return True

    global _TOOL_TRACE_MECHANISM
    if (
        os.environ.get("ALK_TOOL_TRACE_MECHANISM") != "profile"
        and sys.version_info >= (3, 12)
        and install_monitoring()
    ):
        _TOOL_TRACE_MECHANISM = "monitoring"
    else:
        install_profile()
        _TOOL_TRACE_MECHANISM = "profile"


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
