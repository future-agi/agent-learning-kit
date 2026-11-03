"""The items a person reviews in a draft, by id, with a digest of each one's content.

The platform's review (`simulate/services/harness_versioning/review.py`) lists a draft's changes item by item with the
same ids: a tool, a rule, any other contract field, a sub-goal, an actor, a world store, a source, a scenario folder,
or any other file. The builder chat reads digests before and after each tool call to know which items that call changed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PROVENANCE_FILE = ".futureagi-provenance.jsonl"
_CONTRACT_IGNORED = frozenset({"chosen_evals", "amendments"})
_LISTS = {
    "sub_goals.json": ("sub_goal", "sub_goals", "name"),
    "actors.json": ("actor", "actors", "id"),
    "sources.json": ("source", "sources", "id"),
}
# The platform's world seed files (`hashing.WORLD_SEED_FILES`): a change to one is a change to the world's state.
_WORLD_STATE = frozenset({"world.sqlite", "store.json", "collections.json", "schema.sql"})
_SKIPPED = ("environment-bundle", "generic-harness", "documents-text", "__pycache__", "node_modules")


def _digest(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def item_digests(workspace: Path) -> dict[str, str]:
    """Every reviewable item of a builder workspace, by id, with its content digest."""
    root = Path(workspace)
    items: dict[str, str] = {}
    contract = _json(root / "contract.json")
    if isinstance(contract, dict):
        for tool in contract.get("tools") or []:
            if isinstance(tool, dict) and tool.get("name"):
                items[f"tool:{tool['name']}"] = _digest(tool)
        for rule in contract.get("hard_constraints") or []:
            if rule is not None:
                items[f"rule:{hashlib.sha256(str(rule).encode()).hexdigest()[:12]}"] = _digest(rule)
        for field, value in contract.items():
            if field not in ("tools", "hard_constraints") and field not in _CONTRACT_IGNORED:
                items[f"contract_field:{field}"] = _digest(value)
    for name, (kind, field, key) in _LISTS.items():
        body = _json(root / name)
        entries = body.get(field) if isinstance(body, dict) else body
        for entry in entries or []:
            if isinstance(entry, dict) and entry.get(key):
                items[f"{kind}:{entry[key]}"] = _digest(entry)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        parts = relative.parts
        if parts[0].startswith(".") or any(part in _SKIPPED for part in parts) or relative.as_posix() in (
            "contract.json",
            *_LISTS,
        ):
            continue
        if parts[0] in ("scenarios", "scenario") and len(parts) >= 3:
            item = f"scenario:{parts[1]}"
            items[item] = _digest([items.get(item, ""), relative.as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()])
            continue
        kind = "world_state" if path.name in _WORLD_STATE else "file"
        items[f"{kind}:{relative.as_posix()}"] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return items


def moved_items(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """The items whose content a change moved, added or removed."""
    return sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
