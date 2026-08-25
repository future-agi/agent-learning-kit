"""Run one harness voice scenario through ALK's public SimulationRunner.

This is deliberately a thin adapter.  The harness owns the generated world and
exports the scenario/connection through environment variables; the SDK owns the
LiveKit room, simulated caller, transcript, recordings, and terminal status.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

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
from fi.simulate.runtime.runner import SimulationRunner


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing environment variable: {name}")
    return value


def _json_env(name: str, default):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    parsed = json.loads(raw)
    return parsed


# Language names a persona may carry, to the codes Deepgram STT expects. Unrecognised values that
# already look like a code are passed through; everything else falls back to English.
_LANGUAGE_CODES: dict[str, str] = {
    "english": "en", "hindi": "hi", "hinglish": "hi", "spanish": "es", "french": "fr",
    "german": "de", "portuguese": "pt", "italian": "it", "dutch": "nl", "japanese": "ja",
    "korean": "ko", "mandarin": "zh", "chinese": "zh", "arabic": "ar", "russian": "ru",
    "tamil": "ta", "telugu": "te", "bengali": "bn",
}


def _persona_stt_language() -> str:
    """The STT language for this call's caller, from the persona's languages.

    An explicit SIMULATOR_STT_LANGUAGE always wins. Otherwise the persona's first language is used,
    so a caller who speaks Hindi is transcribed as Hindi rather than forced to English.
    """
    override = os.environ.get("SIMULATOR_STT_LANGUAGE", "").strip()
    if override:
        return override
    raw = os.environ.get("HARNESS_PERSONA", "").strip()
    if raw:
        try:
            languages = (json.loads(raw) or {}).get("languages") or []
        except ValueError:
            languages = []
        if isinstance(languages, list) and languages:
            first = str(languages[0]).strip().lower()
            if first in _LANGUAGE_CODES:
                return _LANGUAGE_CODES[first]
            if 2 <= len(first) <= 5 and first.replace("-", "").isalpha():
                return first
    return "en"


def _simulator() -> simulate.SimulatorAgentDefinition:
    llm_provider = os.environ.get("SIMULATOR_LLM_PROVIDER", "google")
    stt_provider = os.environ.get("SIMULATOR_STT_PROVIDER", "deepgram")
    tts_provider = os.environ.get("SIMULATOR_TTS_PROVIDER", "deepgram")
    defaults = {
        "llm": {"google": "gemini-2.5-flash-lite", "openai": "gpt-4o-mini"},
        "stt": {"deepgram": "nova-2", "google": "chirp_2"},
        "tts": {"deepgram": "aura-asteria-en", "google": "en-US-Chirp3-HD-Aoede"},
    }

    def model(kind: str, provider: str) -> str:
        return os.environ.get(
            f"SIMULATOR_{kind.upper()}_MODEL", ""
        ).strip() or defaults[kind].get(
            provider.lower(), next(iter(defaults[kind].values()))
        )

    return simulate.SimulatorAgentDefinition(
        llm={
            "provider": llm_provider,
            "model": model("llm", llm_provider),
            "temperature": float(os.environ.get("SIMULATOR_LLM_TEMPERATURE", "0.35")),
        },
        stt={
            "provider": stt_provider,
            "model": model("stt", stt_provider),
            "language": _persona_stt_language(),
        },
        tts={
            "provider": tts_provider,
            "model": model("tts", tts_provider),
            "voice": os.environ.get("SIMULATOR_TTS_VOICE", "aura-asteria-en"),
        },
        instructions=(
            "Act as the customer described by the scenario. Speak naturally and briefly. "
            "Use only the supplied facts and never invent account, address, payment, or "
            "verification data. Do not volunteer private data: agree when asked whether a "
            "verification code should be sent, and disclose the actual code only after the "
            "agent says it was sent and explicitly asks you to read it. Answer repair questions "
            "with the missing fact, not by restarting the request. Never repeat the same answer "
            "more than twice. Wait for the agent to finish the task rather than ending as soon as "
            "it asks to proceed: say yes and let it complete and confirm the outcome. But if the "
            "agent gives essentially the same response two or three times without making progress, "
            "do not keep looping: say once that it is not working and that you will try again "
            "later, then end the call. Once the outcome is actually completed and confirmed, thank "
            "the agent and end the call."
        ),
        allow_interruptions=True,
    )


# Deepgram aura encodes the speaker in the model name, so a persona's accent selects a voice by
# choosing the aura model. Only English accents aura actually ships are mapped; anything else keeps
# the default so a caller never loses a voice to an accent the provider cannot render.
_AURA_BY_ACCENT: dict[str, dict[str, list[str]]] = {
    "american": {
        "female": ["aura-asteria-en", "aura-luna-en", "aura-hera-en", "aura-stella-en"],
        "male": ["aura-orion-en", "aura-arcas-en", "aura-perseus-en", "aura-zeus-en"],
    },
    "british": {"female": ["aura-athena-en"], "male": ["aura-helios-en"]},
    "irish": {"female": ["aura-athena-en"], "male": ["aura-angus-en"]},
    "australian": {"female": ["aura-athena-en"], "male": ["aura-helios-en"]},
}


def _aura_voice_for(persona: dict) -> str:
    """A stable aura voice for one caller, chosen by accent and gender.

    Callers who share an accent still differ: the voice within the accent's set is picked by the
    persona name, so a suite varies without being random between runs of the same scenario.
    """
    accent = str(persona.get("accent") or "").strip().lower()
    gender = str(persona.get("gender") or "").strip().lower()
    if gender not in ("male", "female"):
        gender = "female"
    bucket = next(
        (voices for key, voices in _AURA_BY_ACCENT.items() if key in accent),
        _AURA_BY_ACCENT["american"],
    )
    voices = bucket.get(gender) or next(iter(bucket.values()))
    index = sum(ord(character) for character in str(persona.get("name") or "")) % len(voices)
    return voices[index]


def _scenario() -> simulate.Scenario:
    fixture = _json_env("HARNESS_FIXTURE", {})
    persona = _json_env("HARNESS_PERSONA", {"name": "customer"})
    persona = dict(persona) if isinstance(persona, dict) else {"name": "customer"}
    persona["role"] = "customer"
    # Give the caller a voice from its accent when the suite runs on aura and none was set, so
    # different callers sound different and match the accent the scenario wrote.
    if (
        not persona.get("voice")
        and not persona.get("voice_id")
        and os.environ.get("SIMULATOR_TTS_PROVIDER", "deepgram").lower() == "deepgram"
    ):
        persona["voice"] = _aura_voice_for(persona)
    metadata = dict(persona.get("metadata") or {})
    if isinstance(fixture, dict) and fixture.get("phone"):
        # LiveKit exposes this as participant metadata/attributes. A target can
        # hydrate the correct seeded caller without knowing scenario internals.
        metadata["caller_phone"] = str(fixture["phone"])
    persona["metadata"] = metadata
    initial = os.environ.get("HARNESS_INITIAL_MESSAGE", "").strip()
    if initial:
        persona["initial_message"] = initial
    knowledge = [
        {
            "key": str(key),
            "value": json.dumps(value, ensure_ascii=False),
            "disclosure": "on_request",
        }
        for key, value in (fixture.items() if isinstance(fixture, dict) else [])
        if key != "origin"
    ]
    return simulate.Scenario(
        name=os.environ.get("HARNESS_SCENARIO", "harness-voice"),
        dataset=[
            simulate.Persona(
                persona=persona,
                situation=_required("HARNESS_INSTRUCTION"),
                outcome=os.environ.get(
                    "HARNESS_OUTCOME",
                    "Complete the requested task and close naturally.",
                ),
                knowledge=knowledge,
                behavior_policy={
                    "disclosure_policy": 0.72,
                    "cooperation_bounds": 0.9,
                    "repair_propensity": 0.85,
                },
            )
        ],
    )


def build_spec(run_id: str) -> SimulationSpec:
    direction = os.environ.get("HARNESS_CONVERSATION_DIRECTION", "agent_first")
    max_seconds = float(os.environ.get("VOICE_MAX_SECONDS", "300"))
    params = {
        "record_audio": True,
        "recording_root": str(_output_root() / run_id / "1.1.2" / "recordings"),
        "recording_case_directory": str(
            _output_root() / run_id / "1.1.2" / "recordings"
        ),
        "min_turn_messages": int(os.environ.get("VOICE_MIN_TURN_MESSAGES", "6")),
        "max_seconds": max_seconds,
        "connect_timeout": 60,
        "readiness_timeout": 120,
        "cleanup_timeout": 30,
        "conversation_direction": direction,
        "agent_first_silence_timeout_seconds": float(
            os.environ.get("VOICE_AGENT_FIRST_SILENCE_SECONDS", "45")
        ),
    }
    agent = simulate.AgentDefinition(
        name="harness-livekit-target",
        agent_name=_required("LIVEKIT_TARGET_AGENT_NAME"),
        system_prompt=_required("LIVEKIT_TARGET_SYSTEM_PROMPT"),
        transport={"kind": "webrtc"},
    )
    runtime = simulate.LiveKitSimulatorRuntime(
        url=os.environ.get("ACCEPTANCE_LIVEKIT_URL") or _required("LIVEKIT_URL"),
        room_name=f"harness-{run_id}",
        room_mode="managed",
    )
    run_seconds = max(300.0, max_seconds + 60 + 120 + 30 + 60)
    return SimulationSpec(
        run_id=run_id,
        environment=EnvironmentSpec(
            adapter="voice",
            world_kind="voice_telephony",
            config={
                "agent_definition": agent.model_dump(mode="json", exclude_none=True),
                "livekit_runtime": runtime.model_dump(mode="json", exclude_none=True),
                "simulator": _simulator().model_dump(mode="json", exclude_none=True),
                "params": params,
            },
        ),
        target=AgentEndpointSpec(adapter="webrtc"),
        simulator=SimulatorPolicySpec(adapter="livekit_simulator"),
        scenario=_scenario(),
        # Keep the canonical execution policy and the voice engine parameters
        # aligned. Dev's typed runner exposes direction at the spec level even
        # though the voice adapter currently hydrates the engine from params;
        # disagreement here makes planners and future adapters see the opposite
        # call direction from the engine that actually runs.
        execution=ExecutionPolicy(
            direction=direction,
            timeout=TimeoutPolicy(run_seconds=run_seconds),
        ),
    )


def _output_root() -> Path:
    return Path(
        os.environ.get("HARNESS_VOICE_OUTPUT_ROOT", "artifacts/simulation-acceptance")
    )


async def _run(run_id: str) -> int:
    report = await SimulationRunner().run(build_spec(run_id))
    output = _output_root() / run_id / "1.1.2"
    output.mkdir(parents=True, exist_ok=True)
    # The harness evidence reader still consumes the legacy TestReport envelope.
    # Keep that local compatibility boundary while execution itself uses the new
    # typed runner and report internally.
    legacy = report.to_legacy()
    (output / "report.json").write_text(
        legacy.model_dump_json(indent=2), encoding="utf-8"
    )
    (output / "canonical-report.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )
    case = report.test_cases[0] if report.test_cases else None
    print(
        json.dumps(
            {
                "run_id": run_id,
                "status": report.status.value,
                "test_case_status": case.status.value if case else "missing",
                "report": str(output / "canonical-report.json"),
            },
            indent=2,
        )
    )
    return 0 if case is not None and case.status.value == "completed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=("1.1.2",))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    run_id = new_run_id()
    if args.dry_run:
        print(build_spec(run_id).model_dump_json(indent=2))
        return 0
    return asyncio.run(_run(run_id))


if __name__ == "__main__":
    raise SystemExit(main())
