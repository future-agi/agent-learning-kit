"""Everything a generated scenario needs around a live run, so one command does the whole job.

A voice run on its own answers "did a conversation happen". A generated scenario also needs its
tools served, its tool calls recorded, its checkpoints graded, and a trace of what occurred. Doing
those as separate steps means they get skipped, so they are wired into the same invocation.

Three pieces:

- ``tool_session`` brings up the scenario's mock tools on a public URL and points the provider
  assistant at them for the duration of one run, then puts the assistant back as it was.
- ``grade`` runs the scenario's own checkpoints against what the run actually produced.
- ``write_trace`` writes the timeline: every turn, every tool call with its arguments and response,
  the world state it left behind, and the verdict per checkpoint.

Nothing here decides how a scenario is generated or what a checkpoint means. It connects what
already exists.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Sequence

from .checks import evaluate_scenario
from .vapi_live import ScenarioMockServer, assistant_payload, load_registry

logger = logging.getLogger(__name__)

_TUNNEL_TIMEOUT_SECONDS = 40
_TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


@dataclass
class ToolSession:
    """The live mock-tool surface for one run."""

    server: ScenarioMockServer
    public_url: str
    assistant_id: str = ""
    started_at: float = field(default_factory=time.time)

    def calls(self) -> list[dict[str, Any]]:
        return self.server.log.snapshot()

    @property
    def final_state(self) -> dict[str, Any]:
        return self.server.final_state


def _open_tunnel(port: int) -> tuple[str, subprocess.Popen | None]:
    """A public URL for the local mock server. Providers call tools from their own cloud."""
    explicit = os.environ.get("ALK_TOOL_PUBLIC_URL", "").strip()
    if explicit:
        return explicit.rstrip("/"), None
    process = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    deadline = time.time() + _TUNNEL_TIMEOUT_SECONDS
    assert process.stdout is not None
    while time.time() < deadline:
        line = process.stdout.readline()
        if not line:
            if process.poll() is not None:
                break
            continue
        found = _TUNNEL_URL.search(line)
        if found:
            return found.group(0), process
    process.terminate()
    raise RuntimeError("could not obtain a public URL for the mock tool server")


def _patch_assistant_tools(assistant_id: str, contract: Any, public_url: str) -> None:
    import httpx

    key = os.environ.get("VAPI_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "VAPI_API_KEY is required to point the assistant at the mock tools"
        )
    payload = assistant_payload(contract, tool_base_url=public_url, name="")
    # Cloudflare blocks some default client signatures on this API; httpx is what works.
    response = httpx.patch(
        f"https://api.vapi.ai/assistant/{assistant_id}",
        headers={"authorization": f"Bearer {key}"},
        json={"model": payload["model"]},
        timeout=60,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"could not wire the assistant to the mock tools: {response.status_code}"
        )


@contextlib.contextmanager
def tool_session(
    record: Mapping[str, Any], *, agent: str = "drive_thru"
) -> Iterator[ToolSession]:
    """Serve this scenario's mock tools for the length of one run."""
    from .contract import AgentContract

    registry = load_registry().get(agent) or {}
    assistant_id = str(
        registry.get("assistant_id") or os.environ.get("VAPI_ASSISTANT_ID", "")
    )
    contract_path = str(registry.get("contract") or "")
    server = ScenarioMockServer(
        port=int(os.environ.get("ALK_TOOL_PORT", "8799"))
    ).start()
    server.bind(record)
    tunnel: subprocess.Popen | None = None
    try:
        public_url, tunnel = _open_tunnel(server.port)
        if assistant_id and contract_path and os.path.exists(contract_path):
            with open(contract_path, encoding="utf-8") as fh:
                contract = AgentContract.model_validate(json.load(fh))
            _patch_assistant_tools(assistant_id, contract, public_url)
        else:
            logger.warning(
                "no registered assistant; tools will not be reachable by the provider"
            )
        yield ToolSession(
            server=server, public_url=public_url, assistant_id=assistant_id
        )
    finally:
        if tunnel is not None:
            tunnel.terminate()
        server.stop()


def agent_turns(messages: Sequence[Mapping[str, Any]]) -> list[str]:
    return [
        str(m.get("content") or "")
        for m in messages
        if str(m.get("role")) == "assistant" and m.get("content")
    ]


def grade(
    record: Mapping[str, Any],
    *,
    messages: Sequence[Mapping[str, Any]],
    tool_calls: Sequence[Mapping[str, Any]],
    final_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The scenario's own checkpoints against what the run produced."""
    results = evaluate_scenario(
        record,
        tool_calls=tool_calls,
        transcript_turns=agent_turns(messages),
        final_state=dict(final_state or {}),
    )
    checks = [
        {"name": r.name, "kind": r.kind, "passed": r.passed, "reason": r.reason}
        for r in results
    ]
    graded = [c for c in checks if c["passed"] is not None]
    failed = [c for c in graded if c["passed"] is False]
    # A conversation that never got going is not a verdict about the agent, so say so rather
    # than reporting a confident failure.
    user_turns = sum(1 for m in messages if str(m.get("role")) == "user")
    return {
        "scenario_id": record.get("id"),
        "target_failure": record.get("target_failure"),
        "checks": checks,
        "passed": len(graded) - len(failed),
        "failed": len(failed),
        "skipped": len(checks) - len(graded),
        "verdict": (
            "inconclusive"
            if user_turns < 2 or not graded
            else ("pass" if not failed else "fail")
        ),
    }


def write_trace(
    output_dir: str,
    *,
    record: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    tool_calls: Sequence[Mapping[str, Any]],
    final_state: Mapping[str, Any] | None,
    grading: Mapping[str, Any] | None,
    metadata: Mapping[str, Any] | None = None,
) -> str:
    """One file holding what happened, in the order it happened."""
    trace = {
        "scenario": {
            "id": record.get("id"),
            "use_case": record.get("use_case"),
            "situation": record.get("situation"),
            "target_failure": record.get("target_failure"),
            "why_it_matters": record.get("why_it_matters"),
            "provenance": record.get("provenance"),
        },
        "caller": {
            "instruction": record.get("agent_input"),
            "facts": record.get("facts"),
            "objective": (record.get("expected_outcome") or {}).get("world_state"),
        },
        "conversation": [
            {"role": m.get("role"), "content": m.get("content")} for m in messages
        ],
        "tool_calls": list(tool_calls),
        "final_state": dict(final_state or {}),
        "checks": (grading or {}).get("checks"),
        "verdict": (grading or {}).get("verdict"),
        "run": dict(metadata or {}),
    }
    path = os.path.join(output_dir, "trace.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(trace, fh, indent=2, ensure_ascii=False, default=str)
    _write_readable_trace(output_dir, trace)
    return path


def _write_readable_trace(output_dir: str, trace: Mapping[str, Any]) -> None:
    scenario = trace["scenario"]
    lines = [
        f"# {scenario.get('id')}",
        "",
        f"**Tests for:** {scenario.get('target_failure')}",
        f"**Matters because:** {scenario.get('why_it_matters')}",
        f"**Origin:** {(scenario.get('provenance') or {}).get('kind')}",
        "",
        "## What the caller was told",
        "",
        f"{trace['caller'].get('instruction')}",
        "",
        "## Conversation",
        "",
    ]
    for turn in trace["conversation"]:
        who = "caller" if turn.get("role") == "user" else "agent "
        lines.append(f"- **{who}** {turn.get('content')}")
    lines += ["", "## Tool calls", ""]
    if trace["tool_calls"]:
        for call in trace["tool_calls"]:
            lines.append(f"- `{call.get('name')}` {json.dumps(call.get('arguments'))}")
    else:
        lines.append("- none")
    lines += ["", "## Checks", ""]
    for check in trace.get("checks") or []:
        mark = {True: "PASS", False: "FAIL", None: "SKIP"}[check.get("passed")]
        lines.append(
            f"- **{mark}** `{check.get('name')}` ({check.get('kind')}): {check.get('reason')}"
        )
    lines += ["", f"**Verdict: {trace.get('verdict')}**", ""]
    with open(os.path.join(output_dir, "trace.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
