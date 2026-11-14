"""Whether each source a scenario cites says what the scenario claims it says.

A case with no sources is not blocked: it reads as assumed by the builder. A source that does not
resolve is blocking, because a false citation is worse than none. Chat messages and evidence cases
live on the platform and are resolved there.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .leaks import normalise

# Kinds whose resolution ALK decides; the rest are left to the platform.
ALK_RESOLVED_KINDS = frozenset({"rule", "file", "requirement", "assumption"})
_FILE_ROOTS = ("knowledge", "")


def _file(world_root: Path, ref: str) -> Path | None:
    name = ref.removeprefix("file:").strip().lstrip("/")
    if not name or ".." in name.split("/"):
        return None
    for root in _FILE_ROOTS:
        candidate = Path(world_root) / root / name if root else Path(world_root) / name
        if candidate.is_file():
            return candidate
    return None


def resolve_sources(scenario: Any, contract: Any, world_root: Path) -> list[dict[str, Any]]:
    """One entry per cited source: `{kind, ref, resolved, problem, ...}`; `resolved` None means
    the platform decides it."""
    rules = list(getattr(contract, "hard_constraints", None) or []) if contract is not None else []
    resolved: list[dict[str, Any]] = []
    for source in getattr(scenario, "sources", None) or []:
        kind, ref, quote = source.kind, source.ref, source.quote
        entry: dict[str, Any] = {"kind": kind, "ref": ref, "resolved": True, "problem": ""}
        if kind == "rule":
            wanted = normalise(quote)
            matched = next((at for at, rule in enumerate(rules) if wanted and wanted in normalise(rule)), None)
            if matched is None:
                entry.update(resolved=False, problem="no rule of the agent contains the quoted words")
            else:
                entry["resolved_ref"] = f"rule:{matched}"
        elif kind == "file":
            path = _file(world_root, ref)
            if path is None:
                # Uploaded files may live only on the platform; it resolves them there.
                entry.update(resolved=None, problem="the file is not in the authoring tree")
            else:
                text = path.read_bytes()
                entry["matched_digest"] = "sha256:" + hashlib.sha256(text).hexdigest()
                if normalise(quote) not in normalise(text.decode("utf-8", errors="replace")):
                    entry.update(resolved=False, problem=f"{path.name} does not contain the quoted words")
        elif kind == "requirement":
            if not quote.strip():
                entry.update(resolved=False, problem="a requirement needs the person's own words")
        elif kind == "assumption":
            pass
        else:
            entry.update(resolved=None, problem="resolved on the platform")
        resolved.append(entry)
    return resolved


def unresolved(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The entries ALK resolved and found false."""
    return [one for one in entries if one["kind"] in ALK_RESOLVED_KINDS and one.get("resolved") is False]
