"""Authoring helpers for the committed environment packages.

    python -m fi.alk.environments.authoring sync-checks <package>

The sub-goal catalogue (`suite/sub_goals.json`) is what sealing proves, and runs execute the copy
of each deterministic check in `suite/scenarios/<name>/checks/<sub_goal>.py`. The two must be the
same bytes (`fi.alk.harness.folder.check_file_drift`); `sync_check_files` rewrites every scenario's
check files from the catalogue so a check edited once is the check everywhere.

The shared check shapes of the Wave A plan (§3, §3.2) live in each vertical's catalogue: a check
reads its target from the scenario's reference (the values its solution marks `fixed`), requires
the evidence the scenario tests to have been retrieved by any valid path, and holds a draft's
contents to the records. `banking/suite/sub_goals.json` is the worked example the other verticals
copy.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fi.alk.harness.catalogue import load_catalogue
from fi.alk.harness.folder import check_file

SCENARIOS = "scenarios"


def sync_check_files(package: Path) -> list[str]:
    """Rewrite every scenario's check files from the package's catalogue; returns what changed."""
    suite = package / "suite"
    catalogue = load_catalogue(suite)
    changed: list[str] = []
    for folder in sorted(
        path for path in (suite / SCENARIOS).iterdir() if path.is_dir()
    ):
        body = folder / "scenario.json"
        if not body.is_file():
            continue
        named = json.loads(body.read_text(encoding="utf-8")).get("sub_goals") or []
        wanted = {
            f"{name}.py": check_file(sub_goal)
            for name in named
            if (sub_goal := catalogue.named(name)) is not None
            and sub_goal.deterministic()
        }
        checks = folder / "checks"
        checks.mkdir(exist_ok=True)
        for filename, source in wanted.items():
            path = checks / filename
            if not path.is_file() or path.read_text(encoding="utf-8") != source:
                path.write_text(source, encoding="utf-8")
                changed.append(f"{folder.name}/checks/{filename}")
        for stray in sorted(checks.glob("*.py")):
            if stray.name not in wanted:
                stray.unlink()
                changed.append(f"{folder.name}/checks/{stray.name} (removed)")
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fi.alk.environments.authoring")
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser(
        "sync-checks", help="rewrite scenario check files from the catalogue"
    )
    sync.add_argument("package")
    args = parser.parse_args(argv)
    root = Path(__file__).parent
    if args.command == "sync-checks":
        changed = sync_check_files(root / args.package)
        print(
            "\n".join(changed) if changed else "check files already match the catalogue"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
