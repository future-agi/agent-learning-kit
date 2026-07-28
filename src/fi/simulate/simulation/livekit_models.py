from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType

import aiohttp
from livekit.agents import llm as livekit_llm
from livekit.agents import stt as livekit_stt
from livekit.agents import tts as livekit_tts

from fi.simulate.agent.definition import LLMConfig, STTConfig, TTSConfig


@dataclass
class LiveKitModels:
    stt: livekit_stt.STT
    llm: livekit_llm.LLM
    tts: livekit_tts.TTS
    http_session: aiohttp.ClientSession | None = None

    async def aclose(self) -> None:
        if self.http_session is not None and not self.http_session.closed:
            await self.http_session.close()


STTFactory = Callable[[STTConfig, aiohttp.ClientSession | None], livekit_stt.STT]
LLMFactory = Callable[[LLMConfig], livekit_llm.LLM]
TTSFactory = Callable[[TTSConfig, aiohttp.ClientSession | None], livekit_tts.TTS]


def _import_plugin(name: str) -> ModuleType:
    try:
        import importlib
        return importlib.import_module(f"livekit.plugins.{name}")
    except ImportError:
        raise ImportError(
            f"livekit-plugins-{name} is not installed. "
            f"Install it with: pip install livekit-plugins-{name}"
        ) from None


def _openai_llm(config: LLMConfig) -> livekit_llm.LLM:
    openai = _import_plugin("openai")
    return openai.LLM(model=config.model, temperature=config.temperature)


def _openai_stt(
    config: STTConfig,
    _http_session: aiohttp.ClientSession | None,
) -> livekit_stt.STT:
    openai = _import_plugin("openai")
    return openai.STT(
        model=config.model,
        language=config.language or "en",
    )


def _elevenlabs_stt(
    config: STTConfig,
    http_session: aiohttp.ClientSession | None,
) -> livekit_stt.STT:
    elevenlabs = _import_plugin("elevenlabs")
    return elevenlabs.STT(
        api_key=_required_env("ELEVEN_API_KEY", "ELEVENLABS_API_KEY"),
        http_session=http_session,
        model_id=_provider_model(
            config.model,
            default="gpt-4o-mini-transcribe",
            replacement="scribe_v2_realtime",
        ),
        server_vad={
            "vad_silence_threshold_secs": 0.8,
            "vad_threshold": 0.4,
            "min_speech_duration_ms": 100,
            "min_silence_duration_ms": 500,
        },
    )


def _deepgram_stt(
    config: STTConfig,
    http_session: aiohttp.ClientSession | None,
) -> livekit_stt.STT:
    deepgram = _import_plugin("deepgram")
    return deepgram.STT(
        api_key=_required_env("DEEPGRAM_API_KEY"),
        http_session=http_session,
        model=_provider_model(
            config.model,
            default="gpt-4o-mini-transcribe",
            replacement="nova-3",
        ),
        language=config.language or "en-US",
    )


def _openai_tts(
    config: TTSConfig,
    _http_session: aiohttp.ClientSession | None,
) -> livekit_tts.TTS:
    openai = _import_plugin("openai")
    return openai.TTS(model=config.model, voice=config.voice)


def _elevenlabs_tts(
    config: TTSConfig,
    http_session: aiohttp.ClientSession | None,
) -> livekit_tts.TTS:
    elevenlabs = _import_plugin("elevenlabs")
    return elevenlabs.TTS(
        api_key=_required_env("ELEVEN_API_KEY", "ELEVENLABS_API_KEY"),
        http_session=http_session,
        model=_provider_model(
            config.model,
            default="gpt-4o-mini-tts",
            replacement="eleven_flash_v2_5",
        ),
        voice_id=config.voice,
    )


def _deepgram_tts(
    config: TTSConfig,
    http_session: aiohttp.ClientSession | None,
) -> livekit_tts.TTS:
    deepgram = _import_plugin("deepgram")
    return deepgram.TTS(
        api_key=_required_env("DEEPGRAM_API_KEY"),
        http_session=http_session,
        model=_provider_model(
            config.model,
            default="gpt-4o-mini-tts",
            replacement="aura-2-andromeda-en",
        ),
    )


_LLM_FACTORIES: dict[str, LLMFactory] = {
    "openai": _openai_llm,
    "openai_compatible": _openai_llm,
}
_STT_FACTORIES: dict[str, STTFactory] = {
    "openai": _openai_stt,
    "elevenlabs": _elevenlabs_stt,
    "deepgram": _deepgram_stt,
}
_TTS_FACTORIES: dict[str, TTSFactory] = {
    "openai": _openai_tts,
    "elevenlabs": _elevenlabs_tts,
    "deepgram": _deepgram_tts,
}
_HTTP_PROVIDERS = {"deepgram", "elevenlabs"}


async def build_livekit_models(
    *,
    llm_config: LLMConfig,
    stt_config: STTConfig,
    tts_config: TTSConfig,
) -> LiveKitModels:
    llm_provider = llm_config.provider.lower()
    stt_provider = stt_config.provider.lower()
    tts_provider = tts_config.provider.lower()
    llm_factory = _factory(_LLM_FACTORIES, llm_provider, "LLM")
    stt_factory = _factory(_STT_FACTORIES, stt_provider, "STT")
    tts_factory = _factory(_TTS_FACTORIES, tts_provider, "TTS")
    http_session = (
        aiohttp.ClientSession()
        if {stt_provider, tts_provider} & _HTTP_PROVIDERS
        else None
    )
    try:
        return LiveKitModels(
            stt=stt_factory(stt_config, http_session),
            llm=llm_factory(llm_config),
            tts=tts_factory(tts_config, http_session),
            http_session=http_session,
        )
    except Exception:
        if http_session is not None:
            await http_session.close()
        raise


def _factory(factories: dict[str, Callable], provider: str, model_type: str):
    factory = factories.get(provider)
    if factory is None:
        supported = ", ".join(sorted(factories))
        raise ValueError(
            f"Unsupported LiveKit {model_type} provider: {provider!r}. "
            f"Supported: {supported}"
        )
    return factory


def _required_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    raise ValueError(f"Missing provider credential: {' or '.join(names)}")


def _provider_model(configured: str, *, default: str, replacement: str) -> str:
    return replacement if configured == default else configured
