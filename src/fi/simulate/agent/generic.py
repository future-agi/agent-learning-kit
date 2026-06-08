import inspect
import json
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Callable, Dict, Iterable, List, Literal, Mapping, Optional, Union

from fi.simulate.agent.wrapper import (
    AgentInput,
    AgentResponse,
    AgentWrapper,
    SimulationArtifact,
    SimulationEvent,
)

InputMode = Literal["auto", "agent_input", "dict", "messages", "text"]

_KEYWORD_INPUT_NAMES = (
    "inputs",
    "input",
    "payload",
    "frame",
    "request",
    "contents",
    "arguments",
    "task",
    "user_prompt",
    "prompt",
    "message",
    "messages",
    "query",
    "data",
)

_METHOD_INPUT_KEY_PREFERENCES = {
    "execute_task": ("task", "input", "payload"),
    "kickoff": ("inputs", "input", "payload"),
    "run": ("task", "user_prompt", "prompt", "input"),
    "arun": ("task", "user_prompt", "prompt", "input"),
    "run_stream": ("task", "user_prompt", "prompt", "input"),
    "send": ("message", "messages", "input"),
    "achat": ("message", "messages", "input"),
    "chat": ("message", "messages", "input"),
    "query": ("query", "input", "message"),
    "respond": ("message", "input", "payload"),
    "process": ("frame", "payload", "input", "data"),
    "process_frame": ("frame", "payload", "input", "data"),
    "responses.create": ("input", "messages", "payload"),
    "chat.completions.create": ("messages", "input", "payload"),
    "messages.create": ("messages", "input", "payload"),
    "completion": ("request", "payload", "input"),
    "call_tool": ("payload", "input", "arguments"),
    "invoke_model": ("payload", "input", "request"),
    "generate_content": ("contents", "input", "payload"),
    "generate": ("prompt", "input", "payload"),
}

_AUTO_METHOD_ORDER = (
    "call",
    "ainvoke",
    "invoke",
    "astream",
    "stream",
    "stream_events",
    "execute_task",
    "kickoff",
    "process_frame",
    "process",
    "responses.create",
    "chat.completions.create",
    "messages.create",
    "run_stream",
    "arun",
    "run",
    "send",
    "respond",
    "achat",
    "chat",
    "query",
    "completion",
    "call_tool",
    "invoke_model",
    "generate_content",
    "generate",
)

_METHOD_INPUT_MODES: dict[str, InputMode] = {
    "ainvoke": "dict",
    "invoke": "dict",
    "astream": "dict",
    "stream": "dict",
    "stream_events": "dict",
    "execute_task": "dict",
    "kickoff": "dict",
    "process": "dict",
    "process_frame": "dict",
    "responses.create": "text",
    "chat.completions.create": "messages",
    "messages.create": "messages",
    "completion": "dict",
    "call_tool": "dict",
    "invoke_model": "dict",
    "generate_content": "dict",
    "generate": "dict",
    "call": "agent_input",
    "achat": "text",
    "chat": "text",
    "query": "text",
    "respond": "text",
    "run": "text",
    "run_stream": "text",
    "arun": "text",
    "send": "text",
}


class GenericAgentWrapper(AgentWrapper):
    """
    Framework-neutral adapter for agent objects, callables, and orchestration SDKs.

    The wrapper intentionally depends on conventions instead of optional imports:
    LangChain/LangGraph expose invoke/ainvoke, AutoGen and OpenAI-style runners often
    expose run/arun/run_stream, voice stacks usually expose send/respond/chat, and
    plain Python agents are just callables. Users can override method/input_mode when
    a framework has a custom shape.
    """

    def __init__(
        self,
        agent: Any,
        *,
        method: str | Callable[..., Any] | None = None,
        input_mode: InputMode = "auto",
        input_key: str | None = None,
        input_kwargs: Optional[Mapping[str, Any]] = None,
        output_key: str | None = None,
        system_prompt: str | None = None,
        metadata: Optional[Dict[str, Any]] = None,
        trace_runtime: bool = False,
        runtime_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.agent = agent
        self.method = method
        self.input_mode = input_mode
        self.input_key = input_key
        self.input_kwargs = dict(input_kwargs or {})
        self.output_key = output_key
        self.system_prompt = system_prompt
        self.metadata = metadata or {}
        self.trace_runtime = trace_runtime
        self.runtime_metadata = runtime_metadata or {}

    async def call(self, input: AgentInput) -> Union[str, AgentResponse]:
        method = self._resolve_method()
        method_name = (
            self.method
            if isinstance(self.method, str)
            else getattr(method, "__name__", None)
        )
        runtime_input_mode = (
            self._infer_input_mode(method_name)
            if self.input_mode == "auto"
            else self.input_mode
        )
        payload = self._build_payload(input, method_name=method_name)
        started_at = time.time()
        streamed = False

        raw, call_style, selected_input_key = _invoke_method_with_payload(
            method,
            payload,
            method_name=method_name,
            input_key=self.input_key,
            input_kwargs=self.input_kwargs,
        )

        if inspect.isawaitable(raw):
            raw = await raw

        if _is_async_stream(raw):
            streamed = True
            raw = await self._coerce_async_stream(raw)
        elif _is_sync_stream(raw):
            streamed = True
            raw = self._coerce_sync_stream(raw)

        response = self._coerce_response(raw)
        if not self.trace_runtime:
            return response
        trace = _framework_runtime_trace(
            framework=str(self.metadata.get("framework") or "generic"),
            method_name=method_name,
            input_mode=runtime_input_mode,
            payload=payload,
            response=response,
            duration_ms=int((time.time() - started_at) * 1000),
            streamed=streamed,
            call_style=call_style,
            input_key=selected_input_key,
            input_kwargs_keys=sorted(str(key) for key in self.input_kwargs),
            wrapper_metadata=self.metadata,
            runtime_metadata=self.runtime_metadata,
        )
        return _attach_framework_runtime_trace(response, trace)

    def _resolve_method(self) -> Callable[..., Any]:
        if isinstance(self.agent, AgentWrapper):
            return self.agent.call

        if callable(self.method):
            return self.method

        if isinstance(self.method, str):
            candidate = _resolve_callable_attr_path(self.agent, self.method)
            if callable(candidate):
                return candidate
            raise AttributeError(f"Agent does not expose method '{self.method}'.")

        for name in _AUTO_METHOD_ORDER:
            candidate = _resolve_callable_attr_path(self.agent, name)
            if callable(candidate):
                return candidate

        if callable(self.agent):
            return self.agent

        raise TypeError(
            "GenericAgentWrapper needs a callable agent or an object exposing one "
            "of the supported framework adapter method names."
        )

    def _build_payload(self, input: AgentInput, *, method_name: str | None) -> Any:
        mode = self.input_mode
        if mode == "auto":
            mode = self._infer_input_mode(method_name)

        if mode == "agent_input":
            return input

        messages = self._messages_with_system(input.messages)
        latest_text = _message_content(input.new_message) if input.new_message else ""

        if mode == "messages":
            return messages
        if mode == "text":
            return latest_text
        if mode == "dict":
            return {
                "messages": messages,
                "input": latest_text,
                "thread_id": input.thread_id,
                "execution_id": input.execution_id,
                "turn_index": input.turn_index,
                "scenario_name": input.scenario_name,
                "persona": input.persona,
                "situation": input.situation,
                "expected_outcome": input.expected_outcome,
                "modality": input.modality,
                "artifacts": [_model_to_dict(artifact) for artifact in input.artifacts],
                "events": [_model_to_dict(event) for event in input.events],
                "memory": input.memory,
                "tools": input.tools,
                "metadata": {**input.metadata, **self.metadata},
            }

        return input

    def _infer_input_mode(self, method_name: str | None) -> InputMode:
        return (
            _METHOD_INPUT_MODES.get(str(method_name or ""))
            or _METHOD_INPUT_MODES.get(_method_leaf(method_name))
            or "agent_input"
        )

    def _messages_with_system(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized = [dict(message) for message in messages]
        if not self.system_prompt:
            return normalized
        if normalized and normalized[0].get("role") == "system":
            return normalized
        return [{"role": "system", "content": self.system_prompt}, *normalized]

    def _coerce_response(self, raw: Any) -> str | AgentResponse:
        if isinstance(raw, AgentResponse):
            return raw
        if isinstance(raw, str):
            return raw
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")

        content = self._extract_content(raw)
        tool_calls = self._extract_tool_calls(raw)
        tool_responses = self._extract_tool_responses(raw)
        artifacts = self._extract_artifacts(raw)
        events = self._extract_events(raw)
        memory_updates = self._extract_memory_updates(raw)
        state = self._extract_state(raw)
        metadata = self._extract_metadata(raw)
        if self.metadata:
            metadata = {**metadata, **self.metadata}

        return AgentResponse(
            content=content,
            tool_calls=tool_calls,
            tool_responses=tool_responses,
            artifacts=artifacts,
            events=events,
            memory_updates=memory_updates,
            state=state or None,
            metadata=metadata or None,
        )

    async def _coerce_async_stream(self, raw: Any) -> AgentResponse:
        chunks: List[Any] = []
        async for chunk in raw:
            chunks.append(chunk)
        return self._coerce_stream_chunks(chunks)

    def _coerce_sync_stream(self, raw: Any) -> AgentResponse:
        return self._coerce_stream_chunks(list(raw))

    def _coerce_stream_chunks(self, chunks: List[Any]) -> AgentResponse:
        content_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        tool_responses: List[Dict[str, Any]] = []
        artifacts: List[SimulationArtifact] = []
        events: List[SimulationEvent] = []

        for index, chunk in enumerate(chunks, start=1):
            text = _stream_chunk_text(chunk)
            if text:
                content_parts.append(text)
            tool_calls.extend(self._extract_tool_calls(chunk) or [])
            tool_responses.extend(self._extract_tool_responses(chunk) or [])
            artifacts.extend(self._extract_artifacts(chunk))
            events.extend(self._extract_events(chunk))
            events.append(_stream_chunk_event(chunk, index=index, text=text))

        trace = _streaming_trace_from_chunks(chunks, self.metadata)
        state: Dict[str, Any] = {}
        if trace.get("events"):
            artifacts.append(
                SimulationArtifact(
                    type="trace",
                    role="assistant",
                    data=trace,
                    metadata={
                        "kind": "streaming_trace",
                        "framework": trace.get("framework", "generic"),
                        "source": "generic_agent_wrapper",
                    },
                )
            )
            state["streaming_trace"] = trace

        metadata = {
            "streaming": {
                "chunk_count": len(chunks),
                "content_part_count": len(content_parts),
                "signals": list(trace.get("signals", [])),
                "summary": dict(trace.get("summary", {})),
            },
            **self.metadata,
        }
        return AgentResponse(
            content="".join(content_parts),
            tool_calls=tool_calls or None,
            tool_responses=tool_responses or None,
            artifacts=artifacts,
            events=events,
            state=state or None,
            metadata=metadata,
        )

    def _extract_content(self, raw: Any) -> str:
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")

        raw_mapping = _object_mapping(raw)
        if raw_mapping is not None:
            if self.output_key and self.output_key in raw_mapping:
                return _stringify(raw_mapping[self.output_key])
            for key in (
                "content",
                "output",
                "response",
                "text",
                "final_output",
                "answer",
                "result",
                "data",
            ):
                if key in raw_mapping and raw_mapping[key] is not None:
                    if key == "content":
                        block_text = _content_blocks_text(raw_mapping[key])
                        if block_text:
                            return block_text
                    return _stringify(raw_mapping[key])
            if "message" in raw_mapping:
                return _message_content(raw_mapping["message"])
            if "messages" in raw_mapping:
                return _last_message_content(raw_mapping["messages"])
            if "choices" in raw_mapping:
                return _choices_content(raw_mapping["choices"])
            realtime_text = _realtime_last_text(raw_mapping)
            if realtime_text:
                return realtime_text

        for attr in ("content", "output", "response", "text", "final_output", "answer"):
            if hasattr(raw, attr):
                value = getattr(raw, attr)
                if value is not None:
                    return _stringify(value)

        if hasattr(raw, "message"):
            return _message_content(getattr(raw, "message"))
        if hasattr(raw, "messages"):
            return _last_message_content(getattr(raw, "messages"))
        if isinstance(raw, (list, tuple)):
            return _last_message_content(raw)
        realtime_text = _realtime_last_text(raw)
        if realtime_text:
            return realtime_text

        return str(raw)

    def _extract_tool_calls(self, raw: Any) -> Optional[List[Dict[str, Any]]]:
        tool_calls = _extract_list_field(
            raw,
            ("tool_calls", "toolCalls", "tool_call_chunks", "toolCallChunks"),
        )
        provider_tool_calls = _provider_tool_calls(raw)
        history_tool_calls = _message_history_tool_calls(raw)
        realtime_tool_calls = _realtime_tool_calls(raw)
        return [
            *(tool_calls or []),
            *provider_tool_calls,
            *history_tool_calls,
            *realtime_tool_calls,
        ] or None

    def _extract_tool_responses(self, raw: Any) -> Optional[List[Dict[str, Any]]]:
        tool_responses = _extract_list_field(raw, ("tool_responses", "toolResponses", "tool_outputs", "toolOutputs"))
        history_tool_responses = _message_history_tool_responses(raw)
        realtime_tool_responses = _realtime_tool_responses(raw)
        return [
            *(tool_responses or []),
            *history_tool_responses,
            *realtime_tool_responses,
        ] or None

    def _extract_metadata(self, raw: Any) -> Dict[str, Any]:
        raw_mapping = _object_mapping(raw)
        metadata: Dict[str, Any] = {}
        if raw_mapping is not None:
            value = raw_mapping.get("metadata")
            if isinstance(value, dict):
                metadata.update(dict(value))
            metadata.update(_provider_metadata(raw_mapping))
            return metadata
        value = getattr(raw, "metadata", None)
        if isinstance(value, dict):
            metadata.update(dict(value))
        metadata.update(_provider_metadata(raw))
        return metadata

    def _extract_memory_updates(self, raw: Any) -> Optional[Dict[str, Any]]:
        raw_mapping = _object_mapping(raw)
        for name in ("memory_updates", "memoryUpdates"):
            value = raw_mapping.get(name) if raw_mapping is not None else getattr(raw, name, None)
            plain = _plain_value(value)
            if isinstance(plain, Mapping):
                return dict(plain)
        return None

    def _extract_state(self, raw: Any) -> Dict[str, Any]:
        raw_mapping = _object_mapping(raw)
        state: Dict[str, Any] = {}
        for name in ("state", "output_state", "outputState"):
            value = raw_mapping.get(name) if raw_mapping is not None else getattr(raw, name, None)
            plain = _plain_value(value)
            if isinstance(plain, Mapping):
                state.update(dict(plain))

        if raw_mapping is not None:
            for name in ("typed_output", "structured_output", "validated_output"):
                value = raw_mapping.get(name)
                if value not in (None, "", [], {}):
                    state[name] = _plain_value(value)
            output_value = raw_mapping.get("output")
            output_payload = _plain_value(output_value)
            if (
                isinstance(output_payload, Mapping)
                and output_payload
                and "typed_output" not in state
            ):
                state["typed_output"] = dict(output_payload)
            provider_state = _provider_response_state(raw_mapping)
            if provider_state:
                state.setdefault("provider_response", provider_state)
        history_state = _message_history_state(raw)
        if history_state:
            state.setdefault("message_history", history_state)
        handoff_state = _message_history_handoff_state(raw)
        if handoff_state:
            state.setdefault("framework_handoffs", handoff_state)
        realtime_state = _realtime_trace_state(raw)
        if realtime_state:
            state.setdefault("realtime_trace", realtime_state)
        return state

    def _extract_artifacts(self, raw: Any) -> List[SimulationArtifact]:
        values = _extract_list_field(raw, ("artifacts", "media", "attachments"))
        artifacts: List[SimulationArtifact] = []
        for value in values or []:
            try:
                artifacts.append(SimulationArtifact(**value))
            except Exception:
                continue
        realtime_state = _realtime_trace_state(raw)
        if realtime_state:
            artifacts.append(
                SimulationArtifact(
                    type="trace",
                    role="assistant",
                    data=realtime_state,
                    metadata={
                        "kind": "realtime_trace",
                        "source": "generic_agent_wrapper",
                    },
                )
            )
        return artifacts

    def _extract_events(self, raw: Any) -> List[SimulationEvent]:
        values = _extract_list_field(raw, ("events", "trajectory", "spans"))
        events: List[SimulationEvent] = []
        for value in values or []:
            try:
                events.append(SimulationEvent(**value))
            except Exception:
                continue
        events.extend(_provider_events(raw))
        events.extend(_message_history_events(raw))
        events.extend(_message_history_coordination_events(raw))
        events.extend(_realtime_trace_events(raw))
        return events


class _NoPayload:
    pass


_NO_PAYLOAD = _NoPayload()


def wrap_agent(
    agent: Any,
    *,
    method: str | Callable[..., Any] | None = None,
    input_mode: InputMode = "auto",
    input_key: str | None = None,
    input_kwargs: Optional[Mapping[str, Any]] = None,
    output_key: str | None = None,
    system_prompt: str | None = None,
    metadata: Optional[Dict[str, Any]] = None,
    trace_runtime: bool = False,
    runtime_metadata: Optional[Dict[str, Any]] = None,
) -> AgentWrapper:
    """Return an AgentWrapper for an existing AgentWrapper, object, or callable."""

    if isinstance(agent, AgentWrapper) and method is None and input_mode == "auto":
        return agent
    return GenericAgentWrapper(
        agent,
        method=method,
        input_mode=input_mode,
        input_key=input_key,
        input_kwargs=input_kwargs,
        output_key=output_key,
        system_prompt=system_prompt,
        metadata=metadata,
        trace_runtime=trace_runtime,
        runtime_metadata=runtime_metadata,
    )


def _extract_list_field(raw: Any, names: Iterable[str]) -> Optional[List[Dict[str, Any]]]:
    value = None
    raw_mapping = _object_mapping(raw)
    if raw_mapping is not None:
        for name in names:
            value = raw_mapping.get(name)
            if value is not None:
                break
    else:
        for name in names:
            if hasattr(raw, name):
                value = getattr(raw, name)
                break
    if not isinstance(value, (list, tuple)):
        return None
    items: List[Dict[str, Any]] = []
    for item in value:
        item_mapping = _object_mapping(item)
        if item_mapping is not None:
            items.append(dict(item_mapping))
    return items or None


def _provider_tool_calls(raw: Any) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    raw_mapping = _object_mapping(raw)
    if raw_mapping is not None:
        calls.extend(_tool_calls_from_message(raw_mapping, include_direct_keys=False))
        for choice in _provider_choices(raw_mapping):
            message = _provider_choice_message(choice)
            calls.extend(_tool_calls_from_message(message))
    return calls


def _tool_calls_from_message(
    message: Mapping[str, Any],
    *,
    include_direct_keys: bool = True,
) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    if include_direct_keys:
        for key in ("tool_calls", "toolCalls", "tool_call_chunks", "toolCallChunks"):
            calls.extend(_list_of_mappings(message.get(key)))
    function_call = _object_mapping(message.get("function_call"))
    if function_call:
        calls.append(
            {
                "id": str(function_call.get("id") or "function_call"),
                "type": "function",
                "function": {
                    "name": function_call.get("name"),
                    "arguments": function_call.get("arguments"),
                },
            }
        )
    for block in _content_blocks(message.get("content")):
        block_type = str(block.get("type") or block.get("kind") or "")
        block_type_key = block_type.lower().replace("_", "").replace("-", "")
        has_tool_shape = bool(block.get("name")) and (
            "arguments" in block or "input" in block or "args" in block
        )
        if block_type != "tool_use" and "functioncall" not in block_type_key and not has_tool_shape:
            continue
        name = str(block.get("name") or block.get("tool") or "")
        calls.append(
            {
                "id": str(block.get("id") or name or "tool_use"),
                "type": "tool_use",
                "name": name,
                "arguments": block.get("input") or block.get("arguments") or {},
                "function": {
                    "name": name,
                    "arguments": block.get("input") or block.get("arguments") or {},
                },
            }
        )
    return calls


def _provider_events(raw: Any) -> List[SimulationEvent]:
    events: List[SimulationEvent] = []
    raw_mapping = _object_mapping(raw)
    if raw_mapping is None:
        return events
    for index, choice in enumerate(_provider_choices(raw_mapping), start=1):
        finish_reason = str(choice.get("finish_reason") or choice.get("stop_reason") or "")
        if finish_reason:
            events.append(
                SimulationEvent(
                    type="provider_choice",
                    name=finish_reason,
                    payload={
                        "index": index,
                        "finish_reason": finish_reason,
                    },
                )
            )
    for index, call in enumerate(_provider_tool_calls(raw_mapping), start=1):
        name = str(
            call.get("name")
            or call.get("tool")
            or dict(call.get("function") or {}).get("name")
            or f"provider_tool_call_{index}"
        )
        events.append(
            SimulationEvent(
                type="provider_tool_call",
                name=name,
                payload=call,
            )
        )
    return events


def _provider_metadata(raw: Any) -> Dict[str, Any]:
    raw_mapping = _object_mapping(raw)
    if raw_mapping is None:
        return {}
    metadata: Dict[str, Any] = {}
    for key in ("id", "model", "object", "type", "role", "stop_reason", "stop_sequence"):
        value = raw_mapping.get(key)
        if value not in (None, "", [], {}):
            metadata[f"provider_{key}"] = value
    usage = _object_mapping(raw_mapping.get("usage"))
    if usage:
        metadata["provider_usage"] = usage
    return metadata


def _provider_response_state(raw: Any) -> Dict[str, Any]:
    raw_mapping = _object_mapping(raw)
    if raw_mapping is None:
        return {}
    choices = _provider_choices(raw_mapping)
    tool_calls = _provider_tool_calls(raw_mapping)
    usage = _object_mapping(raw_mapping.get("usage"))
    has_provider_envelope = bool(
        choices
        or tool_calls
        or usage
        or raw_mapping.get("model")
        or raw_mapping.get("object")
        or raw_mapping.get("id")
    )
    if not has_provider_envelope:
        return {}
    finish_reasons = sorted(
        {
            str(choice.get("finish_reason") or choice.get("stop_reason") or "")
            for choice in choices
            if choice.get("finish_reason") or choice.get("stop_reason")
        }
    )
    state: Dict[str, Any] = {}
    if choices:
        state["choice_count"] = len(choices)
    if finish_reasons:
        state["finish_reasons"] = finish_reasons
    if tool_calls:
        state["tool_call_count"] = len(tool_calls)
        state["tool_names"] = sorted(
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
        )
    if usage:
        state["usage"] = usage
    for key in ("id", "model", "object", "type", "role", "stop_reason"):
        value = raw_mapping.get(key)
        if value not in (None, "", [], {}):
            state[key] = value
    return state


def _provider_choices(raw: Any) -> List[Dict[str, Any]]:
    raw_mapping = _object_mapping(raw)
    if raw_mapping is None:
        return []
    return _list_of_mappings(raw_mapping.get("choices"))


def _provider_choice_message(choice: Mapping[str, Any]) -> Dict[str, Any]:
    for key in ("message", "delta"):
        mapping = _object_mapping(choice.get(key))
        if mapping:
            return mapping
    return dict(choice)


def _content_blocks(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return _list_of_mappings(value)


def _content_blocks_text(value: Any) -> str:
    parts: List[str] = []
    if isinstance(value, str):
        return value
    if not isinstance(value, (list, tuple)):
        return ""
    for item in value:
        if isinstance(item, str):
            parts.append(item)
            continue
        block = _object_mapping(item)
        if not block:
            continue
        for key in ("text", "content"):
            text = block.get(key)
            if text not in (None, "", [], {}):
                parts.append(_stringify(text))
                break
    return " ".join(part for part in parts if part)


def _list_of_mappings(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    items: List[Dict[str, Any]] = []
    for item in value:
        mapping = _object_mapping(item)
        if mapping:
            items.append(mapping)
    return items


def _message_history_tool_calls(raw: Any) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    for message in _message_history(raw):
        calls.extend(_tool_calls_from_message(message))
    return calls


def _message_history_tool_responses(raw: Any) -> List[Dict[str, Any]]:
    responses: List[Dict[str, Any]] = []
    for message in _message_history(raw):
        message_type = str(message.get("type") or message.get("kind") or "")
        content_blocks = _content_blocks(message.get("content"))
        if not content_blocks and (
            "ToolCallExecution" in message_type
            or str(message.get("role") or "") == "tool"
        ):
            content = message.get("content") or message.get("result") or message.get("output")
            if content not in (None, "", [], {}):
                responses.append(
                    {
                        "id": str(
                            message.get("id")
                            or message.get("call_id")
                            or message.get("tool_call_id")
                            or "tool_response"
                        ),
                        "name": str(message.get("name") or message.get("tool") or ""),
                        "content": _plain_value(content),
                        "is_error": bool(message.get("is_error") or message.get("error")),
                    }
                )
            continue
        for block in content_blocks:
            block_type = str(block.get("type") or block.get("kind") or "")
            block_type_key = block_type.lower().replace("_", "").replace("-", "")
            is_response = (
                "toolcallresult" in block_type_key
                or "toolresult" in block_type_key
                or bool(block.get("call_id") or block.get("tool_call_id"))
                and ("content" in block or "result" in block or "output" in block)
            )
            if not is_response:
                continue
            responses.append(
                {
                    "id": str(
                        block.get("id")
                        or block.get("call_id")
                        or block.get("tool_call_id")
                        or "tool_response"
                    ),
                    "name": str(block.get("name") or block.get("tool") or ""),
                    "content": _plain_value(
                        block.get("content")
                        if "content" in block
                        else block.get("result", block.get("output"))
                    ),
                    "is_error": bool(block.get("is_error") or block.get("error")),
                }
            )
    return responses


def _message_history_events(raw: Any) -> List[SimulationEvent]:
    events: List[SimulationEvent] = []
    for index, message in enumerate(_message_history(raw), start=1):
        message_type = str(
            message.get("type")
            or message.get("kind")
            or message.get("role")
            or "message_history"
        )
        source = str(
            message.get("source")
            or message.get("name")
            or message.get("speaker")
            or message.get("role")
            or ""
        )
        payload = {
            "index": index,
            "type": message_type,
            "role": message.get("role"),
            "source": source,
            "content_length": len(_message_content(message)),
            "tool_call_count": len(_tool_calls_from_message(message)),
            "tool_response_count": len(
                _message_history_tool_responses({"messages": [message]})
            ),
        }
        for key in ("handoff_from", "handoff_to", "recipient", "task", "stop_reason"):
            value = message.get(key)
            if value not in (None, "", [], {}):
                payload[key] = value
        events.append(
            SimulationEvent(
                type=message_type,
                name=source or message_type,
                payload=payload,
                metadata={"kind": "message_history", "message_index": index},
            )
        )
    return events


def _message_history_coordination_events(raw: Any) -> List[SimulationEvent]:
    state = _message_history_handoff_state(raw)
    if not state:
        return []
    events: List[SimulationEvent] = []
    for index, handoff in enumerate(state.get("handoffs", []), start=1):
        handoff_dict = dict(handoff)
        events.append(
            SimulationEvent(
                type="framework_handoff",
                name=str(
                    handoff_dict.get("name")
                    or f"{handoff_dict.get('from', '')}->{handoff_dict.get('to', '')}"
                ),
                payload={**handoff_dict, "sequence": index},
                metadata={"kind": "framework_coordination", "coordination": "handoff"},
            )
        )
    for index, review in enumerate(state.get("reviews", []), start=1):
        review_dict = dict(review)
        events.append(
            SimulationEvent(
                type="framework_review",
                name=str(review_dict.get("name") or review_dict.get("reviewer") or "review"),
                payload={**review_dict, "sequence": index},
                metadata={"kind": "framework_coordination", "coordination": "review"},
            )
        )
    for index, reconciliation in enumerate(state.get("reconciliations", []), start=1):
        reconciliation_dict = dict(reconciliation)
        events.append(
            SimulationEvent(
                type="framework_reconciliation",
                name=str(
                    reconciliation_dict.get("name")
                    or reconciliation_dict.get("accepted_source")
                    or "reconciliation"
                ),
                payload={**reconciliation_dict, "sequence": index},
                metadata={
                    "kind": "framework_coordination",
                    "coordination": "reconciliation",
                },
            )
        )
    return events


def _message_history_handoff_state(raw: Any) -> Dict[str, Any]:
    messages = _message_history(raw)
    if not messages:
        return {}
    handoffs: List[Dict[str, Any]] = []
    reviews: List[Dict[str, Any]] = []
    reconciliations: List[Dict[str, Any]] = []
    participants: set[str] = set()

    for index, message in enumerate(messages, start=1):
        source = str(
            message.get("source")
            or message.get("speaker")
            or message.get("name")
            or message.get("role")
            or ""
        )
        if source:
            participants.add(source)
        target = str(message.get("handoff_to") or message.get("recipient") or "")
        if target:
            participants.add(target)
        if _is_handoff_message(message):
            handoffs.append(
                {
                    "index": index,
                    "name": str(message.get("name") or "framework_handoff"),
                    "from": str(message.get("handoff_from") or source),
                    "to": target,
                    "task": str(message.get("task") or _message_content(message)),
                    "reason": str(message.get("reason") or message.get("rationale") or ""),
                    "message_type": str(
                        message.get("type") or message.get("kind") or message.get("role") or ""
                    ),
                }
            )
        review = _review_payload_from_message(message, index=index, source=source)
        if review:
            reviewer = str(review.get("reviewer") or "")
            if reviewer:
                participants.add(reviewer)
            review_target = str(review.get("target") or "")
            if review_target:
                participants.add(review_target)
            reviews.append(review)
        reconciliation = _reconciliation_payload_from_message(
            message,
            index=index,
            source=source,
        )
        if reconciliation:
            accepted_source = str(reconciliation.get("accepted_source") or "")
            if accepted_source:
                participants.add(accepted_source)
            reconciliations.append(reconciliation)

    if not handoffs and not reviews and not reconciliations:
        return {}
    return {
        "handoff_count": len(handoffs),
        "review_count": len(reviews),
        "reconciliation_count": len(reconciliations),
        "participants": sorted(participants),
        "handoffs": handoffs,
        "reviews": reviews,
        "reconciliations": reconciliations,
    }


def _is_handoff_message(message: Mapping[str, Any]) -> bool:
    if message.get("handoff_to") or message.get("recipient"):
        return True
    message_type = str(message.get("type") or message.get("kind") or "").lower()
    name = str(message.get("name") or "").lower()
    return "handoff" in message_type or "handoff" in name


def _review_payload_from_message(
    message: Mapping[str, Any],
    *,
    index: int,
    source: str,
) -> Dict[str, Any]:
    review = _object_mapping(message.get("review"))
    if review:
        payload = {
            "index": index,
            "name": str(message.get("name") or review.get("name") or "framework_review"),
            "reviewer": str(review.get("reviewer") or review.get("by") or source),
            "target": str(review.get("target") or review.get("target_agent") or ""),
            "status": str(review.get("status") or review.get("verdict") or ""),
            "message_type": str(message.get("type") or message.get("kind") or ""),
        }
        if review.get("notes") not in (None, "", [], {}):
            payload["notes"] = _plain_value(review.get("notes"))
        return payload
    if not (
        message.get("review_target")
        or message.get("reviewer")
        or "review" in str(message.get("type") or message.get("kind") or "").lower()
        or "review" in str(message.get("name") or "").lower()
    ):
        return {}
    return {
        "index": index,
        "name": str(message.get("name") or "framework_review"),
        "reviewer": str(message.get("reviewer") or source),
        "target": str(message.get("review_target") or message.get("target") or ""),
        "status": str(message.get("review_status") or message.get("status") or ""),
        "message_type": str(message.get("type") or message.get("kind") or ""),
        "content": _message_content(message),
    }


def _reconciliation_payload_from_message(
    message: Mapping[str, Any],
    *,
    index: int,
    source: str,
) -> Dict[str, Any]:
    reconciliation = _object_mapping(message.get("reconciliation"))
    if reconciliation:
        payload = {
            "index": index,
            "name": str(
                message.get("name")
                or reconciliation.get("name")
                or "framework_reconciliation"
            ),
            "source": source,
            "accepted_source": str(reconciliation.get("accepted_source") or ""),
            "status": str(reconciliation.get("status") or reconciliation.get("verdict") or ""),
            "message_type": str(message.get("type") or message.get("kind") or ""),
        }
        if reconciliation.get("notes") not in (None, "", [], {}):
            payload["notes"] = _plain_value(reconciliation.get("notes"))
        return payload
    if not (
        message.get("accepted_source")
        or message.get("reconciliation_status")
        or "reconciliation" in str(message.get("type") or message.get("kind") or "").lower()
        or "reconciliation" in str(message.get("name") or "").lower()
    ):
        return {}
    return {
        "index": index,
        "name": str(message.get("name") or "framework_reconciliation"),
        "source": source,
        "accepted_source": str(message.get("accepted_source") or ""),
        "status": str(message.get("reconciliation_status") or message.get("status") or ""),
        "message_type": str(message.get("type") or message.get("kind") or ""),
        "content": _message_content(message),
    }


def _realtime_trace_state(raw: Any) -> Dict[str, Any]:
    frames = _realtime_frames(raw)
    events = _realtime_session_events(raw)
    if not frames and not events:
        return {}

    frame_entries = [
        _realtime_item_entry(frame, index=index, source="frame")
        for index, frame in enumerate(frames, start=1)
    ]
    event_entries = [
        _realtime_item_entry(event, index=index, source="event")
        for index, event in enumerate(events, start=1)
    ]
    items = [*frame_entries, *event_entries]
    frame_types = sorted(
        {
            str(item.get("item_type") or "")
            for item in frame_entries
            if item.get("item_type")
        }
    )
    event_types = sorted(
        {
            str(item.get("item_type") or "")
            for item in event_entries
            if item.get("item_type")
        }
    )
    categories = sorted(
        {
            str(item.get("category") or "")
            for item in items
            if item.get("category")
        }
    )
    directions = sorted(
        {
            str(item.get("direction") or "")
            for item in items
            if item.get("direction")
        }
    )
    modalities = sorted(
        {
            str(item.get("modality") or "")
            for item in items
            if item.get("modality")
        }
    )
    tool_names = sorted(
        {
            str(tool.get("name") or "")
            for tool in _realtime_tool_calls(raw)
            if tool.get("name")
        }
    )
    transcripts = [
        _realtime_compact_transcript(item)
        for item in items
        if _realtime_compact_transcript(item)
    ]
    signals = sorted(
        {
            signal
            for item in items
            for signal in _realtime_item_signals(item)
        }
    )
    kind_counts: Dict[str, int] = {}
    for item in items:
        kind = str(item.get("kind") or "event")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1

    return {
        "kind": "framework_realtime_trace",
        "frame_count": len(frame_entries),
        "event_count": len(event_entries),
        "tool_call_count": len(_realtime_tool_calls(raw)),
        "tool_response_count": len(_realtime_tool_responses(raw)),
        "transcript_count": kind_counts.get("transcript", 0),
        "audio_frame_count": kind_counts.get("audio", 0),
        "lifecycle_event_count": kind_counts.get("lifecycle", 0),
        "interruption_count": kind_counts.get("interruption", 0),
        "error_count": kind_counts.get("error", 0),
        "completion_count": kind_counts.get("completion", 0),
        "signals": signals,
        "frame_types": frame_types,
        "event_types": event_types,
        "categories": categories,
        "directions": directions,
        "modalities": modalities,
        "tool_names": tool_names,
        "transcripts": transcripts,
        "frames": frame_entries,
        "events": event_entries,
        "summary": {
            "frame_count": len(frame_entries),
            "event_count": len(event_entries),
            "tool_call_count": len(_realtime_tool_calls(raw)),
            "tool_response_count": len(_realtime_tool_responses(raw)),
            "transcript_count": kind_counts.get("transcript", 0),
            "audio_frame_count": kind_counts.get("audio", 0),
            "lifecycle_event_count": kind_counts.get("lifecycle", 0),
            "completion_count": kind_counts.get("completion", 0),
            "error_count": kind_counts.get("error", 0),
        },
    }


def _realtime_trace_events(raw: Any) -> List[SimulationEvent]:
    frames = _realtime_frames(raw)
    events = _realtime_session_events(raw)
    normalized: List[SimulationEvent] = []
    for index, frame in enumerate(frames, start=1):
        entry = _realtime_item_entry(frame, index=index, source="frame")
        normalized.append(
            SimulationEvent(
                type="realtime_frame",
                name=str(entry.get("name") or entry.get("item_type") or f"frame_{index}"),
                payload=entry,
                timestamp_ms=_realtime_timestamp_ms(frame),
                metadata={
                    "kind": "realtime_trace",
                    "source": "frame",
                    "category": str(entry.get("category") or ""),
                },
            )
        )
        specialized = _realtime_specialized_event_type(entry)
        if specialized != "realtime_frame":
            normalized.append(
                SimulationEvent(
                    type=specialized,
                    name=str(entry.get("name") or entry.get("item_type") or specialized),
                    payload=entry,
                    timestamp_ms=_realtime_timestamp_ms(frame),
                    metadata={
                        "kind": "realtime_trace",
                        "source": "frame",
                        "category": str(entry.get("category") or ""),
                    },
                )
            )
    for index, event in enumerate(events, start=1):
        entry = _realtime_item_entry(event, index=index, source="event")
        event_type = _realtime_specialized_event_type(entry)
        normalized.append(
            SimulationEvent(
                type=event_type,
                name=str(entry.get("name") or entry.get("item_type") or f"event_{index}"),
                payload=entry,
                timestamp_ms=_realtime_timestamp_ms(event),
                metadata={
                    "kind": "realtime_trace",
                    "source": "event",
                    "category": str(entry.get("category") or ""),
                },
            )
        )
    return normalized


def _realtime_tool_calls(raw: Any) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    for index, item in enumerate([*_realtime_frames(raw), *_realtime_session_events(raw)], start=1):
        item_type = _realtime_item_type(item).lower()
        if not _realtime_is_tool_call(item, item_type):
            continue
        name = _realtime_tool_name(item) or f"realtime_tool_{index}"
        calls.append(
            {
                "id": str(
                    item.get("id")
                    or item.get("call_id")
                    or item.get("tool_call_id")
                    or name
                ),
                "type": "function",
                "name": name,
                "arguments": _plain_value(
                    item.get("arguments")
                    if "arguments" in item
                    else item.get("args", item.get("input", item.get("payload", {})))
                ),
                "function": {
                    "name": name,
                    "arguments": _plain_value(
                        item.get("arguments")
                        if "arguments" in item
                        else item.get("args", item.get("input", item.get("payload", {})))
                    ),
                },
            }
        )
    return calls


def _realtime_tool_responses(raw: Any) -> List[Dict[str, Any]]:
    responses: List[Dict[str, Any]] = []
    for index, item in enumerate([*_realtime_frames(raw), *_realtime_session_events(raw)], start=1):
        item_type = _realtime_item_type(item).lower()
        if not _realtime_is_tool_response(item, item_type):
            continue
        name = _realtime_tool_name(item) or f"realtime_tool_{index}"
        content = item.get("result", item.get("output", item.get("response", item.get("content", ""))))
        responses.append(
            {
                "id": str(
                    item.get("id")
                    or item.get("call_id")
                    or item.get("tool_call_id")
                    or name
                ),
                "name": name,
                "content": _plain_value(content),
                "is_error": bool(item.get("is_error") or item.get("error")),
            }
        )
    return responses


def _realtime_last_text(raw: Any) -> str:
    entries = [
        *[
            _realtime_item_entry(frame, index=index, source="frame")
            for index, frame in enumerate(_realtime_frames(raw), start=1)
        ],
        *[
            _realtime_item_entry(event, index=index, source="event")
            for index, event in enumerate(_realtime_session_events(raw), start=1)
        ],
    ]
    for entry in reversed(entries):
        text = str(entry.get("text") or "")
        if text:
            return text
    return ""


def _realtime_frames(raw: Any) -> List[Dict[str, Any]]:
    frames = _extract_list_field(
        raw,
        (
            "frames",
            "frame_trace",
            "pipeline_frames",
            "pipecat_frames",
            "media_frames",
        ),
    )
    return [dict(frame) for frame in frames or []]


def _realtime_session_events(raw: Any) -> List[Dict[str, Any]]:
    candidates = _extract_list_field(
        raw,
        (
            "session_events",
            "sessionEvents",
            "livekit_events",
            "realtime_events",
            "events",
            "trajectory",
            "spans",
        ),
    )
    return [
        dict(event)
        for event in candidates or []
        if _is_realtime_item(event)
    ]


def _is_realtime_item(item: Mapping[str, Any]) -> bool:
    keys = set(item)
    if keys & {
        "frame_type",
        "frameType",
        "direction",
        "sample_rate",
        "sample_rate_hz",
        "audio",
        "transcript",
        "utterance",
        "agent_state",
        "user_state",
        "from_state",
        "to_state",
        "tool_name",
        "function_name",
        "speech_id",
        "interrupted",
    }:
        return True
    text = " ".join(
        str(item.get(key) or "")
        for key in ("type", "event", "name", "kind", "category", "source")
    ).lower()
    return any(
        token in text
        for token in (
            "audio",
            "speech",
            "tts",
            "stt",
            "vad",
            "transcript",
            "utterance",
            "session",
            "participant",
            "agent_state",
            "user_state",
            "tool_execution",
            "function_call",
            "interruption",
            "turn_start",
            "turn_end",
        )
    )


def _realtime_item_entry(
    item: Mapping[str, Any],
    *,
    index: int,
    source: str,
) -> Dict[str, Any]:
    item_type = _realtime_item_type(item)
    text = _realtime_item_text(item)
    entry: Dict[str, Any] = {
        "index": index,
        "source": source,
        "item_type": item_type,
        "name": _realtime_item_name(item, item_type=item_type),
        "kind": _realtime_item_kind(item, item_type=item_type),
        "category": _realtime_item_category(item, item_type=item_type),
        "direction": str(item.get("direction") or item.get("frame_direction") or ""),
        "modality": str(item.get("modality") or _realtime_item_modality(item, item_type)),
        "payload": _plain_value(dict(item)),
    }
    timestamp = _realtime_timestamp_ms(item)
    if timestamp is not None:
        entry["timestamp_ms"] = timestamp
    if text:
        entry["text"] = text
        entry["text_length"] = len(text)
    for key in (
        "participant",
        "participant_id",
        "agent",
        "speaker",
        "role",
        "from_state",
        "to_state",
        "state",
        "sample_rate",
        "sample_rate_hz",
        "duration_ms",
    ):
        value = item.get(key)
        if value not in (None, "", [], {}):
            entry[key] = _plain_value(value)
    tool_name = _realtime_tool_name(item)
    if tool_name:
        entry["tool_name"] = tool_name
    return entry


def _realtime_item_type(item: Mapping[str, Any]) -> str:
    for key in ("frame_type", "frameType", "type", "event", "kind", "name"):
        value = item.get(key)
        if value not in (None, "", [], {}):
            return str(value)
    return "realtime_item"


def _realtime_item_name(item: Mapping[str, Any], *, item_type: str) -> str:
    for key in ("name", "event", "id", "tool_name", "function_name"):
        value = item.get(key)
        if value not in (None, "", [], {}):
            return str(value)
    return item_type


def _realtime_item_kind(item: Mapping[str, Any], *, item_type: str) -> str:
    normalized = _realtime_key(
        " ".join(
            str(item.get(key) or "")
            for key in ("type", "event", "kind", "name", "frame_type", "frameType")
        )
        or item_type
    )
    if "error" in normalized or item.get("error"):
        return "error"
    if "interrupt" in normalized or item.get("interrupted"):
        return "interruption"
    if _realtime_is_tool_response(item, normalized):
        return "tool_response"
    if _realtime_is_tool_call(item, normalized):
        return "tool_call"
    if "transcript" in normalized or "utterance" in normalized or item.get("transcript"):
        return "transcript"
    if (
        "audio" in normalized
        or "tts" in normalized
        or "stt" in normalized
        or "vad" in normalized
        or item.get("audio")
        or item.get("sample_rate")
        or item.get("sample_rate_hz")
    ):
        return "audio"
    if (
        "complete" in normalized
        or "completed" in normalized
        or "final" in normalized
        or "closed" in normalized
        or "end" in normalized
    ):
        return "completion"
    if (
        "session" in normalized
        or "state" in normalized
        or "participant" in normalized
        or "start" in normalized
        or "connect" in normalized
        or item.get("from_state")
        or item.get("to_state")
    ):
        return "lifecycle"
    return "frame" if "frame" in normalized else "event"


def _realtime_is_tool_call(item: Mapping[str, Any], item_type: str) -> bool:
    normalized = _realtime_key(item_type)
    return bool(
        item.get("tool_name")
        or item.get("function_name")
        or item.get("function")
        or "functioncall" in normalized
        or "toolcall" in normalized
        or "toolexecutionstarted" in normalized
        or "toolexecutionrequested" in normalized
    ) and not _realtime_is_tool_response(item, item_type)


def _realtime_is_tool_response(item: Mapping[str, Any], item_type: str) -> bool:
    normalized = _realtime_key(item_type)
    return bool(
        item.get("result") not in (None, "", [], {})
        or item.get("tool_result") not in (None, "", [], {})
        or "functioncallresult" in normalized
        or "toolresult" in normalized
        or "toolexecutioncompleted" in normalized
        or "toolexecutionfailed" in normalized
    )


def _realtime_tool_name(item: Mapping[str, Any]) -> str:
    function = _object_mapping(item.get("function")) or {}
    tool_call = _object_mapping(item.get("tool_call")) or {}
    for value in (
        item.get("tool_name"),
        item.get("function_name"),
        item.get("tool"),
        function.get("name"),
        tool_call.get("name"),
        item.get("name"),
    ):
        if value not in (None, "", [], {}):
            return str(value)
    return ""


def _realtime_item_category(item: Mapping[str, Any], *, item_type: str) -> str:
    for key in ("category", "frame_category", "frameCategory"):
        value = item.get(key)
        if value not in (None, "", [], {}):
            return str(value)
    normalized = _realtime_key(item_type)
    if "systemframe" in normalized:
        return "system"
    if "controlframe" in normalized:
        return "control"
    if "dataframe" in normalized or "audio" in normalized or "transcript" in normalized:
        return "data"
    if "frame" in normalized:
        return "frame"
    return "event"


def _realtime_item_modality(item: Mapping[str, Any], item_type: str) -> str:
    normalized = _realtime_key(item_type)
    if (
        item.get("audio")
        or item.get("sample_rate")
        or item.get("sample_rate_hz")
        or "audio" in normalized
        or "speech" in normalized
        or "tts" in normalized
        or "stt" in normalized
        or "vad" in normalized
    ):
        return "voice"
    if "video" in normalized:
        return "video"
    return ""


def _realtime_item_text(item: Mapping[str, Any]) -> str:
    for key in ("transcript", "text", "content", "utterance", "delta"):
        value = item.get(key)
        if value not in (None, "", [], {}):
            return _stringify(value)
    payload = _object_mapping(item.get("payload"))
    if payload:
        for key in ("transcript", "text", "content", "utterance", "delta"):
            value = payload.get(key)
            if value not in (None, "", [], {}):
                return _stringify(value)
    return ""


def _realtime_timestamp_ms(item: Mapping[str, Any]) -> Optional[int]:
    for key in ("timestamp_ms", "time_ms", "start_ms", "elapsed_ms"):
        value = item.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    value = item.get("timestamp")
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _realtime_specialized_event_type(entry: Mapping[str, Any]) -> str:
    kind = str(entry.get("kind") or "")
    return {
        "audio": "realtime_audio_frame",
        "completion": "realtime_completion",
        "error": "realtime_error",
        "interruption": "realtime_interruption",
        "lifecycle": "realtime_lifecycle",
        "tool_call": "realtime_tool_call",
        "tool_response": "realtime_tool_response",
        "transcript": "realtime_transcript",
    }.get(kind, "realtime_frame")


def _realtime_item_signals(entry: Mapping[str, Any]) -> set[str]:
    signals = {"realtime"}
    source = str(entry.get("source") or "")
    kind = str(entry.get("kind") or "")
    category = str(entry.get("category") or "")
    if source:
        signals.add(source)
    if kind:
        signals.add(kind)
    if category:
        signals.add(f"{category}_frame" if category != "event" else "event")
    if entry.get("direction"):
        signals.add("direction")
    if entry.get("tool_name"):
        signals.add("tool")
    if entry.get("modality"):
        signals.add(str(entry["modality"]))
    return signals


def _realtime_compact_transcript(entry: Mapping[str, Any]) -> Dict[str, Any]:
    if entry.get("kind") != "transcript" or not entry.get("text"):
        return {}
    return {
        "index": entry.get("index"),
        "source": entry.get("source"),
        "role": entry.get("role"),
        "speaker": entry.get("speaker") or entry.get("participant"),
        "text": entry.get("text"),
    }


def _realtime_key(value: Any) -> str:
    return str(value or "").lower().replace("_", "").replace("-", "").replace(" ", "")


def _message_history_state(raw: Any) -> Dict[str, Any]:
    messages = _message_history(raw)
    if not messages:
        return {}
    tool_calls = [
        call
        for message in messages
        for call in _tool_calls_from_message(message)
    ]
    tool_responses = _message_history_tool_responses(raw)
    roles = sorted(
        {
            str(message.get("role"))
            for message in messages
            if message.get("role") not in (None, "", [], {})
        }
    )
    sources = sorted(
        {
            str(message.get("source") or message.get("speaker") or message.get("name"))
            for message in messages
            if message.get("source") or message.get("speaker") or message.get("name")
        }
    )
    types = sorted(
        {
            str(message.get("type") or message.get("kind") or message.get("role") or "")
            for message in messages
            if message.get("type") or message.get("kind") or message.get("role")
        }
    )
    stop_reason = _message_history_stop_reason(raw)
    state: Dict[str, Any] = {
        "message_count": len(messages),
        "roles": roles,
        "sources": sources,
        "types": types,
        "tool_call_count": len(tool_calls),
        "tool_response_count": len(tool_responses),
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
        "last_content": _message_content(messages[-1]),
        "messages": [
            {
                "index": index,
                "type": str(message.get("type") or message.get("kind") or ""),
                "role": str(message.get("role") or ""),
                "source": str(message.get("source") or message.get("speaker") or message.get("name") or ""),
                "content_length": len(_message_content(message)),
                "tool_call_count": len(_tool_calls_from_message(message)),
            }
            for index, message in enumerate(messages, start=1)
        ],
    }
    if stop_reason:
        state["stop_reason"] = stop_reason
    handoffs = [
        {
            "from": message.get("handoff_from"),
            "to": message.get("handoff_to") or message.get("recipient"),
            "task": message.get("task"),
        }
        for message in messages
        if message.get("handoff_to") or message.get("recipient")
    ]
    if handoffs:
        state["handoff_count"] = len(handoffs)
        state["handoffs"] = handoffs
    return state


def _message_history(raw: Any) -> List[Dict[str, Any]]:
    value = None
    raw_mapping = _object_mapping(raw)
    for name in ("messages", "history", "chat_history", "conversation"):
        if raw_mapping is not None and raw_mapping.get(name) is not None:
            value = raw_mapping.get(name)
            break
        if raw_mapping is None and hasattr(raw, name):
            value = getattr(raw, name)
            break
    if not isinstance(value, (list, tuple)):
        return []
    messages: List[Dict[str, Any]] = []
    for item in value:
        mapping = _message_mapping(item)
        if mapping:
            messages.append(mapping)
    return messages


def _message_mapping(message: Any) -> Dict[str, Any]:
    mapping = _object_mapping(message)
    if mapping is not None:
        return mapping
    values: Dict[str, Any] = {}
    for attr in (
        "id",
        "type",
        "kind",
        "role",
        "source",
        "speaker",
        "name",
        "content",
        "tool_calls",
        "tool_responses",
        "metadata",
        "models_usage",
        "handoff_from",
        "handoff_to",
        "recipient",
        "task",
        "call_id",
        "tool_call_id",
        "result",
        "output",
        "is_error",
    ):
        if not hasattr(message, attr):
            continue
        value = getattr(message, attr)
        if value not in (None, "", [], {}):
            values[attr] = _plain_value(value)
    if values and "type" not in values:
        values["type"] = type(message).__name__
    return values


def _message_history_stop_reason(raw: Any) -> str:
    raw_mapping = _object_mapping(raw)
    if raw_mapping is not None:
        for key in ("stop_reason", "finish_reason", "termination", "termination_reason"):
            value = raw_mapping.get(key)
            if value not in (None, "", [], {}):
                return str(value)
    for attr in ("stop_reason", "finish_reason", "termination", "termination_reason"):
        if hasattr(raw, attr):
            value = getattr(raw, attr)
            if value not in (None, "", [], {}):
                return str(value)
    return ""


def _resolve_callable_attr_path(root: Any, path: str | None) -> Callable[..., Any] | None:
    if not path:
        return root if callable(root) else None
    value = root
    for raw_part in str(path).split("."):
        part = raw_part.strip()
        if not part:
            return None
        try:
            value = getattr(value, part)
        except Exception:
            return None
    return value if callable(value) else None


def _method_leaf(method_name: str | None) -> str:
    return str(method_name or "").rsplit(".", 1)[-1]


def _invoke_method_with_payload(
    method: Callable[..., Any],
    payload: Any,
    *,
    method_name: str | None,
    input_key: str | None,
    input_kwargs: Mapping[str, Any] | None,
) -> tuple[Any, str, str | None]:
    static_kwargs = {str(key): value for key, value in dict(input_kwargs or {}).items()}
    if payload is _NO_PAYLOAD:
        if static_kwargs:
            return method(**static_kwargs), "keyword", None
        return method(), "none", None

    if input_key:
        selected_key = str(input_key)
        return method(**{**static_kwargs, selected_key: payload}), "keyword", selected_key

    selected_key = _signature_input_key(method, method_name=method_name)
    if selected_key:
        return method(**{**static_kwargs, selected_key: payload}), "keyword", selected_key

    if _signature_accepts_positional(method):
        if static_kwargs:
            return method(payload, **static_kwargs), "positional_with_kwargs", None
        return method(payload), "positional", None

    if _signature_accepts_var_keyword(method) and isinstance(payload, Mapping):
        return method(**{**dict(payload), **static_kwargs}), "expanded_kwargs", None

    if static_kwargs:
        return method(payload, **static_kwargs), "positional_with_kwargs", None
    return method(payload), "positional", None


def _signature_input_key(
    method: Callable[..., Any],
    *,
    method_name: str | None,
) -> str | None:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return None
    params = list(signature.parameters.values())
    names = {param.name: param for param in params}
    method_preferences = (
        _METHOD_INPUT_KEY_PREFERENCES.get(str(method_name or ""))
        or _METHOD_INPUT_KEY_PREFERENCES.get(_method_leaf(method_name), ())
    )
    preferred_names = method_preferences + _KEYWORD_INPUT_NAMES
    accepts_positional = _params_accept_positional(params)

    for name in preferred_names:
        param = names.get(name)
        if param is None:
            continue
        if param.kind == inspect.Parameter.POSITIONAL_ONLY:
            continue
        if name == "inputs" or not accepts_positional or _keyword_only(param):
            return name
    if not accepts_positional:
        for param in params:
            if param.kind == inspect.Parameter.KEYWORD_ONLY:
                return param.name
        if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params):
            first_preference = next(iter(preferred_names), None)
            return first_preference
    return None


def _signature_accepts_positional(method: Callable[..., Any]) -> bool:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return True
    return _params_accept_positional(list(signature.parameters.values()))


def _params_accept_positional(params: List[inspect.Parameter]) -> bool:
    return any(
        param.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        }
        for param in params
    )


def _signature_accepts_var_keyword(method: Callable[..., Any]) -> bool:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return False
    return any(
        param.kind == inspect.Parameter.VAR_KEYWORD
        for param in signature.parameters.values()
    )


def _keyword_only(param: inspect.Parameter) -> bool:
    return param.kind == inspect.Parameter.KEYWORD_ONLY


def _is_async_stream(value: Any) -> bool:
    if isinstance(value, (AgentResponse, str, bytes, dict, list, tuple)):
        return False
    return inspect.isasyncgen(value) or hasattr(value, "__anext__") or hasattr(value, "__aiter__")


def _is_sync_stream(value: Any) -> bool:
    if isinstance(value, (AgentResponse, str, bytes, dict, list, tuple)):
        return False
    return inspect.isgenerator(value) or hasattr(value, "__next__")


def _stream_chunk_text(chunk: Any) -> str:
    if chunk is None:
        return ""
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, bytes):
        return chunk.decode("utf-8", errors="replace")
    if isinstance(chunk, dict):
        for key in (
            "content",
            "delta",
            "text",
            "transcript",
            "output",
            "response",
            "final_output",
        ):
            value = chunk.get(key)
            if value is not None:
                return _stringify(value)
        for key in ("message", "chunk"):
            if key in chunk:
                return _message_content(chunk[key])
        if "choices" in chunk:
            return _choices_content(chunk["choices"])
        for key in ("data", "payload"):
            value = chunk.get(key)
            if isinstance(value, dict):
                text = _stream_chunk_text(value)
                if text:
                    return text
    for attr in ("content", "delta", "text", "transcript", "output", "response"):
        if hasattr(chunk, attr):
            value = getattr(chunk, attr)
            if value is not None:
                return _stringify(value)
    if hasattr(chunk, "message"):
        return _message_content(getattr(chunk, "message"))
    if hasattr(chunk, "choices"):
        return _choices_content(getattr(chunk, "choices"))
    return ""


def _stream_chunk_event(chunk: Any, *, index: int, text: str) -> SimulationEvent:
    payload = _stream_chunk_payload(chunk)
    if text:
        payload.setdefault("delta", text)
    return SimulationEvent(
        type=_stream_chunk_event_type(chunk),
        name=_stream_chunk_event_name(chunk, index=index),
        payload=payload,
        timestamp_ms=_stream_chunk_timestamp_ms(chunk),
        metadata={"stream_index": index},
    )


def _stream_chunk_event_type(chunk: Any) -> str:
    value = _stream_chunk_field(chunk, ("type", "event", "frame_type", "method"))
    if value:
        return str(value)
    return "stream_chunk"


def _stream_chunk_event_name(chunk: Any, *, index: int) -> str:
    value = _stream_chunk_field(chunk, ("name", "id", "event_id"))
    if value:
        return str(value)
    return f"stream_chunk_{index}"


def _stream_chunk_timestamp_ms(chunk: Any) -> Optional[int]:
    value = _stream_chunk_field(chunk, ("timestamp_ms", "time_ms"))
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _stream_chunk_payload(chunk: Any) -> Dict[str, Any]:
    chunk_mapping = _object_mapping(chunk)
    if chunk_mapping is not None:
        return dict(chunk_mapping)
    if isinstance(chunk, (str, bytes)):
        return {"delta": _stream_chunk_text(chunk)}
    payload: Dict[str, Any] = {}
    for key in ("id", "type", "event", "name", "content", "delta", "text", "transcript"):
        if hasattr(chunk, key):
            value = getattr(chunk, key)
            if value is not None:
                payload[key] = value
    return payload or {"value": str(chunk)}


def _stream_chunk_field(chunk: Any, names: Iterable[str]) -> Any:
    chunk_mapping = _object_mapping(chunk)
    if chunk_mapping is not None:
        for name in names:
            value = chunk_mapping.get(name)
            if value is not None:
                return value
        for key in ("data", "payload"):
            value = chunk_mapping.get(key)
            if isinstance(value, dict):
                nested = _stream_chunk_field(value, names)
                if nested is not None:
                    return nested
    for name in names:
        if hasattr(chunk, name):
            value = getattr(chunk, name)
            if value is not None:
                return value
    return None


def _streaming_trace_from_chunks(chunks: List[Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
    from fi.simulate.environment import normalize_streaming_trace_events

    framework = str(metadata.get("framework") or "generic")
    trace_metadata = {
        "source": "generic_agent_wrapper",
        **dict(metadata),
    }
    return normalize_streaming_trace_events(
        framework,
        chunks,
        metadata=trace_metadata,
    )


def _framework_runtime_trace(
    *,
    framework: str,
    method_name: str | None,
    input_mode: str,
    payload: Any,
    response: str | AgentResponse,
    duration_ms: int,
    streamed: bool,
    call_style: str,
    input_key: str | None,
    input_kwargs_keys: List[str],
    wrapper_metadata: Dict[str, Any],
    runtime_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    response_dict = _response_summary(response)
    signals = {"framework", "runtime", "method", "input", "output", "latency"}
    if streamed or response_dict.get("streaming"):
        signals.add("streaming")
    if response_dict.get("tool_call_count", 0) > 0:
        signals.add("tool")
    if response_dict.get("artifact_count", 0) > 0:
        signals.add("artifact")
    if response_dict.get("event_count", 0) > 0:
        signals.add("event")
    if response_dict.get("state_keys"):
        signals.add("state")
    if response_dict.get("metadata_keys"):
        signals.add("metadata")

    invocation = {
        "id": "framework_runtime_1",
        "framework": framework or "generic",
        "method": method_name or "callable",
        "input_mode": "none" if payload is _NO_PAYLOAD else input_mode,
        "input": _shape_summary(payload),
        "output": response_dict,
        "duration_ms": max(0, int(duration_ms)),
        "call_style": call_style,
        "signals": sorted(signals),
    }
    if input_key:
        invocation["input_key"] = input_key
    if input_kwargs_keys:
        invocation["input_kwargs_keys"] = input_kwargs_keys
    summary = {
        "invocation_count": 1,
        "framework": framework or "generic",
        "methods": [invocation["method"]],
        "input_modes": [invocation["input_mode"]],
        "call_styles": [call_style],
        "input_keys": [input_key] if input_key else [],
        "input_kwargs_keys": input_kwargs_keys,
        "output_types": [response_dict["type"]],
        "tool_call_count": response_dict.get("tool_call_count", 0),
        "artifact_count": response_dict.get("artifact_count", 0),
        "event_count": response_dict.get("event_count", 0),
        "state_key_count": len(response_dict.get("state_keys", [])),
        "metadata_key_count": len(response_dict.get("metadata_keys", [])),
        "streamed": bool(streamed or response_dict.get("streaming")),
        "error_count": 0,
        "duration_ms": invocation["duration_ms"],
    }
    return {
        "kind": "framework_runtime",
        "framework": framework or "generic",
        "modality": wrapper_metadata.get("modality"),
        "invocations": [invocation],
        "summary": summary,
        "signals": sorted(signals),
        "metadata": {
            "source": "generic_agent_wrapper",
            **dict(wrapper_metadata),
            **dict(runtime_metadata),
        },
    }


def _attach_framework_runtime_trace(
    response: str | AgentResponse,
    trace: Dict[str, Any],
) -> AgentResponse:
    artifact = SimulationArtifact(
        type="trace",
        role="assistant",
        data=trace,
        metadata={
            "kind": "framework_runtime",
            "framework": trace.get("framework", "generic"),
            "source": "generic_agent_wrapper",
        },
    )
    event = SimulationEvent(
        type="framework_runtime",
        name=str(trace["invocations"][0].get("method") or "callable"),
        payload=trace["invocations"][0],
        metadata={"kind": "framework_runtime", "framework": trace.get("framework", "generic")},
    )
    runtime_metadata = {
        "framework_runtime": {
            "framework": trace.get("framework", "generic"),
            "signals": list(trace.get("signals", [])),
            "summary": dict(trace.get("summary", {})),
        }
    }
    if not isinstance(response, AgentResponse):
        return AgentResponse(
            content=str(response),
            artifacts=[artifact],
            events=[event],
            state={"framework_runtime": trace},
            metadata=runtime_metadata,
        )

    state = dict(response.state or {})
    state["framework_runtime"] = trace
    metadata = {**dict(response.metadata or {}), **runtime_metadata}
    return AgentResponse(
        content=response.content,
        tool_calls=response.tool_calls,
        tool_responses=response.tool_responses,
        artifacts=[*response.artifacts, artifact],
        events=[*response.events, event],
        memory_updates=response.memory_updates,
        state=state,
        metadata=metadata,
    )


def _response_summary(response: str | AgentResponse) -> Dict[str, Any]:
    if not isinstance(response, AgentResponse):
        return {
            "type": type(response).__name__,
            "content_length": len(str(response)),
            "tool_call_count": 0,
            "artifact_count": 0,
            "event_count": 0,
            "state_keys": [],
            "metadata_keys": [],
            "streaming": False,
        }
    metadata = dict(response.metadata or {})
    state = dict(response.state or {})
    return {
        "type": "AgentResponse",
        "content_length": len(response.content or ""),
        "tool_call_count": len(response.tool_calls or []),
        "tool_names": sorted(
            {
                str(call.get("name") or call.get("tool") or call.get("function", {}).get("name") or "")
                for call in response.tool_calls or []
                if isinstance(call, dict)
            }
        ),
        "tool_response_count": len(response.tool_responses or []),
        "artifact_count": len(response.artifacts),
        "artifact_types": sorted({artifact.type for artifact in response.artifacts}),
        "event_count": len(response.events),
        "event_types": sorted({event.type for event in response.events}),
        "state_keys": sorted(str(key) for key in state.keys()),
        "metadata_keys": sorted(str(key) for key in metadata.keys()),
        "streaming": bool(metadata.get("streaming") or state.get("streaming_trace")),
    }


def _shape_summary(value: Any) -> Dict[str, Any]:
    if value is _NO_PAYLOAD:
        return {"type": "none"}
    if isinstance(value, AgentInput):
        return {
            "type": "AgentInput",
            "message_count": len(value.messages),
            "tool_count": len(value.tools),
            "artifact_count": len(value.artifacts),
            "event_count": len(value.events),
            "modality": value.modality,
        }
    if isinstance(value, dict):
        return {
            "type": "dict",
            "keys": sorted(str(key) for key in value.keys()),
            "message_count": len(value.get("messages") or []),
            "tool_count": len(value.get("tools") or []),
            "artifact_count": len(value.get("artifacts") or []),
            "event_count": len(value.get("events") or []),
            "has_metadata": isinstance(value.get("metadata"), dict),
        }
    if isinstance(value, list):
        return {"type": "list", "length": len(value)}
    if isinstance(value, tuple):
        return {"type": "tuple", "length": len(value)}
    if isinstance(value, (str, bytes)):
        text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
        return {"type": type(value).__name__, "length": len(text)}
    return {"type": type(value).__name__}


def _choices_content(choices: Any) -> str:
    if not choices:
        return ""
    first = choices[0]
    if isinstance(first, dict):
        return _message_content(first.get("message") or first.get("delta") or first)
    return _message_content(getattr(first, "message", None) or getattr(first, "delta", None) or first)


def _last_message_content(messages: Any) -> str:
    if not messages:
        return ""
    try:
        return _message_content(list(messages)[-1])
    except TypeError:
        return _message_content(messages)


def _message_content(message: Any) -> str:
    if message is None:
        return ""
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        if "content" in message and message["content"] is not None:
            block_text = _content_blocks_text(message["content"])
            if block_text:
                return block_text
            return _stringify(message["content"])
        if "text" in message and message["text"] is not None:
            return _stringify(message["text"])
        if "parts" in message:
            return " ".join(_stringify(part) for part in message["parts"])
    for attr in ("content", "text"):
        if hasattr(message, attr):
            value = getattr(message, attr)
            if value is not None:
                return _stringify(value)
    return str(message)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    plain = _plain_value(value)
    if isinstance(plain, (dict, list, tuple)):
        return json.dumps(plain, default=str)
    return str(value)


def _model_to_dict(value: Any) -> Dict[str, Any]:
    mapping = _object_mapping(value)
    if mapping is not None:
        return dict(mapping)
    return dict(value)


def _object_mapping(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, Mapping):
        return {
            str(key): _plain_value(item)
            for key, item in value.items()
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            str(key): _plain_value(item)
            for key, item in asdict(value).items()
        }
    for method_name in ("model_dump", "dict"):
        method = getattr(value, method_name, None)
        if not callable(method):
            continue
        try:
            dumped = method()
        except TypeError:
            try:
                dumped = method(mode="json")
            except TypeError:
                continue
        if isinstance(dumped, Mapping):
            return {
                str(key): _plain_value(item)
                for key, item in dumped.items()
            }
    return None


def _plain_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Mapping):
        return {
            str(key): _plain_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_plain_value(item) for item in value]
    mapping = _object_mapping(value)
    if mapping is not None:
        return mapping
    return value
