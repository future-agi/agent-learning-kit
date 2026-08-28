"""Caller voice and behaviour settings shared by the local and hosted lanes.

Both lanes build the same simulated customer. Keeping the rules and the provider choice here
means a change lands in both rather than in whichever one the author had open.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from fi import simulate
from fi.simulate.runtime import (
    AgentEndpointSpec,
    EnvironmentSpec,
    ExecutionPolicy,
    SimulationSpec,
    SimulatorPolicySpec,
    TimeoutPolicy,
)

logger = logging.getLogger(__name__)

CARTESIA_DEFAULT_VOICE = "f786b574-daa5-4673-aa0c-cbe3e8534c02"

CONNECT_TIMEOUT_SECONDS = 60.0
READINESS_TIMEOUT_SECONDS = 120.0
CLEANUP_TIMEOUT_SECONDS = 30.0

_TARGET_NAME = "harness-livekit-target"
_BEHAVIOR_POLICY = {
    "disclosure_policy": 0.72,
    "cooperation_bounds": 0.9,
    "repair_propensity": 0.85,
}

# Languages transcribed with Deepgram's multilingual model rather than a single language code.
_MULTILINGUAL_STT = ("ar", "es")

# Written as separate numbered rules rather than one paragraph. These arrive late in a long
# prompt, and a rule buried mid-sentence there does not survive: a caller ignored the loop rule
# for four turns while it was the tail of a compound sentence.
SIMULATOR_INSTRUCTIONS = (
    "Act as the customer described by the scenario. Speak naturally and briefly.\n"
    "These rules override anything else when they conflict:\n"
    "1. Use ONLY the facts you were given. Never invent an account detail, address, "
    "payment state, or verification code.\n"
    "2. If the agent asks about something you were given no fact for, say plainly that "
    "you do not know or cannot tell. Never guess, and never claim something happened on "
    "your end when you were not told it did.\n"
    "3. Do not volunteer private data. Agree when asked whether a verification code "
    "should be sent, and read the code out only after the agent says it was sent and "
    "asks you for it.\n"
    "4. Answer a repair question with the missing fact, not by restarting your request.\n"
    "5. STOP AFTER THREE. Count the agent's replies. If three of them say essentially "
    "the same thing without the task moving forward, do not try a fifth time and do not "
    "rephrase the same point again. Say once that this is not working and you will try "
    "later, then end the call.\n"
    "6. Otherwise let the agent finish. Say yes when it asks to proceed and wait for it "
    "to confirm the outcome rather than hanging up early.\n"
    "7. Once the outcome is confirmed, thank the agent and end the call."
)

_LANGUAGE_CODES: dict[str, str] = {
    "ar": "ar",
    "ar-sa": "ar",
    "arabic": "ar",
    "bg": "bg",
    "bulgarian": "bg",
    "ca": "ca",
    "catalan": "ca",
    "chinese": "zh",
    "chinese simplified": "zh",
    "chinese traditional": "zh-TW",
    "chinese (cantonese, traditional)": "zh-HK",
    "chinese (mandarin, simplified)": "zh",
    "chinese (mandarin, traditional)": "zh-TW",
    "cs": "cs",
    "czech": "cs",
    "da": "da",
    "da-dk": "da",
    "danish": "da",
    "de": "de",
    "de-ch": "de-CH",
    "dutch": "nl",
    "el": "el",
    "en": "en-US",
    "en-au": "en-AU",
    "en-gb": "en-GB",
    "en-in": "en-IN",
    "en-nz": "en-NZ",
    "en-us": "en-US",
    "english": "en-US",
    "es": "es",
    "es-419": "es-419",
    "estonian": "et",
    "et": "et",
    "fi": "fi",
    "finnish": "fi",
    "flemish": "nl-BE",
    "fr": "fr",
    "fr-ca": "fr-CA",
    "french": "fr",
    "german": "de",
    "greek": "el",
    "hi": "hi",
    "hindi": "hi",
    "hu": "hu",
    "hungarian": "hu",
    "id": "id",
    "indonesian": "id",
    "it": "it",
    "italian": "it",
    "ja": "ja",
    "japanese": "ja",
    "ko": "ko",
    "ko-kr": "ko",
    "korean": "ko",
    "latvian": "lv",
    "lithuanian": "lt",
    "lt": "lt",
    "lv": "lv",
    "malay": "ms",
    "ms": "ms",
    "nl": "nl",
    "nl-be": "nl-BE",
    "no": "no",
    "norwegian": "no",
    "pl": "pl",
    "polish": "pl",
    "portuguese": "pt",
    "pt": "pt",
    "pt-br": "pt-BR",
    "pt-pt": "pt-PT",
    "ro": "ro",
    "romanian": "ro",
    "ru": "ru",
    "russian": "ru",
    "sk": "sk",
    "slovak": "sk",
    "spanish": "es",
    "sv": "sv",
    "sv-se": "sv",
    "swedish": "sv",
    "th": "th",
    "th-th": "th",
    "thai": "th",
    "tr": "tr",
    "turkish": "tr",
    "uk": "uk",
    "ukrainian": "uk",
    "vi": "vi",
    "vietnamese": "vi",
    "zh": "zh",
    "zh-cn": "zh",
    "zh-hans": "zh",
    "zh-hant": "zh-TW",
    "zh-hk": "zh-HK",
    "zh-tw": "zh-TW",
}


def voice_providers(get: Callable[[str], str]) -> tuple[str, str]:
    """The (stt, tts) providers for the caller.

    An explicit override wins; otherwise Cartesia when its key is present (richer, multi-language
    voices), else Deepgram aura. `get` resolves a setting name for the calling lane, which reads
    the environment locally and the run-scoped secrets when hosted.
    """
    keyed = bool((get("CARTESIA_API_KEY") or "").strip())
    default = "cartesia" if keyed else "deepgram"
    stt = (get("SIMULATOR_STT_PROVIDER") or "").strip() or default
    tts = (get("SIMULATOR_TTS_PROVIDER") or "").strip() or default
    if tts == "deepgram" and not keyed and not (get("SIMULATOR_TTS_PROVIDER") or ""):
        # Deepgram aura is one voice, so every persona sounds the same and the accent, language
        # and gender the scenario chose are silently dropped. The call still runs, which is why
        # this has to be said out loud rather than left to whoever listens to the recording.
        logger.warning(
            "cartesia_key_missing_personas_share_one_voice",
            extra={"tts": "deepgram/aura-asteria-en"},
        )
    return stt, tts


def transcriber_for(language: str) -> tuple[str, str, str]:
    """The (provider, model, language) a persona's language needs for speech to text.

    Deepgram throughout, because Deepgram and Cartesia are the only providers configured. A
    language Deepgram serves better multilingually is sent to that model instead of its own code.
    """
    code = (language or "").lower()
    if code.split("-", 1)[0] in _MULTILINGUAL_STT:
        return ("deepgram", "nova-3", "multi")
    return ("deepgram", "nova-3", language or "en-US")


def persona_stt_language(
    persona: Mapping[str, object] | None, override: str = ""
) -> str:
    """The STT language for one caller, from the persona's languages.

    An explicit override always wins. Otherwise the persona's first language is used, so a caller
    who speaks Hindi is transcribed as Hindi rather than forced to English.
    """
    if override and override.strip():
        return override.strip()
    languages = (persona or {}).get("languages") or []
    if isinstance(languages, list) and languages:
        first = str(languages[0]).strip().lower()
        if first in _LANGUAGE_CODES:
            return _LANGUAGE_CODES[first]
        if 2 <= len(first) <= 5 and first.replace("-", "").isalpha():
            return first
    return "en"


_CARTESIA_SUPPORTED_LANGS = frozenset(
    {
        "en",
        "es",
        "hi",
        "de",
        "fr",
        "it",
        "pl",
        "ru",
        "pt",
        "ja",
        "ko",
        "zh",
        "tr",
        "sv",
        "nl",
        "no",
        "te",
        "kn",
        "fi",
        "mr",
        "da",
        "bn",
        "sk",
        "uk",
        "el",
        "ta",
        "vi",
        "id",
        "ro",
        "ka",
        "ml",
        "ms",
        "he",
        "bg",
        "th",
        "hu",
        "pa",
        "cs",
        "tl",
        "ar",
        "gu",
        "hr",
    }
)
_CARTESIA_ACCENT_TO_LANG: dict[str, str] = {
    "spanish": "es",
    "south american": "es",
    "indian": "hi",
    "german": "de",
    "french": "fr",
    "italian": "it",
    "polish": "pl",
    "russian": "ru",
    "portuguese": "pt",
    "brazilian": "pt",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
    "mandarin": "zh",
    "turkish": "tr",
    "swedish": "sv",
    "dutch": "nl",
    "norwegian": "no",
    "finnish": "fi",
    "danish": "da",
    "slovak": "sk",
    "ukrainian": "uk",
    "greek": "el",
    "romanian": "ro",
    "georgian": "ka",
    "bulgarian": "bg",
    "thai": "th",
    "hungarian": "hu",
    "czech": "cs",
    "croatian": "hr",
    "vietnamese": "vi",
    "indonesian": "id",
    "malay": "ms",
    "malaysian": "ms",
    "tagalog": "tl",
    "filipino": "tl",
    "arabic": "ar",
    "hebrew": "he",
    "israeli": "he",
    "telugu": "te",
    "kannada": "kn",
    "marathi": "mr",
    "bengali": "bn",
    "tamil": "ta",
    "malayalam": "ml",
    "punjabi": "pa",
    "gujarati": "gu",
}
_CARTESIA_LANGUAGE_TO_LANG: dict[str, str] = {
    "english": "en",
    "chinese simplified": "zh",
    "chinese traditional": "zh",
    "hinglish": "hi",
    "spanish": "es",
    "hindi": "hi",
    "german": "de",
    "french": "fr",
    "italian": "it",
    "polish": "pl",
    "russian": "ru",
    "portuguese": "pt",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
    "mandarin": "zh",
    "turkish": "tr",
    "swedish": "sv",
    "dutch": "nl",
    "norwegian": "no",
    "telugu": "te",
    "kannada": "kn",
    "finnish": "fi",
    "marathi": "mr",
    "danish": "da",
    "bengali": "bn",
    "slovak": "sk",
    "ukrainian": "uk",
    "greek": "el",
    "tamil": "ta",
    "vietnamese": "vi",
    "indonesian": "id",
    "romanian": "ro",
    "georgian": "ka",
    "malayalam": "ml",
    "malay": "ms",
    "hebrew": "he",
    "bulgarian": "bg",
    "thai": "th",
    "hungarian": "hu",
    "punjabi": "pa",
    "czech": "cs",
    "tagalog": "tl",
    "filipino": "tl",
    "arabic": "ar",
    "gujarati": "gu",
    "croatian": "hr",
}
def _norm(value) -> str:
    return str(value or "").strip().lower().replace("-", " ")


@lru_cache(maxsize=1)
def _cartesia_catalog() -> dict:
    path = Path(__file__).parent / "run" / "data" / "voices_by_language_and_gender.json"
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


def cartesia_voice_for(persona: dict) -> str:
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
        return CARTESIA_DEFAULT_VOICE
    index = sum(ord(character) for character in str(persona.get("name") or "")) % len(
        voices
    )
    return voices[index]





_AURA_BY_ACCENT: dict[str, dict[str, list[str]]] = {
    "american": {
        "female": ["aura-asteria-en", "aura-luna-en", "aura-hera-en", "aura-stella-en"],
        "male": ["aura-orion-en", "aura-arcas-en", "aura-perseus-en", "aura-zeus-en"],
    },
    "british": {"female": ["aura-athena-en"], "male": ["aura-helios-en"]},
    "irish": {"female": ["aura-athena-en"], "male": ["aura-angus-en"]},
    "australian": {"female": ["aura-athena-en"], "male": ["aura-helios-en"]},
}


def aura_voice_for(persona: dict) -> str:
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
    index = sum(ord(character) for character in str(persona.get("name") or "")) % len(
        voices
    )
    return voices[index]


def simulator_definition(
    get: Callable[[str], str], persona: Mapping[str, Any] | None = None
) -> "simulate.SimulatorAgentDefinition":
    """The caller's brain and voice. `get` resolves one setting for the calling lane.

    Speech follows the persona's language: a caller who speaks Japanese cannot be transcribed
    as English.
    """
    llm_provider = (get("SIMULATOR_LLM_PROVIDER") or "").strip() or "google"
    stt_override = (get("SIMULATOR_STT_PROVIDER") or "").strip()
    _, tts_provider = voice_providers(get)
    language = persona_stt_language(
        dict(persona or {}), (get("SIMULATOR_STT_LANGUAGE") or "").strip()
    )
    stt_default_provider, stt_model, stt_language = transcriber_for(language)
    stt_provider = stt_override or stt_default_provider
    defaults = {
        "llm": {"google": "gemini-2.5-flash", "openai": "gpt-4o-mini"},
        "stt": {"deepgram": stt_model, "cartesia": "ink-2", "google": "chirp_2"},
        "tts": {
            "deepgram": "aura-asteria-en",
            "cartesia": "sonic-3.5",
            "google": "en-US-Chirp3-HD-Aoede",
        },
    }

    def model(kind: str, provider: str) -> str:
        return (get(f"SIMULATOR_{kind.upper()}_MODEL") or "").strip() or defaults[
            kind
        ].get(provider.lower(), next(iter(defaults[kind].values())))

    # Voice and model are different fields: aura encodes the speaker in the model name, Cartesia
    # takes a voice id. Sending the model as the voice silently breaks Cartesia.
    default_voice = (
        CARTESIA_DEFAULT_VOICE
        if tts_provider.lower() == "cartesia"
        else "aura-asteria-en"
    )
    return simulate.SimulatorAgentDefinition(
        llm={
            "provider": llm_provider,
            "model": model("llm", llm_provider),
            "temperature": float(
                (get("SIMULATOR_LLM_TEMPERATURE") or "").strip() or "0.35"
            ),
        },
        stt={
            "provider": stt_provider,
            "model": model("stt", stt_provider),
            "language": stt_language,
        },
        tts={
            "provider": tts_provider,
            "model": model("tts", tts_provider),
            "voice": (get("SIMULATOR_TTS_VOICE") or "").strip() or default_voice,
        },
        instructions=SIMULATOR_INSTRUCTIONS,
        allow_interruptions=True,
    )


_PHONE_ALIASES = ("phone", "caller_phone", "caller_ani", "ani")


def fixture_caller_phone(fixture: Mapping[str, Any] | None) -> str:
    """The number the target must see for this scenario, wherever the fixture nested it.

    Prose is not an identity transport. Miss this and every persona falls back to the worker's
    demo ANI, so they all query the same rider and exercise the wrong cards, places and OTP rows.
    """

    def find(value: Any) -> str:
        if isinstance(value, Mapping):
            for name in _PHONE_ALIASES:
                candidate = str(value.get(name) or "").strip()
                if candidate:
                    return candidate
            for nested in value.values():
                candidate = find(nested)
                if candidate:
                    return candidate
        return ""

    return find(fixture)


def caller_scenario(
    *,
    name: str,
    persona: Mapping[str, Any] | None,
    situation: str,
    fixture: Mapping[str, Any] | None,
    tts_provider: str,
    outcome: str = "",
    initial_message: str = "",
) -> "simulate.Scenario":
    """One simulated caller.

    `outcome` is empty by default: the situation already says what this person wants in their own
    words, and the grading criteria as an objective make the caller recite a checklist.
    """
    persona = dict(persona) if isinstance(persona, Mapping) else {"name": "customer"}
    persona["role"] = "customer"
    provider = (tts_provider or "").lower()
    # A voice from the persona's accent/language, so callers in one suite sound different.
    if not persona.get("voice") and not persona.get("voice_id"):
        if provider == "cartesia":
            persona["voice"] = cartesia_voice_for(persona)
        elif provider == "deepgram":
            persona["voice"] = aura_voice_for(persona)
    fixture = fixture if isinstance(fixture, Mapping) else {}
    metadata = dict(persona.get("metadata") or {})
    if caller_phone := fixture_caller_phone(fixture):
        # LiveKit exposes this as participant metadata/attributes, so a target hydrates the
        # seeded caller without knowing scenario internals.
        metadata["caller_phone"] = caller_phone
    persona["metadata"] = metadata
    if initial_message.strip():
        persona["initial_message"] = initial_message.strip()
    knowledge = [
        {
            "key": str(key),
            "value": json.dumps(value, ensure_ascii=False, default=str),
            "disclosure": "on_request",
        }
        for key, value in fixture.items()
        if key != "origin"
    ]
    return simulate.Scenario(
        name=name or "harness-voice",
        dataset=[
            simulate.Persona(
                persona=persona,
                situation=situation,
                outcome=outcome,
                knowledge=knowledge,
                behavior_policy=dict(_BEHAVIOR_POLICY),
            )
        ],
    )


def simulation_spec(
    *,
    run_id: str,
    room_name: str,
    agent_name: str,
    system_prompt: str,
    livekit_url: str,
    recording_dir: Path,
    scenario: "simulate.Scenario",
    simulator: "simulate.SimulatorAgentDefinition",
    direction: str,
    max_seconds: float,
    min_turn_messages: int,
    agent_first_silence_seconds: float,
    run_seconds: float,
) -> SimulationSpec:
    """The voice run both lanes execute. Only the values differ between them."""
    params = {
        "record_audio": True,
        "recording_root": str(recording_dir),
        "recording_case_directory": str(recording_dir),
        "min_turn_messages": min_turn_messages,
        "max_seconds": max_seconds,
        "connect_timeout": CONNECT_TIMEOUT_SECONDS,
        "readiness_timeout": READINESS_TIMEOUT_SECONDS,
        "cleanup_timeout": CLEANUP_TIMEOUT_SECONDS,
        "conversation_direction": direction,
        "agent_first_silence_timeout_seconds": agent_first_silence_seconds,
    }
    agent = simulate.AgentDefinition(
        name=_TARGET_NAME,
        agent_name=agent_name,
        system_prompt=system_prompt,
        transport={"kind": "webrtc"},
    )
    runtime = simulate.LiveKitSimulatorRuntime(
        url=livekit_url, room_name=room_name, room_mode="managed"
    )
    return SimulationSpec(
        run_id=run_id,
        environment=EnvironmentSpec(
            adapter="voice",
            world_kind="voice_telephony",
            config={
                "agent_definition": agent.model_dump(mode="json", exclude_none=True),
                "livekit_runtime": runtime.model_dump(mode="json", exclude_none=True),
                "simulator": simulator.model_dump(mode="json", exclude_none=True),
                "params": params,
            },
        ),
        target=AgentEndpointSpec(adapter="webrtc"),
        simulator=SimulatorPolicySpec(adapter="livekit_simulator"),
        scenario=scenario,
        # Keep the execution policy and the engine parameters aligned: disagreement makes
        # planners see the opposite call direction from the engine that actually runs.
        execution=ExecutionPolicy(
            direction=direction, timeout=TimeoutPolicy(run_seconds=run_seconds)
        ),
    )


__all__ = [
    "CARTESIA_DEFAULT_VOICE",
    "CLEANUP_TIMEOUT_SECONDS",
    "CONNECT_TIMEOUT_SECONDS",
    "READINESS_TIMEOUT_SECONDS",
    "SIMULATOR_INSTRUCTIONS",
    "aura_voice_for",
    "caller_scenario",
    "fixture_caller_phone",
    "cartesia_voice_for",
    "persona_stt_language",
    "simulation_spec",
    "simulator_definition",
    "transcriber_for",
    "voice_providers",
]
