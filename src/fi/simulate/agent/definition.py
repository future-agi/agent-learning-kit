from typing import List, Optional
from pydantic import BaseModel, Field, AnyUrl

class LLMConfig(BaseModel):
    """Configuration for the OpenAI Language Model (LLM)."""
    model: str = Field("gpt-4o", description="The OpenAI model to use (e.g., 'gpt-4o', 'gpt-3.5-turbo').")
    temperature: float = Field(0.7, ge=0.0, le=2.0, description="Controls randomness in the LLM's output.")

class TTSConfig(BaseModel):
    """Configuration for the OpenAI Text-to-Speech (TTS)."""
    model: str = Field("tts-1", description="The OpenAI TTS model to use (e.g., 'tts-1', 'tts-1-hd').")
    voice: str = Field("alloy", description="The voice to use for speech generation (e.g., 'alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer').")

class STTConfig(BaseModel):
    """Configuration for the OpenAI Speech-to-Text (STT)."""
    language: Optional[str] = Field("en", description="The language for transcription, specified in ISO-639-1 format.")

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
    room_name: str = Field(..., description="The name of the room the agent is waiting in.")
    
    system_prompt: str = Field(..., description="The main system prompt or instructions that define the agent's behavior.")
    
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    initial_message: str = Field("Hello! How can I help you today?", description="The first message the agent speaks to start the conversation.")

    class Config:
        """Pydantic configuration."""
        schema_extra = {
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
        schema_extra = {
            "example": {
                "name": "simulator-customer",
                "instructions": "You are a concise customer. Ask clarifying questions and confirm resolution.",
                "llm": {"model": "gpt-4o-mini", "temperature": 0.6},
                "tts": {"model": "tts-1", "voice": "alloy"},
                "stt": {"language": "en"},
                "vad": {"provider": "silero"},
                "allow_interruptions": True,
                "min_endpointing_delay": 0.3,
                "max_endpointing_delay": 4.0,
                "use_tts_aligned_transcript": False,
            }
        }