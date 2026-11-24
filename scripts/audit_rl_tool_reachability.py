#!/usr/bin/env python3
"""Which agent contracts could serve an external RL trainer, and which cannot.

Gate 0 of the RL-endpoint tool sweep (T28). An external trainer has no submitted agent
process, so a tool whose implementation lives *inside* that process cannot run for it.
``ProvisionedWorld.call`` returns a hollow acknowledgement for exactly those tools --
``{"observed": True, "execution": "submitted_agent_runtime"}``, ``ok=True`` -- so the
trainer sees success while nothing happened. See
``src/fi/alk/harness/world/provisioned.py`` (``local_runtime_tools``).

``ToolEntry.mode`` decides it:

    service      reachable over HTTP          -> usable, if the service runs without the
                                                agent's decision loop (verify live)
    import       module-level callable        -> hollow for a trainer
    construct    method on a built instance   -> hollow for a trainer
    unreachable  no runnable seam             -> the bundle build already stops

A hollow *read* is survivable: the trainer sees less. A hollow *write* is
disqualifying, because the trainer is told an effect landed when it did not -- the
refund that never happened. That is the verdict this script reports.

This is a static pre-filter. It reads sealed contracts and never boots a world, so it
cannot prove a ``service`` tool actually works; it narrows the candidate set before the
live sweep pays for that. ALK deliberately never synthesises a missing implementation
("The harness never generates agent behavior"), so a hollow write is fixed only by the
agent exposing that tool over HTTP.

Usage:
    python scripts/audit_rl_tool_reachability.py <path-or-dir>...
    python scripts/audit_rl_tool_reachability.py --self-check

Exit status: 0 when every contract qualifies, 1 when any is disqualified, 2 on bad input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, NamedTuple

# Modes whose implementation runs inside the submitted agent's own process.
IN_PROCESS_MODES = frozenset({"import", "construct"})
# HTTP methods that only read. Mirrors ToolEntry.effect's documented fallback.
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class ToolVerdict(NamedTuple):
    tool: str
    mode: str
    effect: str
    reachable: bool
    disqualifying: bool


class ContractVerdict(NamedTuple):
    name: str
    path: Path
    tools: list[ToolVerdict]

    @property
    def hollow_writes(self) -> list[ToolVerdict]:
        return [t for t in self.tools if t.disqualifying]

    @property
    def hollow_reads(self) -> list[ToolVerdict]:
        return [t for t in self.tools if not t.reachable and not t.disqualifying]

    @property
    def qualifies(self) -> bool:
        # No tools at all is not a pass: there is nothing for a trainer to do.
        return bool(self.tools) and not self.hollow_writes


def tool_effect(entry: dict[str, Any]) -> str:
    """`effect` as declared, else derived from the HTTP method, as ToolEntry documents.

    An absent method is treated as a write: ToolEntry defaults `method` to POST, and
    guessing `read` on silence would hide the failure this audit exists to find.
    """
    declared = str(entry.get("effect") or "").strip().lower()
    if declared in {"read", "write"}:
        return declared
    method = str(entry.get("method") or "POST").strip().upper()
    return "read" if method in READ_METHODS else "write"


def unwrap(payload: dict[str, Any]) -> dict[str, Any]:
    """The contract itself, whether it is the document or nested under ``contract``.

    Authored fixtures wrap it (``{"agent": ..., "contract": {...}}``); agent readings store
    it flat. Returning the outer document for a wrapped file would find no tools and pass it
    as "nothing declared", which is the one failure a qualification audit must not have.
    """
    inner = payload.get("contract")
    if isinstance(inner, dict) and ("tools" in inner or "tool_entrypoints" in inner):
        return inner
    return payload


def audit_contract(payload: dict[str, Any], path: Path) -> ContractVerdict:
    payload = unwrap(payload)
    entries = {
        str(e.get("tool")): e
        for e in (payload.get("tool_entrypoints") or [])
        if isinstance(e, dict) and e.get("tool")
    }
    verdicts: list[ToolVerdict] = []
    for spec in payload.get("tools") or []:
        if not isinstance(spec, dict) or not spec.get("name"):
            continue
        name = str(spec["name"])
        # A tool with no entry has no recorded seam at all, which is strictly worse than
        # `unreachable`; treat it as in-process so it cannot pass unnoticed.
        entry = entries.get(name, {})
        mode = str(entry.get("mode") or "unreachable").strip().lower()
        effect = tool_effect(entry)
        reachable = mode == "service"
        verdicts.append(
            ToolVerdict(
                tool=name,
                mode=mode,
                effect=effect,
                reachable=reachable,
                disqualifying=not reachable and effect == "write",
            )
        )
    name = str(payload.get("agent") or path.stem)
    return ContractVerdict(name=name, path=path, tools=verdicts)


def contract_paths(targets: list[str]) -> list[Path]:
    found: list[Path] = []
    for target in targets:
        path = Path(target)
        if path.is_dir():
            found.extend(sorted(path.rglob("*.json")))
        elif path.is_file():
            found.append(path)
        else:
            print(f"error: no such path: {target}", file=sys.stderr)
            raise SystemExit(2)
    return found


def report(verdicts: list[ContractVerdict]) -> None:
    for verdict in verdicts:
        status = "QUALIFIES" if verdict.qualifies else "DISQUALIFIED"
        print(f"\n{status}  {verdict.name}  ({verdict.path})")
        if not verdict.tools:
            print("    no tools declared - nothing for a trainer to drive")
            continue
        for tool in verdicts and verdict.tools:
            mark = "ok    " if tool.reachable else ("HOLLOW" if tool.disqualifying else "thin  ")
            print(f"    {mark} {tool.tool}  mode={tool.mode} effect={tool.effect}")
        if verdict.hollow_writes:
            names = ", ".join(t.tool for t in verdict.hollow_writes)
            print(f"    -> hollow WRITE tools, trainer told an effect landed: {names}")
        if verdict.hollow_reads:
            names = ", ".join(t.tool for t in verdict.hollow_reads)
            print(f"    -> hollow reads, trainer sees less than the agent does: {names}")

    qualifying = [v for v in verdicts if v.qualifies]
    print(f"\n{'=' * 60}")
    print(f"{len(qualifying)} of {len(verdicts)} contracts could serve an external trainer")
    if qualifying:
        print("candidates for the live sweep: " + ", ".join(v.name for v in qualifying))
    else:
        print("no candidate: every contract has a tool that only the agent's process runs.")
        print("RL access needs the agent to expose its write tools over HTTP (mode=service).")


def self_check() -> None:
    """Assert the verdict rules on the four cases that matter."""
    service_write = {"tool": "refund", "mode": "service", "endpoint": "refund", "method": "POST"}
    import_write = {"tool": "refund", "mode": "import", "module": "agent", "callable": "refund"}
    import_read = {"tool": "lookup", "mode": "import", "effect": "read"}
    get_no_effect = {"tool": "status", "mode": "import", "method": "GET"}

    def one(entry: dict[str, Any]) -> ToolVerdict:
        payload = {"agent": "t", "tools": [{"name": entry["tool"]}], "tool_entrypoints": [entry]}
        return audit_contract(payload, Path("t.json")).tools[0]

    assert one(service_write).reachable, "service mode must be reachable"
    assert not one(service_write).disqualifying, "a reachable write must not disqualify"
    assert one(import_write).disqualifying, "an in-process write must disqualify"
    assert not one(import_read).disqualifying, "a declared read must not disqualify"
    assert one(get_no_effect).effect == "read", "GET with no effect must read"
    assert one(import_write).effect == "write", "POST default must count as a write"

    # A tool with no entry at all must not slip through as reachable.
    orphan = audit_contract(
        {"agent": "t", "tools": [{"name": "refund"}], "tool_entrypoints": []}, Path("t.json")
    )
    assert orphan.tools[0].disqualifying, "a tool with no entry must disqualify"
    assert not orphan.qualifies

    # A contract nested under `contract` must be read, not silently skipped as empty.
    nested = audit_contract(
        {"agent": "t", "contract": {"tools": [{"name": "refund"}], "tool_entrypoints": [import_write]}},
        Path("t.json"),
    )
    assert nested.tools, "a nested contract must be unwrapped, not read as toolless"
    assert nested.tools[0].disqualifying
    assert not nested.qualifies, "a nested contract must not pass by being skipped"

    # An all-service contract qualifies; an empty one does not.
    assert audit_contract(
        {"agent": "t", "tools": [{"name": "refund"}], "tool_entrypoints": [service_write]},
        Path("t.json"),
    ).qualifies
    assert not audit_contract({"agent": "t", "tools": []}, Path("t.json")).qualifies

    print("self-check: all assertions passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="*", help="contract .json files, or directories to search")
    parser.add_argument("--self-check", action="store_true", help="verify the verdict rules and exit")
    args = parser.parse_args()

    if args.self_check:
        self_check()
        return 0
    if not args.targets:
        parser.error("give at least one contract path, or --self-check")

    verdicts: list[ContractVerdict] = []
    for path in contract_paths(args.targets):
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"skipped {path}: {exc}", file=sys.stderr)
            continue
        if not isinstance(payload, dict):
            continue
        if "tools" not in unwrap(payload):
            continue
        verdicts.append(audit_contract(payload, path))

    if not verdicts:
        print("no agent contracts found in the given paths", file=sys.stderr)
        return 2

    report(verdicts)
    return 0 if all(v.qualifies for v in verdicts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
