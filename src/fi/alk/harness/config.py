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
    if os.environ.get("AGENTCC_API_KEY", "").strip():
        return "credentials: Agent Command Center virtual key"
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
    return (
        model
        or os.environ.get("ALK_HARNESS_MODEL")
        or resolve().default_model
    )


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


# Models this harness is allowed to spend on. Karthik's constraint, and it is a hard one: the
# Gemini credits are what we have, Claude models are what we cannot afford. `CLAUDE_CODE_USE_VERTEX`
# is the specific trap, because it means Anthropic's own models hosted on Vertex rather than
# Google's, so a single stray flag spends on exactly what is forbidden.
BILLABLE = ("gemini",)
FORBIDDEN = ("claude", "sonnet", "opus", "haiku")


def refuse_a_model_we_cannot_afford(model: str) -> None:
    """Raise unless this is a model we are allowed to spend on.

    Called wherever a model is resolved rather than once at the edge, because the ways a Claude id
    can arrive are many: a default, an env var, a worker override, a gateway that silently
    substitutes. One check at the boundary would miss most of them.
    """
    named = (model or "").strip().lower()
    if not named:
        raise ValueError("no model was chosen; refusing to let the provider pick one")
    if any(word in named for word in FORBIDDEN):
        raise ValueError(
            f"refusing to run on {model!r}: this harness may only spend on "
            f"{', '.join(BILLABLE)} models. Set ALK_HARNESS_MODEL to a Gemini model."
        )
    if not any(word in named for word in BILLABLE):
        raise ValueError(
            f"refusing to run on {model!r}: it is not recognisably a "
            f"{'/'.join(BILLABLE)} model, and an unrecognised id is how a Claude model gets "
            "billed by accident."
        )


def gateway_wire_model(model: str) -> str:
    """The name the SDK puts on the wire for `model`.

    The Claude Agent SDK validates a model name locally before it makes a request, so a Gemini id
    never leaves the process. Behind Agent Command Center the wire carries a Claude-shaped alias
    the gateway resolves back to the model this run chose; everywhere else the name is its own.
    """
    if not os.environ.get("AGENTCC_API_KEY", "").strip():
        return model
    if "claude" in (model or "").lower():
        return model
    return os.environ.get("AGENTCC_CLAUDE_MODEL_ALIAS", "claude-sonnet-4-6").strip()


def provider_env(model: str | None = None) -> dict[str, str]:
    """The provider block passed to the session.

    With ``AGENTCC_API_KEY`` set, Claude Code speaks the Anthropic Messages protocol to Agent
    Command Center.  The gateway translates that request to the provider selected by the model
    name.  Otherwise Claude Code talks to Anthropic on Vertex directly and resolves the GCP
    project from ``GOOGLE_CLOUD_PROJECT``, the credential file, or active gcloud configuration.
    """
    # Every model a session can reach is pinned to the same one. Naming only the main model
    # leaves the sub-agent and fast-path settings to the CLI's own preference, and a suite written
    # by twenty writers then runs on whatever that preference happens to be rather than on the
    # model the run asked for.
    chosen = chosen_model(model)
    refuse_a_model_we_cannot_afford(chosen)
    # Agent Command Center speaks Anthropic Messages in front of the model this run chose. The
    # SDK validates a model name locally before it makes a request, so the wire carries a
    # Claude-shaped alias the gateway maps back to `chosen`; the alias is never what is billed.
    agentcc_key = os.environ.get("AGENTCC_API_KEY", "").strip()
    if agentcc_key:
        base_url = (
            os.environ.get("AGENTCC_BASE_URL", "https://gateway.futureagi.com")
            .strip()
            .rstrip("/")
        )
        wire = gateway_wire_model(chosen)
        return {
            # AUTH_TOKEN is sent as a Bearer token, which is how a virtual key authenticates.
            # API_KEY would instead use Anthropic's x-api-key header.
            "ANTHROPIC_AUTH_TOKEN": agentcc_key,
            "ANTHROPIC_BASE_URL": base_url,
            # Turn off the direct Vertex transport in case the parent process has it on:
            # ClaudeAgentOptions.env is merged over the parent environment, and Vertex here
            # would mean Anthropic's own models hosted on Vertex, which is not this.
            "CLAUDE_CODE_USE_VERTEX": "0",
            "ANTHROPIC_MODEL": wire,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": wire,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": wire,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": wire,
            "ANTHROPIC_SMALL_FAST_MODEL": wire,
            # Sub-agents run on the same alias, so a suite written by twenty of them cannot
            # land on whatever the CLI would otherwise prefer.
            "CLAUDE_CODE_SUBAGENT_MODEL": wire,
        }
    env = {
        # The CLI's own Vertex route. CLAUDE_CODE_USE_VERTEX means Anthropic's models hosted on
        # Vertex, so this path is only reachable for a model we are allowed to spend on.
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


def read_only_session(
    *,
    system_prompt: str,
    cwd: str | Path,
    servers: dict[str, Any] | None = None,
    extra_builtins: Iterable[str] = (),
    max_turns: int = 40,
    model: str | None = None,
) -> SessionSpec:
    """A session that may read the agent under test but never write to it.

    The agent under test is somebody's real repository. The harness reads it and writes its own
    artifacts elsewhere, so the built-in write tools are simply not granted; the only way this
    session can produce anything is by calling one of ours.
    """
    return SessionSpec(
        system_prompt=system_prompt,
        servers=dict(servers or {}),
        builtins=tuple(
            dict.fromkeys([*_READ_ONLY_TOOLS, "AskUserQuestion", *extra_builtins])
        ),
        cwd=str(cwd),
        max_turns=max_turns,
        model=chosen_model(model),
        thinking=True,
    )


# Tools the host offers every session that no stage of this harness has any use for. Denying
# them at the gate works and is the backstop, but a denial still costs the turn that discovered
# it — and these get reached for in almost every stage. Naming them as disallowed keeps them out
# of the tool list the model is shown, so the turn is never spent.
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

    ``can_use_tool`` alone does not do this. An ``allowed_tools`` entry approves those tools
    before the callback is consulted, and the SDK then warns that the callback is shadowed — so
    the gate never runs for the tools we granted, and in practice does not stop the ones we did
    not either. A host ``ToolSearch`` reached every stage, returned nothing, and cost a turn each
    time.

    A PreToolUse hook is consulted for every call, which is what the deny-by-default rule needed
    in order to be true rather than intended.
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
    expose, and anything not named here is by definition not part of how this stage works. An
    allow-by-default gate let a host search tool through, which returned nothing useful and cost
    a stage its entire turn budget looping on it; the same hole would let a file write through.

    Tools granted through ``allowed_tools`` are approved before this is consulted, so this only
    ever sees the ones that were not.
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


def artifact_dir(agent: str, root: str | Path | None = None) -> Path:
    """The folder holding one conversation: its contract, world, scenarios and runs.

    One conversation, one directory. Everything about testing one agent lives together, which is
    what makes a session something you can close, reopen, hand over or delete as one thing.
    """
    base = Path(root) if root else ARTIFACTS_ROOT / "sessions"
    return base / agent


HARNESS = SKILLS_ROOT / "harness.md"


def declared_modalities() -> tuple[str, ...]:
    """Every modality a kind file under ``skills/kinds/`` says it is for.

    The point of the kind directory is that supporting a new sort of agent is adding a file. That
    only holds if the contract will *accept* the new modality, and until this existed the accepted
    list was a tuple in code, so a browser or computer-use agent needed an edit in two more places
    before its file could ever be read.

    Read from ``applies_to`` rather than from the file name, because that is the declaration the
    matcher already trusts.
    """
    root = SKILLS_ROOT / "kinds"
    found: set[str] = set()
    if not root.is_dir():
        return ()
    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        head = text.split("---")[1] if text.startswith("---") and "---" in text[3:] else ""
        for line in head.splitlines():
            if not line.strip().lower().startswith("applies_to:"):
                continue
            for clause in line.split(":", 1)[1].split(","):
                key, _, value = clause.strip().lower().partition("=")
                if key.strip() == "modality" and value.strip():
                    found.add(value.strip())
    return tuple(sorted(found))


def discovered_skills(**about: str) -> str:
    """Every extra skill that says it applies to this agent, found by looking rather than by name.

    Two directories are read. ``skills/kinds/`` is what kind of agent this is: voice, chat,
    browser, and whatever a customer turns up with next. ``skills/modules/`` is everything that
    cuts across kinds: the people an agent talks to apply to voice and chat alike and to no
    agent that talks to nobody. Both use the same declaration, so where a file lives says what
    sort of thing it is and nothing else.

    A skill is a markdown file under either whose first lines declare what it is for::

        ---
        name: voice
        applies_to: modality=voice
        ---

    The harness reads the directory, keeps the files whose ``applies_to`` matches what it was told
    about this agent, and appends them in name order. ``applies_to: any`` always matches, and a file
    with no declaration is skipped rather than guessed at.

    Naming each kind in code would mean editing code to add one, and there will be many: voice, chat,
    browser, and whatever a customer turns up with next. **Adding support for a kind of agent is
    adding a file to ``kinds/``, and adding something that cuts across them is adding one to
    ``modules/``.** Deleting either file removes what it taught, which is how the people block
    stops existing for an agent that talks to nobody.
    """
    roots = [SKILLS_ROOT / "kinds", SKILLS_ROOT / "modules"]
    wanted = {
        key.lower(): str(value).strip().lower() for key, value in about.items() if value
    }
    found: list[tuple[str, str]] = []
    for path in sorted(
        (one for root in roots if root.is_dir() for one in root.glob("*.md")),
        key=lambda one: one.stem,
    ):
        text = path.read_text(encoding="utf-8")
        head = text.split("---")[1] if text.startswith("---") and "---" in text[3:] else ""
        applies = ""
        for line in head.splitlines():
            if line.strip().lower().startswith("applies_to:"):
                applies = line.split(":", 1)[1].strip().lower()
        if not applies:
            continue
        if applies == "any":
            found.append((path.stem, text))
            continue
        # `key=value`, and every clause has to hold.
        clauses = [one.strip() for one in applies.split(",") if one.strip()]
        if all(
            "=" in clause
            and wanted.get(clause.split("=", 1)[0].strip())
            == clause.split("=", 1)[1].strip()
            for clause in clauses
        ):
            found.append((path.stem, text))
    if not found:
        return ""
    return "\n\n---\n\n" + "\n\n---\n\n".join(text for _name, text in found)


def load_skill(name: str, *, preamble: bool = True) -> str:
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
    # Lookup material a skill keeps beside itself, carried in with it. A session runs with its working
    # directory on the artifacts it is producing, not on the skills tree, and the skills live inside an
    # installed package, so a skill that says "see references/x.md" is naming a path its reader cannot
    # reach. Appending them is what makes the split into a main file and its references safe.
    for reference in sorted((SKILLS_ROOT / name / "references").glob("*.md")):
        stage += (
            f"\n\n---\n\n# references/{reference.name}\n\n"
            f"{reference.read_text(encoding='utf-8')}"
        )
    if not preamble or not HARNESS.exists():
        return stage
    return (
        f"{HARNESS.read_text(encoding='utf-8')}\n\n"
        "---\n\n"
        "# The stage you are in now\n\n"
        f"{stage}"
    )


def writer_model() -> str:
    """The model a scenario writer runs on, from ALK_HARNESS_WRITER_MODEL.

    Empty by default, which inherits the parent's model and is what has always happened. A writer is
    handed its brief, the contract, the world and the skill, so it is doing constrained work rather
    than deciding what the suite should be, and a cheaper model may be enough for it. Whether it is
    enough is a question for a measured run: a writer that fails the admission gates more often
    spends the saving on retries, and the gates are what protect scenario quality.
    """
    return os.environ.get("ALK_HARNESS_WRITER_MODEL", "").strip()
