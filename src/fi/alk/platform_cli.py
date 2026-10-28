"""`agent-learn` commands that act on a FutureAGI platform: thin renderers over `fi.alk.platform`.

Every command takes `--output human|json|jsonl`. With json or jsonl the result, or the
error, is one JSON document on stdout; with human it is text, and errors go to stderr.
A failure exits with its category's code (`fi.alk.platform.EXIT_CODES`).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from typing import Any, Callable, Sequence

from . import __version__
from .platform import Client, PlatformError, reachable
from .platform import profiles
from .platform.profiles import DEFAULT_PROFILE, Profile

DEFAULT_BASE_URL = "https://api.futureagi.com"
OUTPUTS = ("human", "json", "jsonl")


def _common() -> argparse.ArgumentParser:
    """Options every platform subcommand takes, after its own name."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--output", "-o", choices=OUTPUTS, default="human")
    common.add_argument(
        "--non-interactive",
        action="store_true",
        help="never prompt; fail with an exit code when input is missing",
    )
    return common


def _emit(output: str, document: Any, human: Callable[[], str]) -> None:
    if output == "human":
        print(human())
    else:
        print(json.dumps(document, indent=None if output == "jsonl" else 2))


def _fail(output: str, error: PlatformError) -> int:
    if output == "human":
        print(f"error: {error.message}", file=sys.stderr)
        if error.fix:
            print(f"fix: {error.fix}", file=sys.stderr)
    else:
        _emit(output, {"error": error.as_dict()}, lambda: "")
    return error.exit_code


def _run(output: str, action: Callable[[], int]) -> int:
    try:
        return action()
    except PlatformError as error:
        return _fail(output, error)


# --- auth ---------------------------------------------------------------------------


def auth(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="agent-learn auth")
    commands = parser.add_subparsers(dest="command", required=True)
    common = _common()

    login = commands.add_parser(
        "login", parents=[common], help="sign in with a key pair and save it"
    )
    login.add_argument("--profile", default=DEFAULT_PROFILE)
    login.add_argument(
        "--base-url",
        default=os.environ.get("FI_BASE_URL") or DEFAULT_BASE_URL,
    )
    login.add_argument("--workspace", help="workspace id to act in")

    commands.add_parser(
        "status", parents=[common], help="show who you are signed in as, and where"
    )
    logout = commands.add_parser(
        "logout", parents=[common], help="forget a profile and erase its key pair"
    )
    logout.add_argument("--profile", help="default: the current profile")

    args = parser.parse_args(argv)
    action = {"login": _login, "status": _status, "logout": _logout}[args.command]
    return _run(args.output, lambda: action(args))


def _logout(args: argparse.Namespace) -> int:
    name = profiles.remove(args.profile)
    _emit(
        args.output,
        {"profile": name, "removed": True},
        lambda: f"Signed out of {name}.",
    )
    return 0


def _login(args: argparse.Namespace) -> int:
    api_key, secret_key = _read_key_pair(args.non_interactive)
    with Client(
        base_url=args.base_url,
        api_key=api_key,
        secret_key=secret_key,
        workspace_id=args.workspace,
    ) as client:
        identity = client.whoami()
    profile = Profile(
        name=args.profile,
        base_url=args.base_url.rstrip("/"),
        api_key=api_key,
        secret_key=secret_key,
        workspace_id=args.workspace or identity.workspace["id"],
    )
    profiles.save(profile)
    document = _status_document(profile, identity)
    _emit(args.output, document, lambda: _status_text(document, "Signed in"))
    return 0


def _read_key_pair(non_interactive: bool) -> tuple[str, str]:
    api_key = os.environ.get("FI_API_KEY", "")
    secret_key = os.environ.get("FI_SECRET_KEY", "")
    if api_key and secret_key:
        return api_key, secret_key
    if non_interactive or not sys.stdin.isatty():
        raise PlatformError(
            "validation",
            "No key pair given.",
            code="credentials_missing",
            fix="Set FI_API_KEY and FI_SECRET_KEY, or run `agent-learn auth login` in a terminal.",
        )
    return (
        getpass.getpass("API key: ").strip(),
        getpass.getpass("Secret key: ").strip(),
    )


def _status(args: argparse.Namespace) -> int:
    profile = profiles.load()
    with Client(
        base_url=profile.base_url,
        api_key=profile.api_key,
        secret_key=profile.secret_key,
        workspace_id=profile.workspace_id,
    ) as client:
        identity = client.whoami()
    document = _status_document(profile, identity)
    _emit(args.output, document, lambda: _status_text(document, "Signed in"))
    return 0


def _status_document(profile: Profile, identity) -> dict[str, Any]:
    return {
        "profile": profile.name,
        "base_url": profile.base_url,
        "api_key": profile.masked_key,
        **identity.as_dict(),
    }


def _status_text(document: dict[str, Any], heading: str) -> str:
    user = document["user"]
    organization = document["organization"]
    workspace = document["workspace"]
    return "\n".join(
        [
            f"{heading} to {document['base_url']} (profile {document['profile']})",
            f"  user          {user.get('email')}",
            f"  organization  {organization.get('name')} ({organization.get('id')})",
            f"  workspace     {workspace.get('name')} ({workspace.get('id')})",
            f"  key           {document['api_key']}",
        ]
    )


# --- doctor -------------------------------------------------------------------------


def doctor(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-learn doctor",
        parents=[_common()],
        description="Check that this machine can work with the platform: signed in, "
        "workspace resolved, platform reachable. `--boundary` runs the kit's own "
        "module-boundary check instead.",
    )
    parser.add_argument(
        "--profile", help="check this profile instead of the current one"
    )
    args = parser.parse_args(argv)

    checks: list[dict[str, Any]] = []
    failures: list[PlatformError] = []

    def check(check_id: str, run: Callable[[], str]) -> None:
        try:
            checks.append(
                {"id": check_id, "status": "passed", "detail": run(), "fix": ""}
            )
        except PlatformError as error:
            failures.append(error)
            checks.append(
                {
                    "id": check_id,
                    "status": "failed",
                    "detail": error.message,
                    "fix": error.fix,
                }
            )

    def skipped(check_id: str, reason: str) -> None:
        checks.append(
            {"id": check_id, "status": "skipped", "detail": reason, "fix": ""}
        )

    profile: Profile | None = None

    def signed_in() -> str:
        nonlocal profile
        profile = profiles.load(args.profile)
        return f"profile {profile.name}, key {profile.masked_key}"

    check("signed_in", signed_in)
    base_url = (
        profile.base_url
        if profile
        else os.environ.get("FI_BASE_URL") or DEFAULT_BASE_URL
    )
    check("platform_reachable", lambda: _reachable(base_url))
    if profile is None:
        skipped("workspace", "not signed in")
    else:
        check("workspace", lambda: _workspace(profile))
    checks.append(
        {
            "id": "cli_version",
            "status": "info",
            "detail": f"agent-learn {__version__}; the platform does not publish a "
            "minimum CLI version yet",
            "fix": "",
        }
    )

    document = {"checks": checks, "passed": not failures}
    _emit(args.output, document, lambda: _doctor_text(checks))
    return failures[0].exit_code if failures else 0


def _reachable(base_url: str) -> str:
    reachable(base_url)
    return base_url


def _workspace(profile: Profile) -> str:
    with Client(
        base_url=profile.base_url,
        api_key=profile.api_key,
        secret_key=profile.secret_key,
        workspace_id=profile.workspace_id,
    ) as client:
        identity = client.whoami()
    workspace = identity.workspace
    return f"{workspace['name']} ({workspace['id']}) in {identity.organization['name']}"


_MARKS = {"passed": "ok", "failed": "FAIL", "skipped": "skip", "info": "info"}


def _doctor_text(checks: list[dict[str, Any]]) -> str:
    lines = []
    for check in checks:
        lines.append(
            f"{_MARKS[check['status']]:>4}  {check['id']:<20} {check['detail']}"
        )
        if check["fix"]:
            lines.append(f"      fix: {check['fix']}")
    return "\n".join(lines)


# --- profile ------------------------------------------------------------------------


def profile(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="agent-learn profile")
    commands = parser.add_subparsers(dest="command", required=True)
    common = _common()
    commands.add_parser("list", parents=[common], help="list saved profiles")
    show = commands.add_parser("show", parents=[common], help="show one saved profile")
    show.add_argument("name", nargs="?", help="default: the current profile")
    use = commands.add_parser("use", parents=[common], help="make a profile current")
    use.add_argument("name")
    setter = commands.add_parser(
        "set", parents=[common], help="change a profile's base_url or workspace"
    )
    setter.add_argument("field", choices=sorted(profiles.SETTABLE))
    setter.add_argument("value")
    setter.add_argument("--profile", help="default: the current profile")
    args = parser.parse_args(argv)
    action = {
        "list": _profile_list,
        "show": _profile_show,
        "use": _profile_use,
        "set": _profile_set,
    }[args.command]
    return _run(args.output, lambda: action(args))


def _profile_list(args: argparse.Namespace) -> int:
    rows = profiles.listing()

    def text() -> str:
        if not rows:
            return "No saved profiles. Run `agent-learn auth login`."
        return "\n".join(
            f"{'*' if row['current'] else ' '} {row['name']:<16} {row['base_url']}"
            f"  workspace {row['workspace_id'] or '-'}"
            for row in rows
        )

    if args.output == "jsonl":
        for row in rows:
            print(json.dumps(row))
    else:
        _emit(args.output, {"profiles": rows}, text)
    return 0


def _profile_document(saved: Profile) -> dict[str, Any]:
    current = next((row for row in profiles.listing() if row["current"]), None)
    return {
        "name": saved.name,
        "base_url": saved.base_url,
        "workspace_id": saved.workspace_id,
        "api_key": saved.masked_key,
        "current": bool(current and current["name"] == saved.name),
    }


def _profile_text(document: dict[str, Any]) -> str:
    return "\n".join(f"{key:<13} {value}" for key, value in document.items())


def _profile_show(args: argparse.Namespace) -> int:
    document = _profile_document(profiles.load(args.name))
    _emit(args.output, document, lambda: _profile_text(document))
    return 0


def _profile_use(args: argparse.Namespace) -> int:
    profiles.use(args.name)
    document = _profile_document(profiles.load(args.name))
    _emit(args.output, document, lambda: f"Now using profile {args.name}.")
    return 0


def _profile_set(args: argparse.Namespace) -> int:
    saved = profiles.load(args.profile)
    base_url = args.value.rstrip("/") if args.field == "base_url" else saved.base_url
    workspace_id = args.value if args.field == "workspace" else saved.workspace_id
    # Saved only once the platform confirms the profile can act there.
    with Client(
        base_url=base_url,
        api_key=saved.api_key,
        secret_key=saved.secret_key,
        workspace_id=workspace_id,
    ) as client:
        client.whoami()
    value = base_url if args.field == "base_url" else workspace_id
    document = _profile_document(profiles.update(saved.name, args.field, value))
    _emit(args.output, document, lambda: _profile_text(document))
    return 0
