"""`agent-learn` commands that act on a FutureAGI platform: thin renderers over `fi.alk.platform`.

Every command takes the global options of the CLI reference (`--profile`,
`--workspace`, `--api-url`, `--request-timeout`, `--output`, `--non-interactive`,
`--yes`) after its own name. `--output` defaults to human in a terminal and json
otherwise. With json or jsonl the result, or the error, is one JSON document on
stdout; with human it is text, and errors go to stderr. A failure exits with its
category's code (`fi.alk.platform.EXIT_CODES`).
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

OUTPUTS = ("human", "json", "jsonl")


def _positive(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def _global_options() -> argparse.ArgumentParser:
    """The CLI reference's global options, accepted after any platform command."""
    options = argparse.ArgumentParser(add_help=False)
    options.add_argument(
        "--profile", help="saved profile to use (default: the current one)"
    )
    options.add_argument("--workspace", help="workspace id to act in")
    options.add_argument("--api-url", help="platform URL (default: the profile's)")
    options.add_argument(
        "--request-timeout",
        type=_positive,
        default=30.0,
        metavar="SECONDS",
        help="timeout for one platform request (default 30)",
    )
    options.add_argument(
        "--output",
        "-o",
        choices=OUTPUTS,
        help="default: human in a terminal, json otherwise",
    )
    options.add_argument(
        "--non-interactive",
        action="store_true",
        help="never prompt; fail with an exit code when input is missing",
    )
    options.add_argument(
        "--yes", action="store_true", help="accept the shown confirmation"
    )
    return options


def _parse(parser: argparse.ArgumentParser, argv: Sequence[str]) -> argparse.Namespace:
    args = parser.parse_args(argv)
    if args.output is None:
        args.output = "human" if sys.stdout.isatty() else "json"
    return args


def _interactive(args: argparse.Namespace) -> bool:
    return not args.non_interactive and sys.stdin.isatty()


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
        if error.request_id:
            print(f"request id: {error.request_id}", file=sys.stderr)
    else:
        _emit(output, {"error": error.as_dict()}, lambda: "")
    return error.exit_code


def _run(args: argparse.Namespace, action: Callable[[argparse.Namespace], int]) -> int:
    try:
        return action(args)
    except PlatformError as error:
        return _fail(args.output, error)


def _client(args: argparse.Namespace) -> Client:
    return Client.from_profile(
        args.profile,
        api_url=args.api_url,
        workspace_id=args.workspace,
        timeout=args.request_timeout,
    )


def _confirm(args: argparse.Namespace, question: str) -> None:
    if args.yes:
        return
    if _interactive(args) and input(f"{question} [y/N] ").strip().lower() in (
        "y",
        "yes",
    ):
        return
    raise PlatformError(
        "validation",
        "Not confirmed.",
        code="confirmation_required",
        fix="Pass --yes to confirm.",
    )


# --- auth ---------------------------------------------------------------------------


def auth(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="agent-learn auth")
    commands = parser.add_subparsers(dest="command", required=True)
    options = [_global_options()]

    login = commands.add_parser(
        "login",
        parents=options,
        help="sign in with a key pair and save it to a profile",
    )
    login.add_argument(
        "--method",
        choices=("prompt", "token-env"),
        help="prompt for the key pair, or read it from environment variables "
        "(default: prompt in a terminal, token-env otherwise)",
    )
    login.add_argument(
        "--token-env",
        default="FI_API_KEY",
        metavar="NAME",
        help="variable holding the API key (default FI_API_KEY)",
    )
    login.add_argument(
        "--secret-env",
        default="FI_SECRET_KEY",
        metavar="NAME",
        help="variable holding the secret key (default FI_SECRET_KEY)",
    )
    commands.add_parser(
        "status", parents=options, help="show who you are signed in as, and where"
    )
    logout = commands.add_parser(
        "logout", parents=options, help="erase a profile's key pair; its settings stay"
    )
    logout.add_argument(
        "--all-profiles",
        action="store_true",
        help="sign out of every saved profile (asks to confirm)",
    )

    args = _parse(parser, argv)
    action = {"login": _login, "status": _status, "logout": _logout}[args.command]
    return _run(args, action)


def _login(args: argparse.Namespace) -> int:
    name = args.profile or profiles.current_name()
    saved = profiles.settings(name)
    api_url = (
        args.api_url
        or saved["api_url"]
        or os.environ.get("FI_BASE_URL")
        or profiles.DEFAULT_API_URL
    ).rstrip("/")
    api_key, secret_key = _read_key_pair(args)
    with Client(
        api_url=api_url,
        api_key=api_key,
        secret_key=secret_key,
        workspace_id=args.workspace or saved["workspace_id"],
        timeout=args.request_timeout,
    ) as client:
        identity = client.whoami()
    profiles.sign_in(name, api_url, api_key, secret_key, identity.workspace["id"])
    document = _status_document(profiles.load(name), identity)
    _emit(args.output, document, lambda: _status_text(document, "Signed in"))
    return 0


def _read_key_pair(args: argparse.Namespace) -> tuple[str, str]:
    method = args.method or ("prompt" if _interactive(args) else "token-env")
    if method == "prompt":
        if not _interactive(args):
            raise PlatformError(
                "validation",
                "Cannot prompt for a key pair without a terminal.",
                code="credentials_missing",
                fix="Use --method token-env with FI_API_KEY and FI_SECRET_KEY set.",
            )
        return (
            getpass.getpass("API key: ").strip(),
            getpass.getpass("Secret key: ").strip(),
        )
    api_key = os.environ.get(args.token_env, "")
    secret_key = os.environ.get(args.secret_env, "")
    if not api_key or not secret_key:
        missing = [
            name
            for name, value in (
                (args.token_env, api_key),
                (args.secret_env, secret_key),
            )
            if not value
        ]
        raise PlatformError(
            "validation",
            f"{' and '.join(missing)} not set.",
            code="credentials_missing",
            field="token_env" if not api_key else "secret_env",
            fix="Set the key pair from the platform's Keys page in those variables.",
        )
    return api_key, secret_key


def _status(args: argparse.Namespace) -> int:
    with _client(args) as client:
        identity = client.whoami()
    document = _status_document(profiles.load(args.profile), identity, client.api_url)
    _emit(args.output, document, lambda: _status_text(document, "Signed in"))
    return 0


def _status_document(profile, identity, api_url: str | None = None) -> dict[str, Any]:
    return {
        "profile": profile.name,
        "api_url": api_url or profile.api_url,
        "api_key": profile.masked_key,
        **identity.as_dict(),
        # Platform key pairs do not expire; a token sign-in would report its expiry.
        "expires_at": None,
    }


def _status_text(document: dict[str, Any], heading: str) -> str:
    user = document["user"]
    organization = document["organization"]
    workspace = document["workspace"]
    return "\n".join(
        [
            f"{heading} to {document['api_url']} (profile {document['profile']})",
            f"  user          {user.get('email')}",
            f"  organization  {organization.get('name')} ({organization.get('id')})",
            f"  workspace     {workspace.get('name')} ({workspace.get('id')})",
            f"  key           {document['api_key']}",
        ]
    )


def _logout(args: argparse.Namespace) -> int:
    if args.all_profiles:
        names = [row["name"] for row in profiles.listing() if row["signed_in"]]
        _confirm(
            args, f"Sign out of {len(names)} profile(s): {', '.join(names) or 'none'}?"
        )
    else:
        names = [args.profile or profiles.current_name()]
    signed_out = profiles.sign_out(names)
    if not args.all_profiles and not signed_out:
        raise PlatformError(
            "auth", f"Profile {names[0]!r} is not signed in.", code="not_signed_in"
        )
    _emit(
        args.output,
        {"signed_out": signed_out},
        lambda: f"Signed out of {', '.join(signed_out) or 'nothing'}.",
    )
    return 0


# --- profile ------------------------------------------------------------------------


def profile(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="agent-learn profile")
    commands = parser.add_subparsers(dest="command", required=True)
    options = [_global_options()]
    commands.add_parser("list", parents=options, help="list saved profiles")
    show = commands.add_parser("show", parents=options, help="show one saved profile")
    show.add_argument("name", nargs="?", help="default: the current profile")
    use = commands.add_parser("use", parents=options, help="make a profile current")
    use.add_argument("name")
    setter = commands.add_parser(
        "set",
        parents=options,
        help="create or update a profile from --api-url, --workspace and the defaults below",
    )
    setter.add_argument("name")
    setter.add_argument("--default-mode", choices=profiles.MODES)
    setter.add_argument("--default-budget", type=_positive, metavar="AMOUNT")
    setter.add_argument("--default-budget-unit", choices=profiles.BUDGET_UNITS)
    args = _parse(parser, argv)
    action = {
        "list": _profile_list,
        "show": _profile_show,
        "use": _profile_use,
        "set": _profile_set,
    }[args.command]
    return _run(args, action)


def _profile_list(args: argparse.Namespace) -> int:
    rows = profiles.listing()
    if args.output == "jsonl":
        for row in rows:
            print(json.dumps(row))
        return 0

    def text() -> str:
        if not rows:
            return "No saved profiles. Run `agent-learn auth login`."
        return "\n".join(
            f"{'*' if row['current'] else ' '} {row['name']:<16} {row['api_url']}"
            f"  workspace {row['workspace_id'] or '-'}"
            f"{'' if row['signed_in'] else '  (signed out)'}"
            for row in rows
        )

    _emit(args.output, {"profiles": rows}, text)
    return 0


def _profile_row(name: str) -> dict[str, Any]:
    row = next((row for row in profiles.listing() if row["name"] == name), None)
    if row is None:
        raise PlatformError(
            "validation",
            f"No saved profile named {name!r}.",
            code="profile_not_found",
            field="profile",
            fix="See the saved profiles with `agent-learn profile list`.",
        )
    if row["signed_in"]:
        row["api_key"] = profiles.load(name).masked_key
    return row


def _row_text(row: dict[str, Any]) -> str:
    return "\n".join(f"{key:<20} {value}" for key, value in row.items())


def _profile_show(args: argparse.Namespace) -> int:
    row = _profile_row(args.name or profiles.current_name())
    _emit(args.output, row, lambda: _row_text(row))
    return 0


def _profile_use(args: argparse.Namespace) -> int:
    profiles.use(args.name)
    row = _profile_row(args.name)
    _emit(args.output, row, lambda: f"Now using profile {args.name}.")
    return 0


def _profile_set(args: argparse.Namespace) -> int:
    changes = {
        key: value
        for key, value in {
            "api_url": args.api_url.rstrip("/") if args.api_url else None,
            "workspace_id": args.workspace,
            "default_mode": args.default_mode,
            "default_budget": args.default_budget,
            "default_budget_unit": args.default_budget_unit,
        }.items()
        if value is not None
    }
    if not changes:
        raise PlatformError(
            "validation",
            "Nothing to set.",
            code="nothing_to_set",
            fix="Pass --api-url, --workspace, --default-mode, --default-budget or "
            "--default-budget-unit.",
        )
    signed_in = any(
        row["name"] == args.name and row["signed_in"] for row in profiles.listing()
    )
    if signed_in and ({"api_url", "workspace_id"} & set(changes)):
        # Saved only once the platform confirms the profile can act there.
        with Client.from_profile(
            args.name,
            api_url=changes.get("api_url"),
            workspace_id=changes.get("workspace_id"),
            timeout=args.request_timeout,
        ) as client:
            client.whoami()
    diff = profiles.update(args.name, changes)
    document = {
        "name": args.name,
        "changes": {key: {"from": old, "to": new} for key, (old, new) in diff.items()},
    }
    _emit(
        args.output,
        document,
        lambda: (
            "\n".join(
                [f"Profile {args.name}:"]
                + [f"  {key}: {old} -> {new}" for key, (old, new) in diff.items()]
            )
            if diff
            else f"Profile {args.name}: no change."
        ),
    )
    return 0


# --- doctor -------------------------------------------------------------------------


def doctor(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-learn doctor",
        parents=[_global_options()],
        description="Check that this machine can work with the platform: signed in, "
        "platform reachable, workspace resolved. `--boundary` runs the kit's own "
        "module-boundary check instead.",
    )
    args = _parse(parser, argv)

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

    signed_in: list[profiles.Profile] = []

    def sign_in_check() -> str:
        signed_in.append(profiles.load(args.profile))
        return f"profile {signed_in[0].name}, key {signed_in[0].masked_key}"

    check("signed_in", sign_in_check)
    api_url = (
        args.api_url
        or (signed_in[0].api_url if signed_in else None)
        or os.environ.get("FI_BASE_URL")
        or profiles.DEFAULT_API_URL
    )

    def reachable_check() -> str:
        reachable(api_url, timeout=args.request_timeout)
        return api_url

    check("platform_reachable", reachable_check)
    if signed_in:
        check("workspace", lambda: _workspace(args))
    else:
        checks.append(
            {
                "id": "workspace",
                "status": "skipped",
                "detail": "not signed in",
                "fix": "",
            }
        )
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


def _workspace(args: argparse.Namespace) -> str:
    with _client(args) as client:
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
