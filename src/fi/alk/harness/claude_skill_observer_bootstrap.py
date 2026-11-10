from __future__ import annotations

import contextlib
import functools
import json
import os
import time
from pathlib import Path
from typing import Any

_SOURCE = "alk_skill"
_MARKER_SUFFIX = ".skill-observer"
_NAME_KEYS = ("skill", "command", "name")


def _skill_name(arguments: Any) -> str | None:
    if not isinstance(arguments, dict):
        return None
    for key in _NAME_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lstrip("/").split(":")[-1].split()[0]
    return None


def _append(record: dict[str, Any]) -> None:
    path = Path(os.environ["HARNESS_TOOL_TRACE"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as trace:
        trace.write(json.dumps(record, sort_keys=True) + "\n")


def _record(message: Any, assistant_type: type, tool_use_type: type) -> None:
    if not isinstance(message, assistant_type):
        return
    for block in getattr(message, "content", None) or ():
        if isinstance(block, tool_use_type) and getattr(block, "name", None) == "Skill":
            name = _skill_name(getattr(block, "input", None))
            if name:
                _append({"source": _SOURCE, "skill": name, "at": time.time()})


def _mark() -> None:
    trace = Path(os.environ["HARNESS_TOOL_TRACE"])
    marker = trace.with_name(trace.name + _MARKER_SUFFIX)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("1", encoding="utf-8")


def install() -> bool:
    if os.environ.get("ALK_SKILL_TRACE") != "1" or not os.environ.get(
        "HARNESS_TOOL_TRACE"
    ):
        return False
    try:
        from claude_agent_sdk._internal import message_parser
        from claude_agent_sdk.types import AssistantMessage, ToolUseBlock

        original = message_parser.parse_message
        if getattr(original, "__alk_skill_trace__", False):
            return True

        @functools.wraps(original)
        def parse_message(data: Any) -> Any:
            message = original(data)
            with contextlib.suppress(Exception):
                _record(message, AssistantMessage, ToolUseBlock)
            return message

        parse_message.__alk_skill_trace__ = True
        message_parser.parse_message = parse_message
        with contextlib.suppress(Exception):
            from claude_agent_sdk._internal import client as internal_client

            if getattr(internal_client, "parse_message", None) is original:
                internal_client.parse_message = parse_message
        _mark()
        return True
    except Exception:  # noqa: BLE001 - a missing or incompatible SDK is harmless
        return False
