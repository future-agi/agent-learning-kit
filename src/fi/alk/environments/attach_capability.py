"""Attach an MCP capability to a vertical environment, the way Wave A stage 1 did by hand.

    python -m fi.alk.environments.attach_capability <vertical> <capability> [--overwrite]

A capability package (`gmail_inbox_triage`, `drive_policy_search`, `slack_channel_digest`,
`calendar_scheduling_desk`) is a tool set in the official server's shape over its own, namespaced
tables (`gmail_*`, `drive_*`, `slack_*`, `calendar_*`, plus the shared `current_actor`). A vertical
copies the capability rather than importing it, so the sealed release is self-contained. The copy
is a starting point: a vertical then adds its own rules to it (who in the conversation may read or
write, whose records are hidden), so most attached handlers differ from the capability's. The
capability itself is held to the official server by
`tests/harness/test_mcp_capability_reference.py`:

- every capability tool is added to the vertical's `tools.json`, and its handler is copied;
- every capability table the vertical lacks is created in its `world.sql`, schema only: the
  vertical authors the rows, linked to its own records (plan §3.1);
- the capability's `system_invariant` rules join the vertical's `policy.json`; its agent rules
  and configurable parameters do not (the vertical's agent is graded by its own rules, and the
  seal requires every configurable parameter to be seeded, which only the vertical can decide);
- `mcp_capabilities.json` in the vertical records what was attached from where, which is what
  its catalogue card shows.

Re-running the attachment leaves the vertical's own data, and refuses, changing nothing, where the
vertical's handler or tool definition no longer matches the capability's: it would undo the rules
the vertical added. Port a capability's change to those by hand, or pass `--overwrite` to replace
them with the capability's.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ATTACHMENTS = "mcp_capabilities.json"
SHARED_TABLES = frozenset({"clock", "environment_policy"})
_CREATE = re.compile(
    r"CREATE TABLE\s+\"?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\"?\s*\([^;]*\);"
)


class Diverged(ValueError):
    """The vertical has changed its copy of the capability."""


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def create_statements(world_sql: str) -> dict[str, str]:
    """Each `CREATE TABLE` statement in a world, by table name."""
    return {
        match.group("name"): match.group(0) for match in _CREATE.finditer(world_sql)
    }


def capability_tables(capability: Path) -> list[str]:
    """The tables a capability owns: everything in its world but the shared clock and policy."""
    return [
        name
        for name in create_statements(
            (capability / "world.sql").read_text(encoding="utf-8")
        )
        if name not in SHARED_TABLES
    ]


def diverged(vertical: Path, capability: Path) -> list[str]:
    """The capability's tools whose handler or definition the vertical holds and has changed."""
    own = {tool["name"]: tool for tool in _read_json(vertical / "tools.json")}
    changed = []
    for tool in _read_json(capability / "tools.json"):
        name = tool["name"]
        handler = Path("handlers") / f"{name}.py"
        copied = vertical / handler
        if (
            copied.is_file()
            and copied.read_bytes() != (capability / handler).read_bytes()
        ):
            changed.append(str(handler))
        if name in own and own[name] != tool:
            changed.append(f"tools.json {name}")
    return changed


def attach(
    vertical: Path, capability: Path, *, overwrite: bool = False
) -> dict[str, list[str]]:
    """Attach `capability` to `vertical`; returns what was copied, created and left to author."""
    changed = diverged(vertical, capability)
    if changed and not overwrite:
        raise Diverged(
            f"{vertical.name} has its own version of {capability.name}'s {', '.join(changed)}; "
            "attaching would replace them. Port the change by hand, or pass --overwrite."
        )
    report: dict[str, list[str]] = {
        "tools": [],
        "handlers": [],
        "tables_created": [],
        "tables_present": [],
        "rules_added": [],
        "rules_skipped": [],
    }
    capability_tools = _read_json(capability / "tools.json")
    names = [tool["name"] for tool in capability_tools]

    # Tools: the vertical's own first, then the capability's in the capability's order.
    vertical_tools = [
        tool
        for tool in _read_json(vertical / "tools.json")
        if tool["name"] not in names
    ]
    _write_json(vertical / "tools.json", vertical_tools + capability_tools)
    report["tools"] = names

    # Handlers.
    (vertical / "handlers").mkdir(exist_ok=True)
    for name in names:
        shutil.copyfile(
            capability / "handlers" / f"{name}.py", vertical / "handlers" / f"{name}.py"
        )
        report["handlers"].append(name)

    # Tables: schema only, appended where missing.
    capability_sql = (capability / "world.sql").read_text(encoding="utf-8")
    vertical_path = vertical / "world.sql"
    vertical_sql = vertical_path.read_text(encoding="utf-8")
    present = set(create_statements(vertical_sql))
    additions = []
    for name, statement in create_statements(capability_sql).items():
        if name in SHARED_TABLES:
            continue
        if name in present:
            report["tables_present"].append(name)
            continue
        additions.append(statement)
        report["tables_created"].append(name)
    if additions:
        header = (
            f"\n-- {capability.name}: tables attached by attach_capability; the rows are this "
            "vertical's own, linked to its records.\n"
        )
        vertical_path.write_text(
            vertical_sql.rstrip("\n") + "\n" + header + "\n".join(additions) + "\n",
            encoding="utf-8",
        )

    # Policy: system invariants only.
    policy_path = vertical / "policy.json"
    policy = _read_json(policy_path)
    have = {rule["id"] for rule in policy["rules"]}
    for rule in _read_json(capability / "policy.json")["rules"]:
        if rule["id"] in have:
            continue
        if rule["class"] == "system_invariant":
            policy["rules"].append(rule)
            report["rules_added"].append(rule["id"])
        else:
            report["rules_skipped"].append(f"{rule['id']} ({rule['class']})")
    _write_json(policy_path, policy)

    # The record the reference test reads.
    attachments_path = vertical / ATTACHMENTS
    attachments = _read_json(attachments_path) if attachments_path.is_file() else {}
    reference = _read_json(capability / "mcp_reference.json")
    attachments[capability.name] = {
        "server": reference.get("server"),
        "recorded": reference.get("recorded"),
        "tools": names,
        "tables": capability_tables(capability),
    }
    _write_json(attachments_path, attachments)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m fi.alk.environments.attach_capability"
    )
    parser.add_argument(
        "vertical", help="package name under fi.alk.environments, e.g. banking"
    )
    parser.add_argument(
        "capability", help="capability package, e.g. drive_policy_search"
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace handlers and tool definitions the vertical has changed",
    )
    args = parser.parse_args(argv)
    root = Path(__file__).parent
    vertical, capability = root / args.vertical, root / args.capability
    for path in (vertical, capability):
        if (
            not (path / "environment.json").is_file()
            and not (path / "tools.json").is_file()
        ):
            parser.error(f"{path.name} is not an environment package")
    if not (capability / "mcp_reference.json").is_file():
        parser.error(
            f"{capability.name} has no mcp_reference.json; only MCP capabilities attach"
        )
    try:
        report = attach(vertical, capability, overwrite=args.overwrite)
    except Diverged as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2))
    if report["tables_created"]:
        print(
            f"\nAuthor the rows of {', '.join(report['tables_created'])} in {args.vertical}/world.sql, "
            "linked to the vertical's records, then rebuild.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
