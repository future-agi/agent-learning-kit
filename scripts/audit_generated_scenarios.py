"""Audit a generation run against the agent's real source. Deterministic, no model calls.

Usage: python scripts/audit_generated_scenarios.py <run_dir> <agent_repo>

Checks, per scenario and in aggregate:
- every tool named in a checkpoint or mock exists in the agent source;
- every identifier-shaped argument value in a tool_call_args checkpoint appears in the agent source
  (menu ids, enum values), so no checkpoint asserts an id that does not exist;
- checkpoint kind mix and deterministic share;
- sub-goal reuse across scenarios (the roll-up property);
- input/checkpoint separation smells: the agent_input leaking seeded ids that facts do not cover.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _source_blob(agent_repo: str) -> str:
    parts = []
    for dirpath, dirnames, filenames in os.walk(agent_repo):
        dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__", ".venv"}]
        for filename in filenames:
            if filename.endswith((".py", ".md", ".json", ".yaml", ".yml", ".toml")):
                try:
                    with open(os.path.join(dirpath, filename), encoding="utf-8", errors="ignore") as fh:
                        parts.append(fh.read())
                except OSError:
                    pass
    return "\n".join(parts)


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{2,}$")


def audit(run_dir: str, agent_repo: str) -> int:
    scenarios_dir = os.path.join(run_dir, "scenarios")
    files = sorted(os.listdir(scenarios_dir)) if os.path.isdir(scenarios_dir) else []
    if not files:
        print("no scenarios found")
        return 1
    source = _source_blob(agent_repo)

    kind_mix: Counter = Counter()
    subgoal_uses: Counter = Counter()
    deterministic = total = 0
    failures: list[str] = []
    run_declared_handles: list[str] = []

    for name in files:
        record = _load(os.path.join(scenarios_dir, name))
        slug = record.get("id", name)
        fact_values = {str(f.get("value", "")).lower() for f in record.get("facts") or []}
        # A scenario may introduce a handle the agent's source cannot contain (an order
        # reference) provided its own mock declares that value, which is what creates it during
        # the run. Those are grounded; anything else invented is not.
        declared = json.dumps(record.get("environment") or {})
        pass
        for sub_goal in record.get("sub_goals") or []:
            checkpoint = (sub_goal or {}).get("checkpoint") or {}
            definition = checkpoint.get("definition") or {}
            kind = checkpoint.get("kind", "?")
            kind_mix[kind] += 1
            subgoal_uses[str(sub_goal.get("name"))] += 1
            total += 1
            if checkpoint.get("deterministic"):
                deterministic += 1
            tool = definition.get("tool") or definition.get("no_tool_call") or (
                definition.get("no_tool_call_with") or {}
            ).get("tool")
            if tool and f"{tool}" not in source:
                failures.append(f"{slug}: tool `{tool}` not found in agent source")
            for arg, value in (definition.get("args_equal") or {}).items():
                text = str(value)
                if _IDENTIFIER.match(text) and text not in source:
                    # Match the bare token: a handle may be declared in state_updates or
                    # inside the mock's response text, where JSON escaping hides the quotes.
                    if re.search(rf"\b{re.escape(text)}\b", declared):
                        run_declared_handles.append(f"{slug}: {arg}={text}")
                        continue
                    failures.append(f"{slug}: args_equal {arg}={text} not found in agent source")
        agent_input = str(record.get("agent_input", "")).lower()
        for token in re.findall(r"[a-z][a-z0-9_]{4,}", agent_input):
            if "_" in token and token in source and token not in fact_values:
                failures.append(f"{slug}: agent_input leaks internal identifier `{token}`")

    reused = sum(1 for count in subgoal_uses.values() if count >= 2)
    print(f"scenarios: {len(files)}")
    print(f"checkpoints: {total}, deterministic: {deterministic} ({100 * deterministic // max(total, 1)}%)")
    print(f"kind mix: {dict(kind_mix)}")
    print(f"sub-goal names reused in >=2 scenarios: {reused} of {len(subgoal_uses)}")
    if run_declared_handles:
        print(
            f"run-created handles declared by the scenario's own mocks: "
            f"{len(run_declared_handles)} (grounded, not failures)"
        )
    print(f"grounding failures: {len(failures)}")
    for failure in failures[:30]:
        print(f"  - {failure}")
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(audit(sys.argv[1], sys.argv[2]))
