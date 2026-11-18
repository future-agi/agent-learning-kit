"""Build and seal an environment the environment builder designed, from its committed source.

    python -m fi.alk.environments.<name>.build --out DIR [--postgres-dsn DSN]

A designed environment is committed the way the clinic is: its tools, policy, handlers and world
SQL beside its base suite's scenario folders, sub-goals and simulator prompt. `environment.json`
holds what the world's manifest carries besides (notes, declared sequences, world checks) and where
the design came from. The build writes `DIR/environment` and `DIR/suite`, seals both through
`fi.alk.harness.releases` and prints their digests; sealing proves every scenario, on PostgreSQL
when a DSN is given, as hosted runs require.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

from fi.alk.harness.releases import (
    ATTACHED,
    POLICY,
    POLICY_TABLE,
    SERVERS,
    TOOLS,
    WORLD,
    WORLD_IMPLEMENTATION,
    ReleaseError,
    seal_environment_release,
    seal_suite_release,
    world_tool_specs,
)
from fi.alk.harness.world.runtime import GeneratedWorld
from fi.alk.harness.world.snapshot import save

SPEC = "environment.json"
# World tables the runtime keeps for itself (the clock, who is calling, who is acting): not records.
_RUNTIME_TABLES = {"clock", "session_caller", "current_actor"}
SUITE = "suite"
POLICY_PROFILE = "policy_profile.json"


def _spec(here: Path) -> dict[str, Any]:
    return json.loads((here / SPEC).read_text(encoding="utf-8"))


def build_environment(here: Path, destination: Path) -> str:
    spec = _spec(here)
    destination.mkdir(parents=True)
    shutil.copyfile(here / TOOLS, destination / TOOLS)
    shutil.copyfile(here / POLICY, destination / POLICY)
    if (here / SERVERS).is_file():
        shutil.copyfile(here / SERVERS, destination / SERVERS)
    if (here / ATTACHED).is_dir():
        shutil.copytree(here / ATTACHED, destination / ATTACHED)
    world = GeneratedWorld(":memory:", kind="sqlite")
    world.name = spec["name"]
    world.store.apply((here / "world.sql").read_text(encoding="utf-8"))
    world.handlers = {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted((here / "handlers").glob("*.py"))
    }
    world.tools = world_tool_specs(destination)
    save(
        world,
        destination / WORLD,
        notes=spec.get("notes") or "",
        sequences=spec.get("sequences") or [],
        world_checks=spec.get("world_checks") or {},
        implementation=WORLD_IMPLEMENTATION,
    )
    world.close()
    release = seal_environment_release(
        destination,
        name=spec["name"],
        derived_from=spec.get("derived_from"),
        metadata={"catalogue": catalogue(here, destination)},
    )
    return release.digest


def _summary(readme: str) -> tuple[str, str]:
    """The README's title and the first sentence of its first paragraph."""
    title, paragraph = "", ""
    for line in readme.splitlines():
        if not title and line.startswith("# "):
            title = line[2:].strip()
        elif title and line.strip() and not line.startswith("#"):
            paragraph = line.strip()
            break
    first = re.split(r"(?<=\.)\s", paragraph, maxsplit=1)[0]
    return title, first


def catalogue(here: Path, environment: Path) -> dict[str, Any]:
    """What a catalogue card and a ready environment's World tab show, derived from the package:
    the README's title and first sentence, the seeded records per table, the apps attached as
    MCP capabilities and the opening lines of the first scenarios. Nothing here is written by
    hand, so it cannot drift from what the release contains."""
    readme = here / "README.md"
    title, summary = (
        _summary(readme.read_text(encoding="utf-8")) if readme.is_file() else ("", "")
    )
    capabilities_file = here / "mcp_capabilities.json"
    capabilities = (
        json.loads(capabilities_file.read_text(encoding="utf-8"))
        if capabilities_file.is_file()
        else {}
    )
    app_tables = {
        table: name
        for name, one in capabilities.items()
        for table in one.get("tables") or []
    }
    with sqlite3.connect(environment / WORLD / "world.sqlite") as connection:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
            if not row[0].startswith(("_", "sqlite_"))
            and row[0] != POLICY_TABLE
            and row[0] not in _RUNTIME_TABLES
        ]
        counts = {
            table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }
    requests = []
    for scenario in sorted((here / SUITE / "scenarios").glob("*/scenario.json")):
        opening = (
            json.loads(scenario.read_text(encoding="utf-8")).get("persona") or {}
        ).get("initial_message")
        if opening:
            requests.append(opening)
        if len(requests) == 2:
            break
    return {
        "title": title or here.name.replace("_", " ").title(),
        "summary": summary,
        "channel": "chat",
        "records": {
            table: counts[table] for table in tables if table not in app_tables
        },
        "apps": [
            {
                "capability": name,
                "server": one.get("server") or name,
                "tools": list(one.get("tools") or []),
                "records": sum(
                    counts.get(table, 0) for table in one.get("tables") or []
                ),
            }
            for name, one in sorted(capabilities.items())
        ],
        "example_requests": requests,
    }


def build_suite(
    here: Path, destination: Path, environment: Path, *, postgres_dsn: str | None = None
) -> str:
    source = here / SUITE
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(POLICY_PROFILE, "__pycache__"),
    )
    sealed = seal_suite_release(
        destination,
        name=f"{_spec(here)['name']}_base",
        environment_root=environment,
        policy_profile=json.loads(
            (source / POLICY_PROFILE).read_text(encoding="utf-8")
        ),
        postgres_dsn=postgres_dsn,
    )
    return sealed.digest


def main(here: Path, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=f"python -m fi.alk.environments.{here.name}.build"
    )
    parser.add_argument("--out", required=True, help="empty or missing directory")
    parser.add_argument(
        "--postgres-dsn",
        default=os.environ.get("ALK_RELEASE_POSTGRES_DSN"),
        help="role that may create databases; hosted runs need the suite proved on PostgreSQL",
    )
    args = parser.parse_args(argv)
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        parser.error(f"{out} is not empty")
    try:
        environment = build_environment(here, out / "environment")
        suite_digest = build_suite(
            here, out / "suite", out / "environment", postgres_dsn=args.postgres_dsn
        )
    except ReleaseError as exc:
        print(json.dumps({"error": exc.code, "detail": exc.detail}), file=sys.stderr)
        return 1
    print(json.dumps({"environment": environment, "suite": suite_digest}))
    return 0
