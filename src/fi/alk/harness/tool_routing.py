"""Which of an agent's tools reach the test world, read from the world's side during a readiness check.

The qualifying conversation's calls are compared with what the world itself received; each tool it did not show is
then asked for once, within one bound for the whole check. What is recorded is an observation: it never fails the
check."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlsplit
from uuid import uuid4

from . import call_runner, chat_call_runner
from .chat_transport import create_chat_wrapper
from .contract import AgentContract, RuntimeInterface
from .environment import ToolExecutionResult
from .handler_child import HandlerChannel
from .process_runtime import EnvironmentRuntime
from .world.runtime import Call

logger = logging.getLogger(__name__)


TOOL_ROUTING = "tool-routing-observations.json"


class RoutedTool(TypedDict, total=False):
    """One tool in `tool-routing-observations.json`: routed (with its calls and outcomes) or not received."""

    tool: str
    routed: bool
    calls: int
    outcomes: list[str]
    reason: str
    source: str


class UnobservedTool(TypedDict, total=False):
    """A tool whose routing could not be observed, and why."""

    tool: str
    reason: str
    source: str


def world_call_log(runtime: EnvironmentRuntime, since: float = 0.0) -> list[Call]:
    """The calls the test world itself received since ``since``: its call log, written by
    the world's own surfaces (the tool proxy in front of a tools service, the MCP toolbox),
    never by the agent."""
    try:
        return [call for call in call_runner.collect_tool_trace_calls(runtime) if call.at >= since]
    except Exception as unread:  # noqa: BLE001 - routing is an observation: a log that cannot be read is none received
        logger.info("world call log not read: %s", type(unread).__name__)
        logger.debug("the failure in full", exc_info=True)
        return []


def received_calls(logged: Sequence[Call], ran: Sequence[Call]) -> list[Call]:
    """What the world received: its call log, then each call the harness ran at the world that the log does not
    already hold (a door that mirrors its calls into the log writes them there too)."""
    unmatched = [(call.name, call.arguments) for call in logged]
    extra = []
    for call in ran:
        key = (call.name, call.arguments)
        if key in unmatched:
            unmatched.remove(key)
        else:
            extra.append(call)
    return [*logged, *extra]


def _logged_tools(contract, runtime, bundle: Path | None = None) -> set[str]:
    """Tools the world logs every call of: a service tool behind the tool proxy, and a tool
    the bundle's MCP toolbox serves. Any other tool runs inside the agent, so the world holds
    its data but no record of the call."""
    endpoints = getattr(runtime, "endpoints", None) or {}
    logged: set[str] = set()
    if "tool_proxy" in endpoints:
        logged |= {e.tool for e in contract.tool_entrypoints if e.mode == "service"}
    if "mcp_toolbox" in endpoints and bundle is not None:
        try:
            served = json.loads(
                (bundle / "generated" / "mcp-toolbox" / "toolbox.json").read_text(encoding="utf-8")
            ).get("tools")
        except (OSError, ValueError, AttributeError):
            served = None
        logged |= set(served) if isinstance(served, dict) else set()
    return logged


def _route(endpoint: str) -> str:
    return "/" + urlsplit(endpoint).path.strip("/")


def _tool_names(contract):
    """Which tools a call can be: by the full path the proxy logs with it, else by its name.
    A name is a tool's own, or for a service tool the last segment of its path, which is all an
    older proxy logged; a segment two tools' paths share names both, so neither is credited."""
    names = {tool.name: {tool.name} for tool in contract.tools}
    paths: dict[str, set[str]] = {}
    segments: dict[str, set[str]] = {}
    for entry in contract.tool_entrypoints:
        if entry.mode == "service" and entry.endpoint.strip():
            route = _route(entry.endpoint)
            paths.setdefault(route, set()).add(entry.tool)
            segments.setdefault(route.rsplit("/", 1)[-1], set()).add(entry.tool)

    def tools(call) -> set[str]:
        endpoint = getattr(call, "endpoint", "")
        if endpoint and _route(endpoint) in paths:
            return paths[_route(endpoint)]
        name = str(getattr(call, "name", "") or "")
        return names.get(name) or segments.get(name, set())

    return tools


def _outcome(call) -> str:
    if getattr(call, "refused", False):
        return "refused"
    return "ok" if getattr(call, "ok", True) else "error"


def _routing(
    contract: AgentContract,
    runtime: EnvironmentRuntime,
    *,
    received: Sequence[Call],
    reported: Sequence[Call],
    bundle: Path | None = None,
) -> tuple[list[RoutedTool], list[UnobservedTool]]:
    """Routing read from the world's side only. A tool the world received is routed. A tool
    the agent reports calling that the world logs but never received is not routed, unless a
    received call could have been it or another tool. Any other reported tool runs inside the
    agent, so its routing cannot be observed."""
    names = _tool_names(contract)
    arrived: dict[str, list] = {}
    ambiguous: set[str] = set()
    for call in received:
        candidates = names(call)
        if len(candidates) == 1:
            arrived.setdefault(next(iter(candidates)), []).append(call)
        else:
            ambiguous |= candidates
    tools: list[RoutedTool] = [
        {
            "tool": tool,
            "routed": True,
            "calls": len(calls),
            "outcomes": sorted({_outcome(call) for call in calls}),
        }
        for tool, calls in arrived.items()
    ]
    unobserved: list[UnobservedTool] = []
    logged = _logged_tools(contract, runtime, bundle)
    claimed = {
        tool for call in reported for tool in names(call) if len(names(call)) == 1
    }
    for tool in sorted(claimed - set(arrived)):
        if tool in ambiguous:
            unobserved.append({"tool": tool, "reason": "ambiguous_world_record"})
        elif tool in logged:
            tools.append({"tool": tool, "routed": False, "reason": "not_received"})
        else:
            unobserved.append({"tool": tool, "reason": "no_world_record"})
    return tools, unobserved


def _write_routing(authoring: Path, body) -> None:
    body["tools"] = sorted(body["tools"], key=lambda one: one["tool"])
    body["unobserved"] = sorted(body["unobserved"], key=lambda one: one["tool"])
    path = authoring / "generic-harness" / TOOL_ROUTING
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def record_tool_routing(
    authoring: Path,
    scenario_key: str,
    contract: AgentContract | None,
    runtime: EnvironmentRuntime,
    *,
    received: Sequence[Call],
    reported: Sequence[Call],
    bundle: Path | None = None,
) -> None:
    """Which tools the qualification conversation routed to the test world, beside the tool
    certification: what readiness reads as observed routing, per tool. ``received`` is what
    the world got (its call log and the calls the harness ran there); ``reported`` is every
    call the conversation shows, including those only the agent says it made. A bundle with no
    contract names no tools, so there is nothing to record."""
    if contract is None:
        return
    tools, unobserved = _routing(
        contract, runtime, received=received, reported=reported, bundle=bundle
    )
    _write_routing(
        authoring,
        {
            "schema_version": 1,
            "source": "world_call_log",
            "scenario_key": scenario_key,
            "tools": tools,
            "unobserved": unobserved,
        },
    )


def _outside_world(contract: AgentContract) -> set[str]:
    """Tools the contract says cannot be routed to the test world: no reachable
    implementation, or a dependency the environment cannot stand up a copy of."""
    names = {
        entry.tool
        for entry in contract.tool_entrypoints
        if entry.mode == "unreachable" or (entry.mode == "service" and not entry.service)
    }
    for dependency in contract.dependencies:
        if not (dependency.engine and dependency.reached.has_seam()):
            names.update(dependency.used_by)
    return names


# The one text sent to the agent to ask for a tool its qualifying conversation did not show. Certification runs no
# model, so it cannot be written in the conversation's language: English unless this is changed. Filled with the
# tool's name, its description in brackets when it has one, and " with <argument>=<value>, ..." when it takes any.
TOOL_REQUEST_TEMPLATE = (
    "Please use your {tool} tool{about} now{arguments}. This runs in a disposable test world: call the tool once and "
    "tell me what it returns."
)
_TYPE_EXAMPLES = {"int": 1, "integer": 1, "number": 1, "float": 1.0, "bool": True, "boolean": True}


def _tool_request(tool) -> str:
    """One request for one tool, built from its contract: each argument takes the
    contract's first example value, else a plain value of its type."""
    values = {}
    for arg in tool.args:
        listed = tool.arg_values.get(arg)
        if isinstance(listed, list) and listed:
            values[arg] = listed[0]
        elif listed not in (None, [], ""):
            values[arg] = listed
        else:
            values[arg] = _TYPE_EXAMPLES.get(str(tool.arg_types.get(arg, "")).lower(), "test")
    shown = ", ".join(f"{arg}={value!r}" for arg, value in values.items())
    return TOOL_REQUEST_TEMPLATE.format(
        tool=tool.name,
        about=f" ({tool.description.strip()})" if tool.description.strip() else "",
        arguments=f" with {shown}" if shown else "",
    )


# The per-tool requests after qualification: one fresh message per tool not yet shown,
# bounded so a check with many tools stays well inside the platform's check deadline.
TOOL_REQUEST_LIMIT = 8
TOOL_REQUEST_SECONDS = 300.0


async def request_uncovered_tools(
    *,
    interface: RuntimeInterface,
    endpoint: str,
    runtime: EnvironmentRuntime,
    contract: AgentContract,
    authoring: Path,
    bundle: Path,
    source: Path | None,
    secret_values: Mapping[str, str],
    candidate_hash: str,
    local_auth_values: Mapping[str, str] | None = None,
    work_directory: Path | None = None,
    timeout: float = 120.0,
    budget: float = TOOL_REQUEST_SECONDS,
    limit: int = TOOL_REQUEST_LIMIT,
) -> None:
    """Ask the agent once for each tool the qualification conversation did not show.

    At most ``limit`` requests within ``budget`` seconds in all, so a check stays well
    inside its deadline; the tools left over are recorded as ``not_asked``.

    A tool the contract says has effects is never asked (``has_effects``), whether or not the
    world logs its calls: a logged call is observable, not safe. A tool the contract says
    leaves the world (no reachable implementation, or a dependency the environment cannot
    copy) is not asked either (``reaches_outside``). Each request is a fresh one-message
    conversation driven like a scenario's; at the world only the asked tool runs, and any
    other call the agent returns is refused and listed under ``refused_during_requests``. A
    tool whose contract leaves ``effects`` empty is asked: the asked tool runs only in the test
    world, which is reset after the check, so an effect the contract did not name changes only
    that world, never a real system (tools that leave the world are never asked). A callback agent
    runs its own tools before answering, which the harness cannot stop; the world is reset
    after the check. Routing is read from the world's side as in the conversation. Never
    raises.

    Under per-user isolation each request's tools run in a handler child of its own, started
    in ``work_directory`` as a conversation's are, and the child is stopped when the request
    ends, so a tool that outlasts the deadline is killed rather than left writing to the world.
    """
    path = authoring / "generic-harness" / TOOL_ROUTING
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    body.setdefault("unobserved", [])
    shown = {one["tool"] for one in [*body["tools"], *body["unobserved"]]}
    outside = _outside_world(contract)
    # Requests already sent by an earlier attempt of this check: their count and time spent
    # stay within one bound for the whole check, and an answer is reused only for the same
    # candidate and tool definition.
    ledger = _request_ledger(authoring)
    deadline = time.monotonic() + max(0.0, budget - ledger["seconds"])
    for tool in getattr(contract, "tools", None) or []:
        if tool.name in shown:
            continue
        if tool.name in outside:
            # Asking could reach a real system: the contract says the tool leaves the world.
            body["unobserved"].append({"tool": tool.name, "reason": "reaches_outside"})
            continue
        if getattr(tool, "effects", None):
            # Asking could change something; a synthetic request must only read.
            body["unobserved"].append({"tool": tool.name, "reason": "has_effects"})
            continue
        fingerprint = _tool_fingerprint(candidate_hash, contract, tool)
        earlier = ledger["asked"].get(tool.name)
        if isinstance(earlier, dict) and earlier.get("fingerprint") == fingerprint:
            body["tools"].extend(earlier["tools"])
            body["unobserved"].extend(earlier["unobserved"])
            continue
        left = deadline - time.monotonic()
        if ledger["requests"] >= limit or left <= 0:
            body["unobserved"].append({"tool": tool.name, "reason": "not_asked"})
            continue
        ledger["requests"] += 1
        request_timeout = min(timeout, left)
        since = time.time()
        started = time.monotonic()
        handlers = None
        try:
            traced = len(call_runner.collect_file_tool_calls(runtime))
            if work_directory is not None:
                handlers = await asyncio.to_thread(
                    chat_call_runner.handler_child_at, bundle, source, work_directory, runtime
                )
            key = f"tool-request-{uuid4().hex}"
            target = chat_call_runner.HostedChatTarget(
                wrapper=create_chat_wrapper(
                    interface,
                    endpoint,
                    timeout=request_timeout,
                    total_timeout=request_timeout,
                    secret_values=secret_values,
                    local_auth_values=local_auth_values,
                ),
                contract=contract,
                world=_OnlyAsked(
                    chat_call_runner.tool_world(
                        bundle,
                        contract,
                        runtime,
                        source,
                        HandlerChannel(handlers.sock) if handlers is not None else None,
                    ),
                    tool.name,
                    deadline=time.monotonic() + request_timeout,
                ),
                scenario_key=key,
                scenario_id=key,
            )
            # One bound for the whole request: say() may call the agent up to eight times.
            async with asyncio.timeout(request_timeout):
                await target.say(_tool_request(tool))
            if target.world.timed_out:
                raise TimeoutError(f"{tool.name} outlasted the readiness request deadline")
            reported = [
                *target.world.calls,
                *call_runner.collect_file_tool_calls(runtime)[traced:],
            ]
        except Exception as exc:  # noqa: BLE001 - an unanswered request leaves the tool unobserved
            logger.info("readiness request for %s failed: %s", tool.name, type(exc).__name__)
            logger.debug("the failure in full", exc_info=True)
            failed = [{"tool": tool.name, "reason": "request_failed"}]
            body["unobserved"].extend(failed)
            _note_request(
                ledger, tool.name, fingerprint, [], failed, time.monotonic() - started
            )
            continue
        finally:
            if handlers is not None:
                # Kills the child's whole process group: a tool still running past the deadline
                # makes no late write to the world.
                await asyncio.to_thread(handlers.close)
        tools, unobserved = _routing(
            contract,
            runtime,
            received=received_calls(world_call_log(runtime, since), target.received),
            reported=[call for call in reported if call.name == tool.name],
            bundle=bundle,
        )
        asked = [
            {**one, "source": "tool_request"}
            for one in tools
            if one["tool"] == tool.name
        ]
        left_over = [one for one in unobserved if one["tool"] == tool.name]
        if target.world.refused:
            body.setdefault("refused_during_requests", []).append(
                {"asked": tool.name, "refused": sorted(set(target.world.refused))}
            )
        if not asked and not left_over:
            left_over = [{"tool": tool.name, "reason": "not_called"}]
        body["tools"].extend(asked)
        body["unobserved"].extend(left_over)
        _note_request(
            ledger, tool.name, fingerprint, asked, left_over, time.monotonic() - started
        )
    _write_routing(authoring, body)
    _save_ledger(authoring, ledger)


class _OnlyAsked:
    """The world as one tool request sees it: only the asked tool runs.

    Any other call the agent returns is refused, not run, and named in ``refused``, so a
    request for a read-only tool can never make the world run a tool with effects. The asked
    tool runs in a worker thread bounded by the request's ``deadline``: the world runs a call
    synchronously, so a handler that blocks would otherwise hold the request past it.
    """

    def __init__(self, world, asked: str, *, deadline: float):
        self._world = world
        self._asked = asked
        self._deadline = deadline
        self.refused: list[str] = []
        # The asked tool outlasted the deadline: the world's door answers the agent with an error, so the request
        # reads this rather than the reply.
        self.timed_out = False

    def __getattr__(self, name):
        return getattr(self._world, name)

    def handle_tool_call(self, tool_call, **context):
        name = str(
            tool_call.get("name") or (tool_call.get("function") or {}).get("name") or ""
        )
        if name == self._asked:
            outcome: dict = {}

            def run():
                try:
                    outcome["result"] = self._world.handle_tool_call(
                        tool_call, **context
                    )
                except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's side
                    outcome["error"] = exc

            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            worker.join(max(0.0, self._deadline - time.monotonic()))
            if worker.is_alive():
                # The thread cannot be stopped; its late result is discarded with this world. Under
                # per-user isolation the request's handler child is killed when the request ends.
                self.timed_out = True
                raise TimeoutError(
                    f"{self._asked} outlasted the readiness request deadline"
                )
            if "error" in outcome:
                raise outcome["error"]
            return outcome["result"]
        error = f"only {self._asked} may run during this readiness request"
        self._world.calls.append(
            Call(
                name=name,
                arguments={},
                ok=False,
                refused=True,
                error=error,
                at=time.time(),
            )
        )
        self.refused.append(name)
        return ToolExecutionResult(
            tool_call_id=tool_call.get("id") or tool_call.get("tool_call_id"),
            tool_name=name or "unknown",
            content=error,
            result=None,
            success=False,
            error=error,
            state_updates={},
        )


TOOL_REQUESTS = "tool-requests.json"


def _tool_fingerprint(candidate_hash: str, contract, tool) -> str:
    """What an earlier answer about ``tool`` holds for: the candidate and the tool's definition."""
    definition = {
        "tool": tool.model_dump(mode="json"),
        "entrypoints": [
            entry.model_dump(mode="json")
            for entry in contract.tool_entrypoints
            if entry.tool == tool.name
        ],
    }
    text = json.dumps([candidate_hash, definition], sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class AskedTool(TypedDict):
    """One tool asked for by an earlier attempt of the check: what the answer holds for, and what it showed."""

    fingerprint: str
    tools: list[RoutedTool]
    unobserved: list[UnobservedTool]


class RequestLedger(TypedDict):
    """The tool requests this check's attempts have sent, so all of them stay within one bound."""

    asked: dict[str, AskedTool]
    requests: int
    seconds: float


def _request_ledger(authoring: Path) -> RequestLedger:
    """The tool requests earlier attempts of this check sent, and the time they took."""
    path = authoring / "generic-harness" / TOOL_REQUESTS
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    asked = stored.get("asked") if isinstance(stored, dict) else None
    seconds = stored.get("seconds") if isinstance(stored, dict) else None
    requests = stored.get("requests") if isinstance(stored, dict) else None
    return {
        "asked": asked if isinstance(asked, dict) else {},
        "requests": requests if isinstance(requests, int) else 0,
        "seconds": float(seconds) if isinstance(seconds, (int, float)) else 0.0,
    }


def _note_request(
    ledger: RequestLedger,
    tool: str,
    fingerprint: str,
    tools: list[RoutedTool],
    unobserved: list[UnobservedTool],
    spent: float,
) -> None:
    ledger["asked"][tool] = {
        "fingerprint": fingerprint,
        "tools": tools,
        "unobserved": unobserved,
    }
    ledger["seconds"] += max(0.0, spent)


def _save_ledger(authoring: Path, ledger: RequestLedger) -> None:
    path = authoring / "generic-harness" / TOOL_REQUESTS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
