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

_AUTH_MARKERS = ("401", "403", "unauthorized", "unauthenticated", "permission", "credential")


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
        self, system: str, user: str, *, temperature: float = 0.3, max_tokens: int = 8000
    ) -> Any: ...

    def complete_turn(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 8000,
    ) -> dict[str, Any]:
        """One chat turn. Returns ``{"content": str | None, "tool_calls": [{"id", "name",
        "arguments": dict}, ...]}`` so a harness can run a bounded tool loop."""
        ...

    @property
    def usage(self) -> Usage: ...


def _extract_json(text: str) -> Any:
    """Parse the first JSON object or array in ``text``, tolerating code fences."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
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
    raise ValueError(f"model returned no parseable JSON (first 200 chars: {text[:200]!r})")


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
        self, system: str, user: str, *, temperature: float = 0.3, max_tokens: int = 8000
    ) -> Any:
        self._check_budget()
        text = self._chat(system, user, temperature=temperature, max_tokens=max_tokens)
        return _extract_json(text)

    def complete_turn(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 8000,
    ) -> dict[str, Any]:
        self._check_budget()
        try:
            import litellm
        except Exception as exc:  # pragma: no cover - import guard
            raise RuntimeError("fi.alk.generation requires litellm; reinstall agent-learning-kit") from exc
        litellm.drop_params = True
        kwargs = self._provider_kwargs()
        kwargs.update({"temperature": temperature, "max_tokens": max_tokens})
        if tools:
            kwargs["tools"] = tools
        last: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                response = litellm.completion(model=self.model, messages=messages, **kwargs)
                self._meter(response)
                message = response.choices[0].message
                calls = []
                for call in getattr(message, "tool_calls", None) or []:
                    function = getattr(call, "function", None)
                    try:
                        arguments = json.loads(getattr(function, "arguments", "") or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    calls.append(
                        {"id": getattr(call, "id", ""), "name": getattr(function, "name", ""),
                         "arguments": arguments}
                    )
                return {"content": message.content, "tool_calls": calls, "raw": message}
            except Exception as exc:  # noqa: BLE001 - classified below
                text = str(exc).lower()
                if any(marker in text for marker in _AUTH_MARKERS):
                    raise AuthFailed(f"provider auth failed for {self.model}: {exc}") from exc
                last = exc
                time.sleep(min(2**attempt, 8))
        raise RuntimeError(f"model call failed after {self.max_attempts} attempts: {last}")

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

    def _chat(self, system: str, user: str, *, temperature: float, max_tokens: int) -> str:
        try:
            import litellm
        except Exception as exc:  # pragma: no cover - import guard
            raise RuntimeError("fi.alk.generation requires litellm; reinstall agent-learning-kit") from exc

        litellm.drop_params = True
        kwargs = self._provider_kwargs()
        kwargs.update({"temperature": temperature, "max_tokens": max_tokens})
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                response = litellm.completion(model=self.model, messages=messages, **kwargs)
                self._meter(response)
                content = response.choices[0].message.content
                if not content or not str(content).strip():
                    raise ValueError("model returned empty content")
                return str(content)
            except Exception as exc:  # noqa: BLE001 - classified below
                message = str(exc).lower()
                if any(marker in message for marker in _AUTH_MARKERS):
                    raise AuthFailed(f"provider auth failed for {self.model}: {exc}") from exc
                last = exc
                time.sleep(min(2**attempt, 8))
        raise RuntimeError(f"model call failed after {self.max_attempts} attempts: {last}")

    def _meter(self, response: Any) -> None:
        self._usage.calls += 1
        usage = getattr(response, "usage", None)
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        self._usage.prompt_tokens += prompt
        self._usage.completion_tokens += completion
        self._usage.usd += prompt * self.input_cost_per_token + completion * self.output_cost_per_token


@dataclass
class FakeLLMClient:
    """Deterministic offline client for tests: pops queued responses in order."""

    responses: list[Any] = field(default_factory=list)
    _usage: Usage = field(default_factory=Usage)

    @property
    def usage(self) -> Usage:
        return self._usage

    def complete_json(
        self, system: str, user: str, *, temperature: float = 0.3, max_tokens: int = 8000
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
        max_tokens: int = 8000,
    ) -> dict[str, Any]:
        if not self.responses:
            raise AssertionError("FakeLLMClient exhausted; queue more responses")
        self._usage.calls += 1
        turn = self.responses.pop(0)
        if isinstance(turn, dict) and ("tool_calls" in turn or "content" in turn):
            return {"content": turn.get("content"), "tool_calls": turn.get("tool_calls", [])}
        return {"content": json.dumps(turn), "tool_calls": []}
