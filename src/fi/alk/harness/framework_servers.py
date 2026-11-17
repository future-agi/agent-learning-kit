"""The framework servers a source may start, recognised so the bundle reaches them the way each serves its agents:
Google ADK's `adk web` / `adk api_server` and LangGraph's `langgraph dev`.

This module is the harness's one framework launch adapter. Each framework's own server command, options and app layout
are known here and nowhere else in the bundle author, which only asks this module whether a command is one of them.
"""

from __future__ import annotations

import ast
import functools
import json
import os
import re
from pathlib import Path
from collections.abc import Callable
from typing import Any

from .contract import Runtime



def _ignored_parts() -> set[str]:
    from .bundle_author_v2 import _IGNORED_ARTIFACT_PARTS

    return _IGNORED_ARTIFACT_PARTS


def _adk_server_arguments(command: list[str]) -> list[str] | None:
    """The arguments after ``adk web`` or ``adk api_server`` (one HTTP API; ``web`` adds the dev UI), or None for any
    other argv."""

    normalized = [str(item).strip() for item in command]
    for index in range(len(normalized) - 1):
        token = normalized[index]
        # `adk` on PATH or by path (`.venv/bin/adk`), or the module (`python -m google.adk.cli`).
        if Path(token).name == "adk" or (
            token == "google.adk.cli" and index > 0 and normalized[index - 1] == "-m"
        ):
            if normalized[index + 1] in {"web", "api_server"}:
                return normalized[index + 2 :]
    return None


# A copy of ADK's server options that take no value, for when ``google.adk.cli`` cannot be imported.
_ADK_SERVER_FLAGS = frozenset(
    {
        "-v",
        "--verbose",
        "--trace_to_cloud",
        "--otel_to_cloud",
        "--reload",
        "--no-reload",
        "--a2a",
        "--reload_agents",
        "--use_local_storage",
        "--no_use_local_storage",
        "--auto_create_session",
        "--express_mode",
        "--with_ui",
    }
)


def _both_separators(options) -> frozenset[str]:
    """Each option also under the other spelling of its separator."""
    return frozenset(
        spelling
        for option in options
        for spelling in (option, "--" + option[2:].replace("-", "_"), "--" + option[2:].replace("_", "-"))
    )


# A copy of ADK's server options that take a value.
_ADK_SERVER_VALUE_OPTIONS = _both_separators(
    (
        "--allow_origins",
        "--artifact_service_uri",
        "--artifact_storage_uri",
        "--default_llm_model",
        "--disable_features",
        "--enable_features",
        "--eval_storage_uri",
        "--extra_plugins",
        "--gemini_enterprise_app_name",
        "--host",
        "--log_level",
        "--logo-image-url",
        "--logo-text",
        "--memory_service_uri",
        "--port",
        "--session_db_url",
        "--session_service_uri",
        "--trigger_sources",
        "--url_prefix",
    )
)

# An option neither table knows, so which token is the agents directory cannot be told.
_UNKNOWN_ADK_OPTION = object()


@functools.cache
def _adk_server_options() -> tuple[frozenset[str], frozenset[str]]:
    """ADK's ``web`` and ``api_server`` options as (flags, options taking a value): the copies above, with what the
    installed ADK's own CLI declares when it can be imported."""
    try:
        from google.adk.cli.cli_tools_click import main

        params = [param for name in ("web", "api_server") for param in main.commands[name].params]
    except Exception:
        return _ADK_SERVER_FLAGS, _ADK_SERVER_VALUE_OPTIONS
    flags: set[str] = set()
    values: set[str] = set()
    for param in params:
        names = [name for name in (*param.opts, *param.secondary_opts) if name.startswith("-")]
        (flags if getattr(param, "is_flag", False) or getattr(param, "count", False) else values).update(names)
    return _ADK_SERVER_FLAGS | flags, _ADK_SERVER_VALUE_OPTIONS | _both_separators(values)


def _adk_agents_dir_argument(arguments: list[str]) -> str | object | None:
    """The positional ``AGENTS_DIR`` among ``adk web`` / ``adk api_server`` arguments, if any;
    ``_UNKNOWN_ADK_OPTION`` when an option before it is not one ADK is known to take."""

    flags, value_options = _adk_server_options()
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            return arguments[index + 1] if index + 1 < len(arguments) else None
        if token.startswith("-"):
            name = token.split("=", 1)[0]
            if name in flags or (name in value_options and "=" in token):
                index += 1
            elif name in value_options:
                index += 2
            else:
                return _UNKNOWN_ADK_OPTION
            continue
        return token
    return None


def _is_adk_web_command(command: list[str]) -> bool:
    """Return whether argv starts Google ADK's source-owned HTTP server."""

    return _adk_server_arguments(command) is not None


def _command_port(command: list[str], *, default: int) -> int:
    """Read a conventional ``--port`` option without interpreting arbitrary shell text."""

    for index, token in enumerate(command):
        if token == "--port" and index + 1 < len(command):
            try:
                port = int(command[index + 1])
            except (TypeError, ValueError):
                return default
            return port if 1 <= port <= 65535 else default
        if token.startswith("--port="):
            try:
                port = int(token.partition("=")[2])
            except ValueError:
                return default
            return port if 1 <= port <= 65535 else default
    return default


def _adk_app_name(
    root: Path, configured_workdir: str, command: list[str] | None = None
) -> str | None:
    """The one ADK app ``adk web`` or ``adk api_server`` serves from the agents directory its argv names
    (default: its working directory), by ADK's own loading rules; None when there is not exactly one.
    """

    configured = configured_workdir.strip()
    workdir = (
        (root / configured).resolve()
        if configured not in {"", ".", "/"}
        else root.resolve()
    )
    if not workdir.is_relative_to(root.resolve()) or not workdir.is_dir():
        return None
    agents_arg = _adk_agents_dir_argument(_adk_server_arguments(command or []) or [])
    if agents_arg is _UNKNOWN_ADK_OPTION:
        return None
    if isinstance(agents_arg, str):
        agents_dir = (workdir / agents_arg).resolve()
        # A named directory ADK cannot serve from this checkout is no app to guess from.
        if not agents_dir.is_relative_to(root.resolve()) or not agents_dir.is_dir():
            return None
        workdir = agents_dir
    # ADK treats a directory as one app when it holds agent.py or root_agent.yaml.
    if _is_adk_app_directory(workdir, package=False):
        # The checkout root is renamed at run time, so its name here is not the app's.
        if workdir == root.resolve() or not _ADK_APP_NAME.fullmatch(workdir.name):
            return None
        return workdir.name
    candidates = sorted(
        path.name
        for path in workdir.iterdir()
        if path.is_dir()
        and path.name not in _ignored_parts()
        and _ADK_APP_NAME.fullmatch(path.name)
        and _is_adk_app_directory(path, package=True)
    )
    return candidates[0] if len(candidates) == 1 else None


# ADK loads only apps whose name is a Python identifier made of these characters.
_ADK_APP_NAME = re.compile(r"[A-Za-z0-9_]+")


def _is_adk_app_directory(path: Path, *, package: bool) -> bool:
    """Whether ADK loads an app from ``path``: ``agent.py``, ``root_agent.yaml`` or, for a
    sub-app, a package whose ``__init__.py`` binds ``root_agent``."""

    if (path / "agent.py").is_file() or (path / "root_agent.yaml").is_file():
        return True
    init = path / "__init__.py"
    if not package or not init.is_file():
        return False
    try:
        tree = ast.parse(init.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return False
    return _ADK_ROOT_AGENT in _module_bindings(tree)


# The name ADK's loader looks up on an app package.
_ADK_ROOT_AGENT = "root_agent"


def _module_bindings(tree: ast.Module) -> set[str]:
    """The names a module's top-level statements assign or import."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(target.id for target in targets if isinstance(target, ast.Name))
    return names


def _adk_web_interface(root: Path, runtime: dict[str, Any]) -> dict[str, Any] | None:
    """Compile ADK's documented web API into the generic JSON HTTP interface."""

    command = [str(item) for item in (runtime.get("command") or [])]
    if not _is_adk_web_command(command):
        return None
    app_name = _adk_app_name(root, str(runtime.get("workdir") or ""), command)
    if app_name is None:
        return None
    return _adk_interface_for(command, app_name)


def _adk_interface_for(command: list[str], app_name: str) -> dict[str, Any]:
    port = _command_port(command, default=8000)
    return {
        "kind": "http",
        "protocol": "json_template",
        "port": port,
        "path": "/run_sse",
        "health_path": "/docs",
        "include_tools": False,
        "setup_requests": [
            {
                "method": "POST",
                "path": f"/apps/{app_name}/users/{{{{thread_id}}}}/sessions",
                "body_template": {},
                "accepted_statuses": [200],
                "capture": {"session_id": "id"},
            }
        ],
        "request_template": {
            "user_id": "{{thread_id}}",
            "app_name": app_name,
            "streaming": True,
            "session_id": "{{session_id}}",
            "new_message": {
                "role": "user",
                "parts": [{"text": "{{new_message_content}}"}],
            },
        },
        "response_path": "content.parts.0.text",
    }


def _is_langgraph_server_command(command: list[str]) -> bool:
    """Return whether argv starts the LangGraph development server (``langgraph dev``, by any path to the CLI).
    ``langgraph up`` builds and runs a Docker image of the app, which a sandbox does not run."""

    normalized = [str(item).strip() for item in command]
    return any(
        Path(normalized[index]).name == "langgraph" and normalized[index + 1] == "dev"
        for index in range(max(0, len(normalized) - 1))
    )


def _langgraph_config_argument(command: list[str]) -> str | None:
    """The ``--config`` a ``langgraph dev`` command names, or None."""
    for index, token in enumerate(command):
        if token == "--config" and index + 1 < len(command):
            return command[index + 1]
        if token.startswith("--config="):
            return token.partition("=")[2]
    return None


def _uv_option(command: list[str], option: str) -> str | None:
    """The value a ``uv run`` gives ``option`` (``--project``, ``--directory``) before the program it runs, or None."""
    if not command or Path(command[0]).name != "uv":
        return None
    for index, token in enumerate(command):
        if Path(token).name == "langgraph":
            break
        if token == option and index + 1 < len(command):
            return command[index + 1]
        if token.startswith(option + "="):
            return token.partition("=")[2]
    return None


def _workdir(root: Path, runtime: dict[str, Any]) -> Path:
    configured = str(runtime.get("workdir") or "").strip()
    return (root / configured) if configured not in {"", ".", "/"} else root


def _langgraph_config(root: Path, runtime: dict[str, Any]) -> Path | None:
    """The ``langgraph.json`` a ``langgraph dev`` app is served from: the one its command names, relative to where it
    starts (the workdir, or the ``uv run --directory`` it changes to) or to the ``uv run --project`` it runs in, else
    the nearest one from that workdir up to the source root; None when there is none."""
    command = [str(item) for item in (runtime.get("command") or [])]
    workdir = _workdir(root, runtime)
    named = _langgraph_config_argument(command)
    directory = _uv_option(command, "--directory")
    started = workdir / directory if directory else workdir
    project = _uv_option(command, "--project")
    resolved_root = root.resolve()
    candidates = (
        [started / named, workdir / named, *([started / project / named] if project else [])]
        if named
        else [directory / "langgraph.json" for directory in (started, *started.parents)]
    )
    for candidate in candidates:
        config = candidate.resolve()
        if not config.is_relative_to(resolved_root):
            continue
        if config.is_file() and config.suffix == ".json":
            return config
    return None


def langgraph_config_problem(root: Path, runtime: dict[str, Any]) -> str | None:
    """Why a ``langgraph dev`` command's ``--config`` cannot be the app's LangGraph config, or None: it must be a JSON
    file that declares graphs. Any other command has none."""
    command = [str(item) for item in (runtime.get("command") or [])]
    named = _langgraph_config_argument(command)
    if not named or not _is_langgraph_server_command(command):
        return None
    if not named.endswith(".json"):
        return f"--config {named} is not a langgraph.json; name the app's LangGraph config file"
    config = _langgraph_config(root, runtime)
    if config is None:
        return f"--config {named} names no file the app's source holds"
    try:
        graphs = json.loads(config.read_text(encoding="utf-8")).get("graphs")
    except (OSError, ValueError, AttributeError):
        graphs = None
    if not isinstance(graphs, dict) or not graphs:
        return f"--config {named} declares no graphs"
    return None


def _langgraph_server_interface(
    root: Path, runtime: dict[str, Any]
) -> dict[str, Any] | None:
    """``langgraph dev``'s threads and runs API as the generic JSON HTTP interface, for the graph the written interface
    names as its model, else the only graph declared; None when several are declared and none is named."""

    command = [str(item) for item in (runtime.get("command") or [])]
    if not _is_langgraph_server_command(command):
        return None
    config = _langgraph_config(root, runtime)
    if config is None:
        return None
    try:
        graphs = json.loads(config.read_text(encoding="utf-8")).get("graphs")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(graphs, dict) or not graphs:
        return None
    interface = runtime.get("interface")
    named = interface.get("model") if isinstance(interface, dict) else None
    assistant = named if named in graphs else next(iter(graphs)) if len(graphs) == 1 else None
    if assistant is None:
        return None
    return {
        "kind": "http",
        "protocol": "json_template",
        "port": _command_port(command, default=2024),
        "path": "/threads/{{langgraph_thread_id}}/runs/wait",
        "health_path": "/ok",
        "include_tools": False,
        "setup_requests": [
            {
                "method": "POST",
                "path": "/threads",
                "body_template": {},
                "accepted_statuses": [200],
                "capture": {"langgraph_thread_id": "thread_id"},
            }
        ],
        "request_template": {
            "assistant_id": assistant,
            "input": {
                "messages": [{"role": "user", "content": "{{new_message_content}}"}]
            },
        },
        "response_path": "messages.-1.content",
    }


def server_command(command: list[str]) -> list[str]:
    """A framework server's argv as the sandbox runs it: `langgraph dev` without its file watcher, which restarts the
    server on the files it writes beside the app (`.langgraph_api/*.pckl`) mid-call. Any other argv is kept as it is."""
    if _is_langgraph_server_command(command) and "--no-reload" not in command:
        return [*command, "--no-reload"]
    return command


def server_runtime(root: Path, runtime: dict[str, Any]) -> dict[str, Any] | None:
    """The runtime with its framework server's documented API (``adk web``, ``langgraph dev``) sealed as its interface,
    a LangGraph port written out so each world's can replace it; None for any other command."""
    interface = _adk_web_interface(root, runtime) or _langgraph_server_interface(root, runtime)
    if interface is None:
        return None
    command = [str(item) for item in (runtime.get("command") or [])]
    if _is_langgraph_server_command(command) and not any(
        token == "--port" or token.startswith("--port=") for token in command
    ):
        command = [*command, "--port", str(interface["port"])]
    return {**runtime, "command": _launch_from_config(root, runtime, command), "interface": interface}


def _launch_from_config(root: Path, runtime: dict[str, Any], command: list[str]) -> list[str]:
    """`langgraph dev` resolves its config, the graphs it names and their dependencies from where it starts, so it
    starts in the folder that holds its config and names it there. A ``uv run`` keeps the project it ran in
    (``--project``, rebased) and changes to that folder (``--directory``); any other command changes to it first."""
    config = _langgraph_config(root, runtime) if _is_langgraph_server_command(command) else None
    if config is None or not _langgraph_config_argument(command):
        return command
    workdir = _workdir(root, runtime).resolve()
    # The process starts in the workdir, or in the config's folder when the workdir is inside it (`server_start_folder`).
    start = config.parent if workdir.is_relative_to(config.parent) else workdir
    uv = Path(command[0]).name == "uv" and "run" in command[:2]
    directory = _uv_option(command, "--directory")
    project = _uv_option(command, "--project")
    base = (start / directory).resolve() if directory else start
    project_dir = (base / project).resolve() if project else (base if directory else None)
    if project_dir is None or not (project_dir / "pyproject.toml").is_file():
        # The project the app's own dependencies are installed in: one the config lists among its dependencies.
        project_dir = _dependency_project(config, root) or project_dir
    kept: list[str] = []
    skip = False
    for index, token in enumerate(command):
        if skip:
            skip = False
            continue
        option = token.partition("=")[0]
        if option == "--config":
            # Named where it starts, in its own place.
            kept += ["--config", config.name]
            skip = "=" not in token and index + 1 < len(command)
            continue
        if uv and option in {"--directory", "--project"} and index < _program_index(command):
            skip = "=" not in token and index + 1 < len(command)
            continue
        kept.append(token)
    folder = os.path.relpath(config.parent, start)
    if uv:
        options = [*(["--directory", folder] if folder != "." else [])]
        if project_dir is not None:
            options += ["--project", os.path.relpath(project_dir, config.parent)]
        at = kept.index("run") + 1
        return [*kept[:at], *options, *kept[at:]]
    if folder == ".":
        return kept
    return ["sh", "-c", 'cd "$1" && shift && exec "$@"', "alk-langgraph", folder, *kept]


def _dependency_project(config: Path, root: Path) -> Path | None:
    """The first folder a LangGraph config lists among its `dependencies` that is a Python project inside the source."""
    try:
        dependencies = json.loads(config.read_text(encoding="utf-8")).get("dependencies") or []
    except (OSError, ValueError, AttributeError):
        return None
    inside = root.resolve()
    for dependency in dependencies if isinstance(dependencies, list) else []:
        folder = (config.parent / str(dependency)).resolve()
        if folder.is_relative_to(inside) and (folder / "pyproject.toml").is_file():
            return folder
    return None


def _program_index(command: list[str]) -> int:
    return next((index for index, token in enumerate(command) if Path(token).name == "langgraph"), len(command))


def server_start_folder(root: Path, runtime: dict[str, Any], component: Path) -> Path:
    """Where the server starts: `langgraph dev` reads its config, and the graphs it names, from the config's folder.
    Any other runtime starts in `component`."""
    command = [str(item) for item in (runtime.get("command") or [])]
    config = _langgraph_config(root, runtime) if _is_langgraph_server_command(command) else None
    if config is not None and component.is_relative_to(config.parent) and component != config.parent:
        return config.parent
    return component


def serves_port_option(command: list[str]) -> bool:
    """Whether the command is a framework server that takes its port as ``--port``."""
    return _is_adk_web_command(command) or _is_langgraph_server_command(command)


def server_app(source: Path, contract: dict[str, Any]) -> dict[str, str]:
    """The ADK app a build resolves for an ``adk web`` runtime, as manifest metadata."""
    runtime = contract.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    command = [str(item) for item in (runtime.get("command") or [])]
    if not _is_adk_web_command(command):
        return {}
    app_name = _adk_app_name(source, str(runtime.get("workdir") or ""), command)
    return {"adk_web_app": app_name or ""}


def server_interface_rewritten(sealed: dict[str, Any], metadata: dict[str, Any]) -> bool:
    """Whether a chat ``adk web`` bundle seals an HTTP interface other than the one its
    build derived from the app it resolved. A bundle that recorded no app predates that."""
    runtime = sealed.get("runtime") or {}
    command = [str(item) for item in (runtime.get("command") or [])]
    interface = runtime.get("interface")
    if (
        str(sealed.get("modality") or "").strip().lower() != "chat"
        or not _is_adk_web_command(command)
        or not isinstance(interface, dict)
        or str(interface.get("kind") or "").strip().lower() not in {"", "http"}
    ):
        return False
    app_name = metadata.get("adk_web_app")
    if not isinstance(app_name, str):
        return True
    if not app_name:
        # With no resolvable app a build keeps the written interface too.
        return False
    own = {"interface": _adk_interface_for(command, app_name)}
    return _sealed_interface(runtime) != _sealed_interface(own)


def _sealed_interface(runtime: dict[str, Any]) -> Any:
    return Runtime.model_validate(runtime).model_dump(mode="json")["interface"]


# `langgraph dev` installs its CLI (`uv run --with langgraph-cli[inmem]`) and imports every graph before it answers;
# deep agents graphs can take minutes. Other servers keep the default wait.
_LANGGRAPH_START_SECONDS = 600.0


def server_start_seconds(command: list[str], default: float) -> float:
    """How long a server may take to answer its first readiness check: longer for `langgraph dev`, else `default`."""
    return max(default, _LANGGRAPH_START_SECONDS) if _is_langgraph_server_command(command) else default


# Tools a framework's own middleware adds to an agent while it runs: no module-level callable exists for them, so a
# contract records them with no seam. Known by the package that injects them and the names it gives them.
FRAMEWORK_INJECTED_TOOLS: dict[str, frozenset[str]] = {
    "deepagents": frozenset(
        {
            "launch_async_subagent",
            "check_async_subagent",
            "update_async_subagent",
            "cancel_async_subagent",
            "list_async_subagent_jobs",
        }
    ),
}


def framework_injected(tool: str, notes: str, imports: Callable[[str], bool]) -> str | None:
    """The framework that injects ``tool`` as the agent runs, when the source shows it (it imports the package, or the
    contract's note names it); None for any tool no known framework injects."""
    for package, names in FRAMEWORK_INJECTED_TOOLS.items():
        if tool in names and (package in notes or imports(package)):
            return package
    return None
