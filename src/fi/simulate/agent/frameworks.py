from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from fi.simulate.agent.generic import GenericAgentWrapper, InputMode
from fi.simulate.agent.wrapper import AgentWrapper


@dataclass(frozen=True)
class FrameworkAdapterSpec:
    """Import-free adapter preset for a common agent/orchestration framework."""

    name: str
    method: Optional[str]
    input_mode: InputMode
    modality: str = "text"
    transport: str = "in_process"
    lifecycle_hooks: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    notes: str = ""


FRAMEWORK_PRESETS: Dict[str, FrameworkAdapterSpec] = {
    # Text/chat orchestration
    "custom": FrameworkAdapterSpec("custom", None, "auto", notes="Bring-your-own framework adapter."),
    "callable": FrameworkAdapterSpec("callable", None, "agent_input", notes="Plain Python callable."),
    "langchain": FrameworkAdapterSpec("langchain", "ainvoke", "dict", notes="LangChain Runnable/Chain."),
    "langgraph": FrameworkAdapterSpec("langgraph", "ainvoke", "dict", notes="LangGraph compiled graph."),
    "llamaindex": FrameworkAdapterSpec("llamaindex", "achat", "text", notes="LlamaIndex chat/query engines."),
    "crewai": FrameworkAdapterSpec("crewai", "kickoff", "dict", notes="CrewAI Crew kickoff."),
    "autogen": FrameworkAdapterSpec("autogen", "run", "text", notes="AutoGen AgentChat style task run."),
    "semantic_kernel": FrameworkAdapterSpec("semantic_kernel", "invoke", "dict", notes="Semantic Kernel function/agent."),
    "openai_agents": FrameworkAdapterSpec("openai_agents", "run", "text", notes="OpenAI Agents SDK runner/agent."),
    "pydantic_ai": FrameworkAdapterSpec("pydantic_ai", "run", "text", notes="PydanticAI agent."),
    "haystack": FrameworkAdapterSpec("haystack", "run", "dict", notes="Haystack pipeline."),
    "agno": FrameworkAdapterSpec("agno", "run", "dict", notes="Agno agent/team runner."),
    "beeai": FrameworkAdapterSpec("beeai", "run", "dict", notes="BeeAI agent runner."),
    "claude_agent_sdk": FrameworkAdapterSpec("claude_agent_sdk", "query", "text", notes="Claude Agent SDK query runner."),
    "dspy": FrameworkAdapterSpec("dspy", "__call__", "dict", notes="DSPy module/program."),
    "google_adk": FrameworkAdapterSpec("google_adk", "run", "dict", notes="Google ADK runner/agent."),
    "guardrails": FrameworkAdapterSpec("guardrails", "__call__", "text", notes="Guardrails validation wrapper."),
    "litellm": FrameworkAdapterSpec("litellm", "completion", "dict", notes="LiteLLM completion shim."),
    "mcp": FrameworkAdapterSpec("mcp", "call_tool", "dict", notes="MCP client/server tool session."),
    "portkey": FrameworkAdapterSpec("portkey", "chat", "dict", notes="Portkey gateway client."),
    "smolagents": FrameworkAdapterSpec("smolagents", "run", "text", notes="SmolAgents runner."),
    "strands": FrameworkAdapterSpec("strands", "__call__", "text", notes="Strands agent callable."),
    # Voice and realtime
    "livekit": FrameworkAdapterSpec("livekit", "respond", "text", modality="voice", notes="LiveKit agent/session shim."),
    "pipecat": FrameworkAdapterSpec("pipecat", "process", "dict", modality="voice", notes="Pipecat pipeline/processor shim."),
    "vapi": FrameworkAdapterSpec("vapi", "respond", "dict", modality="voice", notes="Webhook/local adapter shim."),
    "retell": FrameworkAdapterSpec("retell", "respond", "dict", modality="voice", notes="Webhook/local adapter shim."),
    "elevenlabs": FrameworkAdapterSpec("elevenlabs", "respond", "dict", modality="voice", notes="ElevenLabs conversational agent shim."),
    "deepgram": FrameworkAdapterSpec("deepgram", "respond", "dict", modality="voice", notes="Deepgram voice agent shim."),
    "agora": FrameworkAdapterSpec("agora", "respond", "dict", modality="voice", notes="Agora conversational AI shim."),
    "twilio": FrameworkAdapterSpec("twilio", "respond", "dict", modality="voice", notes="Twilio voice/media stream webhook shim."),
    # Model/provider clients commonly instrumented by TraceAI
    "anthropic": FrameworkAdapterSpec("anthropic", "chat", "dict", notes="Anthropic messages client shim."),
    "bedrock": FrameworkAdapterSpec("bedrock", "invoke_model", "dict", notes="AWS Bedrock client shim."),
    "cerebras": FrameworkAdapterSpec("cerebras", "chat", "dict", notes="Cerebras client shim."),
    "cohere": FrameworkAdapterSpec("cohere", "chat", "dict", notes="Cohere client shim."),
    "deepseek": FrameworkAdapterSpec("deepseek", "chat", "dict", notes="DeepSeek OpenAI-compatible client shim."),
    "fireworks": FrameworkAdapterSpec("fireworks", "chat", "dict", notes="Fireworks client shim."),
    "google_genai": FrameworkAdapterSpec("google_genai", "generate_content", "dict", notes="Google GenAI client shim."),
    "groq": FrameworkAdapterSpec("groq", "chat", "dict", notes="Groq client shim."),
    "huggingface": FrameworkAdapterSpec("huggingface", "__call__", "dict", notes="Hugging Face pipeline/client shim."),
    "instructor": FrameworkAdapterSpec("instructor", "chat", "dict", notes="Instructor structured output client shim."),
    "mistralai": FrameworkAdapterSpec("mistralai", "chat", "dict", notes="Mistral AI client shim."),
    "ollama": FrameworkAdapterSpec("ollama", "chat", "dict", notes="Ollama client shim."),
    "openai": FrameworkAdapterSpec("openai", "chat", "dict", notes="OpenAI chat client shim."),
    "together": FrameworkAdapterSpec("together", "chat", "dict", notes="Together AI client shim."),
    "vertexai": FrameworkAdapterSpec("vertexai", "generate_content", "dict", notes="Vertex AI client shim."),
    "vllm": FrameworkAdapterSpec("vllm", "generate", "dict", notes="vLLM server/client shim."),
    "xai": FrameworkAdapterSpec("xai", "chat", "dict", notes="xAI client shim."),
    # Computer-use / browser / multimodal
    "computer_use": FrameworkAdapterSpec("computer_use", "run", "dict", modality="cua", notes="Browser or desktop CUA runner."),
    "browser_use": FrameworkAdapterSpec("browser_use", "run", "dict", modality="cua", notes="Browser automation agent."),
    "playwright": FrameworkAdapterSpec("playwright", "run", "dict", modality="cua", notes="Playwright-backed agent harness."),
    "vision_agent": FrameworkAdapterSpec("vision_agent", "run", "dict", modality="image", notes="Image or multimodal agent."),
}


def supported_frameworks() -> list[str]:
    """Return built-in framework preset names.

    ``wrap_framework`` also accepts unknown framework names as custom adapters
    when the caller supplies method/input-mode overrides or the generic wrapper
    can infer a callable method.
    """

    return sorted(FRAMEWORK_PRESETS)


def framework_adapter_contract(
    framework: str,
    *,
    target: str | None = None,
    method: str | None = None,
    input_mode: InputMode | None = None,
    modality: str | None = None,
    trace_runtime: bool = True,
    metadata: Optional[Dict[str, Any]] = None,
) -> dict[str, Any]:
    """Return the native adapter contract used for framework simulation.

    The contract is import-free and local: it describes the framework shim,
    lifecycle, transport, capabilities, schemas, and replay requirements without
    pulling in LangGraph, LiveKit, Pipecat, or any other framework package.
    """

    meta = dict(metadata or {})
    key = _framework_key(framework)
    spec = FRAMEWORK_PRESETS.get(key)
    adapter_kind = "preset" if spec is not None else "custom"
    resolved_method = str(method or (spec.method if spec else "") or "auto")
    resolved_input_mode = str(input_mode or (spec.input_mode if spec else "") or "auto")
    resolved_modality = str(modality or meta.get("modality") or (spec.modality if spec else "text"))
    transport = str((spec.transport if spec else "") or _default_transport(resolved_modality))
    lifecycle_hooks = list(
        spec.lifecycle_hooks
        if spec and spec.lifecycle_hooks
        else _default_lifecycle_hooks(resolved_modality)
    )
    capabilities = list(
        spec.capabilities
        if spec and spec.capabilities
        else _default_capabilities(resolved_modality, resolved_input_mode)
    )
    target_scheme = urlparse(str(target or "")).scheme.lower()
    target_is_external = target_scheme in {"http", "https"}
    local_fixture = not target_is_external

    contract: dict[str, Any] = {
        "kind": "agent-learning.framework-adapter-contract.v1",
        "framework": key,
        "adapter": adapter_kind,
        "method": resolved_method,
        "input_mode": resolved_input_mode,
        "modality": resolved_modality,
        "transport": transport,
        "lifecycle_hooks": lifecycle_hooks,
        "capabilities": capabilities,
        "schemas": {
            "input": _input_schema(resolved_input_mode),
            "output": _output_schema(),
        },
        "trace_runtime": bool(trace_runtime),
        "requires_external_service": False,
        "local_executable_fixture": local_fixture,
        "evidence_requirements": [
            "framework_runtime",
            "framework_trace",
            "tool_calls",
            "adapter_conformance",
            "metric_evidence",
        ],
    }
    if target:
        contract["target"] = str(target)
        contract["target_scheme"] = target_scheme
    if spec and spec.notes:
        contract["notes"] = spec.notes
    return contract


def wrap_framework(
    framework: str,
    agent: Any,
    *,
    target: str | None = None,
    method: str | None = None,
    input_mode: InputMode | None = None,
    system_prompt: str | None = None,
    output_key: str | None = None,
    metadata: Optional[Dict[str, Any]] = None,
    trace_runtime: bool = False,
    runtime_metadata: Optional[Dict[str, Any]] = None,
) -> AgentWrapper:
    """
    Wrap a known or custom framework by name without importing that framework.

    Presets are intentionally thin. They encode the most common method/payload
    shape while leaving escape hatches for custom method, input_mode, and
    output_key.
    """

    key = framework.lower().replace("-", "_")
    spec = FRAMEWORK_PRESETS.get(key)
    raw_metadata = dict(metadata or {})
    contract = raw_metadata.get("framework_adapter_contract")
    if not isinstance(contract, dict):
        contract = framework_adapter_contract(
            key,
            target=target,
            method=method,
            input_mode=input_mode,
            trace_runtime=trace_runtime,
            metadata=raw_metadata,
        )
    runtime = dict(runtime_metadata or {})
    runtime.setdefault("framework_adapter_contract", contract)
    if spec is None:
        return GenericAgentWrapper(
            agent,
            method=method,
            input_mode=input_mode or "auto",
            output_key=output_key,
            system_prompt=system_prompt,
            metadata={
                "framework": key,
                "modality": str(raw_metadata.get("modality") or "text"),
                "adapter": "custom",
                "framework_adapter_contract": contract,
                **raw_metadata,
            },
            trace_runtime=trace_runtime,
            runtime_metadata=runtime,
        )

    return GenericAgentWrapper(
        agent,
        method=method or spec.method,
        input_mode=input_mode or spec.input_mode,
        output_key=output_key,
        system_prompt=system_prompt,
        metadata={
            "framework": spec.name,
            "modality": spec.modality,
            "framework_adapter_contract": contract,
            **raw_metadata,
        },
        trace_runtime=trace_runtime,
        runtime_metadata=runtime,
    )


def _framework_key(value: str) -> str:
    return str(value or "custom").strip().lower().replace("-", "_") or "custom"


def _default_transport(modality: str) -> str:
    if modality == "voice":
        return "realtime_adapter"
    if modality == "cua":
        return "browser_adapter"
    if modality == "image":
        return "multimodal_adapter"
    return "in_process"


def _default_lifecycle_hooks(modality: str) -> tuple[str, ...]:
    if modality == "voice":
        return ("setup", "connect", "stream", "respond", "teardown")
    if modality == "cua":
        return ("setup", "observe", "act", "verify", "teardown")
    return ("setup", "invoke", "observe", "teardown")


def _default_capabilities(modality: str, input_mode: str) -> tuple[str, ...]:
    capabilities = [
        "messages",
        "tool_calls",
        "runtime_trace",
        "state",
        "artifacts",
    ]
    if input_mode == "dict":
        capabilities.append("structured_input")
    if modality == "voice":
        capabilities.extend(["voice_frames", "realtime_events"])
    elif modality == "cua":
        capabilities.extend(["browser_actions", "visual_grounding"])
    elif modality == "image":
        capabilities.extend(["image_context", "multimodal_grounding"])
    return tuple(capabilities)


def _input_schema(input_mode: str) -> dict[str, Any]:
    if input_mode == "dict":
        return {
            "type": "object",
            "required": ["messages", "scenario"],
            "additionalProperties": True,
        }
    if input_mode == "messages":
        return {
            "type": "array",
            "items": {"type": "object", "required": ["role", "content"]},
        }
    if input_mode == "agent_input":
        return {"type": "object", "class": "AgentInput"}
    if input_mode == "text":
        return {"type": "string"}
    return {"type": "any"}


def _output_schema() -> dict[str, Any]:
    return {
        "oneOf": [
            {"type": "string"},
            {"type": "object", "class": "AgentResponse"},
        ],
        "required_trace_state": ["framework_runtime"],
    }
