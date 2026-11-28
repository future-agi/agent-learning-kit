"""Seal the ready environments a release ships, one archive each, with the list naming them.

    python -m fi.alk.environments.ready --out DIR --postgres-dsn DSN [--world NAME ...]

The ready environments are the vertical environments of this package's README; the MCP
capabilities and the `appointment_booking` reference are not customer-facing. Each world is built
and sealed as its own `build` would, with the suite proved on PostgreSQL, then packed as
`ready-environment-<name>.tar.gz` holding `environment/` and `suite/`. `ready-environments.json` lists each
archive with its SHA-256 and both release digests. The release workflow attaches all of them to a
GitHub release, and the platform registers what the list names on every deploy.

Archives are reproducible: the same source seals to the same digests and packs to the same bytes,
so a world that did not change between releases is not registered again.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib
import io
import json
import os
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from fi.alk.environments.designed import build_environment, build_suite
from fi.alk.harness.releases import ReleaseError

SCHEMA = "futureagi.ready-environments.v1"
MANIFEST = "ready-environments.json"
READY_ENVIRONMENTS = (
    "banking",
    "clinic_scheduling",
    "hr",
    "internal_it",
    "internal_knowledge",
    "retail",
    "saas_subscriptions",
    "sales_intake",
    "service_scheduling",
)


def archive_name(world: str) -> str:
    return f"ready-environment-{world}.tar.gz"


def pack(sealed: Path) -> bytes:
    """`sealed/environment` and `sealed/suite` as a reproducible tar.gz: sorted regular files,
    no owners, times or modes beyond 0644."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for part in ("environment", "suite"):
            for path in sorted((sealed / part).rglob("*")):
                if path.is_symlink():
                    raise ReleaseError(
                        "ready_environment_invalid", f"{path} is a symlink"
                    )
                if not path.is_file():
                    continue
                content = path.read_bytes()
                info = tarfile.TarInfo(path.relative_to(sealed).as_posix())
                info.size, info.mtime, info.mode = len(content), 0, 0o644
                tar.addfile(info, io.BytesIO(content))
    return gzip.compress(raw.getvalue(), mtime=0)


def seal(world: str, out: Path, *, postgres_dsn: str) -> dict[str, Any]:
    """Build and seal one ready world, write its archive into `out` and return its list entry."""
    if world not in READY_ENVIRONMENTS:
        raise ReleaseError(
            "ready_environment_unknown", f"{world} is not a ready environment"
        )
    here = Path(importlib.import_module(f"fi.alk.environments.{world}").__file__).parent
    with tempfile.TemporaryDirectory(prefix=f"ready-{world}-") as scratch:
        sealed = Path(scratch)
        environment = build_environment(here, sealed / "environment")
        suite = build_suite(
            here, sealed / "suite", sealed / "environment", postgres_dsn=postgres_dsn
        )
        body = pack(sealed)
    name = archive_name(world)
    (out / name).write_bytes(body)
    return {
        "name": world,
        "archive": name,
        "sha256": hashlib.sha256(body).hexdigest(),
        "size": len(body),
        "environment": environment,
        "suite": suite,
    }


def publish(
    out: Path,
    *,
    postgres_dsn: str,
    version: str,
    revision: str,
    worlds: tuple[str, ...] = READY_ENVIRONMENTS,
) -> dict[str, Any]:
    """Seal `worlds` into `out` and write the list; `out` must be empty or missing."""
    if not postgres_dsn:
        raise ReleaseError(
            "ready_environment_unproved",
            "a ready environment is proved on PostgreSQL: pass a DSN",
        )
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ReleaseError("ready_environment_invalid", f"{out} is not empty")
    manifest = {
        "schema_version": SCHEMA,
        "alk_version": version,
        "alk_source_revision": revision,
        "environments": [seal(world, out, postgres_dsn=postgres_dsn) for world in worlds],
    }
    (out / MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fi.alk.environments.ready")
    parser.add_argument(
        "--out", required=True, type=Path, help="empty or missing directory"
    )
    parser.add_argument(
        "--postgres-dsn",
        default=os.environ.get("ALK_RELEASE_POSTGRES_DSN"),
        help="role that may create databases; every suite is proved on PostgreSQL",
    )
    parser.add_argument("--version", default="", help="the kit version these ship with")
    parser.add_argument(
        "--revision", default="", help="the kit commit these are built from"
    )
    parser.add_argument(
        "--world",
        action="append",
        choices=READY_ENVIRONMENTS,
        help="seal only this world (repeatable); default: every ready world",
    )
    args = parser.parse_args(argv)
    try:
        manifest = publish(
            args.out,
            postgres_dsn=args.postgres_dsn or "",
            version=args.version,
            revision=args.revision,
            worlds=tuple(args.world or READY_ENVIRONMENTS),
        )
    except ReleaseError as exc:
        print(json.dumps({"error": exc.code, "detail": exc.detail}), file=sys.stderr)
        return 1
    print(json.dumps({world["name"]: world["sha256"] for world in manifest["environments"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
