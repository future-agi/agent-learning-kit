"""Hosted voice CallRunner.

Places the simulated call against the already-provisioned agent and captures the agent's tool
calls from its own function-call trace (the `tool_trace` evidence seam). It reuses the proven
voice case (`run/sdk_voice.py`, via `run.call.place_the_call`) to drive the synthetic caller, and
`run.simulation._semantic_calls` to read the agent's `HARNESS_TOOL_TRACE`.

Contract with the rest of the hosted lane:
- The provisioner has already spawned the agent process; it is registered on LiveKit under
  `runtime.metadata["livekit_agent_name"]` and writes its tool trace to
  `runtime.metadata["tool_trace_path"]`.
- The caller-lane provider creds (LiveKit / Deepgram / simulator LLM) live in `os.environ`; the
  entrypoint populates them from the peeked secret map at boot, before the provisioner deletes
  `/run/futureagi/secrets.json`.
- The rich scenario document (persona / instruction / tests / fixture) is read from the bundle at
  `bundle/scenarios/<scenario_key>/scenario.json` (the hosted `Scenario` only carries
  scenario_key + sub_goals, so the driving inputs come from the bundle document directly).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .hosted_scheduler import CallAborted, CallOutcome, EnvironmentRuntime, Scenario
from .run.call import place_the_call
from .run.simulation import _semantic_calls

_VOICE_CASE = os.environ.get("HARNESS_VOICE_CASE", "2.1.2")


def _load_contract(bundle_dir: Path):
    """Best-effort agent contract for tool-name normalization in the trace (optional)."""
    path = bundle_dir / "contract.json"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    for module, name in (("fi.alk.harness.contract", "AgentContract"),
                         ("fi.alk.harness.understand", "AgentContract")):
        try:
            mod = __import__(module, fromlist=[name])
            return getattr(mod, name).model_validate_json(text)
        except Exception:  # noqa: BLE001 - contract is an optional normalization aid
            continue
    return None


class VoiceCallRunner:
    """`CallRunner` (hosted_scheduler.CallRunner): async run(scenario, runtime) -> CallOutcome."""

    def __init__(self, bundle_dir: Path | str = Path("/work/bundle")) -> None:
        self._bundle_dir = Path(bundle_dir)
        self._contract = _load_contract(self._bundle_dir)

    def _scenario_doc(self, scenario_key: str) -> dict[str, Any]:
        path = self._bundle_dir / "scenarios" / scenario_key / "scenario.json"
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return doc if isinstance(doc, dict) else {}

    async def run(self, scenario: Scenario, runtime: EnvironmentRuntime) -> CallOutcome:
        meta = dict(runtime.metadata or {})
        agent_name = str(meta.get("livekit_agent_name") or "").strip()
        if not agent_name:
            raise CallAborted(
                "runtime.metadata has no livekit_agent_name; the provisioner must surface the "
                "agent's dispatch name per world",
                partial=CallOutcome(calls=(), turns=0, started_at=None, ended_at=None, duration_ms=0),
            )
        trace_path = Path(str(meta.get("tool_trace_path") or "").strip() or "")
        doc = self._scenario_doc(scenario.scenario_key)
        persona = doc.get("persona") if isinstance(doc.get("persona"), dict) else {"name": "customer"}

        # The voice case reads all of this from the environment; nothing about the caller is
        # decided twice (mirrors run/simulation._spoken_to).
        os.environ["LIVEKIT_TARGET_AGENT_NAME"] = agent_name
        os.environ["HARNESS_SCENARIO"] = scenario.scenario_key
        os.environ["HARNESS_INSTRUCTION"] = str(doc.get("instruction") or "")
        os.environ["HARNESS_OUTCOME"] = str(doc.get("tests") or "")
        os.environ["HARNESS_PERSONA"] = json.dumps(persona)
        os.environ["HARNESS_INITIAL_MESSAGE"] = str(persona.get("initial_message") or "")
        os.environ["HARNESS_SCRIPTED_CALLER"] = json.dumps(persona.get("scripted_caller") or {})
        os.environ["HARNESS_FIXTURE"] = json.dumps(doc.get("fixture") or {}, default=str)

        if str(trace_path):
            try:
                trace_path.unlink()
            except OSError:
                pass

        turns = {"n": 0}

        def on_exchange(_event: object) -> None:
            turns["n"] += 1

        started = datetime.now(timezone.utc)
        clock = time.time()
        code = await asyncio.to_thread(place_the_call, _VOICE_CASE, False, on_exchange)
        ended = datetime.now(timezone.utc)
        duration_ms = int((time.time() - clock) * 1000)

        calls = _semantic_calls(trace_path, contract=self._contract) if str(trace_path) else []
        outcome = CallOutcome(
            calls=tuple(calls),
            turns=turns["n"],
            started_at=started.isoformat(),
            ended_at=ended.isoformat(),
            duration_ms=duration_ms,
        )
        # A non-zero voice case that still produced tool evidence is a completed-but-imperfect
        # call, not an aborted one -- let the scheduler grade it. Only a failure with no evidence
        # at all is a genuine abort (the receipt's call field must still carry the timing).
        if code != 0 and not calls:
            raise CallAborted(f"voice case exited {code} with no tool evidence", partial=outcome)
        return outcome
