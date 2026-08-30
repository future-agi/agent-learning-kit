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

from .backends import SessionSpec, resolve

# The first backend's default, kept importable because callers and tests name it. The model a
# run actually gets comes from chosen_model, which asks the selected backend.
DEFAULT_MODEL = "claude-sonnet-4-6"

SKILLS_ROOT = Path(__file__).parent / "skills"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_ROOT = PROJECT_ROOT / "artifacts"

_READ_ONLY_TOOLS = ("Read", "Glob", "Grep")


def credentials_hint() -> str:
    """A line saying which credentials a run will use, or a warning that it is guessing.

    Claude Code falls back to the active gcloud login when no service-account file is named,
    which is a legitimate setup and an easy accident. The accident produces a provider auth
    error several layers down, so it is worth saying out loud which one is in play.
    """
    named = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if named:
        return f"credentials: {Path(named).name}"
    return (
        "credentials: none named, falling back to your gcloud login. If calls fail to "
        "authenticate, load the env file first:\n"
        "           set -a; . ./.env.acceptance; set +a"
    )


def chosen_model(model: str | None = None) -> str:
    """The model a session will actually run on.

    Passed to the session explicitly as well as through the environment. The environment alone
    does not win: the CLI has its own default and will quietly use it, so a run meant for Haiku
    goes out on whatever the CLI felt like and the bill says so afterwards.

    With nothing named anywhere, the selected backend's own default runs, so switching
    ``ALK_HARNESS`` never sends one vendor's model name to another vendor's loop.
    """
    return model or os.environ.get("ALK_HARNESS_MODEL") or resolve().default_model


def thinking_config() -> dict[str, Any]:
    """How much the model may think, from ALK_HARNESS_THINKING.

    The Claude Code CLI defaults to adaptive thinking. In this harness the correctness of what a
    stage produces is re-checked by code gates (a scenario is proved against the real world, a
    contract is validated), so the model's private reasoning is spent on decisions the gates make
    again anyway. Left unset, that reasoning was the majority of generated tokens and the majority
    of wall time. Default to disabled for speed; ``adaptive`` restores the old behaviour, and an
    integer sets an explicit budget for models that still honour one.
    """
    setting = os.environ.get("ALK_HARNESS_THINKING", "disabled").strip().lower()
    if setting in {"adaptive", "on", "auto"}:
        return {"type": "adaptive", "display": "omitted"}
    if setting.isdigit() and int(setting) > 0:
        return {"type": "enabled", "budget_tokens": int(setting), "display": "omitted"}
    return {"type": "disabled"}


def provisioning(enabled: bool | None = None) -> bool:
    """Compatibility switch for callers selecting the legacy provisioning surface.

    The autonomous workflow now discovers and provisions source infrastructure automatically;
    explicit stage consumers can still select the older engine-provisioning tool surface while
    they migrate.
    """
    if enabled is not None:
        return enabled
    return os.environ.get("ALK_HARNESS_PROVISION", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def provider_env(model: str | None = None) -> dict[str, str]:
    """The provider block passed to the session.

    Claude Code resolves the GCP project from ``GOOGLE_CLOUD_PROJECT``, the credential file, or
    the active gcloud configuration, in that order, so an unset project id is not an error here.
    """
    # Every model a session can reach is pinned to the same one. Naming only the main model
    # leaves the sub-agent and fast-path settings to the CLI's own preference, and a suite written
    # by twenty writers then runs on whatever that preference happens to be rather than on the
    # model the run asked for.
    chosen = chosen_model(model)
    env = {
        "CLAUDE_CODE_USE_VERTEX": "1",
        "CLOUD_ML_REGION": os.environ.get("CLOUD_ML_REGION", "global"),
        "ANTHROPIC_MODEL": chosen,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": chosen,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": chosen,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": chosen,
        "ANTHROPIC_SMALL_FAST_MODEL": chosen,
        "CLAUDE_CODE_SUBAGENT_MODEL": chosen,
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


def working_session(
    *,
    system_prompt: str,
    cwd: str | Path,
    servers: dict[str, Any] | None = None,
    extra_builtins: Iterable[str] = (),
    max_turns: int = 40,
    model: str | None = None,
) -> SessionSpec:
    """A session that can read, write and run things.

    A stage that has to work out what an unfamiliar agent is, build the infrastructure it talks
    to, and prove that infrastructure answers, is doing engineering, and engineering needs a shell
    and an editor. Withholding them did not make the work safer, it made the stage unable to
    finish it and left the finishing to a person reading logs. What bounds it is this grant,
    enforced on every call, and not the sandbox: the sandbox is only there in the hosted lane.
    """
    return SessionSpec(
        system_prompt=system_prompt,
        servers=dict(servers or {}),
        builtins=tuple(
            dict.fromkeys(
                [
                    *_READ_ONLY_TOOLS,
                    "Write",
                    "Edit",
                    "Bash",
                    "AskUserQuestion",
                    *extra_builtins,
                ]
            )
        ),
        cwd=str(cwd),
        max_turns=max_turns,
        model=chosen_model(model),
        thinking=True,
    )


# Host tools no stage is given. Denying them at the gate works and is the backstop, but a denial
# still costs the turn that discovered it, and these get reached for in almost every stage. Naming
# them as disallowed keeps them out of the tool list the model is shown, so the turn is never
# spent. A stage that was granted one of these keeps it: the list is filtered against the grant.
UNWANTED = (
    "ToolSearch",
    "Bash",
    "Write",
    "Edit",
    "NotebookEdit",
    "WebFetch",
    "WebSearch",
)


def gate_hooks(granted: Iterable[str]) -> dict[str, Any]:
    """Deny anything a stage was not given, at the point the SDK actually asks.

    ``can_use_tool`` alone does not do this, and the SDK is explicit about why: an
    ``allowed_tools`` entry auto-approves that tool before the callback is consulted, so the
    callback is shadowed for everything we granted, and it never sees them. What it does see are
    the tools we did not grant, which is why an allow-everything callback is not a neutral
    default but an open door: it approves precisely the host extras the harness never offered.
    A host ``ToolSearch`` once reached every stage, returned nothing, and cost a turn each time.

    A PreToolUse hook is consulted for every call, which is what makes the deny-by-default rule
    true rather than intended.
    """
    from claude_agent_sdk.types import HookMatcher

    permitted = {*granted, "AskUserQuestion"}

    async def refuse(
        payload: dict[str, Any], _tool_use_id: Any, _context: Any
    ) -> dict[str, Any]:
        name = str(payload.get("tool_name") or "")
        if not name or name in permitted:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"{name} is not part of this stage. You have "
                    f"{', '.join(sorted(permitted)) or 'no other tools'}, and everything you "
                    "produce goes through those, because those are what check it."
                ),
            }
        }

    return {"PreToolUse": [HookMatcher(hooks=[refuse])]}


def permission_gate(ask: Any | None = None, granted: Iterable[str] = ()) -> Any:
    """Decide what a stage may do: nothing it was not given.

    Deny by default, not deny-a-list. A session is offered whatever tools its host happens to
    expose, and anything not named here is by definition not part of how this stage works.

    The sandbox is not the answer to this on its own. It is the boundary for the hosted lane, but
    the same stages run in-process on an operator's machine, where cwd does not confine a shell,
    and a grant that means one thing there and another thing hosted stops the local run being a
    rehearsal of the hosted one. The grant means the same in both.
    """
    permitted = set(granted)

    async def gate(tool_name: str, payload: dict[str, Any], context: Any) -> Any:
        from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

        if tool_name == "AskUserQuestion" and ask is not None:
            return await ask(tool_name, payload, context)
        if tool_name in permitted:
            return PermissionResultAllow(updated_input=payload)
        return PermissionResultDeny(
            message=(
                f"{tool_name} is not part of this stage. You have "
                f"{', '.join(sorted(permitted)) or 'no other tools'}, and everything you "
                "produce goes through those, because those are what check it."
            )
        )

    return gate


def operator_ask(ask: Any | None = None) -> Any:
    """Route the model's questions to whoever is running this.

    This is the operator callback a stage carries in ``ask``, not a permission decision: what a
    stage may do is decided by ``permission_gate`` and ``gate_hooks``.
    """

    async def gate(tool_name: str, payload: dict[str, Any], context: Any) -> Any:
        from claude_agent_sdk.types import PermissionResultAllow

        if tool_name == "AskUserQuestion" and ask is not None:
            return await ask(tool_name, payload, context)
        return PermissionResultAllow(updated_input=payload)

    return gate


def artifact_dir(agent: str, root: str | Path | None = None) -> Path:
    """The folder holding one conversation: its contract, world, scenarios and runs.

    One conversation, one directory. Everything about testing one agent lives together, which is
    what makes a session something you can close, reopen, hand over or delete as one thing.
    """
    base = Path(root) if root else ARTIFACTS_ROOT / "sessions"
    return base / agent


HARNESS = SKILLS_ROOT / "harness.md"


def load_skill(name: str) -> str:
    """One stage's instructions, behind what the harness as a whole is for.

    Every stage gets the same opening: what this harness produces, why the division between what
    a model decides and what code decides exists, and what makes a result worth believing. A
    stage that knows only its own step does its step well and still gets the point of it wrong —
    it works around a gate instead of fixing what the gate named, or it reports a number that
    quietly skipped half its checks.

    The stage's own method follows. Both are files, so how any of this works can be changed
    without touching code.
    """
    path = SKILLS_ROOT / name / "SKILL.md"
    if not path.exists():
        raise FileNotFoundError(f"no skill at {path}")
    stage = path.read_text(encoding="utf-8")
    catalogue = sub_skills(name)
    if catalogue:
        stage = f"{stage}\n\n{catalogue}"
    if not HARNESS.exists():
        return stage
    return (
        f"{HARNESS.read_text(encoding='utf-8')}\n\n"
        "---\n\n"
        "# The stage you are in now\n\n"
        f"{stage}"
    )


def _summarise(entry: Path) -> str:
    """One line describing a sub-skill, for the catalogue the model chooses from.

    A skill states when it applies, and when it does not, in its frontmatter `description` -- that
    is the field written to be read by whoever is deciding. Falling back to the first line of prose
    would otherwise summarise a skill as `---`, and a catalogue that describes nothing is a
    catalogue nobody can choose from.
    """
    text = entry.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if line.strip().startswith("description:"):
                    value = line.split(":", 1)[1].strip()
                    return value.strip('"').strip("'")
    for line in text.splitlines():
        if line.strip() and not line.startswith("#") and not line.startswith("---"):
            return line.strip()
    return ""


def sub_skills(name: str) -> str:
    """The per-kind guidance available inside a stage, for the model to choose between.

    A stage skill says how the stage works for any agent. What differs between a LiveKit voice
    agent, a Vapi assistant reached only by webhook, and a browser agent is method, not stage, so
    that belongs in a sub-skill the model selects after it has read the contract rather than in a
    constant chosen before anyone has looked at the repository.

    Every ``*.md`` under the stage's ``references/`` is offered, name and description only. The
    body stays on disk until the model asks for it, so a stage carries the index of everything it
    could do at a fraction of the cost of carrying all of it. Adding support for a new kind of
    agent is a file in that directory, not a release.
    """
    directory = SKILLS_ROOT / name / "references"
    found = sorted(directory.glob("*.md")) if directory.is_dir() else []
    if not found:
        return ""
    lines = [
        "## References available to you",
        "",
        "Each line is a file you may read, and the text after the name states the evidence that",
        "makes it the right one. Nothing selects for you: gather the evidence first, then choose.",
        "Using none of them is a legitimate answer for an agent none of them describes, and asking",
        "the operator is the right move when the evidence does not settle it.",
        "",
    ]
    for entry in found:
        lines.append(f"- `{entry.name}`: {_summarise(entry) or 'no summary line'}")
    lines.append("")
    lines.append(
        f"They are in `{directory}`. Decide from the evidence in the repository and the contract "
        "which one describes the agent in front of you, say what you concluded and why, then read "
        "that file with the Read tool before you rely on it. A description that does not match "
        "what you found is the wrong file: reading it anyway produces confident, plausible work "
        "against the wrong shape of agent, and nothing will error."
    )
    return "\n".join(lines)
