from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_TRANSFER_TYPES = frozenset({"transfercall", "transfer_call"})
_ENDING_TYPES = frozenset({"endcall", "end_call", "end_chat"})
_DESTINATION_LABELS = ("number", "sipUri", "sip_uri", "assistantName", "agent_id")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _destination_label(destination: Mapping[str, Any]) -> str:
    described = _text(destination.get("description"))
    for key in _DESTINATION_LABELS:
        label = _text(destination.get(key))
        if label:
            return f"{label} ({described})" if described else label
    return described


def _destinations(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    listed = raw.get("destinations")
    found = [
        dict(one)
        for one in (listed if isinstance(listed, list) else [])
        if isinstance(one, Mapping)
    ]
    single = raw.get("transfer_destination")
    if isinstance(single, Mapping):
        found.append(dict(single))
    return found


def _effects(kind: str, destinations: list[dict[str, Any]], modality: str) -> str:
    if kind in _TRANSFER_TYPES:
        labels = [label for label in map(_destination_label, destinations) if label]
        target = ", ".join(labels) if labels else "another line"
        return (
            f"Transfers the call to {target}. The caller is handed off and the agent "
            "leaves the call, so the conversation with the agent ends here."
        )
    if kind in _ENDING_TYPES:
        return "Ends the chat." if modality == "chat" else "Ends the call."
    return ""


def _entry(name: str, raw: Mapping[str, Any], modality: str) -> dict[str, Any]:
    function = _mapping(raw.get("function"))
    kind = _text(raw.get("type"))
    destinations = _destinations(raw) if kind.lower() in _TRANSFER_TYPES else []
    effects = _effects(kind.lower(), destinations, modality)
    entry: dict[str, Any] = {"name": name, "type": kind}
    description = (
        _text(function.get("description")) or _text(raw.get("description")) or effects
    )
    if description:
        entry["description"] = description
    parameters = function.get("parameters") or raw.get("parameters")
    if isinstance(parameters, Mapping) and parameters:
        entry["parameters"] = dict(parameters)
    if destinations:
        entry["destinations"] = destinations
    if effects:
        entry["effects"] = effects
    return entry


def _raw_tools(profile: Mapping[str, Any]) -> list[Any]:
    provider = _text(profile.get("provider")).lower()
    if provider == "vapi":
        model = _mapping(profile.get("model"))
        return [*(model.get("tools") or []), *(profile.get("reusable_tools") or [])]
    if provider == "retell":
        found: list[Any] = list(profile.get("general_tools") or [])
        for state in profile.get("states") or []:
            found.extend(_mapping(state).get("tools") or [])
        found.extend(_mapping(profile.get("conversation_flow")).get("tools") or [])
        return found
    return []


def provider_tools(profile: Mapping[str, Any]) -> list[dict[str, Any]]:
    modality = _text(profile.get("modality")).lower() or "voice"
    tools: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in _raw_tools(profile):
        if not isinstance(raw, Mapping):
            continue
        function = _mapping(raw.get("function"))
        name = (
            _text(function.get("name")) or _text(raw.get("name")) or _text(raw.get("type"))
        )
        if not name or name in seen:
            continue
        seen.add(name)
        tools.append(_entry(name, raw, modality))
    return tools


def with_provider_tools(profile: dict[str, Any]) -> dict[str, Any]:
    if "tools" in profile:
        return profile
    tools = provider_tools(profile)
    return {**profile, "tools": tools} if tools else profile
