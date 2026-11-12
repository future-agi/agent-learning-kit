#!/usr/bin/env python3
"""Run the pinned two-turn AG-UI CrewAI check through ALK's SSE wrapper."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path

from fi.simulate.agent.wrapper import AgentInput
from fi.simulate.agent.wrappers.http import HTTPAgentWrapper


ROOT = Path(__file__).resolve().parent
REFERENCE = ROOT / "ag_ui_crewai" / "reference.json"


async def qualify(base_url: str, timeout: float) -> list[dict[str, object]]:
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    wrapper = HTTPAgentWrapper(
        endpoint=base_url.rstrip("/") + reference["runtime"]["endpoint"],
        protocol="json_template",
        request_template=reference["request"],
        sse=reference["sse"],
        timeout=timeout,
        secret_values={},
    )
    execution_id = uuid.uuid4().hex
    thread_id = f"alk-sse-{execution_id}"
    summaries = []
    for turn_index, content in enumerate(reference["qualification"]["turns"]):
        response = await wrapper.call(
            AgentInput(
                thread_id=thread_id,
                execution_id=execution_id,
                turn_index=turn_index,
                messages=[],
                new_message={"role": "user", "content": content},
            )
        )
        trace = (response.metadata or {}).get("external_agent", {})
        summary = {
            "turn": turn_index + 1,
            "success": trace.get("success") is True,
            "response_characters": len(response.content),
            "stream_events": trace.get("stream_events_received", 0),
            "stream_state": trace.get("stream_state"),
        }
        summaries.append(summary)
        if not summary["success"] or not response.content:
            raise RuntimeError(json.dumps({"qualification": "failed", **summary}))
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    summaries = asyncio.run(qualify(args.base_url, args.timeout))
    print(json.dumps({"qualification": "passed", "turns": summaries}, indent=2))


if __name__ == "__main__":
    main()
