from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Sequence
from urllib.parse import urlparse

from fi.simulate.agent.generic import GenericAgentWrapper, InputMode
from fi.simulate.agent.wrapper import AgentInput, AgentResponse, AgentWrapper


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


def framework_adapter_contract_matrix(
    frameworks: Sequence[str] | str | None = None,
    *,
    targets: Mapping[str, str] | None = None,
    methods: Mapping[str, str] | None = None,
    input_modes: Mapping[str, InputMode] | None = None,
    modalities: Mapping[str, str] | None = None,
    trace_runtime: bool = True,
    allow_external_targets: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
) -> dict[str, Any]:
    """Return a native, import-free adapter contract matrix.

    The matrix is a first-party certification artifact: it proves what the
    Agent Learning simulator can run through local adapter fixtures without
    importing or calling LangGraph, LiveKit, Pipecat, or other frameworks.
    HTTP/HTTPS targets are rejected by default so this path stays native unless
    a caller explicitly opts into external target documentation.
    """

    framework_keys = _framework_matrix_keys(frameworks)
    target_map = {
        _framework_key(key): str(value)
        for key, value in dict(targets or {}).items()
    }
    method_map = {
        _framework_key(key): str(value)
        for key, value in dict(methods or {}).items()
    }
    input_mode_map = {
        _framework_key(key): value for key, value in dict(input_modes or {}).items()
    }
    modality_map = {
        _framework_key(key): str(value)
        for key, value in dict(modalities or {}).items()
    }
    external_targets = {
        key: target
        for key, target in target_map.items()
        if _is_external_target(target)
    }
    if external_targets and not allow_external_targets:
        blocked = ", ".join(
            f"{key}={target}" for key, target in sorted(external_targets.items())
        )
        raise ValueError(
            "external targets are disabled for native framework adapter matrices: "
            f"{blocked}"
        )

    contracts = [
        framework_adapter_contract(
            key,
            target=target_map.get(key) or _local_fixture_target(key),
            method=method_map.get(key),
            input_mode=input_mode_map.get(key),
            modality=modality_map.get(key),
            trace_runtime=trace_runtime,
            metadata=copy_metadata,
        )
        for key in framework_keys
        for copy_metadata in [dict(metadata or {})]
    ]
    findings = _framework_matrix_findings(contracts)
    summary = _framework_matrix_summary(contracts)
    return {
        "kind": "agent-learning.framework-adapter-contract-matrix.v1",
        "status": "passed" if not findings else "failed",
        "requires_external_service": False,
        "runtime": "in_process",
        "allow_external_targets": bool(allow_external_targets),
        "framework_count": len(framework_keys),
        "frameworks": framework_keys,
        "contracts": contracts,
        "summary": summary,
        "findings": findings,
        "contract_quality_gate": {
            "kind": "agent-learning.framework-adapter-contract.v1",
            "required_frameworks": framework_keys,
            "require_trace_runtime": bool(trace_runtime),
            "require_local_executable_fixture": not bool(allow_external_targets),
            "require_no_external_service": True,
            "require_target": True,
            "forbidden_target_schemes": (
                [] if allow_external_targets else ["http", "https"]
            ),
            "required_schema_sections": ["input", "output"],
            "required_lifecycle_hooks": ["setup", "teardown"],
            "required_capabilities": ["messages", "tool_calls", "runtime_trace"],
            "required_evidence_requirements": [
                "framework_runtime",
                "framework_trace",
                "tool_calls",
                "adapter_conformance",
                "metric_evidence",
            ],
        },
        "evidence_requirements": [
            "framework_runtime",
            "framework_trace",
            "adapter_conformance",
            "metric_evidence",
            "matrix_coverage",
        ],
    }


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


async def probe_framework_adapter(
    framework: str,
    agent: Any,
    *,
    cases: Sequence[Mapping[str, Any]] | None = None,
    target: str | None = None,
    method: str | Callable[..., Any] | None = None,
    input_mode: InputMode | None = None,
    system_prompt: str | None = None,
    output_key: str | None = None,
    metadata: Optional[Dict[str, Any]] = None,
    trace_runtime: bool = True,
    allow_external_target: bool = False,
) -> dict[str, Any]:
    """Run a local adapter conformance probe for any framework shim.

    The probe is intentionally import-free: callers pass an already-created
    LangChain/LangGraph/LiveKit/Pipecat/custom object or plain callable. The
    function wraps it with the same generic adapter used by manifests, executes
    representative cases, and returns runtime evidence that can feed evals,
    reports, optimizer proofs, or Future AGI UI cards.
    """

    if target and _is_external_target(target) and not allow_external_target:
        raise ValueError(
            "external targets are disabled for framework adapter probes; "
            "set allow_external_target=True only when the user explicitly "
            "wants to test that live workload"
        )

    key = _framework_key(framework)
    method_name = _probe_method_name(method)
    selected_metadata = dict(metadata or {})
    contract = framework_adapter_contract(
        key,
        target=target,
        method=method_name,
        input_mode=input_mode,
        trace_runtime=trace_runtime,
        metadata=selected_metadata,
    )
    selected_metadata["framework_adapter_contract"] = contract
    wrapper = wrap_framework(
        key,
        agent,
        target=target,
        method=method,
        input_mode=input_mode,
        system_prompt=system_prompt,
        output_key=output_key,
        metadata=selected_metadata,
        trace_runtime=trace_runtime,
        runtime_metadata={"framework_adapter_contract": contract},
    )

    probe_cases = _probe_cases(cases)
    case_results: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []

    for index, case in enumerate(probe_cases, start=1):
        case_result = await _run_probe_case(
            wrapper,
            key,
            case,
            index=index,
            trace_runtime=trace_runtime,
            contract=contract,
        )
        case_results.append(case_result)
        findings.extend(case_result.get("findings", []))

    summary = _probe_summary(case_results, contract)
    status = "passed" if not findings else "failed"
    return {
        "kind": "agent-learning.framework-adapter-probe.v1",
        "status": status,
        "passed": status == "passed",
        "framework": key,
        "method": method_name or str(contract.get("method") or "auto"),
        "input_mode": str(input_mode or contract.get("input_mode") or "auto"),
        "requires_external_service": bool(contract.get("requires_external_service")),
        "allow_external_target": bool(allow_external_target),
        "contract": contract,
        "summary": summary,
        "cases": case_results,
        "findings": findings,
    }


def run_framework_adapter_probe(
    framework: str,
    agent: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """Synchronous wrapper for :func:`probe_framework_adapter`.

    Use ``await probe_framework_adapter(...)`` when already inside an event loop.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(probe_framework_adapter(framework, agent, **kwargs))
    raise RuntimeError(
        "run_framework_adapter_probe cannot run inside an active event loop; "
        "await probe_framework_adapter(...) instead"
    )


def _framework_key(value: str) -> str:
    return str(value or "custom").strip().lower().replace("-", "_") or "custom"


def _framework_matrix_keys(frameworks: Sequence[str] | str | None) -> list[str]:
    default_frameworks = (
        "langchain",
        "langgraph",
        "llamaindex",
        "crewai",
        "autogen",
        "openai_agents",
        "livekit",
        "pipecat",
    )
    values: Sequence[str] | str = frameworks or default_frameworks
    if isinstance(values, str):
        values = [values]
    keys: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = _framework_key(value)
        if key in seen:
            continue
        seen.add(key)
        keys.append(key)
    if not keys:
        raise ValueError("frameworks must contain at least one framework")
    return keys


def _local_fixture_target(framework: str) -> str:
    return f"agent-learning-fixture://framework/{_framework_key(framework)}"


def _is_external_target(target: str) -> bool:
    return urlparse(str(target or "")).scheme.lower() in {"http", "https"}


def _framework_matrix_summary(contracts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    frameworks: list[str] = []
    methods: dict[str, str] = {}
    input_modes: dict[str, str] = {}
    modalities: dict[str, str] = {}
    transports: dict[str, str] = {}
    capabilities: set[str] = set()
    evidence_requirements: set[str] = set()
    target_schemes: set[str] = set()

    for contract in contracts:
        framework = _framework_key(str(contract.get("framework") or "custom"))
        frameworks.append(framework)
        methods[framework] = str(contract.get("method") or "")
        input_modes[framework] = str(contract.get("input_mode") or "")
        modalities[framework] = str(contract.get("modality") or "")
        transports[framework] = str(contract.get("transport") or "")
        capabilities.update(
            str(item) for item in contract.get("capabilities", []) or []
        )
        evidence_requirements.update(
            str(item) for item in contract.get("evidence_requirements", []) or []
        )
        target_scheme = str(contract.get("target_scheme") or "")
        if target_scheme:
            target_schemes.add(target_scheme)

    return {
        "frameworks": frameworks,
        "modalities": sorted(set(modalities.values())),
        "transports": sorted(set(transports.values())),
        "methods": methods,
        "input_modes": input_modes,
        "framework_modalities": modalities,
        "framework_transports": transports,
        "capabilities": sorted(capabilities),
        "evidence_requirements": sorted(evidence_requirements),
        "target_schemes": sorted(target_schemes),
        "contract_count": len(contracts),
        "local_executable_fixture_count": sum(
            1 for contract in contracts if bool(contract.get("local_executable_fixture"))
        ),
        "trace_runtime_count": sum(
            1 for contract in contracts if bool(contract.get("trace_runtime"))
        ),
        "requires_external_service_count": sum(
            1 for contract in contracts if bool(contract.get("requires_external_service"))
        ),
        "external_target_count": sum(
            1
            for contract in contracts
            if _is_external_target(str(contract.get("target") or ""))
        ),
    }


def _framework_matrix_findings(
    contracts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for contract in contracts:
        framework = _framework_key(str(contract.get("framework") or "custom"))
        if contract.get("kind") != "agent-learning.framework-adapter-contract.v1":
            findings.append({"framework": framework, "type": "contract_kind_mismatch"})
        if bool(contract.get("requires_external_service")):
            findings.append(
                {"framework": framework, "type": "external_service_required"}
            )
        if not bool(contract.get("local_executable_fixture")):
            findings.append({"framework": framework, "type": "local_fixture_missing"})
        if _is_external_target(str(contract.get("target") or "")):
            findings.append({"framework": framework, "type": "external_target_scheme"})
    return findings


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


def _probe_method_name(method: str | Callable[..., Any] | None) -> str | None:
    if method is None:
        return None
    if isinstance(method, str):
        return method
    return getattr(method, "__name__", None) or "callable"


def _probe_cases(cases: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if not cases:
        return [
            {
                "id": "default",
                "input": "Return a short adapter probe result.",
            }
        ]
    return [dict(case) for case in cases]


async def _run_probe_case(
    wrapper: AgentWrapper,
    framework: str,
    case: Mapping[str, Any],
    *,
    index: int,
    trace_runtime: bool,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    case_id = str(case.get("id") or f"case_{index}")
    agent_input = _probe_agent_input(framework, case, index=index)
    raw_response = await wrapper.call(agent_input)
    response = (
        raw_response
        if isinstance(raw_response, AgentResponse)
        else AgentResponse(content=str(raw_response))
    )
    response_payload = _probe_response_payload(response)
    runtime_trace = dict((response.state or {}).get("framework_runtime") or {})
    checks = _probe_case_checks(
        case,
        response_payload,
        runtime_trace=runtime_trace,
        trace_runtime=trace_runtime,
        contract=contract,
    )
    findings = [
        {
            "case_id": case_id,
            "check": check["id"],
            "level": "error",
            "message": check["message"],
            "expected": check.get("expected"),
            "observed": check.get("observed"),
        }
        for check in checks
        if not check["passed"]
    ]
    return {
        "id": case_id,
        "status": "passed" if not findings else "failed",
        "passed": not findings,
        "input": {
            "message_count": len(agent_input.messages),
            "tool_count": len(agent_input.tools),
            "artifact_count": len(agent_input.artifacts),
            "event_count": len(agent_input.events),
            "modality": agent_input.modality,
        },
        "response": response_payload,
        "runtime_trace": runtime_trace,
        "checks": checks,
        "findings": findings,
    }


def _probe_agent_input(
    framework: str,
    case: Mapping[str, Any],
    *,
    index: int,
) -> AgentInput:
    messages = _probe_messages(case)
    new_message = dict(case.get("new_message") or messages[-1])
    metadata = {
        "framework": framework,
        "probe_case_id": str(case.get("id") or f"case_{index}"),
        **dict(case.get("metadata") or {}),
    }
    return AgentInput(
        thread_id=str(case.get("thread_id") or f"{framework}-probe-{index}"),
        messages=messages,
        new_message=new_message,
        execution_id=str(case.get("execution_id") or f"{framework}-probe"),
        turn_index=int(case.get("turn_index") or index - 1),
        scenario_name=str(case.get("scenario_name") or "framework-adapter-probe"),
        persona=dict(case.get("persona") or {}),
        situation=str(case.get("situation") or ""),
        expected_outcome=str(case.get("expected_outcome") or ""),
        modality=str(case.get("modality") or ""),
        artifacts=list(case.get("artifacts") or []),
        events=list(case.get("events") or []),
        memory=dict(case.get("memory") or {}),
        tools=[dict(tool) for tool in case.get("tools", []) if isinstance(tool, Mapping)],
        metadata=metadata,
    )


def _probe_messages(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_messages = case.get("messages")
    if isinstance(raw_messages, Sequence) and not isinstance(raw_messages, (str, bytes)):
        messages = [
            dict(message)
            for message in raw_messages
            if isinstance(message, Mapping)
        ]
        if messages:
            return messages
    message = case.get("input", case.get("message", "Run the adapter probe."))
    return [{"role": "user", "content": str(message)}]


def _probe_case_checks(
    case: Mapping[str, Any],
    response: Mapping[str, Any],
    *,
    runtime_trace: Mapping[str, Any],
    trace_runtime: bool,
    contract: Mapping[str, Any],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    content = str(response.get("content") or "")
    tool_names = set(response.get("tool_names") or [])
    event_types = set(response.get("event_types") or [])
    state_keys = set(response.get("state_keys") or [])

    for term in _probe_strings(case.get("expected_contains")):
        checks.append(
            _probe_check(
                f"content_contains_{_framework_key(term)}",
                term.lower() in content.lower(),
                f"response content should contain {term!r}",
                expected=term,
                observed=content,
            )
        )
    for tool in _probe_strings(case.get("required_tools")):
        checks.append(
            _probe_check(
                f"required_tool_{_framework_key(tool)}",
                tool in tool_names,
                f"response should emit required tool {tool!r}",
                expected=tool,
                observed=sorted(tool_names),
            )
        )
    for event_type in _probe_strings(case.get("required_events")):
        checks.append(
            _probe_check(
                f"required_event_{_framework_key(event_type)}",
                event_type in event_types,
                f"response should emit required event {event_type!r}",
                expected=event_type,
                observed=sorted(event_types),
            )
        )
    for state_key in _probe_strings(case.get("required_state_keys")):
        checks.append(
            _probe_check(
                f"required_state_{_framework_key(state_key)}",
                state_key in state_keys,
                f"response should include required state key {state_key!r}",
                expected=state_key,
                observed=sorted(state_keys),
            )
        )
    if trace_runtime:
        runtime_summary = dict(runtime_trace.get("summary") or {})
        runtime_contract = dict(
            dict(runtime_trace.get("metadata") or {}).get("framework_adapter_contract")
            or {}
        )
        checks.extend(
            [
                _probe_check(
                    "framework_runtime_trace_present",
                    bool(runtime_trace),
                    "trace_runtime=True should attach framework_runtime state",
                    expected=True,
                    observed=bool(runtime_trace),
                ),
                _probe_check(
                    "framework_runtime_contract_present",
                    runtime_contract.get("kind")
                    == "agent-learning.framework-adapter-contract.v1",
                    "runtime trace should carry the adapter contract",
                    expected="agent-learning.framework-adapter-contract.v1",
                    observed=runtime_contract.get("kind"),
                ),
                _probe_check(
                    "framework_runtime_invocation_present",
                    int(runtime_summary.get("invocation_count") or 0) >= 1,
                    "runtime trace should record at least one invocation",
                    expected=">=1",
                    observed=runtime_summary.get("invocation_count"),
                ),
            ]
        )
    checks.append(
        _probe_check(
            "adapter_contract_local_first",
            contract.get("requires_external_service") is False,
            "adapter contract should not require a hosted service",
            expected=False,
            observed=contract.get("requires_external_service"),
        )
    )
    return checks


def _probe_check(
    check_id: str,
    passed: bool,
    message: str,
    *,
    expected: Any = None,
    observed: Any = None,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "passed": bool(passed),
        "message": message,
        "expected": expected,
        "observed": observed,
    }


def _probe_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _probe_response_payload(response: AgentResponse) -> dict[str, Any]:
    tool_calls = [dict(call) for call in response.tool_calls or []]
    events = [event.model_dump() for event in response.events]
    artifacts = [artifact.model_dump() for artifact in response.artifacts]
    state = dict(response.state or {})
    metadata = dict(response.metadata or {})
    return {
        "content": response.content,
        "tool_call_count": len(tool_calls),
        "tool_names": sorted(
            {
                str(
                    call.get("name")
                    or call.get("tool")
                    or dict(call.get("function") or {}).get("name")
                    or ""
                )
                for call in tool_calls
                if isinstance(call, Mapping)
            }
        ),
        "event_count": len(events),
        "event_types": sorted({str(event.get("type") or "") for event in events}),
        "artifact_count": len(artifacts),
        "artifact_types": sorted({str(artifact.get("type") or "") for artifact in artifacts}),
        "state_keys": sorted(str(key) for key in state),
        "metadata_keys": sorted(str(key) for key in metadata),
    }


def _probe_summary(
    cases: Sequence[Mapping[str, Any]],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    passed = sum(1 for case in cases if case.get("passed"))
    failed = len(cases) - passed
    response_tool_count = sum(
        int(dict(case.get("response") or {}).get("tool_call_count") or 0)
        for case in cases
    )
    runtime_trace_count = sum(1 for case in cases if case.get("runtime_trace"))
    return {
        "case_count": len(cases),
        "passed_case_count": passed,
        "failed_case_count": failed,
        "runtime_trace_count": runtime_trace_count,
        "tool_call_count": response_tool_count,
        "framework": contract.get("framework"),
        "method": contract.get("method"),
        "input_mode": contract.get("input_mode"),
        "local_executable_fixture": bool(contract.get("local_executable_fixture")),
        "requires_external_service": bool(contract.get("requires_external_service")),
        "trace_runtime": bool(contract.get("trace_runtime")),
    }
