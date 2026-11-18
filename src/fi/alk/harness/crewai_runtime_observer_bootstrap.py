"""Best-effort terminal tool observations in a customer CrewAI interpreter."""

from __future__ import annotations

from datetime import datetime
import functools
import json
from itertools import islice
import math
import os
from pathlib import Path
from typing import Any


def _bounded(value: Any, depth: int = 0, cut: list[bool] | None = None) -> Any:
    """``value`` within the record's limits; ``cut`` gains an entry when part of it is left out."""
    cut = [] if cut is None else cut
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, BaseException):
        value = str(value)
    if isinstance(value, str):
        if len(value) > 4096:
            cut.append(True)
        return value[:4096]
    if depth >= 2:
        cut.append(True)
        return "<truncated>"
    if isinstance(value, dict):
        if len(value) > 16 or any(
            not isinstance(key, str) or len(key) > 256 for key in islice(value, 17)
        ):
            cut.append(True)
        return {
            key[:256]: _bounded(item, depth + 1, cut)
            for key, item in islice(value.items(), 16)
            if isinstance(key, str)
        }
    if isinstance(value, (list, tuple)):
        if len(value) > 16:
            cut.append(True)
        return [_bounded(item, depth + 1, cut) for item in value[:16]]
    cut.append(True)
    return "<" + type(value).__name__[:128] + ">"


def _bounded_into(record: dict[str, Any], key: str, value: Any, flag: str) -> None:
    cut: list[bool] = []
    record[key] = _bounded(value, cut=cut)
    if cut:
        record[flag] = True


def _event_to_record(
    event: Any, terminal_events: dict[type, str]
) -> dict[str, Any] | None:
    output_field = terminal_events.get(type(event))
    if output_field is None:
        return None
    name = getattr(event, "tool_name", None)
    arguments = getattr(event, "tool_args", None)
    if not isinstance(name, str) or not name or not isinstance(arguments, (dict, str)):
        return None
    at = getattr(event, "started_at", None) or getattr(event, "timestamp", None)
    failure = getattr(event, "failure", None)
    is_error = output_field == "error" or failure not in (None, False, "")
    output = (
        failure
        if output_field == "output" and is_error
        else getattr(event, output_field)
    )
    record = {
        "name": name[:256],
        "is_error": is_error,
        "at": at.timestamp() if isinstance(at, datetime) else None,
        "source": "crewai",
    }
    _bounded_into(record, "arguments", arguments, "input_omitted")
    _bounded_into(record, "output", output, "output_omitted")
    for key in (
        "agent_id",
        "agent_key",
        "agent_role",
        "task_id",
        "task_name",
        "event_id",
        "parent_event_id",
        "started_event_id",
    ):
        value = getattr(event, key, None)
        if isinstance(value, str) and value:
            record[key] = value[:256]
    from_cache = getattr(event, "from_cache", None)
    if isinstance(from_cache, bool):
        record["from_cache"] = from_cache
    return record


def _handoff_record(
    event: Any, started: dict[tuple[str, str], float], coordinator: list[str]
) -> dict[str, Any] | None:
    """An agent's execution of a task as a hand-off from the flow or crew that ran it: written once it ends."""
    kind = type(event).__name__
    if kind == "FlowStartedEvent":
        name = getattr(event, "flow_name", None)
        if isinstance(name, str) and name:
            coordinator[:] = [name[:256]]
        return None
    if kind not in {
        "AgentExecutionStartedEvent",
        "AgentExecutionCompletedEvent",
        "AgentExecutionErrorEvent",
    }:
        return None
    agent, task = getattr(event, "agent", None), getattr(event, "task", None)
    role = getattr(agent, "role", None)
    if not isinstance(role, str) or not role.strip():
        return None
    at = getattr(event, "timestamp", None)
    stamp = at.timestamp() if isinstance(at, datetime) else None
    key = (str(getattr(agent, "id", "") or role), str(getattr(task, "id", "") or ""))
    if kind == "AgentExecutionStartedEvent":
        if stamp is not None:
            started[key] = stamp
        return None
    crew = getattr(getattr(agent, "crew", None), "name", None)
    by = (
        coordinator[0]
        if coordinator
        else (crew if isinstance(crew, str) and crew else "crew")
    )
    is_error = kind == "AgentExecutionErrorEvent"
    description = getattr(task, "description", None)
    record = {
        "name": role.strip()[:256],
        "arguments": {},
        "is_error": is_error,
        "at": started.pop(key, stamp),
        "ended_at": stamp,
        "source": "crewai",
        "agent_key": by[:256],
        "agent_role": by[:256],
        "delegated_to": role.strip()[:256],
        "kind": "handoff",
    }
    if isinstance(description, str):
        _bounded_into(record, "arguments", {"task": description}, "input_omitted")
    _bounded_into(
        record,
        "output",
        getattr(event, "error" if is_error else "output", None),
        "output_omitted",
    )
    task_name = getattr(task, "name", None)
    if isinstance(task_name, str) and task_name:
        record["task_name"] = task_name[:256]
    if key[1]:
        record["task_id"] = key[1][:256]
    return record


def install() -> bool:
    if os.environ.get("ALK_CREWAI_TOOL_TRACE") != "1" or not os.environ.get(
        "HARNESS_TOOL_TRACE"
    ):
        return False
    try:
        from crewai.events.event_bus import CrewAIEventsBus
        from crewai.events.types.tool_usage_events import (
            ToolUsageErrorEvent,
            ToolUsageFinishedEvent,
        )

        original = CrewAIEventsBus.emit
        if getattr(original, "__alk_crewai_tool_trace__", False):
            return True
        terminal_events = {
            ToolUsageFinishedEvent: "output",
            ToolUsageErrorEvent: "error",
        }
        # The flow or crew that runs each agent, and when each agent's task began.
        started: dict[tuple[str, str], float] = {}
        coordinator: list[str] = []

        @functools.wraps(original)
        def emit(self: Any, source: Any, event: Any) -> Any:
            result = original(self, source, event)
            try:
                record = _event_to_record(event, terminal_events) or _handoff_record(
                    event, started, coordinator
                )
                if record is not None:
                    line = json.dumps(record, sort_keys=True)
                    if len(line) > 65536:
                        record.update(
                            arguments="<truncated>",
                            output="<truncated>",
                            input_omitted=True,
                            output_omitted=True,
                        )
                        line = json.dumps(record, sort_keys=True)
                    path = Path(os.environ["HARNESS_TOOL_TRACE"])
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("a", encoding="utf-8") as trace:
                        trace.write(line + "\n")
            except Exception:  # noqa: BLE001 - tracing must not affect customer execution
                pass
            return result

        emit.__alk_crewai_tool_trace__ = True
        CrewAIEventsBus.emit = emit
        return True
    except Exception:  # noqa: BLE001 - missing or incompatible CrewAI is harmless
        return False
