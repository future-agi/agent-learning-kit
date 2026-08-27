"""Hosted HTTP chat calls against an already-provisioned Bundle V2 process world."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urljoin

from fi.simulate.agent.wrapper import AgentInput
from fi.simulate.agent.wrappers.http import HTTPAgentWrapper

from .call_runner import ArtifactUploader, CallRunnerContext
from .contract import AgentContract
from .hosted_scheduler import CallAborted, CallOutcome, Scenario, World
from .outbound import ArtifactKind, format_rfc3339_millis
from .process_runtime import EnvironmentRuntime
from .world.runtime import GeneratedWorld
from .world.stores.postgres import AttachedPostgresStore


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


def _tool_world(
    bundle_dir: Path, contract: AgentContract, runtime: EnvironmentRuntime
) -> GeneratedWorld:
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


class HostedChatCallRunner:
    """Drive an OpenAI-compatible repository HTTP endpoint inside its leased world."""

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
        if interface is None or interface.kind != "http":
            raise CallAborted(
                "chat_interface_unsupported: an HTTP runtime interface is required"
            )
        endpoint = runtime.endpoints.get("target_http")
        if endpoint is None:
            raise CallAborted(
                "chat_capability_unavailable: target_http endpoint is absent"
            )

        document = _scenario_document(self._context.bundle_dir, scenario.scenario_key)
        instruction = str(document.get("instruction") or "").strip()
        if not instruction:
            raise CallAborted("chat_scenario_invalid: instruction is empty")
        target_world = _tool_world(self._context.bundle_dir, self._contract, runtime)
        wrapper = HTTPAgentWrapper(
            endpoint=urljoin(
                endpoint.address.rstrip("/") + "/", interface.path.lstrip("/")
            ),
            protocol=interface.protocol,
            include_tools=interface.include_tools,
            timeout=30.0,
            metadata={
                "target": "hosted_repository_runtime",
                "scenario": scenario.scenario_key,
            },
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": instruction}]
        started = datetime.now(timezone.utc)
        answer = ""
        try:
            for continuation in range(8):
                response = await wrapper.call(
                    AgentInput(
                        thread_id=scenario.scenario_key,
                        execution_id=scenario.scenario_id,
                        turn_index=continuation,
                        scenario_name=scenario.scenario_key,
                        modality="text",
                        messages=list(messages),
                        new_message=dict(messages[-1]),
                        tools=_tools(self._contract) if interface.include_tools else [],
                    )
                )
                trace = dict((response.metadata or {}).get("external_agent") or {})
                if trace and not trace.get("success", False):
                    raise CallAborted(
                        "chat_target_failed: "
                        + str(trace.get("error") or "submitted endpoint request failed")
                    )
                returned = list(response.tool_calls or [])
                if not returned:
                    answer = response.content.strip()
                    messages.append({"role": "assistant", "content": answer})
                    break
                messages.append(
                    {
                        "role": "assistant",
                        "content": response.content or "",
                        "tool_calls": returned,
                    }
                )
                for index, call in enumerate(returned, start=1):
                    name, arguments, call_id = _tool_call(call, index)
                    result = target_world.handle_tool_call(
                        {"id": call_id, "name": name, "arguments": arguments}
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": name,
                            "content": result.content
                            if result is not None
                            else f"no such tool {name}",
                        }
                    )
            else:
                raise CallAborted("chat_target_failed: exceeded 8 tool continuations")
        except CallAborted:
            raise
        except Exception as exc:  # noqa: BLE001 - convert target transport failures to call faults
            raise CallAborted(f"chat_call_failed: {type(exc).__name__}: {exc}") from exc

        ended = datetime.now(timezone.utc)
        transcript = f"customer: {instruction}\nagent: {answer or '(said nothing)'}\n"
        transcript_id = await self._adapter.upload_artifact(
            transcript.encode("utf-8"),
            kind=ArtifactKind.TRANSCRIPT,
            scenario_key=scenario.scenario_key,
        )
        return CallOutcome(
            calls=tuple(target_world.calls),
            turns=2,
            started_at=format_rfc3339_millis(started),
            ended_at=format_rfc3339_millis(ended),
            duration_ms=_duration_ms(started, ended),
            transcript_artifact=transcript_id,
        )
