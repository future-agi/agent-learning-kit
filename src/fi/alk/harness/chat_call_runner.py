"""Hosted HTTP chat calls against an already-provisioned Bundle V2 process world."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from fi._diagnostics import sanitize_diagnostic
from fi.simulate.agent.wrapper import AgentInput
from fi.simulate.agent.wrappers.http import HTTPAgentWrapper, HTTPCallFailure

from .actors import with_caller_actor
from .call_runner import (
    ArtifactUploader,
    CallRunnerContext,
    _clear_file_tool_calls,
    _with_scenario_edits,
    collect_seam_calls,
)
from .contract import AgentContract
from .hosted_scheduler import CallAborted, CallOutcome, Scenario, World
from .outbound import ArtifactKind, format_rfc3339_millis
from .process_runtime import EnvironmentRuntime
from .run.conversation import ChatActors, TargetConversationEnded, Transcript, converse
from .scenario import Scenario as ConversationScenario
from .usage import (
    UsageDenied,
    UsageUnavailable,
    failure_domain_for_code,
    simulator_funding,
)
from .world.runtime import Call, GeneratedWorld, trace_row
from .world.stores.postgres import AttachedPostgresStore
from .world_door import world_action

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

    def __init__(self, dsn: str) -> None:
        self._store = AttachedPostgresStore(dsn)

    def start(self) -> None:
        self._store.start()

    def stop(self) -> None:
        return

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return self._store.query(_qmark_to_postgres(sql), params)

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
) -> Any:
    """The world's door for a chat run: the agent's own tools service runs its service actions and every actor's tool
    action when it has one, the world's own handlers the rest; the trial's action log is kept in memory (mirrored to the world's table when it keeps actors), and
    who may do what is the environment's actor list."""
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
    if mirror_dsn:
        from .world_door import PostgresRecords

        records = RecordExecutor(PostgresRecords(mirror_dsn))
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
    try:
        log = door.log.rows()
    except Exception:  # noqa: BLE001 - no rows, never a failed call
        log = []
    mirror = getattr(door.log, "mirror", None)
    if mirror is not None:
        try:
            log = [row for row in mirror.rows() if _at(row) >= since] or log
        except Exception:  # noqa: BLE001 - the world's table unreadable: this process's own rows stand
            pass
    return log


def _world_actors_record(
    door: Any, listing: Any, dispatcher: Any, cast: Any, meter: Any, limits: dict[str, Any], since: float = 0.0
) -> dict[str, Any] | None:
    """What actors did in the world this trial and what it changed, sealed beside the transcript as a call seals it;
    None when no actor took part."""
    from .call_runner import actor_rows, approvals, trial_state_changes

    listed = listing.by_id()
    log = _trial_log(door, since)
    decisions = dispatcher.record() if dispatcher is not None else []
    if cast is not None:
        decisions += [
            {"actor": one["actor"], "play": "live", "action": one.get("action"),
             "outcome": (one.get("outcome") or {}).get("outcome"), "why": one.get("why")}
            for one in cast.events
            if one.get("kind") == "acted"
        ]
    acted = actor_rows(log, listed, decisions)
    changes = trial_state_changes(log, listed, decisions)
    # A worker the world told apart is kept as an actor is, so its refused attempts are never lost.
    workers_acted = any(str(entry.get("who") or "").startswith("worker:") for entry in changes)
    if not acted and not decisions and not workers_acted:
        return None
    return {
        "actions": acted,
        "decisions": decisions,
        "state_changes": changes,
        "approvals": approvals(log, decisions),
        "limits": dict(limits),
        "usage": meter.rows(),
    }


def _chat_actor_summary(cast: Any, world_record: dict[str, Any] | None, listing: Any) -> tuple[dict[str, Any], ...]:
    """The call's actor summary: the actors who wrote in the conversation and every actor that acted in the world."""
    from .actor_dispatch import world_summary

    speaking = tuple(cast.summary()) if cast is not None else ()
    decisions = (world_record or {}).get("decisions") or []
    return speaking + tuple(world_summary(decisions, listing.by_id(), {str(one.get("actor")) for one in speaking}))


def _tool_world(
    bundle_dir: Path,
    contract: AgentContract,
    runtime: EnvironmentRuntime,
    source_directory: Path | None = None,
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
    world = GeneratedWorld(store=_HostedToolStore(endpoint.address))
    world.tools = [tool.model_dump(mode="json") for tool in contract.tools]
    world.handlers = {}
    for tool in contract.tools:
        path = bundle_dir / "handlers" / f"{tool.name}.py"
        if path.is_file():
            world.handlers[tool.name] = path.read_text(encoding="utf-8")
    world.refusal_signature = contract.refusal_signature
    if source_directory is not None:
        world.reach(str(source_directory))
    return world


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
) -> None:
    """Record a tool the submitted callback already executed.

    Callback-backed agents can return the request and its completed response together. Replaying
    that request through the generated world both risks repeating a side effect and incorrectly
    turns a real success into ``no such tool`` when no mock handler was authored. The callback's
    response is the authoritative execution evidence at this seam.
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
    world.calls.append(
        Call(
            name=name,
            arguments=dict(arguments),
            result=_tool_response_result(response),
            ok=ok,
            refused=refused,
            error=error,
            at=time.time(),
        )
    )


class _HostedChatTarget:
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
        outcome = self._door.enter(ToolCall(action=self._action(name), name=name, arguments=arguments, method=method))
        if outcome.fault is not None:
            if outcome.fault.get("mode") == "timeout_after_commit":
                if self.world.calls:
                    # Committed, with the answer lost on the way back.
                    self.world.calls[-1].error = "response_lost"
            else:
                self.world._record(
                    Call(name=name, arguments=arguments, result=None, ok=False, error="timeout_before_commit")
                )
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
                    )
                    result_content = provided.get("content", provided.get("result"))
                    if not isinstance(result_content, str):
                        result_content = json.dumps(result_content, default=str)
                else:
                    result_content = self._world_call(call_id, name, arguments)
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
    target: _HostedChatTarget,
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
        self._adapter = adapter
        self._context = context
        contract_path = context.bundle_dir / "contract.json"
        if not contract_path.is_file():
            self._contract: AgentContract | None = None
        else:
            self._contract = AgentContract.model_validate_json(
                contract_path.read_text(encoding="utf-8")
            )

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
        document = with_caller_actor(document, self._context.bundle_dir)
        target_world = _tool_world(
            self._context.bundle_dir,
            self._contract,
            runtime,
            self._context.source_directory,
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
        # The tool proxy logs the agent's own calls (and the faults it applies) in the world's table, and limited workers
        # act through it: read that table whenever the trial is proxied or has limited workers, cast or not.
        proxied = runtime.endpoints.get("tool_proxy") is not None
        limited_workers = (Path(self._context.bundle_dir) / "generated" / "tool-proxy" / "permissions.json").is_file()
        door = chat_door(
            target_world,
            world_db.address
            if (listing.actors or proxied or limited_workers) and world_db is not None and world_db.protocol == "postgres"
            else None,
            {
                one.id: {"permitted_actions": list(one.permitted_actions), "on_behalf_of": one.on_behalf_of}
                for one in listing.actors
            },
            list(document.get("faults") or []),
            tools_url=service.address if service is not None else None,
            service_actions=service_actions(self._contract),
        )
        acts = DoorActs(door)
        meter = ActorMeter()
        limits = actor_limits(document, self._context.bundle_dir, "chat")
        records = getattr(getattr(target_world, "store", None), "records", None)
        # A readiness probe runs a chat with no job of its own; its trial is still one scenario's.
        job_id = getattr(self._context.job, "job_id", None) or "probe"
        trial = f"{job_id}:{scenario.scenario_key}"
        trial_started = time.time()
        dispatcher = build_dispatcher(
            # What actors watch (an on_tool trigger) includes the agent's calls through its tool proxy.
            document, self._context.bundle_dir, act=acts, read_log=lambda: _trial_log(door, trial_started),
            records=records, trial_id=trial, meter=meter,
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

        wrapper = create_chat_wrapper(
            interface,
            endpoint.address,
            wrapper_type=HTTPAgentWrapper,
            secret_values=self._context.target_provider_secret_values,
            local_auth_values=getattr(runtime, "_local_auth_values", None),
            timeout=_chat_target_timeout_seconds(),
            metadata={
                "target": "hosted_repository_runtime",
                "scenario": scenario.scenario_key,
            },
        )
        funding = simulator_funding()
        await _check_text_usage(self._context.usage_reporter, funding)
        started = datetime.now(timezone.utc)
        transcript: Transcript | None = None
        failure: CallAborted | None = None
        _clear_file_tool_calls(runtime)
        acting: asyncio.Task[None] | None = None
        # An agent that runs its own tools reaches them through the tool proxy, which reads this trial's faults from
        # the world's database, as on a voice call.
        if proxied:
            from .call_runner import _set_trial_faults, worker_deadline, worker_deadlines

            # Every trial writes its own faults, none included, so nothing an earlier trial left can apply to this one.
            _set_trial_faults(
                runtime,
                list(document.get("faults") or []),
                worker_deadline(document),
                worker_deadlines(document),
            )
        try:
            if dispatcher is not None:
                # What the actors wrote before T0 is in the world before the conversation starts.
                await dispatcher.replay()
                acting = asyncio.create_task(dispatcher.run())
            transcript = await _drive_conversation(
                _HostedChatTarget(
                    wrapper=wrapper,
                    contract=self._contract,
                    world=target_world,
                    scenario_key=scenario.scenario_key,
                    scenario_id=scenario.scenario_id,
                    door=door,
                ),
                conversation_scenario,
                self._contract,
                self._context.bundle_dir,
                **({"actors": cast} if cast is not None else {}),
            )
        except CallAborted as exc:
            failure = exc
            transcript = getattr(exc, "partial_transcript", None) or Transcript()
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
            if proxied:
                from .call_runner import _write_trial_state

                with contextlib.suppress(Exception):
                    _write_trial_state(runtime, [])
            if acting is not None:
                acting.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await acting
                # An actor the agent's last calls woke still takes its turn, within the settle's bound.
                with contextlib.suppress(Exception):
                    await dispatcher.settle()
            await _record_text_usage(
                self._context.usage_reporter,
                scenario_key=scenario.scenario_key,
                started=started,
                funding=funding,
                transcript=transcript,
                failure=failure,
            )

        # The submitted agent may execute tools inside its own process or against the world's services instead of
        # returning tool requests over HTTP. These observed calls belong to the same attempt; do not replay the tool
        # through the generated world or fabricate evidence from text.
        transcript.calls[:] = collect_seam_calls(runtime, self._context.evidence_seam, transcript.calls)

        if cast is not None:
            transcript.call_actors = {"resolved": chat_actors, "summary": cast.summary(), "events": cast.events}
        world_record = _world_actors_record(door, listing, dispatcher, cast, meter, limits, since=trial_started)
        if world_record is not None:
            transcript.world_actors = world_record
        faults = list(document.get("faults") or [])
        if faults:
            from .call_runner import fault_notices

            # A fault that did not take effect is said, never only logged: the trial ran without it.
            transcript.notices = fault_notices(faults, _trial_log(door, trial_started), transcript.calls)
        ended = datetime.now(timezone.utc)
        transcript_id = await self._adapter.upload_artifact(
            transcript.artifact(),
            kind=ArtifactKind.TRANSCRIPT,
            scenario_key=scenario.scenario_key,
        )
        calls = tuple(transcript.calls)
        if calls:
            tool_trace = "\n".join(
                json.dumps(trace_row(call), sort_keys=True, default=str)
                for call in calls
            ).encode("utf-8")
            await self._adapter.upload_artifact(
                tool_trace,
                kind=ArtifactKind.TOOL_TRACE,
                scenario_key=scenario.scenario_key,
            )
        outcome = CallOutcome(
            calls=calls,
            turns=len(transcript.exchanges),
            started_at=format_rfc3339_millis(started),
            ended_at=format_rfc3339_millis(ended),
            duration_ms=_duration_ms(started, ended),
            transcript_artifact=transcript_id,
            messages=tuple(transcript.canonical_messages()),
            call_actors=_chat_actor_summary(cast, world_record, listing),
            world_actions=tuple((world_record or {}).get("actions") or ()),
            actor_briefs={
                **{str(one["id"]): str(one.get("brief") or "") for one in chat_actors if one.get("brief")},
                **(dispatcher.briefs if dispatcher is not None else {}),
            },
        )
        if failure is not None:
            failure.partial = outcome
            raise failure
        return outcome
