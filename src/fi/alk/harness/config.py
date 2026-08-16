"""Session configuration for the harness.

One place decides which model runs, how the session reaches it, and what the agent is allowed to
touch. Every stage builds its options from here so that a change of provider or model is one
edit rather than a search across stages.

Credentials are never read from source. The Vertex project and credential path come from the
environment, which is also how the rest of the platform resolves them.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

from claude_agent_sdk import ClaudeAgentOptions

DEFAULT_MODEL = "claude-sonnet-4-6"

SKILLS_ROOT = Path(__file__).parent / "skills"

_READ_ONLY_TOOLS = ("Read", "Glob", "Grep")


def provider_env(model: str | None = None) -> dict[str, str]:
    """The provider block passed to the session.

    Claude Code resolves the GCP project from ``GOOGLE_CLOUD_PROJECT``, the credential file, or
    the active gcloud configuration, in that order, so an unset project id is not an error here.
    """
    env = {
        "CLAUDE_CODE_USE_VERTEX": "1",
        "CLOUD_ML_REGION": os.environ.get("CLOUD_ML_REGION", "global"),
        "ANTHROPIC_MODEL": model or os.environ.get("ALK_HARNESS_MODEL", DEFAULT_MODEL),
    }
    for passthrough in (
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        value = os.environ.get(passthrough)
        if value:
            env[passthrough] = value
    return env


def read_only_session(
    *,
    system_prompt: str,
    cwd: str | Path,
    mcp_servers: dict[str, Any] | None = None,
    extra_tools: Iterable[str] = (),
    max_turns: int = 40,
    model: str | None = None,
) -> ClaudeAgentOptions:
    """A session that may read the agent under test but never write to it.

    The agent under test is somebody's real repository. The harness reads it and writes its own
    artifacts elsewhere, so the built-in write tools are simply not granted; the only way this
    session can produce anything is by calling one of ours.
    """
    allowed = [*_READ_ONLY_TOOLS, "AskUserQuestion", *extra_tools]
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=allowed,
        mcp_servers=dict(mcp_servers or {}),
        permission_mode="acceptEdits",
        cwd=str(cwd),
        setting_sources=[],
        max_turns=max_turns,
        env=provider_env(model),
    )


def artifact_dir(agent: str, root: str | Path | None = None) -> Path:
    """Where a given agent's generated environment lives."""
    base = Path(root) if root else Path("artifacts/environments")
    return base / agent


def load_skill(name: str) -> str:
    """A stage's instructions, kept as a file so the method is editable without touching code."""
    path = SKILLS_ROOT / name / "SKILL.md"
    if not path.exists():
        raise FileNotFoundError(f"no skill at {path}")
    return path.read_text(encoding="utf-8")
