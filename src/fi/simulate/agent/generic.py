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
                    return _stringify(raw_mapping[key])
            if "message" in raw_mapping:
                return _message_content(raw_mapping["message"])
            if "messages" in raw_mapping:
                return _last_message_content(raw_mapping["messages"])
            if "choices" in raw_mapping:
                return _choices_content(raw_mapping["choices"])

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

        return str(raw)

    def _extract_tool_calls(self, raw: Any) -> Optional[List[Dict[str, Any]]]:
        return _extract_list_field(
            raw,
            ("tool_calls", "toolCalls", "tool_call_chunks", "toolCallChunks"),
        )

    def _extract_tool_responses(self, raw: Any) -> Optional[List[Dict[str, Any]]]:
        return _extract_list_field(raw, ("tool_responses", "toolResponses", "tool_outputs", "toolOutputs"))

    def _extract_metadata(self, raw: Any) -> Dict[str, Any]:
        raw_mapping = _object_mapping(raw)
        if raw_mapping is not None:
            value = raw_mapping.get("metadata")
            return dict(value) if isinstance(value, dict) else {}
        value = getattr(raw, "metadata", None)
        return dict(value) if isinstance(value, dict) else {}

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
        return state

    def _extract_artifacts(self, raw: Any) -> List[SimulationArtifact]:
        values = _extract_list_field(raw, ("artifacts", "media", "attachments"))
        artifacts: List[SimulationArtifact] = []
        for value in values or []:
            try:
                artifacts.append(SimulationArtifact(**value))
            except Exception:
                continue
        return artifacts

    def _extract_events(self, raw: Any) -> List[SimulationEvent]:
        values = _extract_list_field(raw, ("events", "trajectory", "spans"))
        events: List[SimulationEvent] = []
        for value in values or []:
            try:
                events.append(SimulationEvent(**value))
            except Exception:
                continue
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
