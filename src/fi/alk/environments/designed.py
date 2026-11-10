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
import shutil
import sys
from pathlib import Path
from typing import Any

from fi.alk.harness.releases import (
    ATTACHED,
    POLICY,
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
        destination, name=spec["name"], derived_from=spec.get("derived_from")
    )
    return release.digest


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
