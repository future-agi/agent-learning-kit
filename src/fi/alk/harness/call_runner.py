"""The hosted lane's `CallRunner` — places one simulated LiveKit voice call and reports what
happened, satisfying `hosted_scheduler.CallRunner` exactly.

Three sub-systems (world-handle-interface.md, hosted-execution-seams.md v1.15 §2a):

1. **Placing the call.** The customer agent is already running INSIDE the Daytona sandbox, as a
   world process the bundle's provisioner spawned (`process_runtime.py`) and registered with
   LiveKit cloud under `LIVEKIT_AGENT_NAME=agent-w{WORLD_INDEX}`-style identity. This runner never
   starts or manages that process. It drives `SimulationRunner` in a supervised subprocess with a
   `SimulationSpec` built by `simulator_voice.simulation_spec`, the same builder the local lane
   uses; only the value lookup differs, resolving from job config and the bundle's scenario
   document rather than `HARNESS_*` env vars. Do not rebuild the spec here: the two lanes drifted
   for exactly that reason. The local-only webhook/subprocess plumbing `run/call.py` and
   `run/live.py` use is neither available nor appropriate in the guest.
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

import ast
import atexit
import asyncio
import contextlib
import dataclasses
import gc
import json
import random
import time
import logging
import os
import stat
import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

from fi import agentcc, simulate
from fi.simulate.runtime import (
    SimulationSpec,
    new_run_id,
)
from fi.simulate.results.futureagi import target_provider_usage
from fi.simulate.runtime.report import SimulationReport
from fi.simulate.runtime.run import TestCaseStatus
from fi.simulate.runtime.runner import SimulationRunner
from fi.simulate.simulation.call_actors import (
    CHANNELS,
    ACTOR_VOICES_ALIAS,
    ACTOR_WORLD_ALIAS,
    known_to_others,
    loudness_ratio,
    with_others,
)

from .actor_dispatch import ActorDispatcher, DoorActs, build_dispatcher, world_summary
from .actor_meter import ActorMeter
from .actors import (
    AGENT,
    ScenarioActor,
    acting_fields,
    is_pre_run,
    actor_brief,
    brief_text,
    load_actors,
    with_caller_actor,
    with_knowledge_read,
)
from .background_noise import place_of, scenario_source
from .bundle_v2 import EvidenceSeam
from .call_actor import (
    load_call_actors,
    speaking_entries,
    logged_tool_name,
)
from .hosted_scheduler import CallAborted, CallOutcome
from .hosted_scheduler import Scenario as HostedScenario
from .job import ExecutionMode, FailureDomain, HarnessJob, ProviderExecutionMode
from .isolated_process import run_json_worker
from .outbound import ArtifactKind, format_rfc3339_millis
from .process_runtime import EnvironmentRuntime, _allowlisted_ambient_env
from .usage import (
    UsageDenied,
    UsageOutcome,
    UsageReporter,
    UsageUnavailable,
    failure_domain_for_code,
    simulator_funding,
)
from .scenario import DEFAULT_VOICEMAIL_STYLE, voicemail_enabled
from .voicemail_audio import clip_for
from .simulator_voice import (
    CLEANUP_TIMEOUT_SECONDS,
    CONNECT_TIMEOUT_SECONDS,
    READINESS_TIMEOUT_SECONDS,
    SIMULATOR_MODEL_ENV,
    caller_scenario,
    persona_emotion,
    persona_speech_rate,
    persona_voice,
    simulation_spec,
    simulator_definition,
    voice_providers,
)
from .world.errors import WorldUnavailable
from .world.runtime import Call, graded_calls, runtime_ids, tool_call_attribution, trace_row

logger = logging.getLogger(__name__)

# --- credential aliases / config keys a voice job must carry ---

LIVEKIT_API_KEY_ALIAS = "LIVEKIT_API_KEY"
LIVEKIT_API_SECRET_ALIAS = "LIVEKIT_API_SECRET"
LIVEKIT_URL_ALIAS = "LIVEKIT_URL"
DEEPGRAM_API_KEY_ALIAS = "DEEPGRAM_API_KEY"
CARTESIA_API_KEY_ALIAS = "CARTESIA_API_KEY"
GEMINI_API_KEY_ALIAS = "GEMINI_API_KEY"
GOOGLE_API_KEY_ALIAS = "GOOGLE_API_KEY"
GOOGLE_APPLICATION_CREDENTIALS_JSON_ALIAS = "GOOGLE_APPLICATION_CREDENTIALS_JSON"
GOOGLE_APPLICATION_CREDENTIALS_ALIAS = "GOOGLE_APPLICATION_CREDENTIALS"
GOOGLE_CLOUD_PROJECT_ALIAS = "GOOGLE_CLOUD_PROJECT"
GOOGLE_CLOUD_LOCATION_ALIAS = "GOOGLE_CLOUD_LOCATION"
GOOGLE_GENAI_USE_VERTEXAI_ALIAS = "GOOGLE_GENAI_USE_VERTEXAI"
OPENAI_API_KEY_ALIAS = "OPENAI_API_KEY"
VAPI_API_KEY_ALIAS = "VAPI_API_KEY"
VAPI_PUBLIC_API_KEY_ALIAS = "VAPI_PUBLIC_API_KEY"
RETELL_API_KEY_ALIAS = "RETELL_API_KEY"
SIP_OUTBOUND_TRUNK_ID_ALIAS = "SIP_OUTBOUND_TRUNK_ID"
SIP_OUTBOUND_FROM_NUMBER_ALIAS = "SIP_OUTBOUND_FROM_NUMBER"
SIMULATOR_LLM_PROVIDER_ALIAS = "SIMULATOR_LLM_PROVIDER"
SIMULATOR_LLM_MODEL_ALIAS = "SIMULATOR_LLM_MODEL"
SIMULATOR_STT_PROVIDER_ALIAS = "SIMULATOR_STT_PROVIDER"
SIMULATOR_STT_MODEL_ALIAS = "SIMULATOR_STT_MODEL"
SIMULATOR_TTS_PROVIDER_ALIAS = "SIMULATOR_TTS_PROVIDER"
SIMULATOR_TTS_MODEL_ALIAS = "SIMULATOR_TTS_MODEL"
BACKGROUND_NOISE_ALIAS = "ALK_BACKGROUND_NOISE"
BACKGROUND_NOISE_CATALOG_ALIAS = "ALK_BACKGROUND_NOISE_CATALOG"
BACKGROUND_NOISE_VOLUME_ALIAS = "HARNESS_BACKGROUND_NOISE_VOLUME"
CALLER_BARGE_IN_RATE_ALIAS = "HARNESS_CALLER_BARGE_IN_RATE"
CALL_DIRECTION_ALIAS = "ALK_CALL_DIRECTION"
AGENT_LANGUAGES_ALIAS = "ALK_AGENT_LANGUAGES"
VOICEMAIL_CLIP_ALIAS = "HARNESS_VOICEMAIL_CLIP"
VOICEMAIL_CLIP_TONE_ALIAS = "HARNESS_VOICEMAIL_CLIP_HAS_TONE"
VOICEMAIL_CLIP_TEXT_ALIAS = "HARNESS_VOICEMAIL_CLIP_TRANSCRIPT"
LIVEKIT_URL_CONFIG_KEY = "livekit_url"
CALL_TIMEOUT_CONFIG_KEY = "voice_call_timeout_seconds"

_SIMULATOR_PLATFORM_ALIAS_MAP = {
    "SIMULATOR_LIVEKIT_URL": LIVEKIT_URL_ALIAS,
    "SIMULATOR_LIVEKIT_API_KEY": LIVEKIT_API_KEY_ALIAS,
    "SIMULATOR_LIVEKIT_API_SECRET": LIVEKIT_API_SECRET_ALIAS,
    "SIMULATOR_DEEPGRAM_API_KEY": DEEPGRAM_API_KEY_ALIAS,
    "SIMULATOR_CARTESIA_API_KEY": CARTESIA_API_KEY_ALIAS,
    "SIMULATOR_GEMINI_API_KEY": GEMINI_API_KEY_ALIAS,
    "SIMULATOR_GOOGLE_API_KEY": GOOGLE_API_KEY_ALIAS,
    "SIMULATOR_GOOGLE_APPLICATION_CREDENTIALS_JSON": (
        GOOGLE_APPLICATION_CREDENTIALS_JSON_ALIAS
    ),
    "SIMULATOR_GOOGLE_CLOUD_PROJECT": GOOGLE_CLOUD_PROJECT_ALIAS,
    "SIMULATOR_GOOGLE_CLOUD_LOCATION": GOOGLE_CLOUD_LOCATION_ALIAS,
    "SIMULATOR_GOOGLE_GENAI_USE_VERTEXAI": GOOGLE_GENAI_USE_VERTEXAI_ALIAS,
    "SIMULATOR_OPENAI_API_KEY": OPENAI_API_KEY_ALIAS,
}

_DEFAULT_CALL_TIMEOUT_SECONDS = 480.0

# sdk_voice.py::build_spec's own phase-overhead constants, reused verbatim so this runner's
# outer budget composes with the SDK's internal one the same way the local template does.
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

# Turns a timed-out call needs before it is worth grading rather than aborting. Low on purpose: the
# question is only whether a conversation happened at all.
_GRADEABLE_AFTER_TIMEOUT_TURNS = 4

# The real engine's zero-turn "agent joined but never spoke" failure codes (engines/livekit.py::
# _conversation_outcome) -- see `_translate_report`'s `is_silent_agent` gate for why these two, and
# only at zero turns, get mapped to a normal CallOutcome instead of a CallAborted.
_SILENT_AGENT_FAILURE_CODES = frozenset(
    {"no_conversation", "conversation_silence_timeout"}
)
_CONVERSATION_STALL_FAILURE_CODES = frozenset(
    {"conversation_silence_timeout", "conversation_stalled"}
)


def _attributed_stall(case: Any) -> tuple[str, str] | None:
    """Attribute a speech stall from committed transcript turns, without guessing."""
    if (
        case.failure is None
        or case.failure.code not in _CONVERSATION_STALL_FAILURE_CODES
    ):
        return None
    if case.result is None or not case.result.messages:
        return None
    last = case.result.messages[-1]
    if not isinstance(last, dict):
        return None
    role = str(last.get("role") or "").strip().lower()
    content = str(last.get("content") or "").strip()
    if role in {"user", "caller", "customer"}:
        return (
            "target_agent_stalled",
            "Target agent produced no response after the caller's final transcribed turn",
        )
    if role in {"assistant", "agent"}:
        if content and content[-1] not in ".?!":
            return (
                "target_agent_stalled",
                "Target agent stopped mid-utterance and produced no further speech",
            )
        return (
            "simulator_stalled",
            "Simulated caller produced no response after the target agent's final turn",
        )
    return None


# C3 §4.5: the engine's dispatch-ack ladder marks +60s exhaustion with this structured
# `failure.code`. Matched here to pass the marker through on `CallAborted.marker` (never
# string-matched from `failure.message`). Kept as a literal — the engine module that owns it
# requires the optional `livekit` dependency this runner must import without.
_VOICE_DISPATCH_UNACKNOWLEDGED = "voice_dispatch_unacknowledged"


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
    source_directory: Path | None = None
    simulator_provider_secret_values: Mapping[str, str] = field(default_factory=dict)
    usage_reporter: UsageReporter | None = None
    # A readiness probe checks how the agent's tools reach the world, not the scenario's own trial rules.
    readiness_probe: bool = False


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


def _resolve_connector(
    job: HarnessJob, target_provider_secret_values: Mapping[str, str]
) -> str:
    """Pin the job's transport connector from the credentials actually present.

    A fresh one-shot ships ``job.json`` with ``connector="auto"``: the platform only writes the
    authored connector back onto the job *after* authoring, by which time this guest has already
    booted from the un-resolved payload.  Mirror the platform rule so a LiveKit-credentialed
    ``auto`` job dispatches to the target agent instead of the simulator lane with no identity.
    """
    connector = job.agent.connector.strip().lower()
    if connector != "auto":
        return connector
    if job.agent.config.get(
        LIVEKIT_URL_CONFIG_KEY
    ) or target_provider_secret_values.get(LIVEKIT_URL_ALIAS):
        return "livekit"
    if target_provider_secret_values.get(VAPI_API_KEY_ALIAS):
        return "vapi"
    if target_provider_secret_values.get(RETELL_API_KEY_ALIAS):
        return "retell"
    return connector


def _check_config(
    job: HarnessJob,
    target_provider_secret_values: Mapping[str, str],
    simulator_values: Mapping[str, str] | None = None,
) -> _MissingVoiceConfig | None:
    simulator_values = simulator_values or {}

    def simulator_value(alias: str) -> str | None:
        # Hosted runs supply platform-owned simulator credentials in the control process.  The
        # target-provider value remains a backwards-compatible fallback for local SDK callers.
        return simulator_values.get(alias) or (
            target_provider_secret_values.get(alias)
            if job.execution is ExecutionMode.LOCAL
            else None
        )

    config = job.agent.config
    llm_provider = str(
        config.get("simulator_llm_provider")
        or simulator_value(SIMULATOR_LLM_PROVIDER_ALIAS)
        or "google"
    ).lower()
    stt_provider = str(
        config.get("simulator_stt_provider")
        or simulator_value(SIMULATOR_STT_PROVIDER_ALIAS)
        or "deepgram"
    ).lower()
    tts_provider = str(
        config.get("simulator_tts_provider")
        or simulator_value(SIMULATOR_TTS_PROVIDER_ALIAS)
        or "deepgram"
    ).lower()

    connector = _resolve_connector(job, target_provider_secret_values)
    livekit_values = (
        target_provider_secret_values if connector == "livekit" else simulator_values
    )
    required = [LIVEKIT_API_KEY_ALIAS, LIVEKIT_API_SECRET_ALIAS]
    if connector == "vapi":
        required.append(VAPI_API_KEY_ALIAS)
    elif connector == "retell":
        required.append(RETELL_API_KEY_ALIAS)
    if connector == "phone" or (
        connector in {"vapi", "retell"} and config.get("phone_number")
    ):
        required.extend((SIP_OUTBOUND_TRUNK_ID_ALIAS, SIP_OUTBOUND_FROM_NUMBER_ALIAS))
    # Through the gateway the model and speech vendors are reached on the gateway's credentials,
    # not the caller's.
    direct = agentcc.gateway() is None
    if direct and "deepgram" in {stt_provider, tts_provider}:
        if not simulator_value(DEEPGRAM_API_KEY_ALIAS):
            required.append(DEEPGRAM_API_KEY_ALIAS)

    def credential(alias: str) -> str | None:
        if alias in {LIVEKIT_API_KEY_ALIAS, LIVEKIT_API_SECRET_ALIAS}:
            return livekit_values.get(alias)
        if alias in {VAPI_API_KEY_ALIAS, RETELL_API_KEY_ALIAS}:
            return target_provider_secret_values.get(alias)
        if alias in {SIP_OUTBOUND_TRUNK_ID_ALIAS, SIP_OUTBOUND_FROM_NUMBER_ALIAS}:
            return simulator_values.get(alias)
        return simulator_value(alias)

    missing_aliases = [alias for alias in required if not credential(alias)]

    if direct and llm_provider == "google":
        has_api_key = bool(
            simulator_value(GEMINI_API_KEY_ALIAS)
            or simulator_value(GOOGLE_API_KEY_ALIAS)
        )
        has_vertex_adc = bool(
            (
                simulator_value(GOOGLE_APPLICATION_CREDENTIALS_ALIAS)
                or simulator_value(GOOGLE_APPLICATION_CREDENTIALS_JSON_ALIAS)
            )
            and simulator_value(GOOGLE_CLOUD_PROJECT_ALIAS)
        )
        if not has_api_key and not has_vertex_adc:
            missing_aliases.append(
                f"{GEMINI_API_KEY_ALIAS}_or_{GOOGLE_API_KEY_ALIAS}_or_VERTEX_ADC"
            )
    elif (
        direct
        and llm_provider == "openai"
        and not simulator_value(OPENAI_API_KEY_ALIAS)
    ):
        missing_aliases.append(OPENAI_API_KEY_ALIAS)

    has_livekit_url = bool(
        config.get(LIVEKIT_URL_CONFIG_KEY) or livekit_values.get(LIVEKIT_URL_ALIAS)
    )
    missing_config_keys = [] if has_livekit_url else [LIVEKIT_URL_CONFIG_KEY]
    if not missing_aliases and not missing_config_keys:
        return None
    return _MissingVoiceConfig(tuple(missing_aliases), tuple(missing_config_keys))


def _canonical_simulator_secrets(values: Mapping[str, str]) -> dict[str, str]:
    """Translate platform-only aliases into the names expected by simulator plugins."""
    return {
        _SIMULATOR_PLATFORM_ALIAS_MAP.get(alias, alias): value
        for alias, value in values.items()
    }


def _dispatch_agent_name(runtime: EnvironmentRuntime, job: HarnessJob) -> str | None:
    """The ONLY place this repo reads the dispatch-identity metadata key, so a
    change to the key name/convention is a one-line adapt. The provisioner
    mirrors the agent process's rendered LIVEKIT_AGENT_NAME here; a bundle
    that declares none (or an ambiguous set) leaves the key absent and the
    caller's typed `CallAborted` below fires."""
    if job.agent.mode is ProviderExecutionMode.CONNECT_ONLY:
        value = job.agent.config.get("agent_name")
    else:
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


# Edits a call actually plays; everything else is as sealed.
_EDITABLE_FIELDS = ("background_noise", "max_turns")
_EDITABLE_PERSONA_FIELDS = (
    "personality",
    "communication_style",
    "accent",
    "languages",
    "occupation",
    "location",
)


def _with_scenario_edits(
    document: dict[str, Any], metadata: object, scenario_key: str
) -> dict[str, Any]:
    """The sealed document with the platform's current edits for this scenario laid over it."""
    edits = metadata.get("scenario_edits") if isinstance(metadata, Mapping) else None
    edit = edits.get(scenario_key) if isinstance(edits, Mapping) else None
    if not isinstance(edit, Mapping):
        return document
    merged = dict(document)
    merged.update({field: edit[field] for field in _EDITABLE_FIELDS if field in edit})
    persona = edit.get("persona")
    if isinstance(persona, Mapping):
        merged["persona"] = {
            **(merged.get("persona") if isinstance(merged.get("persona"), dict) else {}),
            **{key: persona[key] for key in _EDITABLE_PERSONA_FIELDS if key in persona},
        }
    return merged


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


# --- SimulationSpec construction. Shared with the local lane through simulator_voice; only the
# value lookup is lane-specific. ---------------------------------------------------------------


def _dials_the_person(
    doc: Mapping[str, Any], environ: Mapping[str, str] | None = None
) -> bool:
    """Whether the agent places the call: scenario first, then the environment, then inbound."""
    direction = str(
        doc.get("call_direction")
        or (os.environ if environ is None else environ).get(CALL_DIRECTION_ALIAS)
        or "inbound"
    )
    return direction.strip().lower() == "outbound"


def _target_speaks_first(
    doc: Mapping[str, Any],
    environ: Mapping[str, str],
    simulator_config: Mapping[str, Any],
) -> bool:
    # Only an explicit True overrides the agent's own direction.
    if simulator_config.get("target_speaks_first") is True:
        return True
    return not _dials_the_person(doc, environ)


def _setting(
    simulator_config: Mapping[str, Any], environ: Mapping[str, str]
) -> Callable[[str], str]:
    """One simulator setting for this call: lowercase job config wins, then the provider environment alias."""

    def setting(name: str) -> str:
        return str(simulator_config.get(name.lower()) or environ.get(name) or "")

    return setting


def _build_spec(
    *,
    run_id: str,
    room_name: str,
    agent_name: str | None,
    doc: Mapping[str, Any],
    livekit_url: str,
    call_timeout_seconds: float,
    run_seconds: float,
    recordings_root: Path,
    simulator_config: Mapping[str, Any],
    environ: Mapping[str, str],
    connector: str = "livekit",
    provider_target_id: str | None = None,
    call_actors: list[dict[str, Any]] | None = None,
) -> SimulationSpec:
    """The hosted lane: values come from job config and the bundle's scenario document.

    Lowercase job config wins; provider environment aliases are accepted for compatibility.
    """

    setting = _setting(simulator_config, environ)
    simulator = simulator_definition(setting, doc.get("persona"))
    connector = connector.strip().lower()
    # Match the platform agent-definition inbound-phone path. Provider IDs still
    # supply authoring context, but the call itself dials the existing number.
    if connector in {"vapi", "retell"} and simulator_config.get("phone_number"):
        connector = "phone"
    provider_agent: simulate.AgentDefinition | None = None
    if connector == "vapi":
        if not provider_target_id:
            raise ValueError("vapi_target_id_unavailable")
        provider_agent = simulate.AgentDefinition(
            name="harness-vapi-target",
            system_prompt=str(
                simulator_config.get("target_system_prompt")
                or "Provider-hosted Vapi target under test."
            ),
            target={
                "provider": "vapi",
                "assistant_id": provider_target_id,
                "api_base_url": str(
                    simulator_config.get("vapi_api_base_url") or "https://api.vapi.ai"
                ),
                "api_key_env": VAPI_API_KEY_ALIAS,
            },
            transport={"kind": "vapi_websocket"},
            provider_evidence={
                "provider": "vapi",
                "call_id_source": "originator_response",
            },
        )
    elif connector == "retell":
        if not provider_target_id:
            raise ValueError("retell_target_id_unavailable")
        provider_agent = simulate.AgentDefinition(
            name="harness-retell-target",
            system_prompt=str(
                simulator_config.get("target_system_prompt")
                or "Provider-hosted Retell target under test."
            ),
            target={
                "provider": "retell",
                "agent_id": provider_target_id,
                "api_url": str(
                    simulator_config.get("retell_api_url")
                    or "https://api.retellai.com/v2/create-web-call"
                ),
                "livekit_url": str(
                    simulator_config.get("retell_livekit_url")
                    or "wss://retell-ai-4ihahnq7.livekit.cloud"
                ),
                "api_key_env": RETELL_API_KEY_ALIAS,
            },
            transport={"kind": "retell_webcall"},
            provider_evidence={
                "provider": "retell",
                "call_id_source": "originator_response",
            },
        )
    elif connector == "phone":
        phone_number = str(simulator_config.get("phone_number") or "").strip()
        if not phone_number:
            raise ValueError("phone_target_number_unavailable")
        provider_agent = simulate.AgentDefinition(
            name="harness-phone-target",
            system_prompt=str(simulator_config.get("target_system_prompt") or ""),
            transport={
                "kind": "sip_outbound",
                # These come only from the platform simulator channel. A customer config must
                # never choose the platform trunk or spoof its originating caller ID.
                "sip_trunk_id": str(environ.get(SIP_OUTBOUND_TRUNK_ID_ALIAS) or ""),
                "sip_number": str(environ.get(SIP_OUTBOUND_FROM_NUMBER_ALIAS) or ""),
                "sip_call_to": phone_number,
            },
        )
    # A provider-hosted agent owns termination and can legitimately finish an agent-first call
    # after the fifth message: agent greeting, caller request, agent clarification, caller answer,
    # agent confirmation followed by the provider's end-call tool. Requiring the simulator's
    # sixth acknowledgement after Retell/Vapi has already disconnected misclassifies a complete
    # call as infrastructure failure and prevents the tool trace from being graded. Native
    # LiveKit keeps the stricter six-message floor because our simulator owns that hang-up path.
    min_turn_messages = 5 if connector in {"vapi", "retell", "phone"} else 6
    return simulation_spec(
        run_id=run_id,
        room_name=room_name,
        agent_name=agent_name,
        system_prompt=doc["instruction"],
        livekit_url=livekit_url,
        recording_dir=recordings_root / run_id / "recordings",
        scenario=caller_scenario(
            name=str(doc.get("scenario_key") or doc.get("name") or "harness-voice"),
            persona=doc.get("persona"),
            situation=with_others(doc["instruction"], call_actors or []),
            fixture=doc.get("fixture"),
            tts_provider=simulator.tts.provider,
            variation=run_id,
            cartesia_key=setting("CARTESIA_API_KEY"),
        ),
        simulator=simulator,
        # An outbound agent dials; the person answers, so the caller opens.
        direction="agent_first"
        if _target_speaks_first(doc, environ, simulator_config)
        else "simulator_first",
        max_seconds=call_timeout_seconds,
        min_turn_messages=min_turn_messages,
        # Hosted targets can legitimately spend tens of seconds in a provider call or a tool
        # round-trip after the conversation has begun.  The previous 45-second value terminated
        # an otherwise healthy LiveKit call at exactly the watchdog boundary.  Keep a finite
        # liveness guard, but align it with the engine's 60-second conversation-silence backstop.
        agent_first_silence_seconds=60.0,
        run_seconds=run_seconds,
        agent_definition=provider_agent,
    )


def _world_records(runtime: Any) -> Callable[[str], list[dict[str, Any]]] | None:
    """The trial's world records, one collection at a time, for an actor's knowledge; None where none can be read."""
    endpoint = _find_postgres_endpoint(runtime) if runtime is not None else None
    if endpoint is None:
        return None
    from .world.stores.postgres import AttachedPostgresStore

    store = AttachedPostgresStore(endpoint.address)
    return store.table


def _resolved_call_actors(
    doc: Mapping[str, Any],
    bundle_dir: Any,
    tts_provider: str,
    cartesia_key: str,
    seed: str,
    records: Callable[[str], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Every actor the scenario includes, resolved from the environment's list with what it does
    here. One that can speak has a voice no one else on the call has and a level of its own; one that
    cannot is kept with `entered: false` and the reason, so the call's record still names it."""
    if not doc.get("actors"):
        return []
    listing = load_call_actors(bundle_dir)
    full = load_actors(bundle_dir).by_id()
    speaking = speaking_entries(
        [ScenarioActor.model_validate(entry) for entry in doc.get("actors") or []],
        listing,
        set(full),
        full,
    )
    if not speaking:
        return []
    contract_path = Path(bundle_dir) / "contract.json"
    contract = (
        json.loads(contract_path.read_text(encoding="utf-8"))
        if contract_path.is_file()
        else {}
    )
    caller = dict(doc.get("persona") or {})
    taken = {
        str(
            caller.get("voice")
            or caller.get("voice_id")
            or persona_voice(caller, tts_provider, cartesia_key=cartesia_key)
        )
    }
    resolved = []
    for included in speaking:
        actor = listing.get(included.id)
        trigger = included.trigger.model_dump()
        here = {
            **(
                actor.model_dump(exclude_none=True)
                if actor is not None
                else {"id": included.id}
            ),
            "instruction": included.instruction,
            "trigger": trigger,
        }
        if actor is None or "voice" not in CHANNELS.get(actor.channel, {}).get(
            "modalities", ()
        ):
            logger.warning(
                "actor %s does not enter: %s",
                included.id,
                "not in the environment's list"
                if actor is None
                else "not a voice channel",
            )
            reason = "not_in_list" if actor is None else "not_a_voice_channel"
            resolved.append({**here, "entered": False, "reason": reason})
            continue
        persona = {**actor.persona, "name": actor.name}
        voice = persona_voice(persona, tts_provider, frozenset(taken), cartesia_key)
        if not voice:
            # The person's voice or none at all: the agent could not tell them apart.
            logger.warning(
                "actor %s does not enter: no voice of its own on this call",
                included.id,
            )
            resolved.append({**here, "entered": False, "reason": "no_distinct_voice"})
            continue
        taken.add(voice)
        if trigger.get("kind") == "on_tool":
            # The engine counts the calls the trace logs, which name the route a tool calls.
            trigger["tool"] = logged_tool_name(contract, str(trigger.get("tool") or ""))
        resolved.append(
            {
                **here,
                # What it may do in the world, its line limit and its model, so its line and its action are one turn.
                **acting_fields(full.get(actor.id)),
                "loudness_ratio": loudness_ratio(seed, actor.id, actor.loudness_ratio),
                "voice": voice,
                "speed": persona_speech_rate(persona),
                "emotion": persona_emotion(persona),
                "situation": known_to_others(str(doc.get("instruction") or "")),
                "language": ", ".join(caller.get("languages") or []),
                # The same view the platform shows as what this actor sees.
                "brief": brief_text(
                    actor_brief(
                        with_knowledge_read(full[actor.id].model_dump(exclude_none=True), bundle_dir, records) or {},
                        entry=with_knowledge_read(
                            next(
                                (one for one in doc.get("actors") or [] if (one or {}).get("id") == actor.id),
                                None,
                            ),
                            bundle_dir,
                            records,
                        ),
                        scenario=dict(doc),
                    )
                ),
            }
        )
    return resolved


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
        logger.debug(
            "tool_trace clear skipped (table likely absent): %s", type(exc).__name__
        )


def read_tool_trace(dsn: str, *, timeout_ms: int | None = None) -> list[dict[str, Any]]:
    """The agent's own calls in the world's tool trace so far, in call order, read without writing. A world
    actor's actions are in the same table and are left out; a row from before `actor_id` existed is the agent's."""
    return [
        row
        for row in _read_action_log(dsn, timeout_ms=timeout_ms)
        if (row.get("actor_id") or AGENT) == AGENT
    ]


def _read_only(dsn: str, timeout_ms: int | None = None) -> Any:
    import psycopg

    options = "-c default_transaction_read_only=on"
    if timeout_ms is not None:
        options += f" -c statement_timeout={timeout_ms}"
    return psycopg.connect(dsn, autocommit=True, connect_timeout=5, options=options)


def _action_log_rows(connection: Any) -> list[dict[str, Any]]:
    cursor = connection.execute(
        f'SELECT * FROM "{_TOOL_TRACE_TABLE}" ORDER BY at ASC'  # noqa: S608
    )
    columns = [description[0] for description in cursor.description or []]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


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
        records = read_tool_trace(endpoint.address)
    except Exception as exc:  # noqa: BLE001 - missing table / connection failure -> no evidence, not a crash
        # WHY: same DSN-in-exception-message risk as `_clear_tool_trace_calls` above -- log only
        # the exception TYPE, never exc_info/str(exc), which can carry the world DB password.
        logger.debug(
            "tool_trace read failed; treating as no evidence: %s", type(exc).__name__
        )
        return ()

    calls: list[Call] = []
    for record in records:
        # The agent's own tool name when the world recorded it beside the route the call took.
        name = record.get("agent_tool") or record.get("name")
        if not isinstance(name, str) or not name:
            continue
        arguments = record.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        ok = bool(record.get("ok", True))
        result = _structured(record.get("result"))
        error = _truncate(str(record.get("error") or ""))
        raw_at = record.get("at")
        at = float(raw_at) if isinstance(raw_at, (int, float)) else 0.0
        # The door says what happened; a row from before it did reads any failure as a refusal.
        outcome = record.get("outcome")
        refused = outcome == "refused" if outcome else not ok
        calls.append(
            Call(
                name=name,
                arguments=arguments,
                result=result,
                ok=ok,
                error=error,
                refused=refused,
                at=at,
                # The worker the world saw make it, by the channel it came through.
                worker_id=str(record.get("worker_id") or ""),
                refusal_code=str(record.get("refusal_code") or "") if refused else "",
            )
        )
    return tuple(calls)


def collect_seam_calls(
    runtime: EnvironmentRuntime,
    seam: EvidenceSeam | None,
    observed: Sequence[Call] = (),
) -> tuple[Call, ...]:
    """The calls a world observed for one attempt: calls another source saw, else the agent's own in-process trace,
    else where its bundle says evidence lands, joined with the trace file's span lines. Every runner reads them here,
    so a voice and a chat call agree."""
    written, from_spans = _read_file_tool_calls(runtime)
    calls = list(observed) or written
    if not calls and seam is EvidenceSeam.HTTP_TOOL:
        calls = list(_collect_http_tool_calls(runtime))
    elif not calls and seam is EvidenceSeam.TOOL_TRACE:
        calls = list(_collect_tool_trace_calls(runtime))
    # Unrecognized/None (should not happen for a `kind: process` bundle past preflight) degrades to the span lines
    # alone, keeping this on the scheduler's own evidence_missing path, never a raw exception.
    return _merge_span_calls(calls, from_spans)


def _read_action_log(
    dsn: str, *, timeout_ms: int | None = None
) -> list[dict[str, Any]]:
    """Every row of this trial's action log, every column, oldest first."""
    with _read_only(dsn, timeout_ms) as connection:
        return _action_log_rows(connection)


class _ActionLog:
    """This trial's action log, read over one connection while its actors wait; reopened after a failed read."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.connection: Any = None

    def __call__(self) -> list[dict[str, Any]]:
        if self.connection is None:
            self.connection = _read_only(self.dsn)
        try:
            return _action_log_rows(self.connection)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self.connection is not None:
            with contextlib.suppress(Exception):
                self.connection.close()
            self.connection = None


# The outcomes the world's door records for an attempt, beside committed.
_OUTCOMES = ("committed", "refused", "faulted", "duplicate")


def _shown_action(name: str, actor: Any) -> str:
    """How a person reads an actor's logged action: the door logs it by its last part (`change`, `post_message`), as
    the proof does, so the actor's one permitted action ending that way (`approvals.change`) names it. More than one,
    or none, keeps the logged name."""
    if actor is None or "." in name:
        return name
    full = [one for one in getattr(actor, "permitted_actions", None) or () if one.rsplit(".", 1)[-1] == name]
    return full[0] if len(full) == 1 else name


class _ChosenActions:
    """Each actor's actions in the order it chose them (scheduled or decided), with full names: what names a logged
    row when the actor's permissions alone can't (`approvals.put` and `channel_members.put` both log as `put`)."""

    def __init__(self, decisions: Sequence[dict[str, Any]]) -> None:
        self.left: dict[str, list[str]] = {}
        for one in decisions or ():
            action = str(one.get("action") or "")
            if one.get("actor") and "." in action:
                self.left.setdefault(str(one["actor"]), []).append(action)

    def name(self, who: str, logged: str, actor: Any) -> str:
        if "." not in logged:
            queue = self.left.get(who) or []
            for index, action in enumerate(queue):
                if action.rsplit(".", 1)[-1] == logged:
                    return queue.pop(index)
        return _shown_action(logged, actor)


def trial_state_changes(
    log: list[dict[str, Any]], listed: Mapping[str, Any], decisions: Sequence[dict[str, Any]] = ()
) -> list[dict[str, Any]]:
    """What each participant attempted in this trial and what the world made of it: one entry for the agent, one per
    actor that acted and one per agent worker the world told apart (`worker:<id>`, by the channel it came through),
    with attempted, committed, refused, faulted and duplicate counts, the actions it committed and the ones the world
    refused with why, in order. Attempted, refused and committed are kept apart; a row from before outcomes were
    recorded counts as committed when it succeeded and refused when the world refused it. `answer_lost_actions` lists
    the committed actions whose answer a `timeout_after_commit` fault replaced with a timeout."""
    entries: dict[str, dict[str, Any]] = {}
    chosen = _ChosenActions(decisions)
    for row in log:
        if is_pre_run(row.get("idempotency_key")):
            # Written before the run: the trial's starting point, not a change made during it.
            continue
        who = str(row.get("actor_id") or AGENT)
        if who == AGENT and row.get("worker_id"):
            who = f"worker:{row['worker_id']}"
        actor = listed.get(who)
        entry = entries.setdefault(
            who,
            {
                "who": who,
                "name": "the agent"
                if who == AGENT
                else actor.name
                if actor is not None
                else str(row.get("worker_id") or who),
                "attempted": 0,
                **{outcome: 0 for outcome in _OUTCOMES},
                "committed_actions": [],
                "refused_actions": [],
            },
        )
        entry["attempted"] += 1
        outcome = row.get("outcome") or (
            "refused" if row.get("refused") else "committed" if row.get("ok", True) else "faulted"
        )
        if outcome in _OUTCOMES:
            entry[outcome] += 1
        # A call through the tool proxy is named by the agent's own tool, as its checks are graded, not by its route.
        name = str(row.get("agent_tool") or row.get("name") or "")
        if outcome == "committed":
            shown = name if who == AGENT else chosen.name(who, name, actor)
            entry["committed_actions"].append(shown)
            if row.get("error") == "response_lost":
                # Committed, yet the one who acted was told it timed out: a retry after it commits twice.
                entry.setdefault("answer_lost_actions", []).append(shown)
        elif outcome == "refused":
            # An attempted violation stays visible: what was tried and why the world said no.
            entry["refused_actions"].append(
                {
                    "action": name,
                    "code": str(row.get("refusal_code") or ""),
                    "reason": str(row.get("refusal") or row.get("error") or ""),
                }
            )
    # The agent always has a line: "no change" is a result, since doing nothing can be correct. Its workers are the
    # agent too, so a change one of them committed is not "no change".
    agent = entries.pop(AGENT, None) or {
        "who": AGENT, "name": "the agent", "attempted": 0, **{outcome: 0 for outcome in _OUTCOMES},
        "committed_actions": [], "refused_actions": [],
    }
    agent["no_change"] = not agent["committed_actions"] and not any(
        entry["committed_actions"] for who, entry in entries.items() if who.startswith("worker:")
    )
    return [agent] + list(entries.values())


def approvals(log: list[dict[str, Any]], decisions: Sequence[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    """One line per approval, read from the action log only: the agent's request (the agent row an actor's row or a
    timeout points at through its parent), the decision (that actor row, or the timeout's fallback), and the agent's
    writes before the decision and after it, in log order. No record matching, no inference."""
    rows = [row for row in log if not is_pre_run(row.get("idempotency_key"))]
    by_id = {str(row.get("action_id")): row for row in rows if row.get("action_id")}
    agent_writes = [row for row in rows if (row.get("actor_id") or AGENT) == AGENT]
    found = []
    for row in rows:
        parent = by_id.get(str(row.get("parent_event_id") or ""))
        if (row.get("actor_id") or AGENT) == AGENT or parent is None or (parent.get("actor_id") or AGENT) != AGENT:
            continue
        found.append((parent, {"by": row.get("actor_id"), "action": row.get("name"), "outcome": row.get("outcome"),
                               "at": row.get("at")}))
    for one in decisions:
        if one.get("play") == "timeout" and one.get("parent") in by_id:
            found.append((by_id[one["parent"]], {"by": one.get("actor"), "action": f"timeout: {one.get('fallback')}",
                                                 "outcome": one.get("outcome"), "at": None}))
    out = []
    for request, decision in found:
        start = float(request.get("at") or 0)
        decided = decision["at"]
        between = [w for w in agent_writes if w is not request and start < float(w.get("at") or 0)
                   and (decided is None or float(w.get("at") or 0) < float(decided))]
        after = [w for w in agent_writes if decided is not None and float(w.get("at") or 0) > float(decided)]
        out.append(
            {
                "request": {"action": request.get("name"), "at": request.get("at"), "action_id": request.get("action_id")},
                "decision": decision,
                "before_decision": [{"action": w.get("name"), "outcome": w.get("outcome")} for w in between],
                "after_decision": [{"action": w.get("name"), "outcome": w.get("outcome")} for w in after],
                "acted_before_decision": any(w.get("outcome") == "committed" for w in between),
            }
        )
    return out


def _state_changes(
    runtime: EnvironmentRuntime, bundle_dir: Path, decisions: Sequence[dict[str, Any]] = ()
) -> list[dict[str, Any]]:
    database = _find_postgres_endpoint(runtime)
    if database is None:
        return []
    try:
        log = _read_action_log(database.address)
    except Exception as exc:  # noqa: BLE001 - no state changes, never a failed call
        logger.debug("state changes unreadable: %s", type(exc).__name__)
        return []
    return trial_state_changes(log, load_actors(bundle_dir).by_id(), decisions)


def _actor_rows(
    runtime: EnvironmentRuntime, bundle_dir: Path, decisions: Sequence[dict[str, Any]] = ()
) -> list[dict[str, Any]]:
    """This trial's actions by actors, named and with their outcome; none for a world without actors."""
    listed = load_actors(bundle_dir).by_id()
    database = _find_postgres_endpoint(runtime)
    if not listed or database is None:
        return []
    try:
        log = _read_action_log(database.address)
    except Exception as exc:  # noqa: BLE001 - no actor evidence, never a failed call
        logger.debug("actor rows unreadable: %s", type(exc).__name__)
        return []
    return actor_rows(log, listed, decisions)


def actor_rows(
    log: list[dict[str, Any]], listed: Mapping[str, Any], decisions: Sequence[dict[str, Any]] = ()
) -> list[dict[str, Any]]:
    """The actors' rows of an action log, named and with their outcome: the same for every runtime."""
    rows = []
    chosen = _ChosenActions(decisions)
    for row in log:
        who = str(row.get("actor_id") or AGENT)
        actor = listed.get(who)
        if actor is None:
            continue
        rows.append(
            {
                "name": chosen.name(who, str(row.get("name") or ""), actor),
                "arguments": row.get("arguments") or {},
                "result": _structured(row.get("result")),
                "ok": bool(row.get("ok")),
                "error": _truncate(str(row.get("error") or "")),
                "refused": row.get("outcome") == "refused",
                "at": row.get("at"),
                "action_id": row.get("action_id"),
                "actor_id": actor.id,
                "actor_name": actor.name,
                "actor_role": actor.role,
                "outcome": row.get("outcome"),
                "refusal_code": row.get("refusal_code"),
                "parent_event_id": row.get("parent_event_id"),
                "on_behalf_of": row.get("on_behalf_of"),
            }
        )
    return rows


def _agent_marks(
    runtime: EnvironmentRuntime, bundle_dir: Path, calls: Sequence[Call]
) -> list[dict[str, Any]]:
    """Each of the agent's calls' own action id and outcome, so an actor's action can name the call that
    caused it; none unless every call is found in the log, in order."""
    database = _find_postgres_endpoint(runtime)
    if database is None:
        return []
    try:
        log = _read_action_log(database.address)
    except Exception:  # noqa: BLE001 - the calls are sealed as before
        return []
    rows = iter(row for row in log if (row.get("actor_id") or AGENT) == AGENT)
    marks = []
    for call in calls:
        # In order; a row the agent's own trace does not count as a call (a lookup at start) is skipped.
        row = next((one for one in rows if one.get("name") == call.name), None)
        if row is None:
            return []
        marks.append({"action_id": row.get("action_id"), "outcome": row.get("outcome")})
    return marks


# The trial's faults on the agent's calls, kept in the world's own database where the tool proxy reads them: never in a
# file the agent's process could read or rewrite.
TRIAL_FAULTS_DDL = "CREATE TABLE IF NOT EXISTS _alk_trial_faults (id INT PRIMARY KEY, faults JSONB NOT NULL)"
# When this trial's workers stop being allowed to act, beside its faults; null when the scenario sets no budget.
TRIAL_DEADLINE_DDL = "ALTER TABLE _alk_trial_faults ADD COLUMN IF NOT EXISTS worker_deadline_at DOUBLE PRECISION"
# Each named worker's own deadline, {worker id: epoch seconds}, when the scenario gives a budget per worker.
TRIAL_WORKER_DEADLINES_DDL = "ALTER TABLE _alk_trial_faults ADD COLUMN IF NOT EXISTS worker_deadlines JSONB"


def _write_trial_state(
    runtime: EnvironmentRuntime,
    faults: list[Any],
    worker_deadline_at: float | None = None,
    worker_deadlines: Mapping[str, float] | None = None,
) -> None:
    """This trial's faults and its workers' deadline, in the world's database where the door in the tool proxy reads
    them; written by the runner only, over its own connection. Past the deadline the world refuses every worker's
    call (`deadline_passed`)."""
    database = _find_postgres_endpoint(runtime)
    if database is None:
        if worker_deadline_at or worker_deadlines:
            # Workers would act with no deadline at all: the trial fails rather than grade them as if one held.
            raise RuntimeError("the workers' deadline cannot be set: this world has no database the tool proxy reads")
        if faults:
            logger.warning("trial state cannot be set: this world has no database the tool proxy reads it from")
        return
    import psycopg

    with psycopg.connect(database.address, autocommit=True, connect_timeout=5) as connection:
        connection.execute(TRIAL_FAULTS_DDL)
        connection.execute(TRIAL_DEADLINE_DDL)
        connection.execute(TRIAL_WORKER_DEADLINES_DDL)
        connection.execute(
            "INSERT INTO _alk_trial_faults (id, faults, worker_deadline_at, worker_deadlines) "
            "VALUES (1, %s::jsonb, %s, %s::jsonb) ON CONFLICT (id) DO UPDATE SET faults = EXCLUDED.faults, "
            "worker_deadline_at = EXCLUDED.worker_deadline_at, worker_deadlines = EXCLUDED.worker_deadlines",
            (json.dumps(list(faults)), worker_deadline_at, json.dumps(dict(worker_deadlines or {}))),
        )


def _same_action(action: str, name: str) -> bool:
    """A fault names the world action (`crm.apply_discount`); a call may carry the tool's own name (`apply_discount`)."""
    return bool(action) and bool(name) and (action == name or action.rsplit(".", 1)[-1] == name.rsplit(".", 1)[-1])


def fault_notices(
    faults: Sequence[Any],
    log: Sequence[Mapping[str, Any]] | None,
    calls: Sequence[Call],
    *,
    unreachable: str | None = None,
) -> list[dict[str, Any]]:
    """What became of each fault the scenario set, in plain words, for the faults that did not take effect: one that
    could not be applied (no world database to hold it, or the call ran outside the world's door) and one the agent
    never reached. A fault the door applied says nothing; the log already shows it as faulted."""
    notices: list[dict[str, Any]] = []
    for fault in faults:
        if not isinstance(fault, Mapping):
            continue
        action = str(fault.get("action") or "")
        on_call = _int_or(fault.get("on_call"), 1)
        mode = str(fault.get("mode") or "fault")
        base = {"action": action, "on_call": on_call, "mode": mode}
        if unreachable:
            notices.append({**base, "code": "fault_not_applied",
                            "message": f"The {mode} fault on call {on_call} of {action} could not be applied: {unreachable}."})
            continue
        rows = [
            row for row in log or []
            if _same_action(action, str(row.get("name") or "")) or _same_action(action, str(row.get("agent_tool") or ""))
        ]
        if any(_fault_applied(row) for row in rows):
            continue
        made = sum(1 for call in calls if _same_action(action, call.name))
        if made >= on_call:
            notices.append({**base, "code": "fault_not_applied",
                            "message": f"The {mode} fault on call {on_call} of {action} could not be applied: the agent "
                                       "made that call outside the world's door (in its own process), so nothing held it."})
        else:
            notices.append({**base, "code": "fault_not_triggered",
                            "message": f"The {mode} fault on call {on_call} of {action} never took effect: the agent "
                                       f"made {made} call{'s' if made != 1 else ''} of it."})
    return notices


def _fault_applied(row: Mapping[str, Any]) -> bool:
    """The door's row for a fault it applied: refused before commit (`faulted`), or committed with the answer lost
    (`timeout_after_commit` keeps the commit and records `response_lost`)."""
    return row.get("outcome") == "faulted" or (
        row.get("outcome") == "committed" and row.get("error") == "response_lost"
    )


def _int_or(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def worker_deadline(document: Mapping[str, Any]) -> float | None:
    """When all of this trial's workers stop being allowed to act: its start plus the scenario's worker budget, if it
    gives one number for all of them."""
    budget = document.get("worker_budget_seconds")
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget <= 0:
        return None
    return time.time() + budget


def worker_deadlines(document: Mapping[str, Any]) -> dict[str, float]:
    """When each named worker stops being allowed to act, where the scenario gives a budget per worker."""
    budgets = document.get("worker_budget_seconds")
    if not isinstance(budgets, Mapping):
        return {}
    start = time.time()
    return {
        str(worker): start + budget
        for worker, budget in budgets.items()
        if str(worker).strip() and not isinstance(budget, bool) and isinstance(budget, (int, float)) and budget > 0
    }


def trial_worker_deadlines(
    document: Mapping[str, Any], bundle_dir: Path | str | None
) -> tuple[float | None, dict[str, float], list[dict[str, Any]]]:
    """This trial's worker-action deadline for all workers, each named worker's own, and what the person should be
    told. A tool proxy sealed before this release has no worker limits at all, so neither a budget nor a worker's
    permitted actions are held there: a scenario that relies on either says so, and is held rather than graded."""
    deadline, deadlines = worker_deadline(document), worker_deadlines(document)
    relies = bool(deadline or deadlines) or declares_worker_limits(bundle_dir)
    if not relies or _door_holds_worker_limits(bundle_dir):
        return deadline, deadlines, []
    notice = {
        "code": "worker_limits_not_enforced",
        "message": "This environment was sealed before worker limits, so no worker's deadline or permitted actions "
        "were enforced in this trial, and its checks were not graded. Rebuild the environment to enforce them.",
    }
    return deadline, deadlines, [notice]


def declares_worker_limits(bundle_dir: Path | str | None) -> bool:
    """Whether this environment's agent declares a worker held to its own permitted tools: a separately wired worker,
    or one allowed fewer tools than it holds."""
    if bundle_dir is None:
        return False
    workers = _bundle_contract(Path(bundle_dir)).get("workers")
    return any(
        isinstance(one, dict) and (one.get("binding") == "separate" or bool(one.get("allowed_tools")))
        for one in workers or []
    )


def _door_holds_worker_limits(bundle_dir: Path | str | None) -> bool:
    """Whether this bundle's tool proxy enforces workers' limits: only one sealed with its capabilities file does."""
    from .world_door import DOOR_FILE

    if bundle_dir is None:
        # No sealed bundle: the proxy is this harness's own, which holds every limit.
        return True
    try:
        sealed = json.loads((Path(bundle_dir) / "generated" / "tool-proxy" / DOOR_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(sealed, dict) and sealed.get("worker_deadlines") == "per_worker"


def _set_trial_faults(
    runtime: EnvironmentRuntime,
    faults: list[Any],
    worker_deadline_at: float | None = None,
    worker_deadlines: Mapping[str, float] | None = None,
) -> None:
    """This trial's faults and worker deadline at its start. A trial with either to set fails when they cannot be
    set; one with neither only clears what an earlier trial may have left, so a world database out of reach never
    fails it."""
    if worker_deadline_at or worker_deadlines:
        _write_trial_state(runtime, faults, worker_deadline_at, worker_deadlines)
        return
    if faults:
        try:
            _write_trial_state(runtime, faults)
        except Exception as exc:  # noqa: BLE001 - the text of a database error can carry the world's password
            raise RuntimeError(f"trial faults could not be set: {type(exc).__name__}") from None
        return
    try:
        _write_trial_state(runtime, [])
    except Exception as exc:  # noqa: BLE001 - nothing to set; the trial runs as it would without faults
        # Only the type: the text of a database error can carry the world's password.
        logger.warning("trial faults could not be cleared before a trial with none: %s", type(exc).__name__)


def runner_door(tools_url: str, dsn: str, timeout: float) -> Any:
    """The world's door in the runner's process: the agent's tools service behind it, the world's own action log, actor
    list and records. Actors' actions enter here; the agent's own calls keep going through the tool proxy."""
    from .world_door import (
        HttpExecutor,
        PostgresLog,
        PostgresRecords,
        PostgresRoster,
        RecordExecutor,
        Routed,
        WorldDoor,
    )

    return WorldDoor(
        Routed(HttpExecutor(tools_url, timeout=timeout), RecordExecutor(PostgresRecords(dsn))),
        PostgresLog(dsn),
        PostgresRoster(dsn),
    )


def actor_limits(doc: Mapping[str, Any], bundle_dir: Path, modality: str) -> dict[str, Any]:
    """The actor limits in force for this scenario on this channel: the environment's setting, the scenario's own on
    top, and each unset value the channel's default. Stored with the trial."""
    from .actors import Limits

    own = doc.get("limits")
    return load_actors(bundle_dir).limits.merged(
        Limits.model_validate(own) if isinstance(own, dict) else None
    ).resolved(modality)


def _actor_dispatcher(
    doc: Mapping[str, Any],
    bundle_dir: Path,
    runtime: EnvironmentRuntime,
    trial_id: str,
    budget: float,
    meter: Any = None,
    reported: Callable[[], list[dict[str, Any]]] | None = None,
    why_not: list[tuple[str, str]] | None = None,
) -> ActorDispatcher | None:
    """The scenario's actors for this trial, acting through a world door built here in the runner's own process: the
    agent's process holds nothing that lets it act as an actor, and the tool proxy refuses any request that names one.
    None when the scenario has none. An actor that cannot act here is the scenario's problem, recorded by the caller
    from `actor_problems`."""
    if not (doc.get("actors") or doc.get("history") or doc.get("pre_run")):
        return None
    tools = runtime.endpoints.get("tools_api")
    database = _find_postgres_endpoint(runtime)
    if tools is None or database is None or runtime.endpoints.get("tool_proxy") is None:
        logger.warning("actors cannot act: this world has no tools service and action log behind a door")
        if why_not is not None:
            why_not.append(("no_world_door", "no world door: this agent's tools run where actors cannot act"))
        return None
    return build_dispatcher(
        dict(doc),
        bundle_dir,
        act=DoorActs(runner_door(tools.address, database.address, budget), _bundle_contract(bundle_dir)),
        read_log=_ActionLog(database.address),
        records=_world_records(runtime),
        trial_id=trial_id,
        meter=meter,
        reported=reported,
    )


def _bundle_contract(bundle_dir: Path) -> dict[str, Any]:
    path = Path(bundle_dir) / "contract.json"
    try:
        body = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


def _private_actions_file(path: Path) -> Path:
    """An empty file for this call's speaking actors' actions, readable and writable by this run's user alone, in a
    directory only it can enter: what is written there is sent under an actor's id with the trial's token, so the
    agent under test (another user in the same sandbox) must never be able to write to it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.unlink(missing_ok=True)
    os.close(os.open(path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600))
    os.chmod(path, 0o600)
    return path


def _reported_actions(path: Path) -> Callable[[], list[dict[str, Any]]]:
    """The actions speaking actors decided on the call so far, as the voice engine handed them over, oldest first."""

    def read() -> list[dict[str, Any]]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                one = json.loads(line)
            except ValueError:
                continue
            if isinstance(one, dict):
                out.append(one)
        return out

    return read


def _with_world_actors(
    summary: tuple[dict[str, Any], ...], decisions: Sequence[dict[str, Any]], bundle_dir: Path
) -> tuple[dict[str, Any], ...]:
    """The call's actor summary with every actor that acts through the world in it too, so a check that depends on
    any actor reads that actor's real record."""
    speaking = {str(one.get("actor")) for one in summary}
    return summary + tuple(world_summary(list(decisions), load_actors(bundle_dir).by_id(), speaking))


def actor_problems(doc: Mapping[str, Any], bundle_dir: Path, why: str, problem: str = "no_world_door") -> list[dict[str, Any]]:
    """Each actor of the scenario that acts in the world, recorded as the scenario's problem with the reason it could
    not: never silently dropped, never the agent's failure."""
    listed = load_actors(bundle_dir).by_id()
    problems = []
    for entry in doc.get("actors") or []:
        actor = listed.get(str((entry or {}).get("id") or ""))
        if actor is None:
            problems.append({"actor": str((entry or {}).get("id") or ""), "play": "problem",
                             "problem": "not_listed", "why": "the scenario names an actor the environment does not list"})
            continue
        if actor.permitted_actions or (entry or {}).get("actions"):
            problems.append({"actor": actor.id, "play": "problem", "problem": problem, "why": why})
    return problems


def _tool_trace_file(runtime: EnvironmentRuntime) -> Path | None:
    raw = runtime.metadata.get("tool_trace_path")
    return Path(raw) if isinstance(raw, str) and raw.strip() else None


def _clear_file_tool_calls(runtime: EnvironmentRuntime) -> None:
    path = _tool_trace_file(runtime)
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.debug(
            "file tool_trace clear failed; continuing without blocking the call"
        )


# Marks lines written by livekit_tool_trace_bootstrap._record. That module is copied into the
# agent's interpreter as a standalone sitecustomize, so it cannot import this constant.
_LIVEKIT_HOOK_SOURCE = "livekit"
_SPAN_SOURCE = "otel"
_MISSING = object()


def _collect_file_tool_calls(runtime: EnvironmentRuntime) -> tuple[Call, ...]:
    return _with_span_calls((), runtime)


def _with_span_calls(
    observed: Sequence[Call], runtime: EnvironmentRuntime
) -> tuple[Call, ...]:
    """Calls another source saw, else the trace file's own lines, joined with the file's span lines."""
    written, from_spans = _read_file_tool_calls(runtime)
    return _merge_span_calls(list(observed) or written, from_spans)


def _read_file_tool_calls(runtime: EnvironmentRuntime) -> tuple[list[Call], list[Call]]:
    path = _tool_trace_file(runtime)
    if path is None or not path.is_file():
        return [], []
    calls: list[Call] = []
    # Agent-written calls not yet timed by the hook line for the same call.
    untimed: list[Call] = []
    from_spans: list[Call] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        name = record.get("name")
        if not isinstance(name, str) or not name:
            continue
        arguments = record.get("arguments")
        if isinstance(arguments, str) and not arguments.strip():
            # A span that recorded no arguments.
            arguments = {}
        elif isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {"raw": arguments}
        if not isinstance(arguments, dict):
            arguments = {}
        is_error = bool(record.get("is_error", False))
        output = record.get("output")
        raw_at = record.get("at")
        call = Call(
            name=name,
            arguments=arguments,
            result=None if is_error else _structured(output),
            ok=not is_error,
            error=str(output) if is_error and output is not None else None,
            **tool_call_attribution(record),
            runtime_ids=runtime_ids(record),
            handoff=record.get("kind") == "handoff",
            at=float(raw_at) if isinstance(raw_at, (int, float)) else 0.0,
        )
        ended = record.get("ended_at")
        call.ended_at = float(ended) if isinstance(ended, (int, float)) else 0.0
        if record.get("source") == _SPAN_SOURCE:
            from_spans.append(call)
            continue
        if record.get("source") != _LIVEKIT_HOOK_SOURCE:
            calls.append(call)
            if not call.at:
                untimed.append(call)
            continue
        # An agent implementing HARNESS_AGENT_TOOL_TRACE writes its own line while the tool
        # runs: the arguments it executed with and the structured result, but no time. The hook
        # writes the same call after the step, with the model's arguments, a stringified output
        # and when the model issued it. Keep the agent's evidence and take the hook's time.
        # A name alone does not identify the call: LiveKit rejects malformed arguments without
        # running the tool, so a batch can hold a failed hook line with no agent line next to a
        # same-name success. Pair only when the outcome agrees and every argument the model
        # sent is one the agent executed with (agents may add their own, e.g. a booking ref).
        written = next(
            (
                pending
                for pending in untimed
                if pending.name == name
                and pending.ok == call.ok
                and all(
                    pending.arguments.get(key, _MISSING) == value
                    for key, value in call.arguments.items()
                )
            ),
            None,
        )
        if written is None:
            calls.append(call)
            continue
        untimed.remove(written)
        written.at = call.at
    return calls, from_spans


def _tool_key(name: str) -> str:
    """A tool's name as two tracers of one call may spell it ("Delegate work" or "delegate_work")."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _keep_recorded_parents(from_spans: list[Call]) -> None:
    """A span's parent stands only when it is a hand-off the spans recorded; a call of an agent run the framework
    records as handed control sits in that control pass, on that record alone (`parent_event_id` and `passed`)."""
    handed = {
        span.runtime_ids.get("event_id")
        for span in from_spans
        if span.delegated_to or span.transferred
    } - {None}
    passed = {
        span.runtime_ids.get("event_id")
        for span in from_spans
        if span.transferred and span.handoff
    } - {None}
    for span in from_spans:
        agent_run = span.runtime_ids.get("parent_event_id")
        if not span.parent_span_id and agent_run in passed:
            span.parent_span_id, span.relationship = agent_run, "observed"
        elif span.parent_span_id and span.parent_span_id not in handed:
            span.parent_span_id, span.relationship = "", ""


def _merge_span_calls(calls: list[Call], from_spans: list[Call]) -> tuple[Call, ...]:
    """Count a call a framework span and another tracer both report once, with the span's agent, hand-off and times."""
    # A background job is written when it starts and again when it ends; the later line carries its return.
    ended = {
        span.runtime_ids.get("event_id")
        for span in from_spans
        if span.delegated_to and span.ended_at
    } - {None}
    from_spans = [
        span
        for span in from_spans
        if not (
            span.delegated_to
            and not span.ended_at
            and span.runtime_ids.get("event_id") in ended
        )
    ]
    _keep_recorded_parents(from_spans)
    if not calls:
        # A span is written when it ends, so a hand-off lands after the calls it led to.
        return tuple(sorted(from_spans, key=lambda call: call.at))
    launched = {span.runtime_ids.get("event_id"): span.name for span in from_spans}
    merged = list(calls)
    waiting: list[Call] = []
    for span in from_spans:
        if (
            span.delegated_to
            and launched.get(span.runtime_ids.get("parent_event_id")) == span.name
        ):
            # A job's hand-off sits under the call that launched it and is not that call again.
            merged.append(span)
        else:
            waiting.append(span)
    span_ids = set(launched) - {None}
    tools: dict[str, tuple[list[Call], list[Call]]] = {}
    for span in waiting:
        tools.setdefault(_tool_key(span.name), ([], []))[0].append(span)
    for call in calls:
        if _tool_key(call.name) in tools:
            tools[_tool_key(call.name)][1].append(call)
    unknown: set[str | None] = set()
    for spans, natives in tools.values():
        groups, apart, can_be = _pair_tool(spans, natives, span_ids)
        # A span no call left could be is a call only the spans saw.
        merged.extend(apart)
        for spans_left, calls_left in groups:
            # Which of these calls each span is, nothing says: their ids and places stay unknown rather than be swapped.
            unknown |= {span.runtime_ids.get("event_id") for span in spans_left}
            # Only a call every one of these spans could be takes what they agree on.
            shared = [call for call in calls_left if all(can_be(span, call) for span in spans_left)]
            if len(spans_left) >= len(calls_left):
                _lend_shared(spans_left, shared)
            # More spans than the calls they could be are more calls than the other tracer saw: each extra is kept,
            # owned by no one.
            extras = [_unowned(span) for span in spans_left[len(calls_left) :]]
            _lend_shared(spans_left, extras)
            merged.extend(extras)
    for call in merged:
        if call.parent_span_id and call.parent_span_id in unknown:
            call.parent_span_id, call.relationship = "", ""
    return tuple(sorted(merged, key=lambda call: call.at))


def _pair_tool(
    spans: list[Call], natives: list[Call], span_ids: set[str]
) -> tuple[list[tuple[list[Call], list[Call]]], list[Call], Callable[[Call, Call], bool]]:
    """Pair one tool's spans with the calls another tracer saw of it, on evidence that holds across the two tracers'
    clocks: an event id, then arguments and outcome between competing calls, then order when the spans did not
    overlap, then calls that are the same in all but time. Returns the unpaired spans and calls in groups that could be
    each other, the spans that can be none of the calls left, and the test of whether a span could be a call."""
    by_id = {
        call.runtime_ids["event_id"]: call
        for call in natives
        if call.runtime_ids.get("event_id")
    }
    rest: list[Call] = []
    for span in spans:
        own = by_id.pop(span.runtime_ids.get("event_id", ""), None)
        if own is None:
            rest.append(span)
        else:
            _lend_span(span, own)
    # A call holding another span's event id is that span's, whatever else fits.
    free = [
        call for call in natives if call.runtime_ids.get("event_id") not in span_ids
    ]
    values = {id(call): _argument_values(call) for call in [*rest, *free]}

    def fit(span: Call, call: Call) -> tuple[bool, bool] | None:
        """How strongly a span claims a call: matching arguments, then outcome; None when anything both recorded
        differs."""
        if _recorded_differently(span, call):
            return None
        mine, theirs = values[id(span)], values[id(call)]
        shared = (
            mine.keys() & theirs.keys()
            if mine is not None and theirs is not None
            else set()
        )
        if any(mine[key] != theirs[key] for key in shared):
            return None
        return bool(shared), call.ok == span.ok

    if len(rest) == 1 and len(free) == 1:
        # Nothing competes, but count alone is not identity: each tracer may have missed a different call.
        if _compatible(rest[0], free[0], fit):
            _lend_span(rest[0], free[0])
            return [], [], lambda _span, _call: False
        return [], rest, lambda _span, _call: False
    contested_spans: list[Call] = []
    contested_calls: list[Call] = []
    while True:
        claims: dict[int, tuple[Call, list[tuple[tuple[bool, bool], Call]]]] = {}
        for span in rest:
            fits = [
                (fit(span, call), call) for call in free if _compatible(span, call, fit)
            ]
            if not fits:
                continue
            best = max(strength for strength, _ in fits)
            top = [call for strength, call in fits if strength == best]
            if len(top) == 1:
                claims.setdefault(id(top[0]), (top[0], []))[1].append((best, span))
        if not claims:
            break
        for call, claimants in claims.values():
            claimants.sort(key=lambda claim: claim[0], reverse=True)
            free = [c for c in free if c is not call]
            if len(claimants) > 1 and claimants[0][0] == claimants[1][0]:
                # Two spans claim one call equally: neither takes it.
                contested_calls.append(call)
                contested_spans.extend(span for _, span in claimants)
                rest = [s for s in rest if not any(s is span for _, span in claimants)]
            else:
                _lend_span(claimants[0][1], call)
                rest = [s for s in rest if s is not claimants[0][1]]
    if rest and len(rest) == len(free) and _pair_in_order(rest, free, fit):
        rest, free = [], []
    rest, free = _pair_interchangeable(rest, free, fit)
    left = [*free, *contested_calls]

    def can_be(span: Call, call: Call) -> bool:
        return _compatible(span, call, fit)

    spans_left = [*rest, *contested_spans]
    apart = [span for span in spans_left if not any(can_be(span, call) for call in left)]
    spans_left = [span for span in spans_left if not any(span is other for other in apart)]
    return _compatible_groups(spans_left, left, can_be), apart, can_be


def _compatible_groups(
    spans: list[Call], calls: list[Call], can_be: Callable[[Call, Call], bool]
) -> list[tuple[list[Call], list[Call]]]:
    """Spans and calls left unpaired, in groups that share no possible pairing: a span counts only against the calls
    it could be, so an unrelated call never absorbs another call's span. Calls no span could be are left out."""
    group = list(range(len(spans)))

    def root(index: int) -> int:
        while group[index] != index:
            group[index] = group[group[index]]
            index = group[index]
        return index

    owner: dict[int, int] = {}
    for at, span in enumerate(spans):
        for position, call in enumerate(calls):
            if not can_be(span, call):
                continue
            if position in owner:
                group[root(at)] = root(owner[position])
            else:
                owner[position] = at
    found: dict[int, tuple[list[Call], list[Call]]] = {}
    for at, span in enumerate(spans):
        found.setdefault(root(at), ([], []))[0].append(span)
    for position, call in enumerate(calls):
        if position in owner:
            found[root(owner[position])][1].append(call)
    return list(found.values())


def _compatible(span: Call, call: Call, fit: Callable[[Call, Call], Any]) -> bool:
    """Whether a span and a call can be one call: nothing both recorded differs, and they agree on the outcome or
    record the same result (two tracers can read one refusal differently)."""
    strength = fit(span, call)
    return strength is not None and (
        strength[1]
        or (_kept_result(span) and _kept_result(call) and _same_result(span, call))
    )


def _kept_result(call: Call) -> bool:
    # A tracer that wrote no output, or only part of it, recorded nothing to compare.
    return call.result not in (None, "") and not call.output_omitted


def _same_result(span: Call, call: Call) -> bool:
    return _loose(span.result) == _loose(call.result)


def _recorded_differently(span: Call, call: Call) -> bool:
    """Whether a span and a call each recorded something about the call that the other contradicts: its result, the
    worker that made it, the worker it handed work to, or whether it was a worker's run. Event ids are not compared
    here: each tracer may number events in its own way, and a shared id is matched before any of this."""
    return (
        (_kept_result(span) and _kept_result(call) and not _same_result(span, call))
        or _other_worker(span, call)
        or bool(
            span.delegated_to
            and call.delegated_to
            and _tool_key(span.delegated_to) != _tool_key(call.delegated_to)
        )
        or span.handoff != call.handoff
    )


def _pair_in_order(
    spans: list[Call], calls: list[Call], fit: Callable[[Call, Call], Any]
) -> bool:
    """Pair spans that did not overlap with the calls in the order each tracer stamped them. Each side is read on its
    own clock, so an offset between the clocks cannot change the pairing; overlapping spans are never paired by time."""
    spans = sorted(spans, key=lambda span: span.at)
    calls = sorted(calls, key=lambda call: call.at)
    # A span with no end may still be running when the next starts.
    apart = all(a.ended_at and a.ended_at < b.at for a, b in zip(spans, spans[1:]))
    stamped = len(calls) == 1 or all(
        a.at and a.at < b.at for a, b in zip(calls, calls[1:])
    )
    if not (
        apart
        and stamped
        and all(_compatible(span, call, fit) for span, call in zip(spans, calls))
    ):
        return False
    for span, call in zip(spans, calls):
        _lend_span(span, call)
    return True


def _pair_interchangeable(
    spans: list[Call], calls: list[Call], fit: Callable[[Call, Call], Any]
) -> tuple[list[Call], list[Call]]:
    """Pair spans whose only fitting calls are the same in all but time: whichever each takes, the trace reads the
    same. Returns the spans and calls left."""
    if not (spans and calls):
        return spans, calls
    alike: dict[str, list[Call]] = {}
    for call in calls:
        alike.setdefault(_digest(call), []).append(call)
    wanting: dict[str, list[Call]] = {}
    for span in spans:
        fitting = [
            digest
            for digest, group in alike.items()
            if _compatible(span, group[0], fit)
        ]
        if len(fitting) == 1:
            wanting.setdefault(fitting[0], []).append(span)
    taken: set[int] = set()
    for digest, claimants in wanting.items():
        if len(claimants) <= len(alike[digest]):
            for span, call in zip(claimants, alike[digest]):
                _lend_span(span, call)
                taken.update((id(span), id(call)))
    return (
        [span for span in spans if id(span) not in taken],
        [call for call in calls if id(call) not in taken],
    )


def _argument_values(call: Call) -> dict[str, Any] | None:
    """A call's arguments as both tracers would agree on them ("5" and 5 alike), or None when it kept none whole."""
    if call.input_omitted or not call.arguments or set(call.arguments) == {"raw"}:
        return None
    return {key: _loose(value) for key, value in call.arguments.items()}


def _loose(value: Any) -> Any:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return value


def _digest(call: Call) -> str:
    """Everything a call carries but its times."""
    fields = {
        key: value for key, value in vars(call).items() if key not in ("at", "ended_at")
    }
    return json.dumps(fields, sort_keys=True, default=repr)


def _unowned(span: Call) -> Call:
    return dataclasses.replace(
        span,
        worker_id="",
        worker_label="",
        delegated_to="",
        transferred=False,
        runtime_ids={},
        parent_span_id="",
        relationship="",
    )


def _lend_shared(spans: list[Call], calls: list[Call]) -> None:
    """Give calls no span can be told apart from what every one of their spans agrees on: a hand-off or worker that
    certainly happened, whichever call is whose."""

    def agreed(read: Callable[[Call], Any]) -> Any:
        values = {read(span) for span in spans}
        return values.pop() if len(values) == 1 else None

    worker = agreed(lambda span: (span.worker_id, span.worker_label))
    delegated_to = agreed(lambda span: span.delegated_to)
    transferred = agreed(lambda span: span.transferred)
    for call in calls:
        if worker and not (call.worker_id or call.worker_label):
            call.worker_id, call.worker_label = worker
        call.delegated_to = call.delegated_to or delegated_to or ""
        call.transferred = call.transferred or bool(transferred)


def _worker_names(call: Call) -> set[str]:
    return {_tool_key(name) for name in (call.worker_id, call.worker_label) if name}


def _other_worker(span: Call, call: Call) -> bool:
    """Whether two records name different workers. Tracers spell a worker in different id spaces (a framework's
    hashed key, its role name), so they are the same worker when any name either recorded matches, and different only
    when both named a worker and no name matches."""
    mine, theirs = _worker_names(span), _worker_names(call)
    return bool(mine and theirs and not (mine & theirs))


def _lend_span(span: Call, other: Call) -> None:
    """Give another tracer's call the span's agent, hand-off and times, keeping what it recorded itself."""
    if not (other.worker_id or other.worker_label):
        other.worker_id, other.worker_label = span.worker_id, span.worker_label
    other.delegated_to = other.delegated_to or span.delegated_to
    other.transferred = other.transferred or span.transferred
    other.ended_at = other.ended_at or span.ended_at
    if not other.runtime_ids:
        # The span's place among the agents goes with its ids: a parent is an id in the same record.
        other.runtime_ids = span.runtime_ids
        other.parent_span_id, other.relationship = (
            span.parent_span_id,
            span.relationship,
        )
    # A span starts when the call does; another tracer may stamp a call only once it reads its result.
    other.at = span.at or other.at


def _target_metrics(case: Any) -> dict[str, Any] | None:
    """The agent under test's own usage, cost and latency, as its provider reported them.

    Shares the SDK result path's extraction so hosted and SDK runs report the same numbers.
    """
    target = target_provider_usage(case) if case is not None else None
    if target is None:
        return None
    reported = {
        "usage": target.usage,
        "cost_cents": target.cost_cents,
        "latency": target.latency,
        "provider_call_id": target.call_id,
        "provider_end_reason": target.ended_reason,
    }
    return {
        "provider": target.provider,
        **{key: value for key, value in reported.items() if value is not None},
    }


def _collect_provider_tool_calls(case: Any) -> tuple[Call, ...]:
    """Translate provider-reported tool evidence into scheduler calls.

    LiveKit's legacy report conversion stores per-case evidence in
    ``result.metadata.evidence``; canonical reports may populate
    ``case.evidence`` directly. Accept both shapes so hosted execution is not
    coupled to the report representation.
    """
    sources: list[Any] = list(getattr(case, "evidence", None) or [])
    result = getattr(case, "result", None)
    result_metadata = getattr(result, "metadata", None)
    if isinstance(result_metadata, Mapping):
        embedded = result_metadata.get("evidence")
        if isinstance(embedded, list):
            sources.extend(embedded)

    calls: list[Call] = []
    for source in sources:
        if hasattr(source, "model_dump"):
            source = source.model_dump(mode="json", exclude_none=True)
        if not isinstance(source, Mapping):
            continue
        metadata = source.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        raw_calls = metadata.get("tool_calls")
        if not isinstance(raw_calls, list):
            continue
        for raw in raw_calls:
            if not isinstance(raw, Mapping):
                continue
            name = raw.get("name")
            if not isinstance(name, str) or not name:
                continue
            arguments: Any = raw.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {"raw": arguments}
            if not isinstance(arguments, dict):
                arguments = {}
            ok = bool(raw.get("ok", True))
            raw_at = raw.get("at")
            at = float(raw_at) if isinstance(raw_at, (int, float)) else 0.0
            calls.append(
                Call(
                    name=name,
                    arguments=arguments,
                    result=_structured(raw.get("result")) if ok else None,
                    ok=ok,
                    error=str(raw.get("error") or ""),
                    refused=not ok,
                    at=at,
                )
            )
    return tuple(calls)


def _structured(value: Any) -> Any:
    """A tool result that arrives as the text of a dict or list is read back into one, so checks see one shape."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text[:1] in ("{", "["):
        for parse in (json.loads, ast.literal_eval):
            try:
                parsed = parse(text)
            except (ValueError, SyntaxError, MemoryError, RecursionError):
                continue
            if isinstance(parsed, (dict, list)):
                return parsed
    return _truncate(value)


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
    fd, path_text = tempfile.mkstemp(
        prefix="google-", suffix=".json", dir=credential_dir
    )
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
        self._place_call = place_call
        simulator_secret_values = _canonical_simulator_secrets(
            context.simulator_provider_secret_values
        )
        # Local SDK runs remain BYOK and historically carry simulator keys in the one local
        # target map. Hosted runs deliberately do not fall back: their simulator credentials must
        # come from platform configuration and must not be confused with customer-agent keys.
        if context.job.execution is ExecutionMode.LOCAL:
            for alias in (
                LIVEKIT_URL_ALIAS,
                LIVEKIT_API_KEY_ALIAS,
                LIVEKIT_API_SECRET_ALIAS,
                DEEPGRAM_API_KEY_ALIAS,
                CARTESIA_API_KEY_ALIAS,
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
                if alias not in simulator_secret_values:
                    value = context.target_provider_secret_values.get(alias)
                    if value:
                        simulator_secret_values[alias] = value
        # Provider libraries read ambient credentials. Supply a private snapshot to
        # each call subprocess, never export credentials into the parent process.
        ambient = os.environ if environ is None else environ
        target_environ = _allowlisted_ambient_env(dict(ambient))
        allowed = (
            {
                value
                for name, value in globals().items()
                if name.endswith("_ALIAS") and isinstance(value, str)
            }
            | SIMULATOR_MODEL_ENV
            | {"FI_HOSTED_DISPATCH_ACK", "ALK_VOICEMAIL_SCENARIOS"}
        )
        target_environ.update(
            {key: value for key, value in ambient.items() if key in allowed}
        )
        connector = _resolve_connector(
            context.job, context.target_provider_secret_values
        )
        target_aliases = [
            VAPI_API_KEY_ALIAS,
            VAPI_PUBLIC_API_KEY_ALIAS,
            RETELL_API_KEY_ALIAS,
        ]
        if connector == "livekit":
            target_aliases.extend(
                [LIVEKIT_API_KEY_ALIAS, LIVEKIT_API_SECRET_ALIAS, LIVEKIT_URL_ALIAS]
            )
        for alias in target_aliases:
            value = context.target_provider_secret_values.get(alias)
            if value:
                target_environ[alias] = value
        for alias in (
            LIVEKIT_URL_ALIAS,
            LIVEKIT_API_KEY_ALIAS,
            LIVEKIT_API_SECRET_ALIAS,
            DEEPGRAM_API_KEY_ALIAS,
            CARTESIA_API_KEY_ALIAS,
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
            SIP_OUTBOUND_TRUNK_ID_ALIAS,
            SIP_OUTBOUND_FROM_NUMBER_ALIAS,
            BACKGROUND_NOISE_ALIAS,
            BACKGROUND_NOISE_CATALOG_ALIAS,
            BACKGROUND_NOISE_VOLUME_ALIAS,
            CALLER_BARGE_IN_RATE_ALIAS,
        ):
            value = simulator_secret_values.get(alias)
            if value:
                # Platform-owned simulator credentials already present in the hosted control
                # process win.  Target credentials are retained only as the local-SDK fallback.
                target_environ.setdefault(alias, value)
        self._environ = target_environ
        self._adc_path = _materialize_vertex_adc(
            simulator_secret_values,
            context.work_directory,
            target_environ,
        )
        atexit.register(self._cleanup_credentials)
        self._livekit_url = str(
            context.job.agent.config.get(LIVEKIT_URL_CONFIG_KEY)
            or (
                context.target_provider_secret_values.get(LIVEKIT_URL_ALIAS)
                if connector == "livekit"
                else simulator_secret_values.get(LIVEKIT_URL_ALIAS)
            )
            or ""
        )
        self._missing_config = _check_config(
            context.job,
            context.target_provider_secret_values,
            simulator_secret_values,
        )
        self._scenario_room_counts: dict[str, int] = {}
        self._closed = False

    def _cleanup_credentials(self) -> None:
        if self._adc_path is None:
            return
        try:
            self._adc_path.unlink(missing_ok=True)
        except OSError:
            pass
        if self._environ.get(GOOGLE_APPLICATION_CREDENTIALS_ALIAS) == str(
            self._adc_path
        ):
            self._environ.pop(GOOGLE_APPLICATION_CREDENTIALS_ALIAS, None)
        self._adc_path = None

    async def close(self) -> None:
        """Release job-scoped resources before ``asyncio.run`` closes its event loop.

        LiveKit's Python objects own native FFI handles and several of them participate in
        reference cycles.  Leaving those cycles to interpreter shutdown lets their finalizers run
        after LiveKit's callback loop has closed; sufficiently long jobs then abort in the native
        FFI teardown even though every call and artifact already completed.  Collect on the event
        loop thread and yield twice so queued FFI callbacks drain while their loop is still valid.
        """
        if self._closed:
            return
        self._closed = True
        self._cleanup_credentials()
        atexit.unregister(self._cleanup_credentials)
        gc.collect()
        # Native RTC shutdown is not synchronous with the Python objects that requested it.
        # Give finalizers and already-enqueued disconnect/drop-handle callbacks real scheduling
        # windows while the loop is still alive.  Do not mutate LiveKit's private FFI subscriber
        # list here: a subscriber is owned by its AudioStream task, and removing its queue behind
        # that task's back produces stranded coroutines (observed after a 50-call soak as
        # ``cannot reuse already awaited coroutine``).  Deterministic collection on the live loop
        # addresses the shutdown-order problem without violating stream ownership.
        await asyncio.sleep(0.25)
        gc.collect()
        await asyncio.sleep(0.25)

    async def run(
        self,
        scenario: HostedScenario,
        runtime: EnvironmentRuntime,
        *,
        world: Any | None = None,
    ) -> CallOutcome:
        del world  # Voice tools cross the declared evidence seam; they are not response-carried.
        call_environ = dict(self._environ)
        if self._missing_config is not None:
            # Pre-dial: dialing never starts, so no partial -- and never `WorldUnavailable` (that
            # code is reserved by the contract for a world-level capability mismatch, not a
            # job-level voice config gap).
            raise CallAborted(self._missing_config.message())
        if self._context.usage_reporter is not None:
            try:
                await asyncio.to_thread(
                    self._context.usage_reporter.check, "voice_call"
                )
            except UsageDenied as exc:
                raise CallAborted(
                    f"voice_usage_check_denied: {exc}", code="usage_exhausted"
                ) from exc
            except UsageUnavailable as exc:
                raise CallAborted(
                    f"voice_usage_check_failed: {exc}", code="usage_check_failed"
                ) from exc

        connector = _resolve_connector(
            self._context.job, self._context.target_provider_secret_values
        )
        agent_name = (
            _dispatch_agent_name(runtime, self._context.job)
            if connector == "livekit"
            else None
        )
        if connector == "livekit" and agent_name is None:
            raise CallAborted(
                "voice_dispatch_identity_unavailable: runtime.metadata['livekit_agent_name'] is "
                f"not set for world {runtime.world_index}"
            )

        source_key = getattr(scenario, "source_scenario_key", None) or scenario.scenario_key
        try:
            doc = _read_scenario_document(self._context.bundle_dir, source_key)
        except _ScenarioDocumentUnavailable as exc:
            raise CallAborted(f"voice_scenario_document_unavailable: {exc}") from exc
        doc = _with_scenario_edits(
            doc, getattr(self._context.job, "metadata", None), source_key
        )
        doc = with_caller_actor(doc, self._context.bundle_dir)

        room_count = self._scenario_room_counts.get(scenario.scenario_key, 0) + 1
        self._scenario_room_counts[scenario.scenario_key] = room_count
        room_name = _room_name(
            job_id=self._context.job.job_id,
            attempt_number=self._context.attempt_number,
            scenario_key=scenario.scenario_key,
            scenario_attempt=room_count,
        )

        raw_timeout = self._context.job.agent.config.get(CALL_TIMEOUT_CONFIG_KEY)
        call_timeout_seconds = (
            float(raw_timeout)
            if isinstance(raw_timeout, (int, float))
            else _DEFAULT_CALL_TIMEOUT_SECONDS
        )
        run_seconds = (
            call_timeout_seconds
            + CONNECT_TIMEOUT_SECONDS
            + READINESS_TIMEOUT_SECONDS
            + CLEANUP_TIMEOUT_SECONDS
            + _RUN_SECONDS_PAD_SECONDS
        )

        # The engine reads this from the environment at call time, so it is set per
        # scenario and cleared otherwise rather than leaking into the next call.
        run_id = new_run_id()
        noise = scenario_source(
            doc.get("background_noise"),
            doc.get("fixture"),
            seed=f"{doc.get('name') or ''}|{run_id}",
        )
        if noise:
            call_environ["HARNESS_BACKGROUND_NOISE"] = noise
            call_environ["HARNESS_BACKGROUND_PLACE"] = place_of(noise)
            base = self._environ.get(BACKGROUND_NOISE_VOLUME_ALIAS) or "7.0"
            # A scenario about the noise itself is heard loud enough to test it.
            about_noise = any("nois" in str(level) for level in (doc.get("coverage") or {}).values())
            spread = random.uniform(1.3, 1.8) if about_noise else random.uniform(0.7, 1.4)
            try:
                call_environ[BACKGROUND_NOISE_VOLUME_ALIAS] = f"{float(base) * spread:.2f}"
            except ValueError:
                pass
        else:
            call_environ.pop("HARNESS_BACKGROUND_NOISE", None)
            call_environ.pop("HARNESS_BACKGROUND_PLACE", None)

        setting = _setting(self._context.job.agent.config, call_environ)
        resolved = _resolved_call_actors(
            doc,
            self._context.bundle_dir,
            voice_providers(setting)[1],
            setting("CARTESIA_API_KEY"),
            run_id,
            _world_records(runtime),
        )
        # Only an actor that can speak reaches the call; the record keeps every one.
        voices = [one for one in resolved if one.get("entered", True)]
        call_environ.pop(ACTOR_WORLD_ALIAS, None)
        # Where a speaking actor's decided actions are handed to this run, which sends them through the door.
        actions_file = _private_actions_file(
            self._context.work_directory / "call-workers" / f"actor-actions-{run_id}.jsonl"
        )
        if voices:
            call_environ[ACTOR_VOICES_ALIAS] = json.dumps(
                {
                    "voices": voices,
                    "limits": actor_limits(doc, self._context.bundle_dir, "voice"),
                    "actions_file": str(actions_file),
                }
            )
        else:
            call_environ.pop(ACTOR_VOICES_ALIAS, None)

        # Read the same way and for the same reason as the noise source above: the simulator's
        # instructions are built deep inside simulator_definition, which sees the environment and
        # not this scenario. Set per scenario and cleared otherwise so one outbound scenario cannot
        # frame the next inbound one.
        # A scenario that names its own direction wins. Otherwise the contract's, which the
        # understand stage read off the agent's own instructions and `hosted_entrypoint` puts here
        # for this process. Not an operator setting: whether an agent places calls or answers them
        # is a fact about the agent, so there is nothing for a run to choose.
        direction = (
            str(
                doc.get("call_direction")
                or call_environ.get(CALL_DIRECTION_ALIAS)
                or "inbound"
            )
            .strip()
            .lower()
        )
        if direction == "outbound":
            call_environ["HARNESS_CALL_DIRECTION"] = direction
            awareness = str(doc.get("caller_awareness") or "").strip().lower()
            if awareness:
                call_environ["HARNESS_CALLER_AWARENESS"] = awareness
            else:
                call_environ.pop("HARNESS_CALLER_AWARENESS", None)
            # Cleared otherwise, so one voicemail scenario cannot silence the next caller.
            if (
                voicemail_enabled()
                and str(doc.get("answered_by") or "").strip().lower() == "voicemail"
            ):
                call_environ["HARNESS_ANSWERED_BY"] = "voicemail"
                # Which kind of mailbox, which decides the greeting and whether a tone follows it.
                style = str(doc.get("voicemail_style") or "").strip().lower()
                if style:
                    call_environ["HARNESS_VOICEMAIL_STYLE"] = style
                else:
                    call_environ.pop("HARNESS_VOICEMAIL_STYLE", None)
                # A recorded greeting where the catalogue has one for this style AND language. It
                # replaces the spoken greeting rather than joining it.
                languages = doc.get("languages") or []
                chosen = clip_for(
                    style or DEFAULT_VOICEMAIL_STYLE,
                    str(languages[0]) if languages else "",
                )
                if chosen:
                    call_environ[VOICEMAIL_CLIP_ALIAS] = chosen["source"]
                    call_environ[VOICEMAIL_CLIP_TONE_ALIAS] = (
                        "1" if chosen["has_tone"] else "0"
                    )
                    if chosen.get("transcript"):
                        call_environ[VOICEMAIL_CLIP_TEXT_ALIAS] = chosen["transcript"]
                    else:
                        call_environ.pop(VOICEMAIL_CLIP_TEXT_ALIAS, None)
                else:
                    call_environ.pop(VOICEMAIL_CLIP_ALIAS, None)
                    call_environ.pop(VOICEMAIL_CLIP_TONE_ALIAS, None)
                    call_environ.pop(VOICEMAIL_CLIP_TEXT_ALIAS, None)
            else:
                call_environ.pop("HARNESS_ANSWERED_BY", None)
                call_environ.pop("HARNESS_VOICEMAIL_STYLE", None)
                call_environ.pop(VOICEMAIL_CLIP_ALIAS, None)
                call_environ.pop(VOICEMAIL_CLIP_TONE_ALIAS, None)
                call_environ.pop(VOICEMAIL_CLIP_TEXT_ALIAS, None)
        else:
            call_environ.pop("HARNESS_CALL_DIRECTION", None)
            call_environ.pop("HARNESS_CALLER_AWARENESS", None)
            call_environ.pop("HARNESS_ANSWERED_BY", None)
            call_environ.pop("HARNESS_VOICEMAIL_STYLE", None)
            call_environ.pop(VOICEMAIL_CLIP_ALIAS, None)
            call_environ.pop(VOICEMAIL_CLIP_TONE_ALIAS, None)
            call_environ.pop(VOICEMAIL_CLIP_TEXT_ALIAS, None)

        provider_target_key = {"vapi": "assistant_id", "retell": "agent_id"}.get(
            connector
        )
        provider_target_id: str | None = None
        if provider_target_key and self._context.job.agent.mode in {
            None,
            ProviderExecutionMode.CONNECT_ONLY,
        }:
            provider_target_id = str(
                self._context.job.agent.config.get(provider_target_key) or ""
            ).strip()
        if provider_target_key and self._context.job.agent.mode not in {
            None,
            ProviderExecutionMode.CONNECT_ONLY,
        }:
            dynamic_target = runtime.metadata.get("provider_target_id")
            provider_target_id = (
                dynamic_target.strip()
                if isinstance(dynamic_target, str) and dynamic_target.strip()
                else None
            )

        # Off the event loop: choosing the caller's voice may ask Cartesia over HTTP.
        spec = await asyncio.to_thread(
            _build_spec,
            run_id=run_id,
            room_name=room_name,
            connector=connector,
            agent_name=agent_name,
            provider_target_id=provider_target_id,
            doc=doc,
            simulator_config=self._context.job.agent.config,
            environ=call_environ,
            call_actors=voices,
            livekit_url=self._livekit_url,
            call_timeout_seconds=call_timeout_seconds,
            run_seconds=run_seconds,
            recordings_root=self._context.work_directory / "voice-calls",
        )

        if self._context.evidence_seam is EvidenceSeam.TOOL_TRACE:
            _clear_file_tool_calls(runtime)
            endpoint = _find_postgres_endpoint(runtime)
            if endpoint is not None:
                _clear_tool_trace_calls(endpoint.address)
                if voices:
                    # The engine reads the live trace for on_tool triggers.
                    call_environ[ACTOR_WORLD_ALIAS] = endpoint.address

        meter = ActorMeter()
        why_not: list[tuple[str, str]] = []
        actors = (
            _actor_dispatcher(
                doc, self._context.bundle_dir, runtime, spec.run_id, run_seconds, meter,
                reported=_reported_actions(actions_file) if voices else None,
                why_not=why_not,
            )
            if self._context.evidence_seam is EvidenceSeam.TOOL_TRACE
            else None
        )
        # An actor that should act in the world and cannot here is the scenario's problem, in the record, with the
        # reason it could not.
        problem, why = why_not[0] if why_not else (
            "no_world_door", "no world door: this agent's tools run where actors cannot act"
        )
        no_door = actor_problems(doc, self._context.bundle_dir, why, problem) if actors is None else []
        started_at = datetime.now(timezone.utc)
        outer_timeout = run_seconds + _OUTER_WAIT_FOR_PAD_SECONDS
        # What the person is told about how this trial's worker deadlines were held, set when the trial starts.
        deadline_notices: list[dict[str, Any]] = []

        async def place() -> SimulationReport:
            if self._place_call is not None:
                return await self._place_call(spec)
            logger.info(
                "voice worker dispatch scenario=%s voice_run=%s",
                scenario.scenario_key,
                spec.run_id,
            )
            result = await run_json_worker(
                "fi.alk.harness.call_worker",
                spec.model_dump(mode="json"),
                environ=call_environ,
                work_directory=self._context.work_directory / "call-workers",
            )
            return SimulationReport.model_validate(result)

        async def place_with_actors() -> SimulationReport:
            faults = list(doc.get("faults") or [])
            deadline, deadlines, deadline_notices[:] = trial_worker_deadlines(doc, self._context.bundle_dir)
            proxied = runtime.endpoints.get("tool_proxy") is not None
            if faults or deadline or deadlines or proxied:
                # Every trial behind a tool proxy writes its own faults and deadline, none included, so nothing an
                # earlier trial left can apply to this one.
                _set_trial_faults(runtime, faults, deadline, deadlines)
            try:
                return await place_with_world_actors()
            finally:
                if faults or deadline or deadlines:
                    with contextlib.suppress(Exception):
                        _write_trial_state(runtime, [])

        async def place_with_world_actors() -> SimulationReport:
            if actors is None:
                return await place()
            # What the actors wrote before T0 is in the world before the call starts.
            await actors.replay()
            acting = asyncio.create_task(actors.run())
            try:
                return await place()
            finally:
                acting.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await acting
                # An actor the agent's last calls woke still takes its turn, within the settle's bound.
                with contextlib.suppress(Exception):
                    await actors.settle()
                # An action a speaking actor decided on its last line is still sent, inside its own trial.
                with contextlib.suppress(Exception):
                    await actors.send_reported()
                if isinstance(actors.read_log, _ActionLog):
                    await asyncio.to_thread(actors.read_log.close)

        try:
            report = await asyncio.wait_for(place_with_actors(), timeout=outer_timeout)
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
                report,
                runtime=runtime,
                scenario_key=scenario.scenario_key,
                started_at=started_at,
                call_actors=resolved,
                actor_decisions=(actors.record() if actors is not None else []) + no_door,
                actor_usage=meter.rows(),
                limits=actor_limits(doc, self._context.bundle_dir, "voice"),
                actor_briefs=actors.briefs if actors is not None else None,
                faults=list(doc.get("faults") or []),
                notices=deadline_notices,
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

    def _fault_notices(
        self, runtime: EnvironmentRuntime, faults: Sequence[Any], calls: Sequence[Call]
    ) -> list[dict[str, Any]]:
        """The scenario's faults that did not take effect on this call, each with why."""
        if not faults:
            return []
        database = _find_postgres_endpoint(runtime)
        if database is None:
            return fault_notices(
                faults, None, calls, unreachable="this world has no database the tool proxy reads trial state from"
            )
        try:
            log = _read_action_log(database.address)
        except Exception:  # noqa: BLE001 - an unreadable log says nothing rather than something wrong
            return []
        return fault_notices(faults, log, calls)

    def _approvals(self, runtime: EnvironmentRuntime, decisions: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        database = _find_postgres_endpoint(runtime)
        if database is None:
            return []
        try:
            return approvals(_read_action_log(database.address), decisions)
        except Exception:  # noqa: BLE001 - no approval lines, never a failed call
            return []

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
        call_actors: Sequence[dict[str, Any]] = (),
        actor_decisions: Sequence[dict[str, Any]] = (),
        actor_usage: Sequence[dict[str, Any]] = (),
        limits: Mapping[str, Any] | None = None,
        actor_briefs: Mapping[str, str] | None = None,
        faults: Sequence[Any] = (),
        notices: Sequence[dict[str, Any]] = (),
    ) -> CallOutcome:
        case = report.test_cases[0] if report.test_cases else None
        took_part = (
            case.result.metadata if case is not None and case.result is not None else {}
        )
        case_started_at = (
            case.started_at
            if case is not None and case.started_at is not None
            else started_at
        )
        ended_at = (
            case.ended_at
            if case is not None and case.ended_at is not None
            else datetime.now(timezone.utc)
        )
        # The person's and the agent's turns; an actor's lines are not turns of the conversation.
        turns = (
            sum(
                1
                for message in case.result.messages
                if not (
                    isinstance(message, Mapping) and message.get("role") == "actor"
                )
            )
            if case is not None and case.result is not None
            else 0
        )

        transcript_artifact: str | None = None
        recording_artifacts: list[str] = []
        recording: str | None = None
        # Evidence belongs to the call attempt, not only to successful calls.  Collect it before
        # interpreting the simulator status so a timeout/agent failure still carries the exact
        # tool activity in its partial receipt.  Previously the early CallAborted below discarded
        # every tool call from failed calls, making a real upstream tool error indistinguishable
        # from a proxy/transport failure.
        calls = self._collect_calls(runtime) if case is not None else ()
        # Provider-hosted agents execute tools outside the guest process, so their
        # authoritative call evidence is returned by Vapi/Retell after the call.
        # Preserve provider-native controls such as ``end_call`` because scenario
        # checks may verify termination ordering. Fall back to that observed stream
        # when the submitted backend exposes no local trace seam; never infer calls
        # from transcript prose.
        if not calls and case is not None:
            calls = _collect_provider_tool_calls(case)
        acted = (
            _actor_rows(runtime, self._context.bundle_dir, actor_decisions)
            if case is not None
            and self._context.evidence_seam is EvidenceSeam.TOOL_TRACE
            else []
        )
        changes = (
            _state_changes(runtime, self._context.bundle_dir, actor_decisions)
            if case is not None
            and self._context.evidence_seam is EvidenceSeam.TOOL_TRACE
            else []
        )
        # A worker the world told apart is kept beside the actors, so its refused attempts are never lost.
        workers_acted = any(
            str(entry.get("who") or "").startswith("worker:") for entry in changes
        )
        # Hand-off records are not the world's actions: marks pair the graded calls with the action log.
        graded = graded_calls(calls)
        marks = _agent_marks(runtime, self._context.bundle_dir, graded) if acted else []
        mark_of = {id(call): mark for call, mark in zip(graded, marks)}
        if calls:
            tool_trace = "\n".join(
                json.dumps({**trace_row(call), **mark_of.get(id(call), {})}, sort_keys=True, default=str)
                for call in calls
            ).encode("utf-8")
            await self._adapter.upload_artifact(
                tool_trace,
                kind=ArtifactKind.TOOL_TRACE,
                scenario_key=scenario_key,
            )
        if case is not None and case.result is not None:
            result = case.result
            combined = result.audio_combined_path
            recording = combined if combined and Path(combined).is_file() else None
            said = [*notices, *self._fault_notices(runtime, faults, calls)]
            # A call that ended with no words still says what did not take effect, and what actors and workers did in
            # the world, never only logging it.
            if result.transcript or said or acted or actor_decisions or workers_acted or took_part.get("call_actors"):
                transcript_document = {
                    "schema_version": "futureagi.call-transcript.v1",
                    "transcript": result.transcript,
                    "messages": result.messages,
                }
                # Every actor that took part, the ones acting only through the world too, so the call is found by each.
                summary = list(
                    _with_world_actors(
                        tuple(took_part.get("call_actors") or ()), actor_decisions, self._context.bundle_dir
                    )
                )
                if call_actors or summary:
                    transcript_document["call_actors"] = {
                        "resolved": call_actors,
                        "summary": summary,
                        "events": took_part.get("call_actor_events") or [],
                    }
                if acted or actor_decisions or workers_acted:
                    # Kept out of the tool trace, whose every line is one of the agent's own calls. `decisions` is
                    # what each actor chose and why (live) or was scheduled to do (mocked); `actions` what the world
                    # made of it.
                    transcript_document["world_actors"] = {
                        "actions": acted,
                        "decisions": list(actor_decisions),
                        "state_changes": changes,
                        "approvals": self._approvals(runtime, actor_decisions),
                        "limits": dict(limits or {}),
                        "usage": list(actor_usage),
                    }
                if said:
                    # A fault that did not take effect is said, never only logged: the trial ran without it.
                    transcript_document["notices"] = said
                recording_offset_ms = result.metadata.get("recording_offset_ms")
                if isinstance(recording_offset_ms, (int, float)):
                    transcript_document["recording_offset_ms"] = int(
                        recording_offset_ms
                    )
                transcript_payload = json.dumps(
                    transcript_document,
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
                transcript_artifact = await self._adapter.upload_artifact(
                    transcript_payload,
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
                    path.read_bytes(),
                    kind=kind,
                    scenario_key=scenario_key,
                )
                if artifact_id is not None:
                    recording_artifacts.append(artifact_id)

        base = CallOutcome(
            calls=calls,
            turns=turns,
            notices=tuple(notices),
            started_at=format_rfc3339_millis(case_started_at),
            ended_at=format_rfc3339_millis(ended_at),
            duration_ms=_duration_ms(case_started_at, ended_at),
            transcript_artifact=transcript_artifact,
            recording_artifacts=tuple(recording_artifacts),
            recording=recording,
            messages=tuple(
                case.result.messages or ()
                if case is not None and case.result is not None
                else ()
            ),
            stop_reason=(
                str(case.result.metadata.get("stop_reason"))
                if case is not None
                and case.result is not None
                and case.result.metadata.get("stop_reason")
                else None
            ),
            target_metrics=_target_metrics(case),
            world_actions=tuple(acted),
            actor_briefs={
                **{str(one["id"]): str(one.get("brief") or "") for one in call_actors if one.get("brief")},
                **(actor_briefs or {}),
            },
            call_actors=_with_world_actors(
                tuple(took_part.get("call_actors") or ())
                + tuple(
                    {"actor": one["id"], "name": one.get("name"), "triggered": False}
                    for one in call_actors
                    if one.get("entered") is False
                ),
                actor_decisions,
                self._context.bundle_dir,
            ),
        )

        if case is None:
            raise CallAborted(
                "voice_call_no_test_case: SimulationReport carried no test case",
                partial=base,
            )

        if case.status is TestCaseStatus.AGENT_UNAVAILABLE:
            # world-handle-interface.md: "the agent never joined" is a WORLD failure, not a
            # scenario one -- the agent is part of the world, so the scheduler retires it and
            # retries elsewhere. Verified against the engine's own source (engines/livekit.py):
            # this status fires ONLY on a readiness-stage timeout with a session already started
            # but no target dispatched -- exactly "dispatch fails, agent never joins," never a
            # mid-call condition.
            reason = (
                case.failure.message
                if case.failure is not None
                else "agent_unavailable"
            )
            raise WorldUnavailable(f"target agent never joined the room: {reason}")

        async def record_voice_usage(
            *,
            outcome: UsageOutcome = "completed",
            failure_domain: FailureDomain | None = None,
        ) -> None:
            if self._context.usage_reporter is None:
                return
            await asyncio.to_thread(
                self._context.usage_reporter.record,
                action="voice_call",
                scenario_key=scenario_key,
                amount=base.duration_ms / 60_000,
                funding=simulator_funding(self._environ),
                occurred_at=case_started_at,
                outcome=outcome,
                failure_domain=failure_domain,
            )

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

        # An intake agent may ask thirty to fifty questions, so a deadline is an ordinary outcome.
        ran_out_of_time = (
            case.status is TestCaseStatus.TIMED_OUT
            and turns >= _GRADEABLE_AFTER_TIMEOUT_TURNS
        )

        if (
            case.status is not TestCaseStatus.COMPLETED
            and not is_silent_agent
            and not ran_out_of_time
        ):
            reason = (
                case.failure.message if case.failure is not None else case.status.value
            )
            if case.failure is not None:
                safe_details = {
                    key: value
                    for key, value in (case.failure.details or {}).items()
                    if key in {
                        "operation", "exception_type", "provider_code", "http_status",
                        "sip_status_code", "stop_reason", "turn_count", "minimum_turn_count",
                    }
                }
                logger.warning(
                    "voice_call_failed run=%s case=%s code=%s stage=%s details=%s",
                    report.run_id, case.test_case_id, case.failure.code,
                    case.failure.stage.value, json.dumps(safe_details, sort_keys=True),
                )
            # C3 §4.5 step 2 (the one narrow call_runner change): the engine's dispatch-ack ladder
            # surfaces +60s exhaustion via a STRUCTURED marker (`failure.code`), never a substring
            # of `failure.message`. Pass it through on `CallAborted.marker` so the scheduler's
            # code-selection branch emits the `voice_dispatch_unacknowledged` receipt code instead
            # of the generic `call_failed`. No ladder logic lives here.
            marker = (
                case.failure.code
                if case.failure is not None
                and case.failure.code == _VOICE_DISPATCH_UNACKNOWLEDGED
                else None
            )
            attributed = _attributed_stall(case)
            if attributed is not None:
                code, reason = attributed
                await record_voice_usage(
                    outcome="failed",
                    failure_domain=failure_domain_for_code(code),
                )
                raise CallAborted(reason, partial=base, code=code)
            if (
                case.failure is not None
                and case.failure.code == "target_agent_tool_failed"
            ):
                await record_voice_usage(
                    outcome="failed",
                    failure_domain=failure_domain_for_code("target_agent_tool_failed"),
                )
                raise CallAborted(
                    case.failure.message,
                    partial=base,
                    code="target_agent_tool_failed",
                )
            await record_voice_usage(
                outcome="failed",
                failure_domain=failure_domain_for_code("voice_call_not_completed"),
            )
            raise CallAborted(
                f"voice_call_not_completed: {case.status.value}: {reason}",
                partial=base,
                marker=marker,
            )

        await record_voice_usage(
            outcome="failed" if is_silent_agent else "completed",
            failure_domain=(
                failure_domain_for_code("simulator_stalled")
                if is_silent_agent
                else None
            ),
        )
        # Never fabricate calls for a call that produced no conversation -- the scheduler's own
        # coverage guarantee turns an empty `calls` tuple into evidence_missing/simulator
        # regardless of turns (hosted_scheduler.py's own unconditioned-on-turns rule).
        calls = () if is_silent_agent else base.calls
        # Copied rather than rebuilt field by field: relisting them dropped `messages` silently,
        # and the judge then had no transcript to settle a spoken claim against.
        return replace(base, calls=calls)

    def _collect_calls(self, runtime: EnvironmentRuntime) -> tuple[Call, ...]:
        return collect_seam_calls(runtime, self._context.evidence_seam)
