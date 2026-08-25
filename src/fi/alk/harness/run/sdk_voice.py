"""Run one harness voice scenario through ALK's public SimulationRunner.

This is deliberately a thin adapter.  The harness owns the generated world and
exports the scenario/connection through environment variables; the SDK owns the
LiveKit room, simulated caller, transcript, recordings, and terminal status.
"""

from __future__ import annotations

import argparse
import logging
import asyncio
import json
import os
from functools import lru_cache
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

logger = logging.getLogger(__name__)


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
# Language names and region codes to the code the providers expect. Ported from the platform
# so a persona resolves to the same language here as it does there. Anything unrecognised
# falls back to English, which is what the platform does too.
_LANGUAGE_CODES: dict[str, str] = {
    "ar": "ar", "ar-sa": "ar", "arabic": "ar", "bg": "bg",
    "bulgarian": "bg", "ca": "ca", "catalan": "ca", "chinese": "zh",
    "chinese (cantonese, traditional)": "zh-HK", "chinese (mandarin, simplified)": "zh", "chinese (mandarin, traditional)": "zh-TW", "cs": "cs",
    "czech": "cs", "da": "da", "da-dk": "da", "danish": "da",
    "de": "de", "de-ch": "de-CH", "dutch": "nl", "el": "el",
    "en": "en-US", "en-au": "en-AU", "en-gb": "en-GB", "en-in": "en-IN",
    "en-nz": "en-NZ", "en-us": "en-US", "english": "en-US", "es": "es",
    "es-419": "es-419", "estonian": "et", "et": "et", "fi": "fi",
    "finnish": "fi", "flemish": "nl-BE", "fr": "fr", "fr-ca": "fr-CA",
    "french": "fr", "german": "de", "greek": "el", "hi": "hi",
    "hindi": "hi", "hu": "hu", "hungarian": "hu", "id": "id",
    "indonesian": "id", "it": "it", "italian": "it", "ja": "ja",
    "japanese": "ja", "ko": "ko", "ko-kr": "ko", "korean": "ko",
    "latvian": "lv", "lithuanian": "lt", "lt": "lt", "lv": "lv",
    "malay": "ms", "ms": "ms", "nl": "nl", "nl-be": "nl-BE",
    "no": "no", "norwegian": "no", "pl": "pl", "polish": "pl",
    "portuguese": "pt", "pt": "pt", "pt-br": "pt-BR", "pt-pt": "pt-PT",
    "ro": "ro", "romanian": "ro", "ru": "ru", "russian": "ru",
    "sk": "sk", "slovak": "sk", "spanish": "es", "sv": "sv",
    "sv-se": "sv", "swedish": "sv", "th": "th", "th-th": "th",
    "thai": "th", "tr": "tr", "turkish": "tr", "uk": "uk",
    "ukrainian": "uk", "vi": "vi", "vietnamese": "vi", "zh": "zh",
    "zh-cn": "zh", "zh-hans": "zh", "zh-hant": "zh-TW", "zh-hk": "zh-HK",
    "zh-tw": "zh-TW",
}



def _normalised_language(raw: str) -> str:
    """The code the providers expect, from a language name or a region code.

    Ported from the platform: lowercase, strip, exact lookup, and anything unrecognised becomes
    English rather than failing the call.
    """
    return _LANGUAGE_CODES.get((raw or "").strip().lower(), "en-US")


# Languages we transcribe with Deepgram's multilingual model rather than a single language code.
# The platform sends Arabic to Azure, which we do not have, so it joins Spanish on the model that
# does cover it. Deliberate divergence: we only ever use providers we hold keys for.
_MULTILINGUAL_STT = ("ar", "es")


def _transcriber_for(language: str) -> tuple[str, str, str]:
    """The (provider, model, language) a persona's language needs for speech to text.

    Deepgram throughout, because Deepgram and Cartesia are the only providers configured. A
    language Deepgram serves better multilingually is sent to that model instead of its own code.
    """
    code = (language or "").lower()
    if code.split("-", 1)[0] in _MULTILINGUAL_STT:
        return ("deepgram", "nova-3", "multi")
    return ("deepgram", "nova-3", language or "en-US")


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


# Cartesia voice selection: a persona's accent (or, failing that, language) chooses a catalog
# language bucket, and gender chooses within it, so a caller sounds like the accent the scenario
# wrote across dozens of languages rather than the handful of English voices Deepgram aura ships.
# Accent wins over language; both accept ISO codes and demonyms. Runs only when a Cartesia key is
# present; otherwise the Deepgram aura path below is used unchanged.
_CARTESIA_SUPPORTED_LANGS = frozenset(
    {
        "en", "es", "hi", "de", "fr", "it", "pl", "ru", "pt", "ja", "ko", "zh", "tr", "sv",
        "nl", "no", "te", "kn", "fi", "mr", "da", "bn", "sk", "uk", "el", "ta", "vi", "id",
        "ro", "ka", "ml", "ms", "he", "bg", "th", "hu", "pa", "cs", "tl", "ar", "gu", "hr",
    }
)
_CARTESIA_ACCENT_TO_LANG: dict[str, str] = {
    "spanish": "es", "south american": "es", "indian": "hi", "german": "de", "french": "fr",
    "italian": "it", "polish": "pl", "russian": "ru", "portuguese": "pt", "brazilian": "pt",
    "japanese": "ja", "korean": "ko", "chinese": "zh", "mandarin": "zh", "turkish": "tr",
    "swedish": "sv", "dutch": "nl", "norwegian": "no", "finnish": "fi", "danish": "da",
    "slovak": "sk", "ukrainian": "uk", "greek": "el", "romanian": "ro", "georgian": "ka",
    "bulgarian": "bg", "thai": "th", "hungarian": "hu", "czech": "cs", "croatian": "hr",
    "vietnamese": "vi", "indonesian": "id", "malay": "ms", "malaysian": "ms", "tagalog": "tl",
    "filipino": "tl", "arabic": "ar", "hebrew": "he", "israeli": "he", "telugu": "te",
    "kannada": "kn", "marathi": "mr", "bengali": "bn", "tamil": "ta", "malayalam": "ml",
    "punjabi": "pa", "gujarati": "gu",
}
_CARTESIA_LANGUAGE_TO_LANG: dict[str, str] = {
    "english": "en", "hinglish": "hi", "spanish": "es", "hindi": "hi", "german": "de",
    "french": "fr", "italian": "it", "polish": "pl", "russian": "ru", "portuguese": "pt",
    "japanese": "ja", "korean": "ko", "chinese": "zh", "mandarin": "zh", "turkish": "tr",
    "swedish": "sv", "dutch": "nl", "norwegian": "no", "telugu": "te", "kannada": "kn",
    "finnish": "fi", "marathi": "mr", "danish": "da", "bengali": "bn", "slovak": "sk",
    "ukrainian": "uk", "greek": "el", "tamil": "ta", "vietnamese": "vi", "indonesian": "id",
    "romanian": "ro", "georgian": "ka", "malayalam": "ml", "malay": "ms", "hebrew": "he",
    "bulgarian": "bg", "thai": "th", "hungarian": "hu", "punjabi": "pa", "czech": "cs",
    "tagalog": "tl", "filipino": "tl", "arabic": "ar", "gujarati": "gu", "croatian": "hr",
}
_CARTESIA_DEFAULT_VOICE = "f786b574-daa5-4673-aa0c-cbe3e8534c02"


def _norm(value) -> str:
    return str(value or "").strip().lower().replace("-", " ")


@lru_cache(maxsize=1)
def _cartesia_catalog() -> dict:
    path = Path(__file__).parent / "data" / "voices_by_language_and_gender.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _persona_language_name(persona: dict) -> str:
    languages = persona.get("languages")
    if isinstance(languages, list) and languages:
        return _norm(languages[0])
    return _norm(persona.get("language"))


def _cartesia_lang_key(persona: dict) -> str:
    """The catalog language bucket for a persona: accent wins, then language, else English."""
    accent = _norm(persona.get("accent"))
    key = _CARTESIA_ACCENT_TO_LANG.get(accent)
    if key in _CARTESIA_SUPPORTED_LANGS:
        return key
    language = _persona_language_name(persona)
    key = _CARTESIA_LANGUAGE_TO_LANG.get(language)
    if key in _CARTESIA_SUPPORTED_LANGS:
        return key
    if language in _CARTESIA_SUPPORTED_LANGS:
        return language
    return "en"


def _cartesia_voice_for(persona: dict) -> str:
    """A stable Cartesia voice id for one caller, chosen by accent/language and gender.

    Deterministic by persona name so a caller keeps its voice across runs while a suite still
    spreads voices. Falls back across gender and to English when a long-tail language lacks one.
    """
    gender = _norm(persona.get("gender"))
    if gender not in ("male", "female"):
        gender = "female"
    catalog = _cartesia_catalog()
    key = _cartesia_lang_key(persona)
    other = "male" if gender == "female" else "female"
    voices = (
        (catalog.get(key) or {}).get(gender)
        or (catalog.get(key) or {}).get(other)
        or (catalog.get("en") or {}).get(gender)
        or []
    )
    if not voices:
        return _CARTESIA_DEFAULT_VOICE
    index = sum(ord(character) for character in str(persona.get("name") or "")) % len(voices)
    return voices[index]


def _voice_providers() -> tuple[str, str]:
    """The (stt, tts) providers for the caller. An explicit env override wins; otherwise Cartesia
    when its key is present (richer, multi-language voices), else Deepgram aura."""
    default = "cartesia" if os.environ.get("CARTESIA_API_KEY", "").strip() else "deepgram"
    stt = os.environ.get("SIMULATOR_STT_PROVIDER", "").strip() or default
    tts = os.environ.get("SIMULATOR_TTS_PROVIDER", "").strip() or default
    return stt, tts


def _simulator() -> simulate.SimulatorAgentDefinition:
    # The caller's brain is fixed on Vertex Gemini and only the model name is configurable. Its
    # voice is not: speech to text and text to speech follow the persona's language, because a
    # caller who speaks Japanese cannot be transcribed as English.
    llm_provider = os.environ.get("SIMULATOR_LLM_PROVIDER", "google")
    language = _persona_stt_language()
    stt_provider, stt_model, stt_language = _transcriber_for(language)
    _, tts_provider = _voice_providers()
    default_tts_voice = (
        _CARTESIA_DEFAULT_VOICE if tts_provider == "cartesia" else "aura-asteria-en"
    )
    defaults = {
        "llm": {"google": "gemini-2.5-flash", "openai": "gpt-4o-mini"},
        "stt": {"deepgram": stt_model or "nova-3", "cartesia": "ink-2", "google": "chirp_2"},
        "tts": {
            "deepgram": "aura-asteria-en",
            "cartesia": "sonic-3.5",
            "google": "en-US-Chirp3-HD-Aoede",
        },
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
            "language": stt_language,
        },
        tts={
            "provider": tts_provider,
            "model": model("tts", tts_provider),
            "voice": os.environ.get("SIMULATOR_TTS_VOICE", default_tts_voice),
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
    # Give the caller a voice from its accent/language when none was set, so different callers
    # sound different and match what the scenario wrote. Cartesia draws from the multi-language
    # catalog; Deepgram falls back to the aura voices it ships.
    if not persona.get("voice") and not persona.get("voice_id"):
        tts_provider = _voice_providers()[1].lower()
        if tts_provider == "cartesia":
            persona["voice"] = _cartesia_voice_for(persona)
        elif tts_provider == "deepgram":
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
