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


def _bounded(value: Any, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:4096]
    if depth >= 2:
        return "<truncated>"
    if isinstance(value, dict):
        return {
            key[:256]: _bounded(item, depth + 1)
            for key, item in islice(value.items(), 16)
            if isinstance(key, str)
        }
    if isinstance(value, (list, tuple)):
        return [_bounded(item, depth + 1) for item in value[:16]]
    if isinstance(value, BaseException):
        return str(value)[:4096]
    return "<" + type(value).__name__[:128] + ">"


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
        "arguments": _bounded(arguments),
        "output": _bounded(output),
        "is_error": is_error,
        "at": at.timestamp() if isinstance(at, datetime) else None,
        "source": "crewai",
    }
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

        @functools.wraps(original)
        def emit(self: Any, source: Any, event: Any) -> Any:
            result = original(self, source, event)
            try:
                record = _event_to_record(event, terminal_events)
                if record is not None:
                    line = json.dumps(record, sort_keys=True)
                    if len(line) > 65536:
                        record.update(arguments="<truncated>", output="<truncated>")
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
