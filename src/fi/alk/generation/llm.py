"""LLM client boundary: a two-method protocol, a litellm implementation, an offline fake.

The pipeline only ever sees ``LLMClient``. The default implementation routes through litellm the
same way ``fi.simulate.suite`` does: ``vertex_ai/<model>`` reaches Vertex AI with credentials from
``GOOGLE_APPLICATION_CREDENTIALS`` (or an explicit ``vertex_credentials`` path); any other
fully-qualified litellm model string works unchanged. Spend is metered per call against a hard USD
ceiling so an unattended run can never overshoot its budget.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_MODEL = os.environ.get("ALK_GENERATION_MODEL", "vertex_ai/gemini-2.5-flash")

# USD per token, overridable per client. Defaults are Gemini 2.5 Flash list prices.
DEFAULT_INPUT_COST_PER_TOKEN = 0.30 / 1_000_000
DEFAULT_OUTPUT_COST_PER_TOKEN = 2.50 / 1_000_000

_AUTH_MARKERS = (
    "401",
    "403",
    "unauthorized",
    "unauthenticated",
    "permission",
    "credential",
)


class BudgetExceeded(RuntimeError):
    """Raised before a call that would push spend past the configured ceiling."""


class AuthFailed(RuntimeError):
    """Raised on provider auth errors; retrying these only burns time."""


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usd: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "usd": round(self.usd, 4),
        }


class LLMClient(Protocol):
    """What the pipeline needs from a model. Implementations own transport and retries."""

    def complete_json(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.3,
        max_tokens: int = 8000,
    ) -> Any: ...

    def complete_turn(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 16_000,
    ) -> dict[str, Any]:
        """One chat turn. Returns ``{"content": str | None, "tool_calls": [{"id", "name",
        "arguments": dict}, ...]}`` so a harness can run a bounded tool loop."""
        ...

    @property
    def usage(self) -> Usage: ...


def _extract_json(text: str) -> Any:
    """Parse the first JSON object or array in ``text``.

    Tolerates code fences (including an unterminated fence when the output was cut off) and repairs
    truncation by dropping the incomplete tail and closing the open brackets. Truncated model output
    is a routine failure mode, not an exception, so parsing must degrade gracefully before the
    caller decides to retry.
    """
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)(?:```|$)", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start < 0:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == opener:
                depth += 1
            elif text[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    repaired = _repair_truncated(text)
    if repaired is not None:
        return repaired
    raise ValueError(
        f"model returned no parseable JSON (first 200 chars: {text[:200]!r})"
    )


def _repair_truncated(text: str) -> Any | None:
    """Best-effort parse of JSON that was cut off mid-stream.

    Walks the text tracking string and bracket state, discards the incomplete trailing element at
    each failure, and closes whatever remains open. Returns None when nothing parseable survives.
    """
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        return None
    stack: list[str] = []
    in_string = False
    escaped = False
    last_complete = start
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif char in "}]":
            if stack:
                stack.pop()
            last_complete = index
        elif char == ",":
            last_complete = index
    # Retry from progressively earlier cut points: full text, then the last complete element.
    for cut in (len(text), last_complete):
        candidate = text[start:cut].rstrip().rstrip(",")
        # Recompute the open stack for this candidate.
        open_stack: list[str] = []
        in_str = False
        esc = False
        for char in candidate:
            if in_str:
                if esc:
                    esc = False
                elif char == "\\":
                    esc = True
                elif char == '"':
                    in_str = False
                continue
            if char == '"':
                in_str = True
            elif char in "{[":
                open_stack.append("}" if char == "{" else "]")
            elif char in "}]" and open_stack:
                open_stack.pop()
        if in_str:
            candidate += '"'
        candidate += "".join(reversed(open_stack))
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


@dataclass
class LiteLLMClient:
    """litellm-backed client with per-call cost metering and a hard budget ceiling."""

    model: str = DEFAULT_MODEL
    budget_usd: float = 2.0
    vertex_location: str = os.environ.get("VERTEX_LOCATION", "global")
    vertex_credentials: str | None = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    input_cost_per_token: float = DEFAULT_INPUT_COST_PER_TOKEN
    output_cost_per_token: float = DEFAULT_OUTPUT_COST_PER_TOKEN
    max_attempts: int = 4
    _usage: Usage = field(default_factory=Usage)

    @property
    def usage(self) -> Usage:
        return self._usage

    def complete_json(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.3,
        max_tokens: int = 20_000,
    ) -> Any:
        """Chat completion parsed as JSON, retrying once on a cut-off or malformed reply.

        Gemini-family models spend part of ``max_tokens`` on internal reasoning, so a reply can
        arrive truncated even when the visible JSON would have fit. The retry names the problem to
        the model and raises the output budget.
        """
        self._check_budget()
        text = self._chat(system, user, temperature=temperature, max_tokens=max_tokens)
        try:
            return _extract_json(text)
        except ValueError:
            self._check_budget()
            retry_user = (
                user
                + "\n\nYour previous reply was cut off or was not valid JSON. Return ONLY the "
                "complete JSON, with no code fences and no prose."
            )
            text = self._chat(
                system,
                retry_user,
                temperature=temperature,
                max_tokens=min(max_tokens * 2, 50_000),
            )
            return _extract_json(text)

    def complete_turn(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 16_000,
    ) -> dict[str, Any]:
        self._check_budget()
        try:
            import litellm
        except Exception as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "fi.alk.generation requires litellm; reinstall agent-learning-kit"
            ) from exc
        litellm.drop_params = True
        kwargs = self._provider_kwargs()
        kwargs.update({"temperature": temperature, "max_tokens": max_tokens})
        if tools:
            kwargs["tools"] = tools
        last: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                response = litellm.completion(
                    model=self.model, messages=messages, **kwargs
                )
                self._meter(response)
                message = response.choices[0].message
                calls = []
                for call in getattr(message, "tool_calls", None) or []:
                    function = getattr(call, "function", None)
                    try:
                        arguments = json.loads(
                            getattr(function, "arguments", "") or "{}"
                        )
                    except json.JSONDecodeError:
                        arguments = {}
                    calls.append(
                        {
                            "id": getattr(call, "id", ""),
                            "name": getattr(function, "name", ""),
                            "arguments": arguments,
                        }
                    )
                return {"content": message.content, "tool_calls": calls, "raw": message}
            except Exception as exc:  # noqa: BLE001 - classified below
                text = str(exc).lower()
                if any(marker in text for marker in _AUTH_MARKERS):
                    raise AuthFailed(
                        f"provider auth failed for {self.model}: {exc}"
                    ) from exc
                last = exc
                time.sleep(min(2**attempt, 8))
        raise RuntimeError(
            f"model call failed after {self.max_attempts} attempts: {last}"
        )

    def _check_budget(self) -> None:
        if self._usage.usd >= self.budget_usd:
            raise BudgetExceeded(
                f"spend {self._usage.usd:.2f} USD reached the {self.budget_usd:.2f} USD ceiling"
            )

    def _provider_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self.model.startswith("vertex_ai/"):
            kwargs["vertex_location"] = self.vertex_location
            if self.vertex_credentials:
                kwargs["vertex_credentials"] = self.vertex_credentials
                try:
                    with open(self.vertex_credentials, encoding="utf-8") as fh:
                        kwargs["vertex_project"] = json.load(fh).get("project_id")
                except OSError:
                    pass
        return kwargs

    def _chat(
        self, system: str, user: str, *, temperature: float, max_tokens: int
    ) -> str:
        try:
            import litellm
        except Exception as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "fi.alk.generation requires litellm; reinstall agent-learning-kit"
            ) from exc

        litellm.drop_params = True
        kwargs = self._provider_kwargs()
        kwargs.update({"temperature": temperature, "max_tokens": max_tokens})
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                response = litellm.completion(
                    model=self.model, messages=messages, **kwargs
                )
                self._meter(response)
                content = response.choices[0].message.content
                if not content or not str(content).strip():
                    raise ValueError("model returned empty content")
                return str(content)
            except Exception as exc:  # noqa: BLE001 - classified below
                message = str(exc).lower()
                if any(marker in message for marker in _AUTH_MARKERS):
                    raise AuthFailed(
                        f"provider auth failed for {self.model}: {exc}"
                    ) from exc
                last = exc
                time.sleep(min(2**attempt, 8))
        raise RuntimeError(
            f"model call failed after {self.max_attempts} attempts: {last}"
        )

    def _meter(self, response: Any) -> None:
        self._usage.calls += 1
        usage = getattr(response, "usage", None)
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        self._usage.prompt_tokens += prompt
        self._usage.completion_tokens += completion
        self._usage.usd += (
            prompt * self.input_cost_per_token + completion * self.output_cost_per_token
        )


@dataclass
class FakeLLMClient:
    """Deterministic offline client for tests: pops queued responses in order."""

    responses: list[Any] = field(default_factory=list)
    _usage: Usage = field(default_factory=Usage)

    @property
    def usage(self) -> Usage:
        return self._usage

    def complete_json(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.3,
        max_tokens: int = 8000,
    ) -> Any:
        if not self.responses:
            raise AssertionError("FakeLLMClient exhausted; queue more responses")
        self._usage.calls += 1
        return self.responses.pop(0)

    def complete_turn(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 16_000,
    ) -> dict[str, Any]:
        if not self.responses:
            raise AssertionError("FakeLLMClient exhausted; queue more responses")
        self._usage.calls += 1
        turn = self.responses.pop(0)
        if isinstance(turn, dict) and ("tool_calls" in turn or "content" in turn):
            return {
                "content": turn.get("content"),
                "tool_calls": turn.get("tool_calls", []),
            }
        return {"content": json.dumps(turn), "tool_calls": []}
