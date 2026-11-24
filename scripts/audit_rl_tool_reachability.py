#!/usr/bin/env python3
"""Which certified bundles could serve an external RL trainer, and which could not.

Triage for T28: an external trainer never runs the submitted agent, so a tool is only
usable when this layer can execute it itself. ``certify_tool_inventory`` already decides
that per tool, so this script reads its reports rather than re-deriving the answer from
raw contracts:

    certified + disposable_world   a compiled handler is in the bundle and its effects land
                                   in a world we own -> a trainer can drive it
    runtime_only                   declared, but it executes inside the agent's own process
                                   or service -> a trainer is acknowledged while nothing in
                                   the world changes
    rejected                       no implementation seam was found at all

``ToolEntry.mode`` is NOT the signal. An ``import`` tool is certified once its handler is
compiled into the bundle, so mode alone says nothing about whether a trainer can drive it.

``ToolCertificationReport.serves_external_driver`` is the authority; this script only walks
a tree of reports and prints the verdicts. Use it to pick pilot candidates before paying
for a live sweep, and see ``external_driver_blockers`` for the per-tool reasons.

Usage:
    python scripts/audit_rl_tool_reachability.py <path-or-dir>...
    python scripts/audit_rl_tool_reachability.py --self-check

Exit status: 0 when every report is serviceable, 1 when any is not, 2 on bad input.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fi.alk.harness.tool_certification import (  # noqa: E402
    ToolCertificationReport,
)

REPORT_NAME = "tool-certification.json"


class Verdict(NamedTuple):
    label: str
    path: Path
    report: ToolCertificationReport

    @property
    def serviceable(self) -> bool:
        return self.report.serves_external_driver


def label_for(path: Path) -> str:
    """The agent a report belongs to, read from the bundle layout it sits in."""
    parts = path.parts
    if "artifacts" in parts:
        index = parts.index("artifacts")
        if index + 1 < len(parts):
            return parts[index + 1]
    # …/<agent>/generic-harness/tool-certification.json
    return path.parent.parent.name or path.parent.name


def report_paths(targets: list[str]) -> list[Path]:
    found: list[Path] = []
    for target in targets:
        path = Path(target)
        if path.is_dir():
            found.extend(sorted(path.rglob(REPORT_NAME)))
        elif path.is_file():
            found.append(path)
        else:
            print(f"error: no such path: {target}", file=sys.stderr)
            raise SystemExit(2)
    return found


def load(path: Path) -> ToolCertificationReport | None:
    """The report at ``path``, or None when it is unreadable or not one.

    Validation is deliberate: the model verifies the fingerprint, so a report edited by hand
    is refused rather than quietly steering a pilot choice.
    """
    try:
        payload: Any = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"skipped {path}: {exc}", file=sys.stderr)
        return None
    if not isinstance(payload, dict) or "tools" not in payload:
        return None
    try:
        return ToolCertificationReport.model_validate(payload)
    except Exception as exc:  # pydantic ValidationError, or a canonical/fingerprint failure
        print(f"skipped {path}: not a valid certification report: {exc}", file=sys.stderr)
        return None


def latest_by_label(verdicts: list[Verdict]) -> dict[str, Verdict]:
    """Each agent judged by its most recent report.

    An agent is authored many times and older runs fail for reasons already fixed, so a
    stale report must not decide a pilot. Both the printed verdict and the exit status read
    this same selection; computing it twice let them disagree.
    """
    latest: dict[str, Verdict] = {}
    for verdict in sorted(verdicts, key=lambda v: v.path.stat().st_mtime):
        latest[verdict.label] = verdict
    return latest


def report(latest: dict[str, Verdict]) -> None:
    for label in sorted(latest, key=lambda name: (not latest[name].serviceable, name)):
        verdict = latest[label]
        status = "SERVICEABLE " if verdict.serviceable else "not usable  "
        tools = verdict.report.tools
        counts = Counter(f"{t.availability.value}/{t.probe_mode.value}" for t in tools)
        summary = ", ".join(f"{count}x {kind}" for kind, count in sorted(counts.items()))
        print(f"{status} {label:24s} {len(tools):3d} tools  {summary or 'none declared'}")
        for blocker in verdict.report.external_driver_blockers:
            print(f"                 - {blocker.tool}: {blocker.reason}")

    usable = [v for v in latest.values() if v.serviceable]
    print(f"\n{'=' * 70}")
    print(f"{len(usable)} of {len(latest)} agents could serve an external trainer")
    if usable:
        ranked = sorted(usable, key=lambda v: -len(v.report.tools))
        print("pilot candidates, most tools first:")
        for verdict in ranked:
            print(f"    {verdict.label}  ({len(verdict.report.tools)} tools)  {verdict.path}")
    else:
        print("no candidate: every agent has a tool only its own process can run.")


def self_check() -> None:
    """Assert the serviceability rule on the cases that decide a pilot."""
    from fi.alk.harness.tool_certification import (
        ToolAvailability,
        ToolCertification,
        ToolProbeMode,
    )

    def tool(name: str, availability: ToolAvailability, probe: ToolProbeMode) -> ToolCertification:
        return ToolCertification(
            tool=name,
            entrypoint_mode="import",
            probe_mode=probe,
            availability=availability,
            schema_validated=True,
            implementation_validated=True,
            reason="test",
        )

    drivable = tool("refund", ToolAvailability.CERTIFIED, ToolProbeMode.DISPOSABLE_WORLD)
    in_agent = tool("refund", ToolAvailability.RUNTIME_ONLY, ToolProbeMode.RUNTIME_ONLY)
    no_seam = tool("refund", ToolAvailability.REJECTED, ToolProbeMode.RUNTIME_ONLY)
    # Certified, but only observable during a live call: the trap this audit exists to catch.
    observed_only = tool("refund", ToolAvailability.CERTIFIED, ToolProbeMode.RUNTIME_ONLY)

    assert ToolCertificationReport.create([drivable]).serves_external_driver
    assert not ToolCertificationReport.create([in_agent]).serves_external_driver
    assert not ToolCertificationReport.create([no_seam]).serves_external_driver
    assert not ToolCertificationReport.create([observed_only]).serves_external_driver, (
        "certified is not enough; the handler must run in a world we own"
    )
    # One in-agent tool among drivable ones still blocks, and is named.
    mixed = ToolCertificationReport.create([drivable, tool("look_up", ToolAvailability.RUNTIME_ONLY, ToolProbeMode.RUNTIME_ONLY)])
    assert not mixed.serves_external_driver
    assert [b.tool for b in mixed.external_driver_blockers] == ["look_up"]
    # No tools is not a pass.
    assert not ToolCertificationReport.create([]).serves_external_driver
    assert not ToolCertificationReport.create([]).external_driver_blockers

    print("self-check: all assertions passed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("targets", nargs="*", help=f"{REPORT_NAME} files, or directories to search")
    parser.add_argument("--self-check", action="store_true", help="verify the rule and exit")
    args = parser.parse_args()

    if args.self_check:
        self_check()
        return 0
    if not args.targets:
        parser.error(f"give at least one path to search for {REPORT_NAME}, or --self-check")

    verdicts: list[Verdict] = []
    for path in report_paths(args.targets):
        loaded = load(path)
        if loaded is not None and loaded.tools:
            verdicts.append(Verdict(label=label_for(path), path=path, report=loaded))

    if not verdicts:
        print(f"no {REPORT_NAME} with tools found in the given paths", file=sys.stderr)
        return 2

    latest = latest_by_label(verdicts)
    report(latest)
    return 0 if all(v.serviceable for v in latest.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
