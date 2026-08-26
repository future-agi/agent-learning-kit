"""A harness backend that runs stages on Gemini through Vertex AI.

This backend owns its loop: it declares the stage's tools as Gemini function declarations,
feeds every tool result back, and stops when the model stops calling or the turn budget runs
out. Gating is structural rather than hooked: a tool that was not granted is never declared,
so the model cannot spend a turn discovering a denial.

The Gemini 3.x models this exists for are served from the ``global`` endpoint only, which is
why ``ALK_VERTEX_LOCATION`` defaults to ``global`` rather than to a region. Regional Vertex
deployments of older models can point it elsewhere.

Read, Glob and Grep come from files.py when a stage grants them. AskUserQuestion is not
implemented here yet: unattended runs never use it, and an attended run on this backend simply
proceeds without the option, which is said out loud in the session rather than hidden.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, AsyncIterator

from .base import (
    FILE_TOOLS,
    Call,
    ModelReply,
    Say,
    SessionOpened,
    SessionSpec,
    StageDone,
    ToolHandler,
    ToolReturned,
    ToolSpec,
    qualified,
)
from .files import file_tools

DEFAULT_MODEL = "gemini-3.7-flash"

# Vertex list pricing per 1M tokens (input, output), as of 2026-08; verify before relying on
# cost figures. An unknown model reports no cost rather than a wrong one.
PRICES_PER_MILLION = {
    "gemini-3.7-flash": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
}

_PYTHON_TYPES = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}

# The keys Vertex's Schema type accepts. JSON Schema carries more; anything else is dropped
# rather than passed through, because an unknown key fails declaration validation and takes
# the whole stage down before its first turn.
_SCHEMA_KEYS = (
    "description",
    "enum",
    "format",
    "items",
    "maximum",
    "maxItems",
    "minimum",
    "minItems",
    "nullable",
    "pattern",
    "properties",
    "required",
    "type",
    "anyOf",
)


def _gemini_schema(schema: Any) -> dict[str, Any]:
    """A JSON Schema fragment as Vertex's Schema dialect.

    The real difference is nullability: JSON Schema says ``"type": ["string", "null"]``,
    Vertex says ``"type": "string", "nullable": true``. Everything Vertex does not know is
    dropped, recursively, so a tool schema written for the loosest backend still declares.
    """
    if not isinstance(schema, dict):
        return {"type": "string"}
    cleaned: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _SCHEMA_KEYS:
            continue
        if key == "type" and isinstance(value, list):
            bare = [entry for entry in value if entry != "null"]
            cleaned["type"] = bare[0] if bare else "string"
            if "null" in value:
                cleaned["nullable"] = True
        elif key == "properties" and isinstance(value, dict):
            cleaned["properties"] = {
                name: _gemini_schema(inner) for name, inner in value.items()
            }
        elif key == "items":
            cleaned["items"] = _gemini_schema(value)
        elif key == "anyOf" and isinstance(value, list):
            cleaned["anyOf"] = [_gemini_schema(inner) for inner in value]
        elif key == "enum" and isinstance(value, list):
            # Vertex enums are strings; None inside one is JSON Schema's way of saying
            # nullable, and everything else is stringified the way the model will echo it.
            cleaned["enum"] = [str(entry) for entry in value if entry is not None]
            if None in value:
                cleaned["nullable"] = True
        else:
            cleaned[key] = value
    # Vertex refuses an array that does not say what it holds. JSON Schema treats items as
    # optional, and most such arrays here carry row-shaped dicts, so an open object is the
    # faithful default.
    if cleaned.get("type") == "array" and "items" not in cleaned:
        cleaned["items"] = {"type": "object"}
    return cleaned


def _json_schema(schema: Any) -> dict[str, Any]:
    """The tool's schema as Vertex-safe schema, whichever shorthand it was declared in."""
    if isinstance(schema, dict) and (
        "properties" in schema or schema.get("type") == "object"
    ):
        return _gemini_schema(schema)
    if isinstance(schema, dict):
        return {
            "type": "object",
            "properties": {
                name: (
                    {"type": "array", "items": {"type": "object"}}
                    if kind is list
                    else {"type": _PYTHON_TYPES.get(kind, "string")}
                )
                for name, kind in schema.items()
            },
            "required": list(schema),
        }
    return {"type": "object", "properties": {}}


def _project() -> str:
    named = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if named:
        return named
    credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if credentials:
        try:
            with open(credentials, encoding="utf-8") as handle:
                found = json.load(handle).get("project_id", "")
            if found:
                return found
        except (OSError, ValueError):
            pass
    raise RuntimeError(
        "no GCP project named; set GOOGLE_CLOUD_PROJECT or point "
        "GOOGLE_APPLICATION_CREDENTIALS at a service-account file"
    )


def _location() -> str:
    return os.environ.get("ALK_VERTEX_LOCATION", "global").strip() or "global"


def _flattened(result: dict[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return content if isinstance(content, str) else str(content)


class VertexGeminiSession:
    """One conversation with Gemini, holding its own history across turns."""

    def __init__(self, spec: SessionSpec, model: str) -> None:
        self._spec = spec
        self._model = model
        self._client: Any = None
        self._history: list[Any] = []
        self._handlers: dict[str, ToolHandler] = {}
        self._declarations: list[Any] = []
        self._pending: str | None = None
        self.session_id = f"gemini-{uuid.uuid4().hex[:12]}"

    def _tools(self) -> list[ToolSpec]:
        # ASK_TOOL is deliberately absent: unattended runs never call it, and declaring a tool
        # this backend cannot answer would cost the model a turn finding that out.
        wanted = {name for name in self._spec.builtins if name in FILE_TOOLS}
        if not wanted:
            return []
        return [spec for spec in file_tools(self._spec.cwd) if spec.name in wanted]

    async def start(self) -> None:
        from google import genai
        from google.genai import types

        self._client = genai.Client(
            vertexai=True, project=_project(), location=_location()
        )
        declarations = []
        for spec in self._tools():
            declarations.append(
                types.FunctionDeclaration(
                    name=spec.name,
                    description=spec.description,
                    parameters=_json_schema(spec.input_schema),
                )
            )
            self._handlers[spec.name] = spec.handler
        for server_name, server in self._spec.servers.items():
            for spec in server.tools:
                name = qualified(server_name, spec.name)
                declarations.append(
                    types.FunctionDeclaration(
                        name=name,
                        description=spec.description,
                        parameters=_json_schema(spec.input_schema),
                    )
                )
                self._handlers[name] = spec.handler
        self._declarations = declarations

    async def stop(self) -> None:
        self._client = None

    async def send(self, message: str) -> None:
        if self._client is None:
            raise RuntimeError("session is not open")
        self._pending = message

    async def replies(self) -> AsyncIterator[Any]:
        from google.genai import types

        if self._client is None or self._pending is None:
            raise RuntimeError("nothing to reply to; send a message first")
        yield SessionOpened(session_id=self.session_id)
        self._history.append(
            types.Content(role="user", parts=[types.Part(text=self._pending)])
        )
        self._pending = None
        config = types.GenerateContentConfig(
            system_instruction=self._spec.system_prompt,
            tools=(
                [types.Tool(function_declarations=self._declarations)]
                if self._declarations
                else None
            ),
        )
        turns = 0
        tokens_in = 0
        tokens_out = 0
        models: set[str] = set()
        while turns < max(self._spec.max_turns, 1):
            turns += 1
            try:
                response = await self._client.aio.models.generate_content(
                    model=self._model, contents=self._history, config=config
                )
            except Exception as exc:
                yield StageDone(
                    outcome="failed",
                    turns=turns,
                    cost_usd=self._cost(tokens_in, tokens_out),
                    session_id=self.session_id,
                    models=models or {self._model},
                    is_error=True,
                    api_error_status=getattr(exc, "code", None),
                    errors=[str(exc)[:400]],
                )
                return
            usage = getattr(response, "usage_metadata", None)
            if usage is not None:
                tokens_in += usage.prompt_token_count or 0
                tokens_out += usage.candidates_token_count or 0
            models.add(getattr(response, "model_version", None) or self._model)
            candidate = (response.candidates or [None])[0]
            if candidate is None or candidate.content is None:
                yield StageDone(
                    outcome="failed",
                    turns=turns,
                    cost_usd=self._cost(tokens_in, tokens_out),
                    session_id=self.session_id,
                    models=models,
                    is_error=True,
                    errors=[
                        f"the model returned no content (finish reason: "
                        f"{getattr(candidate, 'finish_reason', 'unknown')})"
                    ],
                )
                return
            parts: list[Any] = []
            calls: list[Any] = []
            for part in candidate.content.parts or []:
                if getattr(part, "text", None):
                    parts.append(Say(text=part.text))
                if getattr(part, "function_call", None):
                    identity = (
                        getattr(part.function_call, "id", None)
                        or f"call-{uuid.uuid4().hex[:8]}"
                    )
                    call = Call(
                        id=identity,
                        name=part.function_call.name or "",
                        arguments=dict(part.function_call.args or {}),
                    )
                    parts.append(call)
                    calls.append(call)
            yield ModelReply(parts=parts, model=self._model)
            self._history.append(candidate.content)
            if not calls:
                break
            responses = []
            for call in calls:
                outcome = await self._execute(call)
                yield outcome
                responses.append(
                    types.Part.from_function_response(
                        name=call.name, response={"result": outcome.text}
                    )
                )
            self._history.append(types.Content(role="user", parts=responses))
        yield StageDone(
            outcome="success",
            turns=turns,
            cost_usd=self._cost(tokens_in, tokens_out),
            session_id=self.session_id,
            models=models,
        )

    async def _execute(self, call: Call) -> ToolReturned:
        handler = self._handlers.get(call.name)
        if handler is None:
            return ToolReturned(
                id=call.id,
                text=(
                    f"{call.name} is not part of this stage. You have "
                    f"{', '.join(sorted(self._handlers)) or 'no other tools'}, and everything "
                    "you produce goes through those, because those are what check it."
                ),
                is_error=True,
            )
        try:
            result = await handler(call.arguments)
        except Exception as exc:
            return ToolReturned(
                id=call.id, text=f"{call.name} crashed: {exc}", is_error=True
            )
        return ToolReturned(
            id=call.id,
            text=_flattened(result if isinstance(result, dict) else {}),
            is_error=bool(isinstance(result, dict) and result.get("is_error")),
        )

    def _cost(self, tokens_in: int, tokens_out: int) -> float | None:
        prices = PRICES_PER_MILLION.get(self._model)
        if prices is None:
            return None
        return (tokens_in * prices[0] + tokens_out * prices[1]) / 1_000_000


class VertexGeminiBackend:
    name = "vertex-gemini"
    default_model = DEFAULT_MODEL

    def can_drive(self, model: str) -> bool:
        return (model or "").lower().startswith("gemini")

    def create(self, spec: SessionSpec) -> VertexGeminiSession:
        return VertexGeminiSession(spec, model=spec.model or self.default_model)
