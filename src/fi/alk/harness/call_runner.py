"""The hosted lane's `CallRunner` — places one simulated LiveKit voice call and reports what
happened, satisfying `hosted_scheduler.CallRunner` exactly.

Three sub-systems (world-handle-interface.md, hosted-execution-seams.md v1.15 §2a):

1. **Placing the call.** The customer agent is already running INSIDE the Daytona sandbox, as a
   world process the bundle's provisioner spawned (`process_runtime.py`) and registered with
   LiveKit cloud under `LIVEKIT_AGENT_NAME=agent-w{WORLD_INDEX}`-style identity. This runner never
   starts or manages that process — it drives `SimulationRunner` IN-PROCESS with a directly-built
   `SimulationSpec` that dials the already-registered identity, mirroring `run/sdk_voice.py::
   build_spec` field-for-field but sourcing values from job config and the bundle's own scenario
   document instead of `HARNESS_*` env vars (the local-only webhook/subprocess plumbing
   `run/call.py`/`run/live.py` use is neither available nor appropriate in the guest).
2. **Collecting evidence.** The bundle declares exactly one `runtime.evidence_seam`:
   `http_tool` or `tool_trace`. `http_tool` has NO guest-side capture surface anywhere in this
   repo today (see `_collect_http_tool_calls`'s docstring — a verified finding, not an assumption)
   and is intentionally left returning zero calls rather than inventing a capture proxy.
   `tool_trace` is read from the world's own postgres database against an unpinned, isolated
   convention (see `_collect_tool_trace_calls`'s docstring). Either way, zero calls captured is
   never fabricated into something else — the scheduler's own `evidence_missing` retry-once policy
   is the contract-correct handling for "no evidence."
3. **Uploading artifacts.** The transcript and any produced recordings are uploaded through the
   adapter's `upload_artifact` (content-addressed, budget/level-gated, returns `None` on refusal —
   never an exception) BEFORE this runner returns, so `CallOutcome.transcript_artifact`/
   `recording_artifacts` only ever carry ids the platform has already acked.
"""

from __future__ import annotations

import atexit
import asyncio
import json
import logging
import os
from dataclasses import dataclass
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol

from fi import simulate
from fi.simulate.runtime import (
    AgentEndpointSpec,
    EnvironmentSpec,
    ExecutionPolicy,
    SimulationSpec,
    SimulatorPolicySpec,
    TimeoutPolicy,
    new_run_id,
)
from fi.simulate.runtime.report import SimulationReport
from fi.simulate.runtime.run import TestCaseStatus
from fi.simulate.runtime.runner import SimulationRunner

from .bundle_v2 import EvidenceSeam
from .hosted_scheduler import CallAborted, CallOutcome
from .hosted_scheduler import Scenario as HostedScenario
from .job import HarnessJob
from .outbound import ArtifactKind, format_rfc3339_millis
from .process_runtime import EnvironmentRuntime
from .world.errors import WorldUnavailable
from .world.runtime import Call

logger = logging.getLogger(__name__)

# --- credential aliases / config keys a voice job must carry ---

LIVEKIT_API_KEY_ALIAS = "LIVEKIT_API_KEY"
LIVEKIT_API_SECRET_ALIAS = "LIVEKIT_API_SECRET"
LIVEKIT_URL_ALIAS = "LIVEKIT_URL"
DEEPGRAM_API_KEY_ALIAS = "DEEPGRAM_API_KEY"
GEMINI_API_KEY_ALIAS = "GEMINI_API_KEY"
GOOGLE_API_KEY_ALIAS = "GOOGLE_API_KEY"
GOOGLE_APPLICATION_CREDENTIALS_JSON_ALIAS = "GOOGLE_APPLICATION_CREDENTIALS_JSON"
GOOGLE_APPLICATION_CREDENTIALS_ALIAS = "GOOGLE_APPLICATION_CREDENTIALS"
GOOGLE_CLOUD_PROJECT_ALIAS = "GOOGLE_CLOUD_PROJECT"
GOOGLE_CLOUD_LOCATION_ALIAS = "GOOGLE_CLOUD_LOCATION"
GOOGLE_GENAI_USE_VERTEXAI_ALIAS = "GOOGLE_GENAI_USE_VERTEXAI"
OPENAI_API_KEY_ALIAS = "OPENAI_API_KEY"
SIMULATOR_LLM_PROVIDER_ALIAS = "SIMULATOR_LLM_PROVIDER"
SIMULATOR_LLM_MODEL_ALIAS = "SIMULATOR_LLM_MODEL"
SIMULATOR_STT_PROVIDER_ALIAS = "SIMULATOR_STT_PROVIDER"
SIMULATOR_STT_MODEL_ALIAS = "SIMULATOR_STT_MODEL"
SIMULATOR_TTS_PROVIDER_ALIAS = "SIMULATOR_TTS_PROVIDER"
SIMULATOR_TTS_MODEL_ALIAS = "SIMULATOR_TTS_MODEL"
LIVEKIT_URL_CONFIG_KEY = "livekit_url"
CALL_TIMEOUT_CONFIG_KEY = "voice_call_timeout_seconds"

_DEFAULT_CALL_TIMEOUT_SECONDS = 300.0

# sdk_voice.py::build_spec's own phase-overhead constants, reused verbatim so this runner's
# outer budget composes with the SDK's internal one the same way the local template does.
_CONNECT_TIMEOUT_SECONDS = 60.0
_READINESS_TIMEOUT_SECONDS = 120.0
_CLEANUP_TIMEOUT_SECONDS = 30.0
_RUN_SECONDS_PAD_SECONDS = 60.0
# Headroom beyond `spec.execution.timeout.run_seconds` -- SimulationRunner.run() already wraps
# `plugin.run(...)` in its OWN `asyncio.wait_for(..., timeout=spec.execution.timeout.run_seconds)`
# (runner.py) and catches that TimeoutError into a graceful `SimulationReport(status=TIMED_OUT)`.
# This runner's own outer wait_for must stay LARGER than that so the SDK's internal timeout fires
# first in the ordinary case; it only ever fires itself for a genuinely hung SDK (a real post-dial
# machinery failure) -- a runner-owned asyncio.wait_for as the last-resort bound.
_OUTER_WAIT_FOR_PAD_SECONDS = 60.0

# Unpinned by any contract and no producer exists yet. Isolated as one
# constant + two functions (`_clear_tool_trace_calls`, `_collect_tool_trace_calls`) so a real
# producer's disagreement on the name/shape is a one-line change.
_TOOL_TRACE_TABLE = "_alk_tool_trace"

_RESULT_TRUNCATE_CHARS = 2000

# The real engine's zero-turn "agent joined but never spoke" failure codes (engines/livekit.py::
# _conversation_outcome) -- see `_translate_report`'s `is_silent_agent` gate for why these two, and
# only at zero turns, get mapped to a normal CallOutcome instead of a CallAborted.
_SILENT_AGENT_FAILURE_CODES = frozenset({"no_conversation", "conversation_silence_timeout"})


# --- collaborator seams (named, injectable test boundaries) -----------------------------------


class ArtifactUploader(Protocol):
    """Narrow slice of `hosted_entrypoint.OutboundAdapter` -- avoids importing that module here
    (it imports THIS module's factory to wire the real CallRunner; importing it back would be
    circular)."""

    async def upload_artifact(
        self,
        data: bytes,
        *,
        kind: ArtifactKind,
        scenario_key: str | None = None,
        deadline: float | None = None,
    ) -> str | None: ...


PlaceCall = Callable[[SimulationSpec], Awaitable[SimulationReport]]


async def _default_place_call(spec: SimulationSpec) -> SimulationReport:
    return await SimulationRunner().run(spec)


@dataclass(frozen=True)
class CallRunnerContext:
    """Everything `hosted_entrypoint.py`'s `run_job` already has in scope by the wiring point
    (~1662) that the real `CallRunnerImpl` needs but the bare `CallRunner` protocol signature
    (`run(scenario, runtime)`) has no room to carry. Threaded through the EXTENDED
    `build_call_runner(adapter, context)` seam."""

    job: HarnessJob
    bundle_dir: Path
    work_directory: Path
    evidence_seam: EvidenceSeam | None
    target_provider_secret_values: Mapping[str, str]
    attempt_number: int


# --- pre-dial validation -----------------------------------------------------------------------


@dataclass(frozen=True)
class _MissingVoiceConfig:
    aliases: tuple[str, ...]
    config_keys: tuple[str, ...]

    def message(self) -> str:
        parts = []
        if self.aliases:
            parts.append("secrets=" + ",".join(self.aliases))
        if self.config_keys:
            parts.append("config=" + ",".join(self.config_keys))
        return "voice_capability_unavailable: missing " + "; ".join(parts)


def _check_config(
    job: HarnessJob, target_provider_secret_values: Mapping[str, str]
) -> _MissingVoiceConfig | None:
    config = job.agent.config
    llm_provider = str(
        config.get("simulator_llm_provider")
        or target_provider_secret_values.get(SIMULATOR_LLM_PROVIDER_ALIAS)
        or "google"
    ).lower()
    stt_provider = str(
        config.get("simulator_stt_provider")
        or target_provider_secret_values.get(SIMULATOR_STT_PROVIDER_ALIAS)
        or "deepgram"
    ).lower()
    tts_provider = str(
        config.get("simulator_tts_provider")
        or target_provider_secret_values.get(SIMULATOR_TTS_PROVIDER_ALIAS)
        or "deepgram"
    ).lower()

    required = [LIVEKIT_API_KEY_ALIAS, LIVEKIT_API_SECRET_ALIAS]
    if "deepgram" in {stt_provider, tts_provider}:
        required.append(DEEPGRAM_API_KEY_ALIAS)
    missing_aliases = [
        alias for alias in required if not target_provider_secret_values.get(alias)
    ]

    if llm_provider == "google":
        has_api_key = bool(
            target_provider_secret_values.get(GEMINI_API_KEY_ALIAS)
            or target_provider_secret_values.get(GOOGLE_API_KEY_ALIAS)
        )
        has_vertex_adc = bool(
            target_provider_secret_values.get(GOOGLE_APPLICATION_CREDENTIALS_JSON_ALIAS)
            and target_provider_secret_values.get(GOOGLE_CLOUD_PROJECT_ALIAS)
        )
        if not has_api_key and not has_vertex_adc:
            missing_aliases.append(
                f"{GEMINI_API_KEY_ALIAS}_or_{GOOGLE_API_KEY_ALIAS}_or_VERTEX_ADC"
            )
    elif llm_provider == "openai" and not target_provider_secret_values.get(
        OPENAI_API_KEY_ALIAS
    ):
        missing_aliases.append(OPENAI_API_KEY_ALIAS)

    has_livekit_url = bool(
        config.get(LIVEKIT_URL_CONFIG_KEY)
        or target_provider_secret_values.get(LIVEKIT_URL_ALIAS)
    )
    missing_config_keys = [] if has_livekit_url else [LIVEKIT_URL_CONFIG_KEY]
    if not missing_aliases and not missing_config_keys:
        return None
    return _MissingVoiceConfig(tuple(missing_aliases), tuple(missing_config_keys))


def _dispatch_agent_name(runtime: EnvironmentRuntime) -> str | None:
    """The ONLY place this repo reads the dispatch-identity metadata key, so a
    change to the key name/convention is a one-line adapt. `EnvironmentRuntime.metadata` defaults to `{}` and nothing
    in `process_runtime.py` populates it yet -- every real "livekit" job
    hits the caller's typed `CallAborted` below until a producer lands."""
    value = runtime.metadata.get("livekit_agent_name")
    return value.strip() if isinstance(value, str) and value.strip() else None


# --- scenario document re-read (the _CompiledScenario the scheduler hands over carries no
# persona/instruction -- scenario_source.py:170-184's deliberately narrow Scenario-protocol
# shape) ------------------------------------------------------------------------------------


class _ScenarioDocumentUnavailable(RuntimeError):
    pass


def _read_scenario_document(bundle_dir: Path, scenario_key: str) -> dict[str, Any]:
    """Re-reads `scenarios/<folder>/scenario.json` from the bundle, matched by the document's OWN
    `scenario_key` field -- never the folder name (`scenario_source.py`'s own convention; the two
    are not guaranteed to match)."""
    root = bundle_dir / "scenarios"
    if not root.is_dir():
        raise _ScenarioDocumentUnavailable(f"no {root} directory in this bundle")
    try:
        children = sorted(root.iterdir())
    except OSError as exc:
        raise _ScenarioDocumentUnavailable(f"cannot list {root}: {exc}") from exc
    for child in children:
        if not child.is_dir():
            continue
        doc_path = child / "scenario.json"
        if not doc_path.is_file():
            continue
        try:
            body = json.loads(doc_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(body, dict) and body.get("scenario_key") == scenario_key:
            instruction = body.get("instruction")
            if not isinstance(instruction, str) or not instruction.strip():
                raise _ScenarioDocumentUnavailable(
                    f"{child.name}/scenario.json has no non-empty instruction"
                )
            return body
    raise _ScenarioDocumentUnavailable(
        f"no scenario.json under {root} carries scenario_key={scenario_key!r}"
    )


# --- deterministic room naming (asserted verbatim by tests/harness/test_call_runner.py). WHY this
# is a PREFIX guarantee, not a full-match one: in managed room_mode, engines/livekit.py::
# _resolve_room_name appends its own `-{invocation_id}-{test_case_id[-12:]}` suffix unless
# `room_name_verbatim` is set (which this runner does not set) -- the scheme below still gives
# every call a unique, deterministic, greppable prefix; only the exact wire-level name is not this
# string verbatim. -------------------------------------------------------------------------------


def _room_name(
    *, job_id: str, attempt_number: int, scenario_key: str, scenario_attempt: int
) -> str:
    return f"harness-{job_id[:8]}-a{attempt_number}-{scenario_key}-s{scenario_attempt}"


def _duration_ms(started_at: datetime, ended_at: datetime) -> int:
    return max(0, int((ended_at - started_at).total_seconds() * 1000))


# --- SimulationSpec construction (mirrors run/sdk_voice.py::build_spec field-for-field; values
# come from job config / the re-read scenario document instead of HARNESS_* env vars) ---------


def _simulator_definition(
    config: Mapping[str, Any],
    environ: Mapping[str, str],
) -> Any:
    """Build the caller from provider-neutral job configuration.

    Lowercase job config wins; provider environment aliases are accepted for compatibility.
    Defaults match the shipped voice runner, but no target-agent or domain-specific behavior is
    encoded here.
    """
    llm_provider = str(
        config.get("simulator_llm_provider")
        or environ.get("SIMULATOR_LLM_PROVIDER")
        or "google"
    )
    stt_provider = str(
        config.get("simulator_stt_provider")
        or environ.get("SIMULATOR_STT_PROVIDER")
        or "deepgram"
    )
    tts_provider = str(
        config.get("simulator_tts_provider")
        or environ.get("SIMULATOR_TTS_PROVIDER")
        or "deepgram"
    )
    default_models = {
        "llm": {"google": "gemini-2.5-flash-lite", "openai": "gpt-4o-mini"},
        "stt": {"deepgram": "nova-2", "google": "chirp_2"},
        "tts": {"deepgram": "aura-asteria-en", "google": "en-US-Chirp3-HD-Aoede"},
    }
    llm_model = str(
        config.get("simulator_llm_model")
        or environ.get("SIMULATOR_LLM_MODEL")
        or default_models["llm"].get(llm_provider, "")
    )
    stt_model = str(
        config.get("simulator_stt_model")
        or environ.get("SIMULATOR_STT_MODEL")
        or default_models["stt"].get(stt_provider, "")
    )
    tts_model = str(
        config.get("simulator_tts_model")
        or environ.get("SIMULATOR_TTS_MODEL")
        or default_models["tts"].get(tts_provider, "")
    )
    return simulate.SimulatorAgentDefinition(
        llm={"provider": llm_provider, "model": llm_model, "temperature": 0.35},
        stt={"provider": stt_provider, "model": stt_model, "language": "en"},
        tts={"provider": tts_provider, "model": tts_model, "voice": tts_model},
        instructions=(
            "Act as the customer described by the scenario. Speak naturally and briefly. "
            "Use only the supplied facts and never invent account, address, payment, or "
            "verification data. Do not volunteer private data: agree when asked whether a "
            "verification code should be sent, and disclose the actual code only after the "
            "agent says it was sent and explicitly asks you to read it. Answer repair questions "
            "with the missing fact, not by restarting the request. Never repeat the same answer "
            "more than twice. When the requested outcome is complete, thank the agent and end "
            "the call."
        ),
        allow_interruptions=True,
    )


def _scenario_spec(doc: Mapping[str, Any]) -> Any:
    """Mirrors `sdk_voice.py::_scenario()`'s own transformation exactly, sourced from the re-read
    scenario document instead of `HARNESS_*` env vars."""
    fixture = doc.get("fixture") if isinstance(doc.get("fixture"), dict) else {}
    persona = dict(doc.get("persona") or {})
    persona["role"] = "customer"
    metadata = dict(persona.get("metadata") or {})
    if fixture.get("phone"):
        metadata["caller_phone"] = str(fixture["phone"])
    persona["metadata"] = metadata
    knowledge = [
        {
            "key": str(key),
            "value": json.dumps(value, ensure_ascii=False, default=str),
            "disclosure": "on_request",
        }
        for key, value in fixture.items()
        if key != "origin"
    ]
    persona_model = simulate.Persona(
        persona=persona,
        situation=doc["instruction"],
        outcome=(doc.get("tests") or "Complete the requested task and close naturally."),
        knowledge=knowledge,
        behavior_policy={
            "disclosure_policy": 0.72,
            "cooperation_bounds": 0.9,
            "repair_propensity": 0.85,
        },
    )
    return simulate.Scenario(
        name=str(doc.get("scenario_key") or doc.get("name") or "harness-voice"),
        dataset=[persona_model],
    )


def _build_spec(
    *,
    run_id: str,
    room_name: str,
    agent_name: str,
    doc: Mapping[str, Any],
    livekit_url: str,
    call_timeout_seconds: float,
    run_seconds: float,
    recordings_root: Path,
    simulator_config: Mapping[str, Any],
    environ: Mapping[str, str],
) -> SimulationSpec:
    recording_dir = recordings_root / run_id / "recordings"
    params = {
        "record_audio": True,
        "recording_root": str(recording_dir),
        "recording_case_directory": str(recording_dir),
        "min_turn_messages": 6,
        "max_seconds": call_timeout_seconds,
        "connect_timeout": _CONNECT_TIMEOUT_SECONDS,
        "readiness_timeout": _READINESS_TIMEOUT_SECONDS,
        "cleanup_timeout": _CLEANUP_TIMEOUT_SECONDS,
        "conversation_direction": "agent_first",
        "agent_first_silence_timeout_seconds": 45.0,
    }
    agent = simulate.AgentDefinition(
        name="harness-livekit-target",
        agent_name=agent_name,
        system_prompt=doc["instruction"],
        transport={"kind": "webrtc"},
    )
    runtime_spec = simulate.LiveKitSimulatorRuntime(
        url=livekit_url, room_name=room_name, room_mode="managed",
    )
    return SimulationSpec(
        run_id=run_id,
        environment=EnvironmentSpec(
            adapter="voice",
            world_kind="voice_telephony",
            config={
                "agent_definition": agent.model_dump(mode="json", exclude_none=True),
                "livekit_runtime": runtime_spec.model_dump(mode="json", exclude_none=True),
                "simulator": _simulator_definition(
                    simulator_config, environ
                ).model_dump(mode="json", exclude_none=True),
                "params": params,
            },
        ),
        target=AgentEndpointSpec(adapter="webrtc"),
        simulator=SimulatorPolicySpec(adapter="livekit_simulator"),
        scenario=_scenario_spec(doc),
        execution=ExecutionPolicy(
            direction="agent_first", timeout=TimeoutPolicy(run_seconds=run_seconds)
        ),
    )


# --- evidence collection -----------------------------------------------------------------------


def _find_postgres_endpoint(runtime: EnvironmentRuntime) -> Any | None:
    """Protocol-based lookup, matching `hosted_entrypoint.py::_find_postgres_endpoint`'s own
    already-correct convention -- capability slugs are bundle-author-chosen (`build_endpoints`,
    process_runtime.py:318-339), never a fixed key, so a hardcoded `endpoints["database"]` would
    break for any bundle that names its capability slug differently. Re-implemented locally rather
    than imported: importing from `hosted_entrypoint.py` here would be circular (it imports this
    module's factory)."""
    for endpoint in runtime.endpoints.values():
        if endpoint.protocol == "postgres":
            return endpoint
    return None


def _collect_http_tool_calls(runtime: EnvironmentRuntime) -> tuple[Call, ...]:
    """No guest-side capture surface exists anywhere in this repo for the
    `http_tool` evidence seam. Verified, not assumed: `world/handle.py::HostedWorld.call()`
    raises `WorldUnavailable` unconditionally with a docstring stating the wire format "is not
    pinned anywhere in the contracts yet"; `process_runtime.py`'s own `provision()` signature
    comment says "evidence-seam wiring is out of this phase's scope"; no `TOOLS_API_URL` wiring
    exists in the hosted lane at all (the local lane's `ProvisionedWorld`/`TOOLS_API_URL` mechanism
    lives in `provision.py`/`world/provisioned.py`, out of scope here and inapplicable to the guest
    regardless). Deliberately stopped rather than inventing a capture proxy: a job whose bundle
    declares `evidence_seam: http_tool` reads zero calls every time, which the scheduler's own
    `evidence_missing` retry-once policy turns into the correct, honest outcome -- never a crash,
    never fabricated evidence."""
    del runtime
    return ()


def _clear_tool_trace_calls(dsn: str) -> None:
    """world-handle-interface.md: "setup's tool calls are NOT evidence (the runner clears them
    before the call starts, as the local runner does)" -- the local runner's analog is
    `world.calls = []` right before dialing (`run/simulation.py`). Best-effort: a missing table (no
    producer yet) or any connection error is swallowed, never raised. Clearing
    is housekeeping, not a correctness requirement, while nothing writes this table yet; once a
    real producer lands this stops being a no-op automatically."""
    try:
        import psycopg

        with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as connection:
            connection.execute(f'DELETE FROM "{_TOOL_TRACE_TABLE}"')  # noqa: S608 - fixed identifier, no interpolated user input
    except Exception as exc:  # noqa: BLE001 - best-effort housekeeping only, never a call-blocking failure
        # WHY: never log exc_info / str(exc) here -- a psycopg connection failure embeds the raw
        # DSN (including the world DB password) in its own exception message; only the exception
        # TYPE is safe for a local log line.
        logger.debug("tool_trace clear skipped (table likely absent): %s", type(exc).__name__)


def _collect_tool_trace_calls(runtime: EnvironmentRuntime) -> tuple[Call, ...]:
    """`_alk_tool_trace`'s name and column shape are an isolated local
    convention -- unpinned by any contract (the only harness-reserved table anywhere in this
    repo is `_alk_conformance`, unrelated), no producer exists yet. Isolated in this one function
    (+ `_clear_tool_trace_calls`) so a real producer's disagreement on the name/shape is a one-line
    change. Any failure (missing table, connection refused, malformed row) degrades to `()` --
    never a crash, never fabricated evidence, matching `_collect_http_tool_calls`'s stopped
    behavior above."""
    endpoint = _find_postgres_endpoint(runtime)
    if endpoint is None:
        return ()
    try:
        import psycopg

        with psycopg.connect(
            endpoint.address,
            autocommit=True,
            connect_timeout=5,
            options="-c default_transaction_read_only=on",
        ) as connection:
            cursor = connection.execute(
                f'SELECT name, arguments, result, ok, error, at FROM "{_TOOL_TRACE_TABLE}" '  # noqa: S608
                "ORDER BY at ASC"
            )
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description or []]
    except Exception as exc:  # noqa: BLE001 - missing table / connection failure -> no evidence, not a crash
        # WHY: same DSN-in-exception-message risk as `_clear_tool_trace_calls` above -- log only
        # the exception TYPE, never exc_info/str(exc), which can carry the world DB password.
        logger.debug("tool_trace read failed; treating as no evidence: %s", type(exc).__name__)
        return ()

    calls: list[Call] = []
    for row in rows:
        record = dict(zip(columns, row, strict=True))
        name = record.get("name")
        if not isinstance(name, str) or not name:
            continue
        arguments = record.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        ok = bool(record.get("ok", True))
        raw_result = record.get("result")
        if isinstance(raw_result, str):
            result: Any = _truncate(raw_result)
        else:
            # Already parsed JSON (dict/list/etc, psycopg's own jsonb decoding) -- per
            # world-handle-interface.md, only the STRING form is truncated at 2000 chars.
            result = raw_result
        error = _truncate(str(record.get("error") or ""))
        raw_at = record.get("at")
        at = float(raw_at) if isinstance(raw_at, (int, float)) else 0.0
        calls.append(
            Call(name=name, arguments=arguments, result=result, ok=ok, error=error, refused=not ok, at=at)
        )
    return tuple(calls)


def _truncate(value: str, *, limit: int = _RESULT_TRUNCATE_CHARS) -> str:
    return value if len(value) <= limit else value[:limit]

def _materialize_vertex_adc(
    secret_values: Mapping[str, str],
    work_directory: Path,
    environ: dict[str, str],
) -> Path | None:
    """Materialize caller-lane Vertex credentials for Google ADC.

    API-key auth needs no file. For Vertex, GOOGLE_APPLICATION_CREDENTIALS_JSON is resolved from
    the platform vault and written mode-0600 under the job work directory; the sandbox is
    ephemeral and the file is removed when the guest exits/deletes.
    """
    raw = secret_values.get(GOOGLE_APPLICATION_CREDENTIALS_JSON_ALIAS)
    if not raw or environ.get(GOOGLE_APPLICATION_CREDENTIALS_ALIAS):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CallAborted(
            "voice_capability_unavailable: GOOGLE_APPLICATION_CREDENTIALS_JSON is invalid"
        ) from exc
    if not isinstance(parsed, dict):
        raise CallAborted(
            "voice_capability_unavailable: GOOGLE_APPLICATION_CREDENTIALS_JSON must be an object"
        )
    credential_dir = work_directory / ".caller-credentials"
    credential_dir.mkdir(parents=True, exist_ok=True)
    fd, path_text = tempfile.mkstemp(prefix="google-", suffix=".json", dir=credential_dir)
    path = Path(path_text)
    try:
        os.write(fd, raw.encode("utf-8"))
    finally:
        os.close(fd)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    environ[GOOGLE_APPLICATION_CREDENTIALS_ALIAS] = str(path)
    return path


# --- the runner ----------------------------------------------------------------------------


class CallRunnerImpl:
    """Satisfies `hosted_scheduler.CallRunner`. See the module docstring for the three
    sub-systems this class implements."""

    def __init__(
        self,
        adapter: ArtifactUploader,
        context: CallRunnerContext,
        *,
        place_call: PlaceCall | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        self._adapter = adapter
        self._context = context
        self._place_call = place_call or _default_place_call
        # WHY: the underlying LiveKit engine reads these directly via `os.environ.get(...)` deep
        # inside `engines/livekit.py` / `livekit_models.py` -- they are NOT `SimulationSpec`
        # fields, so there is no other way to hand them over. Exported ONCE here, at construction,
        # not per-call: the values are job-level (the same secret for every scenario/attempt on
        # this job) and W>1 means each world's CallRunner.run() executes inside this SAME guest
        # process but against a per-world sandboxed agent process reached over the network; no
        # other in-process worker races this job-level environment.
        target_environ = os.environ if environ is None else environ
        for alias in (
            LIVEKIT_API_KEY_ALIAS,
            LIVEKIT_API_SECRET_ALIAS,
            LIVEKIT_URL_ALIAS,
            DEEPGRAM_API_KEY_ALIAS,
            GEMINI_API_KEY_ALIAS,
            GOOGLE_API_KEY_ALIAS,
            GOOGLE_APPLICATION_CREDENTIALS_JSON_ALIAS,
            GOOGLE_CLOUD_PROJECT_ALIAS,
            GOOGLE_CLOUD_LOCATION_ALIAS,
            GOOGLE_GENAI_USE_VERTEXAI_ALIAS,
            OPENAI_API_KEY_ALIAS,
            SIMULATOR_LLM_PROVIDER_ALIAS,
            SIMULATOR_LLM_MODEL_ALIAS,
            SIMULATOR_STT_PROVIDER_ALIAS,
            SIMULATOR_STT_MODEL_ALIAS,
            SIMULATOR_TTS_PROVIDER_ALIAS,
            SIMULATOR_TTS_MODEL_ALIAS,
        ):
            value = context.target_provider_secret_values.get(alias)
            if value:
                target_environ[alias] = value
        self._environ = target_environ
        self._adc_path = _materialize_vertex_adc(
            context.target_provider_secret_values,
            context.work_directory,
            target_environ,
        )
        atexit.register(self._cleanup_credentials)
        self._livekit_url = str(
            context.job.agent.config.get(LIVEKIT_URL_CONFIG_KEY)
            or context.target_provider_secret_values.get(LIVEKIT_URL_ALIAS)
            or ""
        )
        self._missing_config = _check_config(context.job, context.target_provider_secret_values)
        self._scenario_attempt_counts: dict[str, int] = {}

    def _cleanup_credentials(self) -> None:
        if self._adc_path is None:
            return
        try:
            self._adc_path.unlink(missing_ok=True)
        except OSError:
            pass
        if self._environ.get(GOOGLE_APPLICATION_CREDENTIALS_ALIAS) == str(self._adc_path):
            self._environ.pop(GOOGLE_APPLICATION_CREDENTIALS_ALIAS, None)
        self._adc_path = None

    async def run(self, scenario: HostedScenario, runtime: EnvironmentRuntime) -> CallOutcome:
        if self._missing_config is not None:
            # Pre-dial: dialing never starts, so no partial -- and never `WorldUnavailable` (that
            # code is reserved by the contract for a world-level capability mismatch, not a
            # job-level voice config gap).
            raise CallAborted(self._missing_config.message())

        agent_name = _dispatch_agent_name(runtime)
        if agent_name is None:
            raise CallAborted(
                "voice_dispatch_identity_unavailable: runtime.metadata['livekit_agent_name'] is "
                f"not set for world {runtime.world_index}"
            )

        try:
            doc = _read_scenario_document(self._context.bundle_dir, scenario.scenario_key)
        except _ScenarioDocumentUnavailable as exc:
            raise CallAborted(f"voice_scenario_document_unavailable: {exc}") from exc

        scenario_attempt = self._scenario_attempt_counts.get(scenario.scenario_key, 0) + 1
        self._scenario_attempt_counts[scenario.scenario_key] = scenario_attempt
        room_name = _room_name(
            job_id=self._context.job.job_id,
            attempt_number=self._context.attempt_number,
            scenario_key=scenario.scenario_key,
            scenario_attempt=scenario_attempt,
        )

        raw_timeout = self._context.job.agent.config.get(CALL_TIMEOUT_CONFIG_KEY)
        call_timeout_seconds = (
            float(raw_timeout) if isinstance(raw_timeout, (int, float)) else _DEFAULT_CALL_TIMEOUT_SECONDS
        )
        run_seconds = (
            call_timeout_seconds
            + _CONNECT_TIMEOUT_SECONDS
            + _READINESS_TIMEOUT_SECONDS
            + _CLEANUP_TIMEOUT_SECONDS
            + _RUN_SECONDS_PAD_SECONDS
        )

        spec = _build_spec(
            run_id=new_run_id(),
            room_name=room_name,
            agent_name=agent_name,
            doc=doc,
            simulator_config=self._context.job.agent.config,
            environ=self._environ,
            livekit_url=self._livekit_url,
            call_timeout_seconds=call_timeout_seconds,
            run_seconds=run_seconds,
            recordings_root=self._context.work_directory / "voice-calls",
        )

        if self._context.evidence_seam is EvidenceSeam.TOOL_TRACE:
            endpoint = _find_postgres_endpoint(runtime)
            if endpoint is not None:
                _clear_tool_trace_calls(endpoint.address)

        started_at = datetime.now(timezone.utc)
        outer_timeout = run_seconds + _OUTER_WAIT_FOR_PAD_SECONDS
        try:
            report = await asyncio.wait_for(self._place_call(spec), timeout=outer_timeout)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError as exc:
            raise CallAborted(
                "voice_call_runner_timeout: place_call exceeded its outer budget "
                f"({outer_timeout:.0f}s)",
                partial=self._timing_only_outcome(started_at),
            ) from exc
        except Exception as exc:  # noqa: BLE001 - post-dial machinery failure, never let it escape raw
            raise CallAborted(
                f"voice_call_runner_crashed: {type(exc).__name__}: {exc}",
                partial=self._timing_only_outcome(started_at),
            ) from exc

        try:
            return await self._translate_report(
                report, runtime=runtime, scenario_key=scenario.scenario_key, started_at=started_at
            )
        except (CallAborted, WorldUnavailable):
            # `_translate_report`'s own typed control-flow (non-completed status, no test case,
            # agent-never-joined) -- never re-wrap an intentional abort.
            raise
        except Exception as exc:  # noqa: BLE001 - a transcript/recording read or upload surprise
            # must never lose the timing this call already measured (the receipt's `call` field
            # must not be null once the call has genuinely started) by escaping run() raw.
            raise CallAborted(
                f"voice_call_translate_crashed: {type(exc).__name__}: {exc}",
                partial=self._timing_only_outcome(started_at),
            ) from exc

    def _timing_only_outcome(self, started_at: datetime) -> CallOutcome:
        ended_at = datetime.now(timezone.utc)
        return CallOutcome(
            calls=(),
            turns=0,
            started_at=format_rfc3339_millis(started_at),
            ended_at=format_rfc3339_millis(ended_at),
            duration_ms=_duration_ms(started_at, ended_at),
        )

    async def _translate_report(
        self,
        report: SimulationReport,
        *,
        runtime: EnvironmentRuntime,
        scenario_key: str,
        started_at: datetime,
    ) -> CallOutcome:
        case = report.test_cases[0] if report.test_cases else None
        case_started_at = (
            case.started_at if case is not None and case.started_at is not None else started_at
        )
        ended_at = (
            case.ended_at
            if case is not None and case.ended_at is not None
            else datetime.now(timezone.utc)
        )
        turns = len(case.result.messages) if case is not None and case.result is not None else 0

        transcript_artifact: str | None = None
        recording_artifacts: list[str] = []
        if case is not None and case.result is not None:
            result = case.result
            if result.transcript:
                transcript_artifact = await self._adapter.upload_artifact(
                    result.transcript.encode("utf-8"),
                    kind=ArtifactKind.TRANSCRIPT,
                    scenario_key=scenario_key,
                )
            for path_str, kind in (
                (result.audio_combined_path, ArtifactKind.RECORDING_COMBINED),
                (result.audio_stereo_path, ArtifactKind.RECORDING_STEREO),
                (result.audio_input_path, ArtifactKind.RECORDING_CUSTOMER),
                (result.audio_output_path, ArtifactKind.RECORDING_ASSISTANT),
            ):
                if not path_str:
                    continue
                path = Path(path_str)
                if not path.is_file():
                    continue
                artifact_id = await self._adapter.upload_artifact(
                    path.read_bytes(), kind=kind, scenario_key=scenario_key,
                )
                if artifact_id is not None:
                    recording_artifacts.append(artifact_id)

        base = CallOutcome(
            calls=(),
            turns=turns,
            started_at=format_rfc3339_millis(case_started_at),
            ended_at=format_rfc3339_millis(ended_at),
            duration_ms=_duration_ms(case_started_at, ended_at),
            transcript_artifact=transcript_artifact,
            recording_artifacts=tuple(recording_artifacts),
        )

        if case is None:
            raise CallAborted("voice_call_no_test_case: SimulationReport carried no test case", partial=base)

        if case.status is TestCaseStatus.AGENT_UNAVAILABLE:
            # world-handle-interface.md: "the agent never joined" is a WORLD failure, not a
            # scenario one -- the agent is part of the world, so the scheduler retires it and
            # retries elsewhere. Verified against the engine's own source (engines/livekit.py):
            # this status fires ONLY on a readiness-stage timeout with a session already started
            # but no target dispatched -- exactly "dispatch fails, agent never joins," never a
            # mid-call condition.
            reason = case.failure.message if case.failure is not None else "agent_unavailable"
            raise WorldUnavailable(f"target agent never joined the room: {reason}")

        # A genuinely silent agent-first call (agent joined, zero conversational turns) reaches
        # the real engine (engines/livekit.py::_conversation_outcome) as FAILED with code
        # "no_conversation" or "conversation_silence_timeout" and zero messages -- never as a
        # COMPLETED case with zero turns (COMPLETED requires >= min_turn_messages AND role
        # alternation, so the engine cannot produce that shape). Scoped to zero turns only: a
        # short-but-nonzero conversation on either code still failed the completion bar for a real
        # reason and must stay a CallAborted below.
        is_silent_agent = (
            case.status is TestCaseStatus.FAILED
            and turns == 0
            and case.failure is not None
            and case.failure.code in _SILENT_AGENT_FAILURE_CODES
        )

        if case.status is not TestCaseStatus.COMPLETED and not is_silent_agent:
            reason = case.failure.message if case.failure is not None else case.status.value
            raise CallAborted(f"voice_call_not_completed: {case.status.value}: {reason}", partial=base)

        # Never fabricate calls for a call that produced no conversation -- the scheduler's own
        # coverage guarantee turns an empty `calls` tuple into evidence_missing/simulator
        # regardless of turns (hosted_scheduler.py's own unconditioned-on-turns rule).
        calls = () if is_silent_agent else self._collect_calls(runtime)
        return CallOutcome(
            calls=calls,
            turns=base.turns,
            started_at=base.started_at,
            ended_at=base.ended_at,
            duration_ms=base.duration_ms,
            transcript_artifact=base.transcript_artifact,
            recording_artifacts=base.recording_artifacts,
        )

    def _collect_calls(self, runtime: EnvironmentRuntime) -> tuple[Call, ...]:
        seam = self._context.evidence_seam
        if seam is EvidenceSeam.HTTP_TOOL:
            return _collect_http_tool_calls(runtime)
        if seam is EvidenceSeam.TOOL_TRACE:
            return _collect_tool_trace_calls(runtime)
        # Unrecognized/None (should not happen for a `kind: process` bundle past preflight --
        # bundle_v2.py requires `evidence_seam` whenever `kind is PROCESS` -- but degrading rather
        # than crashing keeps this on the scheduler's own evidence_missing path, never a raw
        # exception).
        return ()
