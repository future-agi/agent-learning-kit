import re
from typing import Literal, Optional
from pydantic import BaseModel, Field, AnyUrl, model_validator


_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


class TelephonyTransport(BaseModel):
    """Optional telephony transport for a LiveKit-backed target.

    Default (or omitted): WebRTC — the SDK connects to the room over WS
    and the target is dispatched as a registered agent, unchanged.

    ``sip_outbound``: the SDK creates the per-case room and dials
    ``sip_call_to`` through ``sip_trunk_id``; the target answers on the
    phone. Requires ``sip_trunk_id`` and E.164 ``sip_call_to``.

    ``sip_inbound``: the SDK does not dial; a dispatch rule routes an
    incoming call into the per-case room. Requires ``dispatch_rule_name``
    so the rule is verifiable before the run.
    """

    kind: Literal["webrtc", "sip_outbound", "sip_inbound"] = Field(
        "webrtc",
        description="Transport used to reach the target participant.",
    )
    sip_trunk_id: Optional[str] = Field(
        None,
        description="LiveKit outbound SIP trunk ID (sip_outbound only).",
    )
    sip_call_to: Optional[str] = Field(
        None,
        description="E.164 phone number to dial (sip_outbound only).",
    )
    sip_number: Optional[str] = Field(
        None,
        description="E.164 originating caller ID (sip_outbound only).",
    )
    participant_identity: Optional[str] = Field(
        None,
        description=(
            "Template for the SIP participant identity. May contain "
            "{test_case_id} / {run_id}. Defaults to sip-caller-{test_case_id}."
        ),
    )
    dispatch_rule_name: Optional[str] = Field(
        None,
        description="Dispatch rule that routes the inbound call (sip_inbound only).",
    )
    readiness_timeout_seconds: Optional[float] = Field(
        None,
        gt=0,
        description="Seconds to wait for the inbound SIP participant to appear.",
    )

    @model_validator(mode="after")
    def _check_kind_fields(self) -> "TelephonyTransport":
        if self.kind == "sip_outbound":
            if not self.sip_trunk_id or not self.sip_trunk_id.strip():
                raise ValueError("sip_outbound requires sip_trunk_id")
            if not self.sip_call_to or not _E164.match(self.sip_call_to):
                raise ValueError("sip_outbound requires E.164 sip_call_to (e.g. +14155551234)")
            if not self.sip_number or not _E164.match(self.sip_number):
                raise ValueError("sip_outbound requires E.164 sip_number (e.g. +14155551234)")
        elif self.kind == "sip_inbound":
            if not self.dispatch_rule_name or not self.dispatch_rule_name.strip():
                raise ValueError("sip_inbound requires dispatch_rule_name")
        elif self.kind == "webrtc":
            if any(
                [
                    self.sip_trunk_id,
                    self.sip_call_to,
                    self.sip_number,
                    self.dispatch_rule_name,
                ]
            ):
                raise ValueError("webrtc transport cannot set SIP fields")
        return self

class LLMConfig(BaseModel):
    """Configuration for the simulator language model."""
    provider: str = Field("openai", description="The LiveKit LLM provider.")
    model: str = Field("gpt-4o", description="The language model to use.")
    temperature: float = Field(0.7, ge=0.0, le=2.0, description="Controls randomness in the LLM's output.")

class TTSConfig(BaseModel):
    """Configuration for simulator text-to-speech."""
    provider: str = Field("openai", description="The LiveKit TTS provider.")
    model: str = Field("gpt-4o-mini-tts", description="The TTS model to use.")
    voice: str = Field("alloy", description="The voice or voice ID to use.")

class STTConfig(BaseModel):
    """Configuration for simulator speech-to-text."""
    provider: str = Field("openai", description="The LiveKit STT provider.")
    model: str = Field(
        "gpt-4o-mini-transcribe",
        description="The STT model to use.",
    )
    language: Optional[str] = Field("en", description="The transcription language.")

class VADConfig(BaseModel):
    """Configuration for Voice Activity Detection (VAD)."""
    provider: str = Field("silero", description="The VAD provider to use. 'silero' is recommended.")
    min_silence_duration: float = Field(0.1, description="Minimum duration of silence to consider as the end of a speech segment.")
    speech_pad_ms: int = Field(200, description="Additional padding in milliseconds to add to the end of a speech segment.")

class AgentDefinition(BaseModel):
    """
    The core configuration for a voice AI agent.
    """
    name: str = Field(..., description="A unique name for the agent.")
    description: Optional[str] = Field(None, description="A brief description of the agent's purpose.")
    url: AnyUrl = Field(..., description="The WebRTC URL (e.g., LiveKit server URL) the agent will connect to.")
    room_name: str = Field(..., description="The room name or managed-room prefix.")
    agent_name: Optional[str] = Field(
        None,
        description="Exact registered LiveKit agent name used for managed dispatch.",
    )
    room_mode: Literal["external", "managed"] = Field(
        "external",
        description="Whether the SDK joins an existing room or owns room lifecycle.",
    )
    target_participant_identity: Optional[str] = Field(
        None,
        description="Exact target participant identity when it is known in advance.",
    )
    transport: Optional[TelephonyTransport] = Field(
        None,
        description="Optional telephony transport; omitted = WebRTC (unchanged).",
    )

    system_prompt: str = Field(..., description="The main system prompt or instructions that define the agent's behavior.")
    
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    initial_message: str = Field("Hello! How can I help you today?", description="The first message the agent speaks to start the conversation.")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "name": "openai-support-agent",
                "url": "wss://your-livekit-server.com",
                "room_name": "agent-room-123",
                "system_prompt": "You are a friendly and helpful support agent."
            }
        }

class SimulatorAgentDefinition(BaseModel):
    """
    Configuration for the simulated customer persona agent used by the TestRunner.

    This is intentionally separate from the deployed AgentDefinition so tests can
    run with lightweight/cheaper models and different voice/transcription settings.
    """

    name: Optional[str] = Field(None, description="Optional label for the simulator agent")
    instructions: Optional[str] = Field(
        None,
        description="Optional base instructions for the simulator agent. If omitted, the TestRunner persona prompt is used.",
    )

    llm: LLMConfig = Field(default_factory=lambda: LLMConfig(model="gpt-4o-mini", temperature=0.6))
    tts: TTSConfig = Field(default_factory=TTSConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    vad: VADConfig = Field(default_factory=VADConfig)

    allow_interruptions: Optional[bool] = Field(
        None,
        description="Whether the simulator agent allows interruptions during TTS.",
    )
    min_endpointing_delay: Optional[float] = Field(
        None,
        description="Minimum endpointing delay (s) to declare end of user turn.",
    )
    max_endpointing_delay: Optional[float] = Field(
        None,
        description="Maximum endpointing delay (s) to force end of user turn.",
    )
    use_tts_aligned_transcript: Optional[bool] = Field(
        None,
        description="Whether to use TTS-aligned transcript as transcription source.",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "name": "simulator-customer",
                "instructions": "You are a concise customer. Ask clarifying questions and confirm resolution.",
                "llm": {
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "temperature": 0.6,
                },
                "tts": {
                    "provider": "openai",
                    "model": "gpt-4o-mini-tts",
                    "voice": "alloy",
                },
                "stt": {
                    "provider": "openai",
                    "model": "gpt-4o-mini-transcribe",
                    "language": "en",
                },
                "vad": {"provider": "silero"},
                "allow_interruptions": True,
                "min_endpointing_delay": 0.3,
                "max_endpointing_delay": 4.0,
                "use_tts_aligned_transcript": False,
            }
        }