"""Read an agent version's tools inside a hosted check, for one the platform has never read.

A pairing check certifies against the environment's saved contract: it talks to the agent, but
never reads a new agent version's own tools from its source. When the platform asks, this runs the
understand stage (a model call) on the uploaded source first, with the same generation credentials
an authoring run gets,
and writes the agent document the platform stores for the version: ``agent.json`` holding
``{"sdk_version", "agent"}``, built from the contract the way a build's registration builds it.

The reading never decides the check: any failure is printed on one line and exits 0, so the
certify that follows runs as it would without it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .. import __version__ as _sdk_version

AGENT_DOCUMENT = "agent.json"


def _read(source: Path, output: Path, name: str) -> None:
    from ..providers.agent import build_authored_agent
    from . import cli, spend
    from .contract import AgentContract
    from .hosted_authoring_entrypoint import configure_authoring_credentials

    configure_authoring_credentials()
    output.mkdir(parents=True, exist_ok=True)
    journal = os.environ.get(spend.JOURNAL_ALIAS, "").strip()
    if journal:
        spend.journal_to(journal)
    status = cli.main(
        [
            "understand",
            "--name",
            name,
            "--path",
            str(source),
            "--out",
            str(output),
            "--once",
        ]
    )
    if status != 0:
        raise RuntimeError(f"understand exited {status}")
    contract = AgentContract.model_validate_json(
        (output / "contract.json").read_text(encoding="utf-8")
    ).model_dump(mode="json")
    document = {"sdk_version": _sdk_version, "agent": build_authored_agent(contract)}
    (output / AGENT_DOCUMENT).write_text(json.dumps(document) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hosted_agent_reading")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", default="agent")
    args = parser.parse_args(argv)
    try:
        _read(args.source.resolve(), args.output.resolve(), args.name)
    except (
        Exception
    ) as exc:  # noqa: BLE001 - a failed reading leaves the version unread
        (args.output / AGENT_DOCUMENT).unlink(missing_ok=True)
        print(f"agent reading failed: {type(exc).__name__}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
