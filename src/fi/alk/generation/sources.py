"""Agent connections: where the evidence about an agent comes from.

An ``AgentSource`` turns "point me at an agent" into a bounded text blob the contract extractor can
ground in. The repo-folder source ships today; a Vapi or Retell config fetch, or a platform agent
definition, is a new class with three members registered under a new name. Registration reuses the
runtime's ``AdapterRegistry`` so third-party packages can plug in through the
``fi.alk.generation.sources`` entry-point group without editing this file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from fi.simulate.registry import AdapterRegistry

SOURCE_ENTRY_POINT_GROUP = "fi.alk.generation.sources"

source_registry: AdapterRegistry = AdapterRegistry("agent_source", SOURCE_ENTRY_POINT_GROUP)


def register_source(name: str, factory=None, *, override: bool = False):
    return source_registry.register(name, factory, override=override)


@dataclass
class AgentEvidence:
    """Bounded, grounded raw material about one agent."""

    name: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentSource(Protocol):
    """One way of reaching an agent's definition."""

    name: str

    def describe(self) -> AgentEvidence: ...


# Path fragments that tend to hold the action surface: tools, prompts, commands, data.
_SURFACE_HINTS = (
    "controller", "tool", "tools", "action", "function", "command", "commands", "prompt",
    "prompts", "skill", "skills", "registry", "capabilit", "agent", "database", "menu",
    "order", "schema", "config", "assistant", "instruction",
)
_EXAMPLE_HINTS = ("example", "examples", "demo", "cookbook", "recipe")
_CODE_EXT = (".py", ".ts", ".js", ".yaml", ".yml", ".md", ".txt", ".toml", ".json")
_SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next",
    "frontend", "static", "assets", ".flox", "tests", "test", ".omega", "artifacts",
}
_MAX_FILE_CHARS = 9000
_MAX_TOTAL_CHARS = 60_000


def _read(path: str, limit: int = _MAX_FILE_CHARS) -> str:
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return fh.read(limit)
    except OSError:
        return ""


@register_source("repo")
@dataclass
class RepoFolderSource:
    """Read an agent straight out of its repository folder.

    Collection is deterministic: README first, then files ranked by how strongly their path suggests
    the action surface (tools, prompts, commands, data), then example task names. Nothing is
    executed; nothing leaves the machine.
    """

    path: str
    name: str = "repo"

    def describe(self) -> AgentEvidence:
        root = os.path.abspath(self.path.rstrip("/"))
        if not os.path.isdir(root):
            raise FileNotFoundError(f"agent repo folder not found: {root}")
        agent_name = os.path.basename(root)
        parts: list[str] = [f"# AGENT REPO: {agent_name}\n"]
        total = len(parts[0])

        for candidate in ("README.md", "README.rst", "readme.md", "README"):
            readme = os.path.join(root, candidate)
            if os.path.isfile(readme):
                body = _read(readme)
                parts.append(f"\n## README\n{body}\n")
                total += len(body)
                break

        surface: list[tuple[int, str]] = []
        examples: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in _SKIP_DIRS]
            lowered_dir = dirpath.lower()
            in_examples = any(hint in lowered_dir for hint in _EXAMPLE_HINTS)
            for filename in filenames:
                rel = os.path.relpath(os.path.join(dirpath, filename), root)
                if in_examples:
                    if filename.endswith(_CODE_EXT):
                        examples.append(rel)
                    continue
                if not filename.endswith(_CODE_EXT):
                    continue
                lowered_file = filename.lower()
                score = sum(
                    2 * (hint in lowered_file) + (f"/{hint}" in lowered_dir)
                    for hint in _SURFACE_HINTS
                )
                if score > 0:
                    surface.append((score, os.path.join(dirpath, filename)))

        surface.sort(key=lambda item: -item[0])
        if surface:
            parts.append("\n## ACTION SURFACE (tool / prompt / command / data files)\n")
            for _, filepath in surface:
                if total > _MAX_TOTAL_CHARS:
                    break
                body = _read(filepath)
                if not body.strip():
                    continue
                chunk = f"\n### FILE: {os.path.relpath(filepath, root)}\n{body}\n"
                parts.append(chunk)
                total += len(chunk)

        if examples:
            parts.append("\n## EXAMPLE TASKS (filenames reveal real use-cases)\n")
            parts.append("\n".join(f"- {e}" for e in sorted(examples)[:120]))

        return AgentEvidence(
            name=agent_name,
            text="".join(parts)[: _MAX_TOTAL_CHARS + 12_000],
            metadata={"source": self.name, "root": root, "surface_files": len(surface)},
        )


def resolve_source(kind: str, **kwargs: Any) -> AgentSource:
    """Build a registered source by name (``repo`` today; ``vapi``/``retell`` tomorrow)."""
    return source_registry.create(kind, **kwargs)
