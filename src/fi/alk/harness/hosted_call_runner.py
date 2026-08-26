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

P0 additions (v1.15):
- `GOOGLE_APPLICATION_CREDENTIALS_JSON` ADC materialization: a per-job mode-0600 file written
  before `place_the_call` and cleaned in all terminal paths.
- `voice_case` resolved dynamically per call via shared precedence:
  scenario doc -> bundle metadata -> HARNESS_VOICE_CASE env -> fallback '2.1.2'.
- Named-agent LiveKit dispatch: `LIVEKIT_TARGET_AGENT_NAME` set for the subprocess, ensuring the
  caller joins the correct agent's room (the agent process itself registers under this name).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import outbound as ob
from .hosted_scheduler import CallAborted, CallOutcome, EnvironmentRuntime, Scenario
from .run.call import place_the_call
from .run.simulation import _semantic_calls

logger = logging.getLogger(__name__)

# The module-level constant is gone. Voice case is resolved dynamically per call using
# `_resolve_voice_case()`, which implements the shared precedence contract:
#   scenario document voice_case -> bundle metadata voice_case -> HARNESS_VOICE_CASE -> '2.1.2'.
_VOICE_CASE_FALLBACK = "2.1.2"


def _resolve_voice_case(
    scenario_doc: dict[str, Any],
    bundle_metadata: dict[str, Any],
) -> str:
    """Shared precedence: scenario doc -> bundle metadata -> env -> fallback '2.1.2'."""
    case = scenario_doc.get("voice_case")
    if isinstance(case, str) and case.strip():
        return case.strip()
    case = bundle_metadata.get("voice_case")
    if isinstance(case, str) and case.strip():
        return case.strip()
    return os.environ.get("HARNESS_VOICE_CASE", _VOICE_CASE_FALLBACK)


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


def _materialize_adc(credentials_json: str, job_dir: Path) -> Path:
    """Write GOOGLE_APPLICATION_CREDENTIALS_JSON to a mode-0600 per-job file.

    Returns the path. The caller MUST clean it in all terminal paths (see `_cleanup_adc`).
    Raises `CallAborted` if the JSON is empty or the write fails -- a missing credential is
    typed and loud, never a silent subprocess failure.
    """
    if not credentials_json.strip():
        raise CallAborted(
            "GOOGLE_APPLICATION_CREDENTIALS_JSON is set but empty; the caller ADC cannot "
            "be materialized",
            partial=CallOutcome(calls=(), turns=0, started_at=None, ended_at=None, duration_ms=0),
        )
    try:
        parsed = json.loads(credentials_json)
    except json.JSONDecodeError as exc:
        raise CallAborted(
            "GOOGLE_APPLICATION_CREDENTIALS_JSON is not valid JSON",
            partial=CallOutcome(calls=(), turns=0, started_at=None, ended_at=None, duration_ms=0),
        ) from exc
    if not isinstance(parsed, dict):
        raise CallAborted(
            "GOOGLE_APPLICATION_CREDENTIALS_JSON must contain a JSON object",
            partial=CallOutcome(calls=(), turns=0, started_at=None, ended_at=None, duration_ms=0),
        )
    job_dir.mkdir(parents=True, exist_ok=True)
    fd, path_str = tempfile.mkstemp(suffix=".json", prefix="gac-", dir=str(job_dir))
    path = Path(path_str)
    try:
        os.write(fd, credentials_json.encode("utf-8"))
        os.close(fd)
        os.chmod(path_str, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except Exception as exc:
        _cleanup_adc(path)
        raise CallAborted(
            f"failed to materialize ADC credentials file: {exc}",
            partial=CallOutcome(calls=(), turns=0, started_at=None, ended_at=None, duration_ms=0),
        ) from exc
    return path


def _cleanup_adc(path: Path | None) -> None:
    """Remove the materialized ADC file and unset the env var. Never raises."""
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)


class VoiceCallRunner:
    """`CallRunner` (hosted_scheduler.CallRunner): async run(scenario, runtime) -> CallOutcome."""

    def __init__(
        self,
        bundle_dir: Path | str = Path("/work/bundle"),
        *,
        bundle_metadata: dict[str, Any] | None = None,
    ) -> None:
        self._bundle_dir = Path(bundle_dir)
        self._contract = _load_contract(self._bundle_dir)
        self._bundle_metadata: dict[str, Any] = bundle_metadata or {}

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

        # Voice case: shared precedence (scenario doc -> bundle metadata -> env -> fallback).
        voice_case = _resolve_voice_case(doc, self._bundle_metadata)

        # ADC materialization: write GOOGLE_APPLICATION_CREDENTIALS_JSON to a mode-0600 file
        # before the subprocess, clean it in all terminal paths.
        adc_path: Path | None = None
        gac_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()
        existing_adc = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        uses_vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in {
            "1", "true", "yes",
        }
        if uses_vertex and not existing_adc and not gac_json:
            raise CallAborted(
                "Vertex caller requires GOOGLE_APPLICATION_CREDENTIALS_JSON or "
                "GOOGLE_APPLICATION_CREDENTIALS",
                partial=CallOutcome(
                    calls=(), turns=0, started_at=None, ended_at=None, duration_ms=0
                ),
            )
        if not existing_adc and gac_json:
            adc_dir = self._bundle_dir.parent / ".adc"
            adc_path = _materialize_adc(gac_json, adc_dir)
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(adc_path)
        try:
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

            # sdk_voice requires LIVEKIT_TARGET_SYSTEM_PROMPT — the caller's system prompt
            # produced by the environment authoring step. In the local lane, run/simulation reads
            # it from simulator_prompt.md; in the hosted lane it lives in the bundle.
            prompt_path = self._bundle_dir / "simulator_prompt.md"
            if prompt_path.is_file():
                os.environ["LIVEKIT_TARGET_SYSTEM_PROMPT"] = prompt_path.read_text(
                    encoding="utf-8"
                ).strip()
            elif "LIVEKIT_TARGET_SYSTEM_PROMPT" not in os.environ:
                # Fallback: use the scenario instruction as a minimal prompt.
                os.environ["LIVEKIT_TARGET_SYSTEM_PROMPT"] = str(
                    doc.get("instruction") or "You are a customer."
                )

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
            code = await asyncio.to_thread(place_the_call, voice_case, False, on_exchange)
            ended = datetime.now(timezone.utc)
            duration_ms = int((time.time() - clock) * 1000)

            calls = _semantic_calls(trace_path, contract=self._contract) if str(trace_path) else []
            outcome = CallOutcome(
                calls=tuple(calls),
                turns=turns["n"],
                started_at=ob.format_rfc3339_millis(started),
                ended_at=ob.format_rfc3339_millis(ended),
                duration_ms=duration_ms,
            )
            # A non-zero voice case that still produced tool evidence is a completed-but-imperfect
            # call, not an aborted one -- let the scheduler grade it. Only a failure with no evidence
            # at all is a genuine abort (the receipt's call field must still carry the timing).
            if code != 0 and not calls:
                raise CallAborted(f"voice case exited {code} with no tool evidence", partial=outcome)
            return outcome
        finally:
            _cleanup_adc(adc_path)
