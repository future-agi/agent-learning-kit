"""Hosted HTTP chat calls against an already-provisioned Bundle V2 process world."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from fi._diagnostics import sanitize_diagnostic
from fi.simulate.agent.wrapper import AgentInput
from fi.simulate.agent.wrappers.http import HTTPAgentWrapper, HTTPCallFailure

from .actors import as_played
from .job import job_actors_switch
from .call_runner import (
    ArtifactUploader,
    CallRunnerContext,
    ToolCaptureFailed,
    record_key_columns,
    _clear_file_tool_calls,
    _with_scenario_edits,
    collect_seam_calls,
    contract_worker_names,
    declared_worker_ids,
)
from .contract import AgentContract
from .privilege import IsolationMode, isolation_mode
from .hosted_scheduler import WORKER_LIMITS_NOT_ENFORCED, CallAborted, CallCancelled, CallOutcome, Scenario, World
from .browser_record import (
    WORLD_END_UNREADABLE,
    browser_blocked,
    browser_blocked_from_runtime,
    browser_counts,
    browser_evidence,
    browser_file_body,
    browser_shots_dir,
    sealed_browser_blocked,
    upload_browser_state,
    upload_screenshots,
)
from .kit_browser import browser_notices
from .outbound import ArtifactKind, format_rfc3339_millis
from .process_runtime import EnvironmentRuntime
from .run.conversation import ChatActors, TargetConversationEnded, Transcript, converse
from .scenario import Scenario as ConversationScenario
from .source_folder import contract_folder
from .usage import (
    UsageDenied,
    UsageUnavailable,
    failure_domain_for_code,
    simulator_funding,
)
from .handler_child import ChildWorld, HandlerChannel, HandlerChild, start_handler_child
from . import tool_evidence as evidence
from .world.runtime import Call, GeneratedWorld, graded_calls, trace_row
from .world.stores import StoreError
from .world.stores.postgres import AttachedPostgresStore, attached_world_store
from .world_door import door_failure, world_action

logger = logging.getLogger(__name__)


async def _check_text_usage(reporter: Any, funding: str) -> None:
    """Stop a platform-funded text call the plan does not allow, before it starts."""
    if reporter is None or funding != "platform":
        return
    try:
        await asyncio.to_thread(reporter.check, "text_call")
    except UsageDenied as exc:
        raise CallAborted(
            f"text_usage_check_denied: {exc}", code="usage_exhausted"
        ) from exc
    except UsageUnavailable as exc:
        raise CallAborted(
            f"text_usage_check_failed: {exc}", code="usage_check_failed"
        ) from exc


async def _record_text_usage(
    reporter: Any,
    *,
    scenario_key: str,
    started: datetime,
    funding: str,
    transcript: Transcript | None,
    failure: BaseException | None,
) -> None:
    if reporter is None:
        return
    partial = transcript or getattr(failure, "partial_transcript", None)
    simulator_tokens = (
        partial.simulator_input_tokens + partial.simulator_output_tokens
        if isinstance(partial, Transcript)
        else 0
    )
    completed = transcript is not None
    try:
        await asyncio.to_thread(
            reporter.record,
            action="text_call",
            scenario_key=scenario_key,
            amount=simulator_tokens,
            funding=funding,
            occurred_at=started,
            outcome="completed" if completed else "failed",
            failure_domain=(
                None
                if completed
                else failure_domain_for_code(getattr(failure, "code", "call_failed"))
            ),
        )
    except Exception:  # noqa: BLE001 - call failure must not hide a metering warning
        logger.exception(
            "Could not record hosted text usage for scenario %s",
            scenario_key,
        )
    if simulator_tokens == 0:
        logger.warning(
            "Hosted text call produced no simulator tokens for scenario %s",
            scenario_key,
        )


DEFAULT_CHAT_TARGET_TIMEOUT_SECONDS = 300.0
# After a person's Stop the call seals what it kept: the actors' last turns get this much, so the transcript reaches the
# scheduler inside its seal bound (15 s from the stop), as a stopped voice call's teardown is cut short.
_STOPPED_WIND_DOWN_SECONDS = 8.0
_STOP_POLL_SECONDS = 0.25


async def _sealed_through_stop(
    stop_requested: Callable[[], bool], drive: Callable[[asyncio.Event], Awaitable[CallOutcome]]
) -> CallOutcome:
    """Run a chat call so a person's stop cuts its conversation, never what follows: `drive` sets its event when the
    conversation is over; a stop before that reaches the call, a stop after is absorbed and said as the person's stop
    once the call has sealed. A cancel that is not a stop reaches the call anywhere."""
    sealing = asyncio.Event()
    call = asyncio.ensure_future(drive(sealing))
    stopped_while_sealing = False
    while not call.done():
        try:
            await asyncio.wait({call})
        except asyncio.CancelledError:
            if stop_requested():
                task = asyncio.current_task()
                if task is not None and hasattr(task, "uncancel"):
                    task.uncancel()
                if sealing.is_set():
                    stopped_while_sealing = True
                    continue
            call.cancel()
    outcome = call.result()
    if stopped_while_sealing:
        # Sealed like any call, then said as the person's stop.
        raise CallCancelled(partial=replace(outcome, stop_reason="user_canceled"))
    return outcome


def _stopped_call(cancel: asyncio.CancelledError) -> tuple[CallCancelled, Transcript]:
    """The failure and transcript a call seals when a person's stop cut its conversation (the caller checked the stop)."""
    failure = CallCancelled()
    partial = getattr(cancel, "partial_transcript", None)
    setattr(failure, "partial_transcript", partial)
    task = asyncio.current_task()
    if task is not None and hasattr(task, "uncancel"):
        # Python 3.11+: the uploads that follow run as an uncancelled task. On 3.10 the caught cancel leaves nothing
        # pending.
        task.uncancel()
    return failure, partial or Transcript()


async def _within_stop_bound(stop_requested: Callable[[], bool], step: Awaitable[None]) -> None:
    """Wait for `step`, but no longer than `_STOPPED_WIND_DOWN_SECONDS` after a person's stop."""
    task = asyncio.ensure_future(step)
    loop = asyncio.get_running_loop()
    stopped_at: float | None = None
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=_STOP_POLL_SECONDS)
            if stopped_at is None and stop_requested():
                stopped_at = loop.time()
            cut = stopped_at is not None and loop.time() - stopped_at >= _STOPPED_WIND_DOWN_SECONDS
            if cut and not task.done():
                logger.warning("chat_wind_down_cut_on_stop seconds=%s", _STOPPED_WIND_DOWN_SECONDS)
                break
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    if not task.cancelled():
        task.result()


def _unproxied_worker_notices(document: Mapping[str, Any], bundle_dir: Any = None) -> list[dict[str, Any]]:
    """A chat agent whose tool calls the harness runs itself has no tool proxy, so nothing holds a worker's deadline or
    permitted actions: a scenario that relies on either says so, and is held rather than graded."""
    from .call_runner import relies_on_worker_limits

    budget = document.get("worker_budget_seconds")
    if (not budget or isinstance(budget, bool)) and not relies_on_worker_limits(document, bundle_dir):
        return []
    return [{
        "code": WORKER_LIMITS_NOT_ENFORCED,
        "message": "This agent's tools aren't called through the test environment, so no participant's limits were "
        "enforced in this trial, and its checks were not graded.",
    }]


# An agent's own chat deadline in its config, as `voice_call_timeout_seconds` is for a voice call: how long one reply
# may take, and the readiness conversation with it. Absent, the defaults stand.
CHAT_TIMEOUT_CONFIG_KEY = "chat_call_timeout_seconds"
_BUILD_BUDGET_SECONDS = 3300.0


def agent_chat_timeout_seconds(job: Any) -> float | None:
    """The agent's `chat_call_timeout_seconds`, never past the build's budget
    (`ALK_HOSTED_AUTHORING_MAX_DURATION_SECONDS`); None when it is absent or not a positive number."""
    agent = getattr(job, "agent", None)
    config = getattr(agent, "config", None)
    raw = config.get(CHAT_TIMEOUT_CONFIG_KEY) if isinstance(config, dict) else None
    if isinstance(raw, bool):
        return None
    try:
        seconds = float(raw) if isinstance(raw, (int, float, str)) else None
    except ValueError:
        return None
    if seconds is None or not math.isfinite(seconds) or seconds <= 0:
        return None
    return min(seconds, _build_budget_seconds())


def _build_budget_seconds() -> float:
    try:
        return float(os.environ.get("ALK_HOSTED_AUTHORING_MAX_DURATION_SECONDS") or _BUILD_BUDGET_SECONDS)
    except ValueError:
        return _BUILD_BUDGET_SECONDS


def chat_conversation_timeout(job: Any, default: float, turns: int) -> float:
    """A whole conversation's deadline: `default`, or, for an agent with its own reply deadline, that deadline for each
    of the conversation's turns when longer, never past the build's budget. A short reply deadline never shortens it."""
    per_reply = agent_chat_timeout_seconds(job)
    if per_reply is None:
        return default
    return min(max(default, per_reply * max(1, turns)), max(default, _build_budget_seconds()))


def _chat_target_timeout_seconds() -> float:
    """Return the per-turn target deadline without accepting unusable values."""
    raw = os.getenv("ALK_CHAT_TARGET_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_CHAT_TARGET_TIMEOUT_SECONDS
    try:
        configured = float(raw)
    except ValueError:
        return DEFAULT_CHAT_TARGET_TIMEOUT_SECONDS
    return (
        configured
        if 1.0 <= configured <= 600.0
        else DEFAULT_CHAT_TARGET_TIMEOUT_SECONDS
    )


def _duration_ms(started: datetime, ended: datetime) -> int:
    return max(0, round((ended - started).total_seconds() * 1000))


def _scenario_document(bundle_dir: Path, key: str) -> dict[str, Any]:
    for path in sorted((bundle_dir / "scenarios").glob("*/scenario.json")):
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(body, dict) and body.get("scenario_key") == key:
            return body
    raise CallAborted(f"chat_scenario_document_unavailable: scenario_key={key!r}")


def _qmark_to_postgres(statement: str) -> str:
    """Translate generated SQLite-style positional placeholders, never SQL structure."""
    return re.sub(r"\?", "%s", statement)


class _HostedToolStore:
    """Generated handler ``Db`` adapter over the leased world's attached Postgres database."""

    key = "hosted-postgres"

    def __init__(self, target: str | AttachedPostgresStore) -> None:
        # A store already chosen for the world (`attached_world_store`), or the world role's address (a handler child).
        self._store = (
            target if isinstance(target, AttachedPostgresStore) else AttachedPostgresStore(target, restore_as_role=True)
        )

    def start(self) -> None:
        self._store.start()

    def stop(self) -> None:
        return

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        # A handler's read: the agent's tools' own role, under the world's row security.
        return self._store.query_as_world(_qmark_to_postgres(sql), params)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        return self._store.execute(_qmark_to_postgres(sql), params)

    def state(
        self, only: Sequence[str] | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        return self._store.state(only=only)

    def collections(self) -> list[str]:
        return list(self._store.state())

    def records(self, collection: str) -> list[dict[str, Any]]:
        return self._store.table(collection)

    def add(self, collection: str, record: Mapping[str, Any]) -> dict[str, Any]:
        return self._store.add(collection, dict(record))


def _entries(contract: Any) -> list[dict[str, Any]]:
    return [entry if isinstance(entry, dict) else entry.model_dump() for entry in (contract.tool_entrypoints or [])]


def service_actions(contract: Any) -> set[str]:
    """The agent's tools its own tools service runs, as world actions: a service entry's route, `/` read as `.`.
    A route with a path parameter is left to the world's handler."""
    return {
        world_action(route)
        for entry in _entries(contract)
        if entry.get("mode") == "service"
        and (route := str(entry.get("endpoint") or "").strip("/"))
        and "{" not in route
    }


class _ServiceTools:
    """The agent's own tools service runs its service actions, as the tool proxy forwards them on a voice call; any
    other tool runs through the world's handler. The agent's calls are recorded in the world as its handlers would
    record them, so grading reads them the same way, a call the service never answered included."""

    def __init__(self, world: Any, service: Any, handlers: Any, actions: set[str]) -> None:
        self.world = world
        self.service = service
        self.handlers = handlers
        self.actions = actions

    def run(self, call: Any) -> Any:
        from .world_door import AGENT

        if call.actor == AGENT and call.action not in self.actions:
            return self.handlers.run(call)
        try:
            ran = self.service.run(call)
        except Exception as failed:
            if call.actor == AGENT and hasattr(self.world, "calls"):
                self.world.calls.append(
                    Call(name=call.name, arguments=dict(call.arguments or {}), ok=False, error=str(failed), at=time.time())
                )
            raise
        if call.actor == AGENT and hasattr(self.world, "calls"):
            ok = ran.status < 400
            self.world.calls.append(
                Call(
                    name=call.name,
                    arguments=dict(call.arguments or {}),
                    result=ran.result,
                    ok=ok,
                    refused=not ok and ran.status < 500,
                    error="" if ok else (ran.error or f"HTTP {ran.status}"),
                    at=time.time(),
                )
            )
        return ran


def chat_door(
    world: Any,
    mirror_dsn: str | None,
    roster: dict[str, dict[str, Any]],
    faults: list[Any],
    tools_url: str | None = None,
    service_actions: set[str] | None = None,
    bundle_dir: Any = None,
    records_dsn: str | None = None,
) -> Any:
    """The world's door for a chat run: the agent's own tools service runs its service actions and every actor's tool
    action when it has one, the world's own handlers the rest; the trial's action log is kept in memory (mirrored to the world's table when it keeps actors), and
    who may do what is the environment's actor list."""
    from .call_runner import sealed_routes
    from .world_door import (
        AGENT,
        DictRoster,
        HandlerExecutor,
        HttpExecutor,
        MemoryLog,
        PostgresLog,
        RecordExecutor,
        Routed,
        TeeLog,
        WorldDoor,
    )

    def handle(call: Any) -> tuple[bool, Any, str] | None:
        made = len(getattr(world, "calls", []))
        result = world.handle_tool_call({"name": call.name, "arguments": call.arguments})
        if call.actor != AGENT and hasattr(world, "calls"):
            # An actor's action is in the action log, never in the agent's own calls that grading reads.
            del world.calls[made:]
        if result is None:
            return None
        return bool(getattr(result, "success", True)), getattr(result, "result", None), str(result.content)

    records = None
    from .privilege import IsolationMode, isolation_mode

    if mirror_dsn and (records_dsn or isolation_mode() is not IsolationMode.PER_USER):
        from .world_door import PostgresRecords

        # Record changes run as the world's own role; the mirror's superuser only keeps the log. With world roles and
        # none given, the door has no record changes at all rather than make them as the superuser.
        records = RecordExecutor(PostgresRecords(records_dsn or mirror_dsn))
    tools = (
        _ServiceTools(world, HttpExecutor(tools_url), HandlerExecutor(handle), set(service_actions or ()))
        if tools_url
        else HandlerExecutor(handle)
    )
    return WorldDoor(
        Routed(tools, records),
        TeeLog(MemoryLog(), PostgresLog(mirror_dsn) if mirror_dsn else None),
        DictRoster(roster),
        faults=lambda: [one for one in faults if isinstance(one, dict)],
        faults_by_tool=True,
        workers=sealed_routes(bundle_dir),
    )


def _at(row: Mapping[str, Any]) -> float:
    value = row.get("at")
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _trial_log(door: Any, since: float = 0.0) -> list[dict[str, Any]]:
    """This trial's action log. The world's own table holds every row, the agent's calls through its tool proxy
    included; this process's memory holds only what passed its own door. The table keeps every trial played on it, so
    this trial's rows are the ones from its start on."""
    return _readable_trial_log(door, since) or []


def _faults_readable(runtime: Any) -> bool:
    """Whether the faults set for this trial read back from the world's database, where the tool proxy reads them."""
    from .call_runner import _find_postgres_endpoint, trial_faults_readable

    database = _find_postgres_endpoint(runtime)
    return database is not None and trial_faults_readable(database.address)


def _readable_trial_log(door: Any, since: float = 0.0, *, proxied: bool = False) -> list[dict[str, Any]] | None:
    """This trial's action log as `_trial_log` reads it, or None when it could not be read: neither the world's table
    nor this process's own rows, or, on a proxied trial, the world's table, which alone holds the agent's calls."""
    try:
        log = door.log.rows()
    except Exception:  # noqa: BLE001 - this process's own rows unreadable: the world's table may stand
        log = None
    mirror = getattr(door.log, "mirror", None)
    if mirror is not None:
        try:
            own = [row for row in mirror.rows() if _at(row) >= since]
            log = own or (log if log is not None else own)
        except Exception:  # noqa: BLE001 - the world's table unreadable: this process's own rows stand, unless proxied
            if proxied:
                return None
    return log


def _world_actors_record(
    door: Any,
    listing: Any,
    dispatcher: Any,
    cast: Any,
    meter: Any,
    limits: dict[str, Any],
    since: float = 0.0,
    contract: Any = None,
    keys: Any = frozenset(),
) -> dict[str, Any] | None:
    """What actors did in the world this trial and what each participant changed, sealed beside the transcript as a
    call seals it, on every trial with a door, actors or not (`actions` and `decisions` then empty); None only when
    there is no door."""
    from .call_runner import actor_rows, approvals, trial_shared_record_fields, trial_state_changes

    if door is None:
        return None

    listed = listing.by_id()
    log = _trial_log(door, since)
    decisions = dispatcher.record() if dispatcher is not None else []
    if cast is not None:
        decisions += [
            {"actor": one["actor"], "play": "live", "action": one.get("action"),
             "outcome": (one.get("outcome") or {}).get("outcome"), "why": one.get("why"),
             "verdict": one.get("verdict")}
            for one in cast.events
            if one.get("kind") == "acted"
        ]
        # A wait that ran out, and what was taken for the actor, as the world's dispatcher records its own.
        decisions += [
            {"actor": one["actor"], "play": "timeout",
             **({"action": one.get("fallback")} if one.get("outcome") == "fallback_action" else {}),
             "fallback": one.get("fallback"), "outcome": one.get("outcome"),
             **({"status": one["status"]} if one.get("status") is not None else {}),
             **({"action_outcome": one["action_outcome"]} if one.get("action_outcome") else {}),
             **({"timeout_seconds": one["timeout_seconds"]} if one.get("timeout_seconds") else {})}
            for one in cast.events
            if one.get("kind") == "timeout"
        ]
    acted = actor_rows(log, listed, decisions)
    changes = trial_state_changes(log, listed, decisions)
    record = {
        "actions": acted,
        "decisions": decisions,
        "state_changes": changes,
        "approvals": approvals(log, decisions),
        "limits": dict(limits),
        "usage": meter.rows(),
    }
    # Which participants named the same record and what the world made of each call.
    record.update(trial_shared_record_fields(log, contract, keys))
    return record


def _chat_actor_summary(cast: Any, world_record: dict[str, Any] | None, listing: Any) -> tuple[dict[str, Any], ...]:
    """The call's actor summary: the actors who wrote in the conversation and every actor that acted in the world."""
    from .actor_dispatch import with_acts, world_summary

    speaking = tuple(cast.summary()) if cast is not None else ()
    decisions = (world_record or {}).get("decisions") or []
    return with_acts(
        speaking + tuple(world_summary(decisions, listing.by_id(), {str(one.get("actor")) for one in speaking})),
        decisions,
    )


def handler_sources(bundle_dir: Path, contract: AgentContract) -> dict[str, str]:
    """Each declared tool's generated handler source, by tool name."""
    sources: dict[str, str] = {}
    for tool in contract.tools:
        path = bundle_dir / "handlers" / f"{tool.name}.py"
        if path.is_file():
            sources[tool.name] = path.read_text(encoding="utf-8")
    return sources


def tool_world(
    bundle_dir: Path,
    contract: AgentContract,
    runtime: EnvironmentRuntime,
    source_directory: Path | None = None,
    handler_channel: HandlerChannel | None = None,
) -> GeneratedWorld:
    if runtime.metadata.get("observation_mode") == "native":
        import random
        from .world.observation import ObservationWorld, http_endpoints
        return ObservationWorld(
            runtime.world_index, random.Random(0), http_endpoints(runtime),
            observations=contract.runtime.observations if contract.runtime else {},
        )
    endpoint = runtime.endpoints.get("world_db")
    if endpoint is None or endpoint.protocol != "postgres":
        raise CallAborted(
            "chat_world_unavailable: world_db postgres endpoint is absent"
        )
    tools = [tool.model_dump(mode="json") for tool in contract.tools]
    # The world's own SQL (a handler's, a check's read) runs as its role, never as the harness.
    try:
        attached = attached_world_store(endpoint)
    except StoreError as error:
        raise CallAborted(str(error)) from error
    if handler_channel is not None:
        # Under per-user isolation the handlers, and the agent code they import, run in the
        # handler child; nothing of the agent's code is imported into this process. State is
        # read as the harness, as with one fixed user, so checks grade the same rows.
        return ChildWorld(
            store=_HostedToolStore(attached),
            channel=handler_channel,
            tools=tools,
            refusal_signature=contract.refusal_signature,
        )
    world = GeneratedWorld(store=_HostedToolStore(attached))
    world.tools = tools
    world.handlers = handler_sources(bundle_dir, contract)
    world.refusal_signature = contract.refusal_signature
    if source_directory is not None:
        if isolation_mode() is IsolationMode.PER_USER:
            # This process holds the harness's capabilities; the agent's code runs only in its
            # handler child.
            raise CallAborted(
                "handler_child_required: per-user isolation never imports the agent's code here",
                code="call_failed",
            )
        world.reach(
            str(source_directory),
            contract_folder(contract),
        )
    return world


def handler_child_for(
    context: CallRunnerContext, runtime: EnvironmentRuntime
) -> HandlerChild | None:
    """Under per-user isolation, the process a conversation's generated tool handlers run in.

    None with one fixed user, and for a world that runs no generated handlers.
    """
    return handler_child_at(
        context.bundle_dir, context.source_directory, context.work_directory, runtime
    )


def handler_child_at(
    bundle_dir: Path,
    source_directory: Path | None,
    work_directory: Path,
    runtime: EnvironmentRuntime,
) -> HandlerChild | None:
    """`handler_child_for` from its parts, for a caller that runs no conversation context."""
    if isolation_mode() is not IsolationMode.PER_USER:
        return None
    if runtime.metadata.get("observation_mode") == "native":
        return None
    endpoint = runtime.endpoints.get("world_db")
    contract_path = bundle_dir / "contract.json"
    if (
        endpoint is None
        or endpoint.protocol != "postgres"
        or not contract_path.is_file()
    ):
        return None
    if endpoint.tool_address is None:
        # The world's own role is what the agent's code may use; without one (an authoring lane)
        # there is no safe address to hand it, so the conversation does not start.
        raise CallAborted(
            "handler_world_unscoped: this world has no role of its own",
            code="call_failed",
        )
    contract = AgentContract.model_validate_json(
        contract_path.read_text(encoding="utf-8")
    )
    return start_handler_child(
        handlers=handler_sources(bundle_dir, contract),
        tools=[tool.model_dump(mode="json") for tool in contract.tools],
        refusal_signature=contract.refusal_signature,
        dsn=endpoint.tool_address,
        source=source_directory,
        work_directory=work_directory,
        world_index=runtime.world_index,
        subfolder=contract_folder(contract),
    )


def _tools(contract: AgentContract) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for spec in contract.tools:
        properties = {
            argument: {"type": _json_type(spec.arg_types.get(argument, "string"))}
            for argument in spec.args
        }
        values.append(
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": list(spec.args),
                },
            }
        )
    return values


def _json_type(declared: str) -> str:
    normalized = str(declared or "").lower()
    if any(mark in normalized for mark in ("int", "float", "number")):
        return "number"
    if "bool" in normalized:
        return "boolean"
    if any(mark in normalized for mark in ("list", "array", "sequence")):
        return "array"
    if any(mark in normalized for mark in ("dict", "map", "object")):
        return "object"
    return "string"


def _tool_call(call: dict[str, Any], index: int) -> tuple[str, dict[str, Any], str]:
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = str(call.get("name") or function.get("name") or "")
    raw = call.get("arguments", function.get("arguments", {}))
    if isinstance(raw, str):
        try:
            arguments = json.loads(raw)
        except ValueError:
            arguments = {"_raw": raw}
    else:
        arguments = dict(raw or {}) if isinstance(raw, dict) else {}
    return name, arguments, str(call.get("id") or f"call_{index}")


def _tool_response_result(response: Mapping[str, Any]) -> Any:
    """Return the callback's real tool result without inventing a second execution."""
    value = response.get("result", response.get("content"))
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _record_completed_tool_call(
    world: GeneratedWorld,
    *,
    name: str,
    arguments: Mapping[str, Any],
    response: Mapping[str, Any],
    call_id: str = "",
) -> None:
    """Record a tool the submitted agent says it already ran: the agent's own account of the call.

    Callback-backed agents can return the request and its completed response together. Such a call
    is never replayed through the generated world: that would risk repeating a side effect, and
    would turn a real success into ``no such tool`` when no mock handler was authored. It is kept
    in the world's list, tagged as the agent's own account (tool_evidence.py), since the world did
    not serve it and the kit did not see it run. ``call_id`` is the id the agent gave the call, when
    it gave one; it is kept with the runtime's ids.
    """
    error_value = response.get("error")
    error = str(error_value) if error_value not in (None, "") else ""
    refused = bool(response.get("refused", False))
    declared_success = response.get("success", response.get("ok"))
    ok = (
        bool(declared_success)
        if declared_success is not None
        else not error and not refused
    )
    result = _tool_response_result(response)
    # The proxy's failure answer the agent's tool handed back as its value is a failed call of ours, never a result.
    failed = door_failure(result)
    if failed is not None:
        ok, error, result = False, failed, None
    world.calls.append(
        Call(
            name=name,
            arguments=dict(arguments),
            result=result,
            ok=ok,
            refused=refused,
            error=error,
            at=time.time(),
            runtime_ids={"call_id": call_id} if call_id else {},
            source=evidence.AGENT,
        )
    )


class HostedChatTarget:
    """The already-running Bundle V2 chat process, exposed as the normal conversation target.

    ``converse`` owns the simulated customer's turns.  This target owns only the submitted
    agent's side of the exchange and response-carried tool evidence.  Keeping that split identical
    to the local repository target prevents the hosted lane from silently becoming a one-message
    smoke test again.
    """

    key = "hosted_repository"

    def __init__(
        self,
        *,
        wrapper: Any,
        contract: AgentContract,
        world: GeneratedWorld,
        scenario_key: str,
        scenario_id: str,
        faults: list[dict[str, Any]] | None = None,
        door: Any = None,
    ) -> None:
        self._wrapper = wrapper
        self._contract = contract
        self.world = world
        self._scenario_key = scenario_key
        self._scenario_id = scenario_id
        self._messages: list[dict[str, Any]] = []
        self._tool_call_ids: set[str] = set()
        self._turn = 0
        # Calls the harness ran at the world itself, not ones the agent says it ran.
        self.received: list[Call] = []
        # The world's door for the agent's calls: permission, faults and the action log, as at the HTTP door.
        self._door = door if door is not None else chat_door(world, None, {}, faults or [])

    async def open(self) -> None:
        return

    def _action(self, tool: str) -> str:
        """A tool's world action, named as the HTTP door names it (its route with `/` as `.`), else the tool."""
        for entry in self._contract.tool_entrypoints or []:
            entry = entry if isinstance(entry, dict) else entry.model_dump()
            if entry.get("tool") == tool and str(entry.get("endpoint") or "").strip("/"):
                return world_action(str(entry["endpoint"]))
        return tool

    def _world_call(self, call_id: str, name: str, arguments: Any) -> str:
        """One of the agent's calls in the generated world, through the world's door: the scenario's timeout on it if
        it has one (before commit the world never runs it; after commit it runs and the agent never hears back), then
        the answer the world gave. A retry is not deduplicated, as at the HTTP door."""
        from .world_door import ToolCall

        del call_id
        arguments = dict(arguments or {}) if isinstance(arguments, Mapping) else {}
        method = next(
            (
                str(entry.get("method") or "POST").upper()
                for entry in _entries(self._contract)
                if entry.get("tool") == name and entry.get("mode") == "service"
            ),
            "POST",
        )
        effect = next((str(entry.get("effect") or "") for entry in _entries(self._contract) if entry.get("tool") == name), "")
        outcome = self._door.enter(
            ToolCall(action=self._action(name), name=name, arguments=arguments, method=method, effect=effect)
        )
        if outcome.fault is not None:
            mode = outcome.fault.get("mode")
            if mode == "timeout_after_commit":
                if self.world.calls:
                    # Committed, with the answer lost on the way back.
                    self.world.calls[-1].error = "response_lost"
                return "Error: the request timed out"
            self.world._record(
                Call(name=name, arguments=arguments, result=None, ok=False, error=str(mode or "faulted"))
            )
            if mode == "unavailable":
                return "Error: the service is unavailable (503)"
            if mode == "error":
                return f"Error ({outcome.status}): {outcome.reason}"
            return "Error: the request timed out"
        return outcome.answer().decode("utf-8", errors="replace")

    async def say(self, utterance: str) -> str:
        self._messages.append({"role": "user", "content": utterance})
        for continuation in range(8):
            response = await self._wrapper.call(
                AgentInput(
                    thread_id=self._scenario_key,
                    execution_id=self._scenario_id,
                    turn_index=self._turn,
                    scenario_name=self._scenario_key,
                    modality="text",
                    messages=list(self._messages),
                    new_message=dict(self._messages[-1]),
                    tools=_tools(self._contract)
                    if self._contract.runtime
                    and self._contract.runtime.interface
                    and self._contract.runtime.interface.include_tools
                    else [],
                )
            )
            trace = dict((response.metadata or {}).get("external_agent") or {})
            if trace and not trace.get("success", False):
                raise HTTPCallFailure(trace)
            for observed in (response.metadata or {}).get("native_completed_tools", []):
                _record_completed_tool_call(
                    self.world, name=observed["name"],
                    arguments=observed["arguments"], response=observed,
                    call_id=str(observed.get("id") or ""),
                )
            conversation_ended = bool(
                (response.metadata or {}).get("conversation_ended")
            )
            returned = [
                call
                for call in (response.tool_calls or [])
                if not call.get("id") or str(call["id"]) not in self._tool_call_ids
            ]
            if not returned:
                answer = response.content.strip()
                if conversation_ended:
                    raise TargetConversationEnded(answer)
                self._messages.append({"role": "assistant", "content": answer})
                self._turn += 1
                return answer

            self._messages.append(
                {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": returned,
                }
            )
            response_by_id = {
                str(item.get("tool_call_id") or item.get("id") or ""): item
                for item in response.tool_responses or []
                if isinstance(item, Mapping)
                and (item.get("tool_call_id") or item.get("id"))
            }
            returned_ids: set[str] = set()
            for index, call in enumerate(returned, start=1):
                name, arguments, call_id = _tool_call(call, index)
                returned_ids.add(call_id)
                if call.get("id"):
                    self._tool_call_ids.add(call_id)
                provided = response_by_id.get(call_id)
                if provided is not None:
                    _record_completed_tool_call(
                        self.world,
                        name=name,
                        arguments=arguments,
                        response=provided,
                        # Only an id the agent gave; `call_{index}` is the kit's own stand-in.
                        call_id=call_id if call.get("id") else "",
                    )
                    result_content = provided.get("content", provided.get("result"))
                    if not isinstance(result_content, str):
                        result_content = json.dumps(result_content, default=str)
                else:
                    calls_before = len(self.world.calls)
                    result_content = self._world_call(call_id, name, arguments)
                    self.received.extend(self.world.calls[calls_before:])
                self._messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": result_content,
                    }
                )

            # A callback-backed repository has already run its own real tools.  It returns their
            # responses beside the final text; replaying the calls above records deterministic
            # evidence in the generated world, but asking the callback a second time would run the
            # tools twice.  HTTP agents that only return requests continue normally with the
            # generated-world responses appended above.
            provided_ids = set(response_by_id)
            if returned_ids and returned_ids.issubset(provided_ids):
                answer = response.content.strip()
                if conversation_ended:
                    raise TargetConversationEnded(answer)
                self._messages.append({"role": "assistant", "content": answer})
                self._turn += 1
                return answer
        raise RuntimeError(
            "submitted chat agent exceeded 8 tool continuations in one turn"
        )

    async def close(self) -> None:
        return

    @property
    def spent_usd(self) -> float:
        # Provider-side target cost is not observable at this transport seam.
        return 0.0


def _conversation_scenario(document: dict[str, Any]) -> ConversationScenario:
    try:
        normalized = dict(document)
        normalized.setdefault(
            "name", str(normalized.get("scenario_key") or "hosted-chat-scenario")
        )
        return ConversationScenario.model_validate(normalized)
    except Exception as exc:  # noqa: BLE001 - normalize malformed bundle content at the call seam
        raise CallAborted(f"chat_scenario_invalid: {exc}") from exc


def _resolved_chat_actors(
    document: dict[str, Any], bundle_dir: Path, records: Any = None
) -> list[dict[str, Any]]:
    """Every actor the scenario includes that writes in the conversation, with its brief; one that cannot reach a
    chat agent is kept with `entered: false` and the reason, so the record still names it."""
    from fi.simulate.simulation.call_actors import CHANNELS, known_to_others

    from .actors import ScenarioActor, actor_brief, brief_text, load_actors, with_knowledge_read
    from .call_actor import load_call_actors, speaking_entries

    if not document.get("actors"):
        return []
    listing = load_call_actors(bundle_dir)
    full = load_actors(bundle_dir).by_id()
    entries = [ScenarioActor.model_validate(one) for one in document.get("actors") or []]
    resolved = []
    for included in speaking_entries(entries, listing, set(full), full):
        actor = full.get(included.id)
        here = {
            **(actor.model_dump(exclude_none=True) if actor is not None else {"id": included.id}),
            "instruction": included.instruction,
            "trigger": included.trigger.model_dump(),
        }
        if actor is None or "chat" not in CHANNELS.get(str(actor.channel), {}).get("modalities", ()):
            reason = "not_in_list" if actor is None else "not_a_chat_channel"
            resolved.append({**here, "entered": False, "reason": reason})
            continue
        entry = next((one for one in document.get("actors") or [] if (one or {}).get("id") == actor.id), None)
        resolved.append(
            {
                **here,
                "situation": known_to_others(str(document.get("instruction") or "")),
                "language": ", ".join((document.get("persona") or {}).get("languages") or []),
                "brief": brief_text(
                    actor_brief(
                        with_knowledge_read(actor.model_dump(exclude_none=True), bundle_dir, records) or {},
                        entry=with_knowledge_read(entry, bundle_dir, records),
                        scenario=dict(document),
                    )
                ),
            }
        )
    return resolved


async def _drive_conversation(
    target: HostedChatTarget,
    scenario: ConversationScenario,
    contract: AgentContract,
    bundle_dir: Path,
    actors: ChatActors | None = None,
) -> Transcript:
    return await converse(
        target,
        scenario,
        contract,
        world_root=bundle_dir,
        actors=actors,
    )


class HostedChatCallRunner:
    """Drive a repository chat ingress inside its leased world."""

    def __init__(self, adapter: ArtifactUploader, context: CallRunnerContext) -> None:
        from .call_runner import TrialRecords, sealed_seed

        self._adapter = adapter
        self._context = context
        # The last conversation's calls the harness ran at the world (``HostedChatTarget``).
        self.received: list[Call] = []
        self._records = TrialRecords(sealed_seed(context.work_directory))
        contract_path = context.bundle_dir / "contract.json"
        if not contract_path.is_file():
            self._contract: AgentContract | None = None
        else:
            self._contract = AgentContract.model_validate_json(
                contract_path.read_text(encoding="utf-8")
            )

    def started_before_setup(self, runtime: EnvironmentRuntime, state: dict[str, Any]) -> None:
        """Whether the trial started from the seed, as the process that ran its setup read it first."""
        self._records.started(id(runtime), state)

    async def run(
        self,
        scenario: Scenario,
        runtime: EnvironmentRuntime,
        *,
        world: World | None = None,
    ) -> CallOutcome:
        del (
            world
        )  # Handler execution uses the same leased world's endpoint from runtime.
        return await _sealed_through_stop(
            self._context.stop_requested, lambda sealing: self._run(scenario, runtime, sealing)
        )

    async def _run(self, scenario: Scenario, runtime: EnvironmentRuntime, sealing: asyncio.Event) -> CallOutcome:
        if self._contract is None:
            raise CallAborted(
                "chat_contract_unavailable: bundle/contract.json is absent"
            )
        interface = self._contract.runtime.interface if self._contract.runtime else None
        if interface is None or interface.kind not in {"http", "callable", "command"}:
            raise CallAborted(
                "chat_interface_unsupported: an HTTP, callable, or command runtime interface is required"
            )
        endpoint = runtime.endpoints.get("target_http")
        if endpoint is None:
            raise CallAborted(
                "chat_capability_unavailable: target_http endpoint is absent"
            )

        source_key = (
            getattr(scenario, "source_scenario_key", None) or scenario.scenario_key
        )
        document = _with_scenario_edits(
            _scenario_document(self._context.bundle_dir, source_key),
            getattr(self._context.job, "metadata", None),
            source_key,
        )
        document = as_played(document, self._context.bundle_dir, job_actors_switch(self._context.job))
        target_world = tool_world(
            self._context.bundle_dir,
            self._contract,
            runtime,
            self._context.source_directory,
            self._context.handler_channel,
        )
        # An actor's knowledge of the world's records is read from this trial's own store.
        chat_actors = _resolved_chat_actors(
            document,
            self._context.bundle_dir,
            getattr(getattr(target_world, "store", None), "records", None),
        )
        if chat_actors:
            from fi.simulate.simulation.call_actors import with_others

            # The person is told who else is in the conversation, never what they will write.
            document = {
                **document,
                "instruction": with_others(
                    str(document.get("instruction") or ""),
                    [one for one in chat_actors if one.get("entered") is not False],
                ),
            }
        # The world's door for this trial: the agent's calls and the actors' actions, one rule set, one log.
        from .actor_dispatch import DoorActs, action_descriptions, build_dispatcher
        from .actor_meter import ActorMeter
        from .actors import acting_fields, load_actors
        from .call_runner import actor_limits

        listing = load_actors(self._context.bundle_dir)
        world_db = runtime.endpoints.get("world_db")
        service = runtime.endpoints.get("tools_api")
        # The tool proxy and the in-process door log the agent's and its limited workers' calls in the world's table:
        # read it whenever the trial is proxied or has limited workers, cast or not.
        proxied = runtime.endpoints.get("tool_proxy") is not None or runtime.endpoints.get("world_door") is not None
        limited_workers = (Path(self._context.bundle_dir) / "generated" / "tool-proxy" / "permissions.json").is_file()
        # The scenario's faults are a rule of its graded trials; a readiness probe checks the tools' binding without them.
        from .call_runner import _bundle_contract
        from .world_door import routed_faults

        faults = (
            []
            if self._context.readiness_probe
            else routed_faults(document.get("faults") or [], _bundle_contract(self._context.bundle_dir))
        )
        door = chat_door(
            target_world,
            world_db.address
            if (listing.actors or proxied or limited_workers) and world_db is not None and world_db.protocol == "postgres"
            else None,
            {
                one.id: {"permitted_actions": list(one.permitted_actions), "on_behalf_of": one.on_behalf_of}
                for one in listing.actors
            },
            faults,
            tools_url=service.address if service is not None else None,
            service_actions=service_actions(self._contract),
            bundle_dir=self._context.bundle_dir,
            records_dsn=world_db.tool_address if world_db is not None else None,
        )
        acts = DoorActs(door, self._contract.model_dump())
        meter = ActorMeter()
        limits = actor_limits(document, self._context.bundle_dir, "chat")
        records = getattr(getattr(target_world, "store", None), "records", None)
        # A readiness probe runs a chat with no job of its own; its trial is still one scenario's.
        job_id = getattr(self._context.job, "job_id", None) or "probe"
        trial = f"{job_id}:{scenario.scenario_key}"
        trial_started = time.time()
        records_dsn = world_db.address if world_db is not None and world_db.protocol == "postgres" else None
        # The door sees the agent's tools when it runs them itself (tools the agent asks for over its interface), or
        # when a tool proxy or the in-process door stands in front of the agent's own.
        door_sees_tools = proxied or bool(interface is not None and getattr(interface, "include_tools", False))
        dispatcher = build_dispatcher(
            # What actors watch (an on_tool trigger) includes the agent's calls through its tool proxy.
            document, self._context.bundle_dir, act=acts, read_log=lambda: _trial_log(door, trial_started),
            records=records, trial_id=trial, meter=meter,
            columns=getattr(getattr(target_world, "store", None), "columns", None),
        )
        cast = (
            ChatActors(
                [{**one, **acting_fields(listing.by_id().get(str(one.get("id"))))} for one in chat_actors],
                seed=trial, model=None, limits=limits, act=acts, records=records, meter=meter,
                about=action_descriptions(self._contract.model_dump()),
                # Each actor's own rows in this trial's log (scheduled ones included), so it decides knowing them.
                history=lambda key, since=time.time(): [
                    row for row in _trial_log(door, since) if row.get("actor_id") == key
                ],
            )
            if chat_actors
            else None
        )
        conversation_scenario = _conversation_scenario(document)
        if not conversation_scenario.instruction.strip():
            raise CallAborted("chat_scenario_invalid: instruction is empty")
        from .chat_transport import create_chat_wrapper

        target_timeout = agent_chat_timeout_seconds(self._context.job) or _chat_target_timeout_seconds()
        wrapper = create_chat_wrapper(
            interface,
            endpoint.address,
            wrapper_type=HTTPAgentWrapper,
            secret_values=self._context.target_provider_secret_values,
            local_auth_values=getattr(runtime, "_local_auth_values", None),
            timeout=target_timeout,
            total_timeout=target_timeout if interface.sse else None,
            metadata={
                "target": "hosted_repository_runtime",
                "scenario": scenario.scenario_key,
            },
        )
        if hasattr(wrapper, "abandon_on_cancel"):
            # A stopped run's world is never reused: its call seals what it kept without waiting out the agent's reply.
            wrapper.abandon_on_cancel = self._context.stop_requested
        funding = simulator_funding()
        await _check_text_usage(self._context.usage_reporter, funding)
        started = datetime.now(timezone.utc)
        transcript: Transcript | None = None
        failure: CallAborted | None = None
        _clear_file_tool_calls(runtime)
        acting: asyncio.Task[None] | None = None
        deadline_notices: list[dict[str, Any]] = []
        # An agent that runs its own tools reaches them through the tool proxy, which reads this trial's faults from
        # the world's database, as on a voice call.
        if proxied:
            from .call_runner import _set_trial_faults, trial_worker_deadlines

            deadline, deadlines, deadline_notices = trial_worker_deadlines(document, self._context.bundle_dir)
        else:
            deadline_notices = _unproxied_worker_notices(document, self._context.bundle_dir)
        held = tuple(one for one in deadline_notices if one.get("code") == WORKER_LIMITS_NOT_ENFORCED)
        if held and not self._context.readiness_probe:
            # Known before the conversation: a trial relying on limits nobody can hold is held without starting it.
            ended = datetime.now(timezone.utc)
            return CallOutcome(
                calls=(),
                turns=0,
                started_at=format_rfc3339_millis(started),
                ended_at=format_rfc3339_millis(ended),
                duration_ms=_duration_ms(started, ended),
                notices=held,
            )
        if proxied:
            if self._context.readiness_probe:
                # The workers' deadline is a rule of the scenario's graded trials; the probe checks the tools' binding.
                deadline, deadlines = None, {}
            # Every trial writes its own faults, none included, so nothing an earlier trial left can apply to this one.
            await asyncio.to_thread(_set_trial_faults, runtime, faults, deadline, deadlines)
        target = HostedChatTarget(
            wrapper=wrapper,
            contract=self._contract,
            world=target_world,
            scenario_key=scenario.scenario_key,
            scenario_id=scenario.scenario_id,
            door=door,
        )
        self.received = target.received
        try:
            # The trial's starting records, after its reset: whether it started from the seed.
            await asyncio.to_thread(self._records.begin, id(runtime), records_dsn)
            if dispatcher is not None:
                # What the actors wrote before T0 is in the world before the conversation starts.
                await dispatcher.replay()
                if dispatcher.pre_run:
                    await asyncio.to_thread(self._records.rebase, id(runtime), records_dsn)
                acting = asyncio.create_task(dispatcher.run())
            transcript = await _drive_conversation(
                target,
                conversation_scenario,
                self._contract,
                self._context.bundle_dir,
                **({"actors": cast} if cast is not None else {}),
            )
        except CallAborted as exc:
            failure = exc
            transcript = getattr(exc, "partial_transcript", None) or Transcript()
        except asyncio.CancelledError as exc:
            if not self._context.stop_requested():
                raise
            # The person stopped the run: seal what the call kept through the abort path below, then raise.
            failure, transcript = _stopped_call(exc)
        except Exception as exc:  # noqa: BLE001 - convert target transport failures to call faults
            status = (
                exc.trace.get("status_code")
                if isinstance(exc, HTTPCallFailure)
                else None
            )
            wrapped = CallAborted(
                f"chat_target_failed: {type(exc).__name__}: "
                + sanitize_diagnostic(
                    str(exc),
                    {
                        **self._context.target_provider_secret_values,
                        **getattr(runtime, "_local_auth_values", {}),
                    },
                ),
                code=(
                    "call_failed"
                    if isinstance(status, int)
                    and status in {408, 429, 502, 503, 504}
                    else "target_agent_failed"
                ),
            )
            partial = getattr(exc, "partial_transcript", None)
            if partial is not None:
                setattr(wrapped, "partial_transcript", partial)
            failure = wrapped
            transcript = partial or Transcript()
            trace = getattr(exc, "trace", None)
            if trace is not None:
                await self._adapter.upload_artifact(
                    json.dumps(trace, sort_keys=True).encode("utf-8"),
                    kind=ArtifactKind.TRACE,
                    scenario_key=scenario.scenario_key,
                )
        finally:
            sealing.set()
            if proxied:
                from .call_runner import _write_trial_state

                with contextlib.suppress(Exception):
                    await asyncio.to_thread(_write_trial_state, runtime, [])
            if acting is not None:
                acting.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await acting
                # An actor the agent's last calls woke still takes its turn, within the settle's bound.
                with contextlib.suppress(Exception):
                    await _within_stop_bound(self._context.stop_requested, dispatcher.settle())
            await _record_text_usage(
                self._context.usage_reporter,
                scenario_key=scenario.scenario_key,
                started=started,
                funding=funding,
                transcript=transcript,
                failure=failure,
            )

        # Calls the agent made in its own process or against the world's services belong to this attempt: never
        # replayed through the generated world, never inferred from text.
        try:
            transcript.calls[:] = collect_seam_calls(
                runtime,
                self._context.evidence_seam,
                transcript.calls,
                worker_names=contract_worker_names(self._context.bundle_dir),
                strict=True,
            )
            capture_failed = False
        except ToolCaptureFailed as exc:
            logger.warning("tool capture failed for %s: %s", scenario.scenario_key, exc)
            capture_failed = True
        # Calls returned over HTTP are recorded here as they run, so they are a full capture.
        capture_failed = capture_failed and not graded_calls(transcript.calls)

        # The world's record of its browser, sealed now the agent is done: off the loop, it waits on the kit's client.
        browser = await asyncio.to_thread(browser_evidence, runtime)
        world_record = _world_actors_record(
            door,
            listing,
            dispatcher,
            cast,
            meter,
            limits,
            since=trial_started,
            contract=self._contract,
            keys=record_key_columns(self._context.bundle_dir),
        )
        from .call_runner import (
            NOT_RECORDED,
            SEALED_WITHOUT_DOOR,
            deadlines_not_enforced,
            duplicate_commits,
            missing_evidence,
            one_call_per_lost_answer,
            read_worker_stops,
            sealed_without_door,
            unenforced_deadline_notices,
            unmeasured_reasons,
            world_failure_reasons,
            worker_stop_outcomes,
        )

        if door_sees_tools:
            from .call_runner import END_STATE_KEY, upload_end_state, worker_channels_held

            sealed = await asyncio.to_thread(self._records.end, id(runtime), records_dsn)
            door_log = _trial_log(door, trial_started)
            # The browser's refusals come from one place only: the door's log where its database keeps them, else the
            # kit-owned file the browser was given. Only the world's table holds the browser proxy's rows, so a table
            # that cannot be read leaves the list unreadable, never empty.
            refused = (
                await asyncio.to_thread(
                    browser_blocked, runtime, _readable_trial_log(door, trial_started, proxied=True)
                )
                if browser is not None
                else None
            )
            # Decided before the upload: an upload the platform refused is not a world whose end could not be read.
            end_readable = sealed.get(END_STATE_KEY) is not None
            kept = await upload_end_state(
                self._adapter, scenario.scenario_key, sealed, door_log,
                browser=browser_file_body(browser, refused, adapter=self._adapter) if browser is not None else None,
            )
            if browser is not None and not end_readable:
                # The world's end could not be read: the browser's record is still kept, in a file saying so.
                kept = await upload_browser_state(
                    self._adapter, scenario.scenario_key, browser, refused, why=WORLD_END_UNREADABLE,
                    action_log=door_log,
                )
            # `door` says a door stood for these counts, so a reader never takes a record from before it for one.
            world_record = {
                **(world_record or {}),
                **sealed,
                "door": True,
                # The positive evidence a platform needs before it says a worker was held on its channel.
                **({"worker_channels": True} if worker_channels_held(self._context.bundle_dir) else {}),
                **({"end_state_artifact": kept} if kept else {}),
                # Counts only: the browser's rows are in the world_state file.
                **({"browser": browser_counts(browser)} if browser is not None else {}),
            }
            if browser is not None and isinstance(world_record.get("state_changes"), list):
                # The browser's refusals in the agent's blocked list, which the door's log gives, split into its
                # navigations and its pages' requests and counted; the addresses are in the world_state file.
                world_record["state_changes"] = sealed_browser_blocked(
                    world_record["state_changes"], browser, refused
                )
        else:
            # The agent runs its own tools where no door stands: World changes says so, never "no change".
            world_record = dict(NOT_RECORDED)
            if browser is not None:
                # A world with the kit's browser still keeps a world_state file: the browser's record and refusals,
                # saying the world itself was not recorded. The sealed record names it and keeps its counts only.
                blocked = await asyncio.to_thread(browser_blocked_from_runtime, runtime)
                kept = await upload_browser_state(self._adapter, scenario.scenario_key, browser, blocked)
                world_record.update(
                    {**({"end_state_artifact": kept} if kept else {}), "browser": browser_counts(browser)}
                )
        unenforced: list[dict[str, str]] = []
        stopped_notices: list[dict[str, Any]] = []
        if proxied:
            # Off the loop: the seal may wait on a worker's stop under way, and other calls share this loop.
            stops = await asyncio.to_thread(read_worker_stops, runtime, trial_started)
            stopped = [one for one in stops or [] if one.get("reason") == "deadline_passed"]
            if stopped:
                # Workers whose own process the harness ended past their deadline.
                world_record["stopped_workers"] = stopped
            if stops is None:
                # Whether any worker was stopped is not known: never read as "none was".
                world_record["stopped_workers_unreadable"] = True
            # A listed worker the door inside the agent could not stop at its deadline, each with why.
            unenforced = await asyncio.to_thread(
                deadlines_not_enforced, runtime, document, self._context.bundle_dir, trial_started
            )
            stopped_notices, ran_on = worker_stop_outcomes(stops, document, self._context.bundle_dir)
            held = {one["worker"] for one in unenforced}
            unenforced = [*unenforced, *(one for one in ran_on if one["worker"] not in held)]
            if unenforced:
                world_record["unenforced_deadlines"] = unenforced

        # The world applied one of the agent's writes twice after its answer was lost: the scenario fails.
        trial_log = _readable_trial_log(door, trial_started, proxied=proxied) if door is not None else []
        transcript.calls[:] = one_call_per_lost_answer(transcript.calls, trial_log)
        duplicates = duplicate_commits(trial_log or [], getattr(scenario, "solution", None) or ())
        if duplicates:
            world_record = {**(world_record or {}), "duplicates": duplicates}
        if door_sees_tools and sealed_without_door(runtime.endpoints, transcript.calls, trial_log):
            # Sealed with no world door, the agent ran the tools it was offered itself: its calls are in its own trace
            # and none reached the world here, so what it changed is unknown, never "no change". The workers the
            # harness stopped, and those it could not, are still said.
            world_record = {
                **SEALED_WITHOUT_DOOR,
                **{
                    key: world_record[key]
                    for key in ("stopped_workers", "stopped_workers_unreadable", "unenforced_deadlines")
                    if key in world_record
                },
            }
        if world_record is not None:
            transcript.world_actors = world_record
        # Every actor that took part, the ones acting only through the world too, so the call is found by each.
        summary = list(_chat_actor_summary(cast, world_record, listing))
        if summary:
            transcript.call_actors = {
                "resolved": chat_actors,
                "summary": summary,
                "events": cast.events if cast is not None else [],
            }
        from .call_runner import (
            DUPLICATES_UNREADABLE,
            _bundle_contract,
            channel_bypass_notices,
            evidence_notices,
            fault_notices,
            faults_not_held,
            hand_off_notices,
            in_process_tools,
            repeated_untimed_notices,
            tool_span_notices,
            world_failure_notices,
        )

        said = list(deadline_notices)
        if faults:
            # A fault that did not take effect is said, never only logged: the trial ran without it.
            said += (
                fault_notices(
                    faults, None, transcript.calls, unreachable="the trial's faults were unreadable in the world's "
                    "database", code="fault_unreadable",
                )
                if proxied and not _faults_readable(runtime)
                else faults_not_held(faults, transcript.calls, self._context.bundle_dir)
                or fault_notices(
                    faults, _trial_log(door, trial_started), transcript.calls,
                    contract=_bundle_contract(self._context.bundle_dir),
                )
            )
        if door is not None:
            # A call of the agent's that ran without the door recording it leaves the trial's evidence incomplete.
            # An action log that could not be read is missing evidence, never an empty log.
            said += evidence_notices(
                trial_log, transcript.calls, in_process_tools(_bundle_contract(self._context.bundle_dir), runtime.endpoints)
            ) + ([dict(DUPLICATES_UNREADABLE)] if trial_log is None else [])
        # A separate worker that reached the world on the shared address: said, and held from its own checks.
        said += channel_bypass_notices(transcript.calls, self._context.bundle_dir)
        # Such a worker's own checks are not graded; the rest of the trial is.
        said += unenforced_deadline_notices(unenforced) + stopped_notices
        said += repeated_untimed_notices(transcript.calls) + hand_off_notices(runtime)
        said += tool_span_notices(runtime, transcript.calls) + world_failure_notices(trial_log)
        # Whether the environment's browser was started, and used. Said, not graded: a task may need no browsing, so
        # an unused browser does not change the trial's reading, only what it reports.
        said += browser_notices(runtime)
        if said:
            transcript.notices = said
        ended = datetime.now(timezone.utc)
        transcript_id = await self._adapter.upload_artifact(
            transcript.artifact(),
            kind=ArtifactKind.TRANSCRIPT,
            scenario_key=scenario.scenario_key,
        )
        calls = tuple(transcript.calls)
        if calls:
            workers = declared_worker_ids(self._context.bundle_dir)
            tool_trace = "\n".join(
                json.dumps(trace_row(call, workers), sort_keys=True, default=str)
                for call in calls
            ).encode("utf-8")
            await self._adapter.upload_artifact(
                tool_trace,
                kind=ArtifactKind.TOOL_TRACE,
                scenario_key=scenario.scenario_key,
            )
        # Last, so they never crowd out the transcript and the end state. Never named as the call's recordings: the
        # platform reads those as a voice call's.
        _, shot_notices = await upload_screenshots(
            self._adapter, scenario.scenario_key, browser, browser_shots_dir(runtime)
        )
        outcome = CallOutcome(
            calls=calls,
            tool_capture_failed=capture_failed,
            turns=len(transcript.exchanges),
            notices=tuple(said) + tuple(shot_notices),
            started_at=format_rfc3339_millis(started),
            ended_at=format_rfc3339_millis(ended),
            duration_ms=_duration_ms(started, ended),
            transcript_artifact=transcript_id,
            messages=tuple(transcript.canonical_messages()),
            call_actors=_chat_actor_summary(cast, world_record, listing),
            world_actions=tuple((world_record or {}).get("actions") or ()),
            duplicates=tuple(duplicates),
            unmeasured=unmeasured_reasons(transcript.notices or ()),
            world_failures=world_failure_reasons(transcript.notices or ()),
            missing_evidence=missing_evidence(transcript.notices or (), world_record),
            actor_briefs={
                **{str(one["id"]): str(one.get("brief") or "") for one in chat_actors if one.get("brief")},
                **(dispatcher.briefs if dispatcher is not None else {}),
            },
        )
        if failure is not None and failure.code == "user_canceled":
            outcome = replace(outcome, stop_reason="user_canceled")
        if failure is not None:
            failure.partial = outcome
            raise failure
        return outcome
