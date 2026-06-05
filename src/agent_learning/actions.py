from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from ._schema import AGENT_LEARNING_CLI_SCHEMA_VERSION, normalize_public_payload

AGENT_LEARNING_ACTIONS_KIND = "agent-learning.actions.v1"


def load_artifact_file(path: str | Path) -> dict[str, Any]:
    artifact_path = Path(path).expanduser().resolve()
    loaded = _load_json_or_yaml(artifact_path)
    if not isinstance(loaded, Mapping):
        raise ValueError("action artifact root must be an object")
    return dict(loaded)


def extract_actions(
    artifact: Mapping[str, Any],
    *,
    action_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Return embedded CLI actions from an Agent Learning artifact/report."""

    normalized = normalize_public_payload(artifact)
    if not isinstance(normalized, Mapping):
        return []
    actions: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for path, action in _walk_cli_actions(normalized):
        if action_id is not None and str(action.get("id") or "") != action_id:
            continue
        record = copy.deepcopy(dict(action))
        record["path"] = path
        record["source_card_path"] = _source_card_path(path)
        key = (
            str(record.get("id") or ""),
            str(record.get("command") or ""),
            str(record.get("path") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        actions.append(record)
    return actions


def get_action(
    artifact: Mapping[str, Any],
    action_id: str,
) -> Optional[dict[str, Any]]:
    actions = action_catalog(artifact, action_id=action_id)["actions"]
    return actions[0] if actions else None


def action_catalog(
    artifact: Mapping[str, Any],
    *,
    source_path: str | Path = ".",
    action_id: Optional[str] = None,
    name: Optional[str] = None,
) -> dict[str, Any]:
    artifacts = [artifact]
    synthesized = _synthesized_report_artifact(artifact, source_path=source_path)
    if synthesized is not None:
        artifacts.insert(0, synthesized)
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in artifacts:
        for action in extract_actions(item, action_id=action_id):
            key = str(action.get("id") or action.get("command") or action.get("path") or "")
            if key in seen:
                continue
            seen.add(key)
            action["requires_input"] = bool(action.get("inputs"))
            actions.append(action)
    source = normalize_public_payload(artifact)
    source_kind = source.get("kind", source.get("schema_version")) if isinstance(source, Mapping) else None
    source_name = source.get("name") if isinstance(source, Mapping) else None
    cards = sorted({
        str(item.get("source_card_path"))
        for item in actions
        if item.get("source_card_path")
    })
    payload = {
        "schema_version": AGENT_LEARNING_CLI_SCHEMA_VERSION,
        "kind": AGENT_LEARNING_ACTIONS_KIND,
        "name": str(name or source_name or Path(source_path).stem),
        "status": "passed",
        "exit_code": 0,
        "source_path": str(source_path),
        "actions": actions,
        "summary": {
            "source_kind": source_kind,
            "source_name": source_name,
            "action_count": len(actions),
            "action_ids": [str(item.get("id")) for item in actions if item.get("id")],
            "source_card_paths": cards,
        },
    }
    if action_id is not None:
        payload["summary"]["filter_action_id"] = action_id
    return payload


def _synthesized_report_artifact(
    artifact: Mapping[str, Any],
    *,
    source_path: str | Path,
) -> Optional[dict[str, Any]]:
    try:
        cli = importlib.import_module("fi.simulate.cli")
        report = cli._report_result(
            source=artifact,
            source_path=Path(source_path),
            name=None,
            duration_seconds=0.0,
        )
    except Exception:
        return None
    return report if isinstance(report, dict) else None


def render_markdown(catalog: Mapping[str, Any]) -> str:
    summary = dict(catalog.get("summary") or {})
    rows = [
        "| Action | Label | Source card | Status | Target layers | Command |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for action in catalog.get("actions") or []:
        if not isinstance(action, Mapping):
            continue
        rows.append(
            "| "
            + " | ".join(
                _md_cell(value)
                for value in [
                    action.get("id"),
                    action.get("label"),
                    action.get("source_card_path"),
                    action.get("readiness_status")
                    or action.get("strategy_status")
                    or action.get("diagnosis_status")
                    or action.get("status"),
                    _join_values(action.get("target_layers")),
                    action.get("command"),
                ]
            )
            + " |"
        )
    if len(rows) == 2:
        rows.append("| No actions |  |  |  |  |  |")
    lines = [
        f"# {_md_text(catalog.get('name') or 'artifact-actions')}",
        "",
        f"- Source: `{_md_code(catalog.get('source_path') or '.')}`",
        f"- Source kind: {_md_text(summary.get('source_kind') or 'unknown')}",
        f"- Actions: {_md_text(summary.get('action_count') or 0)}",
        "",
        "## Actions",
        "",
        *rows,
        "",
    ]
    return "\n".join(lines)


def _walk_cli_actions(value: Any, path: str = "") -> Iterable[tuple[str, Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        if value.get("kind") == "cli" and value.get("command_args") is not None:
            yield path, value
            return
        for key, item in value.items():
            item_path = f"{path}.{key}" if path else str(key)
            yield from _walk_cli_actions(item, item_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            item_path = f"{path}.{index}" if path else str(index)
            yield from _walk_cli_actions(item, item_path)


def _source_card_path(action_path: str) -> str:
    marker = ".actions."
    if marker in action_path:
        source = action_path.split(marker, 1)[0]
        return source.removeprefix("report.")
    if action_path.endswith(".actions"):
        return action_path[: -len(".actions")].removeprefix("report.")
    return action_path


def _load_json_or_yaml(path: Path) -> Any:
    if not path.exists():
        raise ValueError(f"action artifact file not found: {path}")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency clarity
            raise ValueError("YAML artifacts require PyYAML; use JSON or install PyYAML.") from exc
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _join_values(value: Any) -> Optional[str]:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, (list, tuple, set)):
        values = [str(item) for item in value if item not in (None, "")]
        return ", ".join(values) if values else None
    return str(value)


def _md_text(value: Any) -> str:
    return str(value).replace("\n", " ")


def _md_code(value: Any) -> str:
    return str(value).replace("`", "\\`")


def _md_cell(value: Any) -> str:
    text = _md_text(value if value is not None else "").replace("|", "\\|")
    return text if len(text) <= 140 else f"{text[:137]}..."


__all__ = [
    "AGENT_LEARNING_ACTIONS_KIND",
    "action_catalog",
    "extract_actions",
    "get_action",
    "load_artifact_file",
    "render_markdown",
]
