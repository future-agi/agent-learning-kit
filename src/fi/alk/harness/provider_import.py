"""Built-in, ownership-safe import adapters for provider-hosted voice targets.

The adapter copies configuration; it never fabricates tool implementations. Custom HTTP tools are
rewired to an already-running world endpoint. Provider-native tools are preserved. Every created
resource is returned in a validated lifecycle receipt so cleanup is exact and retryable.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field
from pydantic import model_validator

from .provider_lifecycle import (
    ProviderCleanupReceipt,
    ProviderContext,
    ProviderProvisionReceipt,
    ProviderResource,
    ProviderTarget,
    ProviderType,
)


class ProviderImportError(RuntimeError):
    """The provider definition could not be copied without guessing."""


class ProviderImportSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ProviderType
    source_target_id: str = Field(min_length=1)
    public_capability: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    event_path: str = "/provider/events"
    tool_path: str = "/provider/tools"
    api_base_url: str | None = None

    @model_validator(mode="after")
    def _provider_api_origin_is_fixed(self) -> "ProviderImportSpec":
        if self.api_base_url:
            expected = {
                ProviderType.VAPI: "https://api.vapi.ai",
                ProviderType.RETELL: "https://api.retellai.com",
            }[self.type]
            if self.api_base_url.rstrip("/") != expected:
                raise ValueError("provider_import_api_base_url_not_allowed")
        for name, value in (
            ("event_path", self.event_path),
            ("tool_path", self.tool_path),
        ):
            if (
                not value.startswith("/")
                or value.startswith("//")
                or ".." in value.split("/")
            ):
                raise ValueError(f"provider_import_{name}_invalid")
        return self


JsonRequest = Callable[[str, str, str, Mapping[str, Any] | None], dict[str, Any]]


def _request_json(
    method: str, url: str, api_key: str, body: Mapping[str, Any] | None
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed provider hosts
            raw = response.read()
    except HTTPError as exc:
        raise ProviderImportError(
            f"provider_api_error: {method} returned HTTP {exc.code}"
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise ProviderImportError(
            f"provider_api_unavailable: {method} {type(exc).__name__}"
        ) from exc
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ProviderImportError("provider_api_response_invalid_json") from exc
    if not isinstance(value, dict):
        raise ProviderImportError("provider_api_response_must_be_object")
    return value


def _without(body: Mapping[str, Any], names: set[str]) -> dict[str, Any]:
    return {key: value for key, value in body.items() if key not in names}


def _join(base: str, original: Any) -> str:
    """Preserve a declared route while moving only its origin into the world."""
    from urllib.parse import urlsplit

    path = urlsplit(str(original or "")).path
    if not path or path == "/":
        return base
    return base.rstrip("/") + "/" + path.lstrip("/")


def _rewire_vapi_tool(tool: Mapping[str, Any], tool_base_url: str) -> dict[str, Any]:
    copied = dict(tool)
    kind = str(copied.get("type") or "")
    if kind == "apiRequest" and copied.get("url"):
        copied["url"] = _join(tool_base_url, copied["url"])
    elif kind == "function":
        server = copied.get("server")
        if isinstance(server, Mapping) and server.get("url"):
            copied["server"] = {**server, "url": _join(tool_base_url, server["url"])}
    return copied


def _rewire_retell_tools(value: Any, tool_base_url: str) -> Any:
    if isinstance(value, list):
        return [_rewire_retell_tools(item, tool_base_url) for item in value]
    if not isinstance(value, Mapping):
        return value
    copied = {
        key: _rewire_retell_tools(item, tool_base_url) for key, item in value.items()
    }
    if str(copied.get("type") or "") == "custom" and copied.get("url"):
        copied["url"] = _join(tool_base_url, copied["url"])
    return copied


@dataclass(frozen=True)
class _CloneResult:
    target_id: str
    target_kind: str
    resources: tuple[ProviderResource, ...]
    metadata: dict[str, Any]


def _clone_vapi(
    spec: ProviderImportSpec,
    context: ProviderContext,
    api_key: str,
    request: JsonRequest,
) -> _CloneResult:
    base = (spec.api_base_url or "https://api.vapi.ai").rstrip("/")
    original = request(
        "GET", f"{base}/assistant/{spec.source_target_id}", api_key, None
    )
    assistant = _without(original, {"id", "orgId", "createdAt", "updatedAt"})
    assistant["name"] = context.provider_resource_prefix[:40]
    assistant["metadata"] = {
        **(
            assistant.get("metadata")
            if isinstance(assistant.get("metadata"), dict)
            else {}
        ),
        "alk_attempt_id": context.attempt_id,
        "alk_world_id": context.world_id,
    }
    assistant["server"] = {"url": context.event_url}
    resources: list[ProviderResource] = []
    try:
        model = assistant.get("model")
        if isinstance(model, dict):
            model = dict(model)
            inline = model.get("tools")
            if isinstance(inline, list):
                model["tools"] = [
                    _rewire_vapi_tool(item, context.tool_base_url)
                    if isinstance(item, Mapping)
                    else item
                    for item in inline
                ]
            tool_ids = model.get("toolIds")
            if isinstance(tool_ids, list):
                cloned_ids: list[str] = []
                for source_id in tool_ids:
                    source = request("GET", f"{base}/tool/{source_id}", api_key, None)
                    create = _rewire_vapi_tool(
                        _without(source, {"id", "orgId", "createdAt", "updatedAt"}),
                        context.tool_base_url,
                    )
                    created = request("POST", f"{base}/tool", api_key, create)
                    target_id = str(created.get("id") or "").strip()
                    if not target_id:
                        raise ProviderImportError("vapi_tool_create_missing_id")
                    cloned_ids.append(target_id)
                    resources.append(
                        ProviderResource(kind="tool", id=target_id, owned=True)
                    )
                model["toolIds"] = cloned_ids
            assistant["model"] = model
        created = request("POST", f"{base}/assistant", api_key, assistant)
    except ProviderImportError:
        for resource in reversed(resources):
            try:
                request("DELETE", f"{base}/tool/{resource.id}", api_key, None)
            except ProviderImportError:
                pass
        raise
    target_id = str(created.get("id") or "").strip()
    if not target_id:
        for resource in reversed(resources):
            try:
                request("DELETE", f"{base}/tool/{resource.id}", api_key, None)
            except ProviderImportError:
                pass
        raise ProviderImportError("vapi_assistant_create_missing_id")
    resources.append(ProviderResource(kind="assistant", id=target_id, owned=True))
    return _CloneResult(
        target_id=target_id,
        target_kind="assistant",
        resources=tuple(resources),
        metadata={
            "source_target_id": spec.source_target_id,
            "clone_kind": "provider_import",
        },
    )


def _clone_retell(
    spec: ProviderImportSpec,
    context: ProviderContext,
    api_key: str,
    request: JsonRequest,
) -> _CloneResult:
    base = (spec.api_base_url or "https://api.retellai.com").rstrip("/")
    original = request(
        "GET", f"{base}/get-agent/{spec.source_target_id}", api_key, None
    )
    engine = original.get("response_engine")
    if not isinstance(engine, Mapping) or engine.get("type") != "retell-llm":
        raise ProviderImportError(
            "retell_response_engine_unsupported: provider_import currently requires retell-llm"
        )
    source_llm_id = str(engine.get("llm_id") or "").strip()
    if not source_llm_id:
        raise ProviderImportError("retell_response_engine_missing_llm_id")
    source_llm = request("GET", f"{base}/get-retell-llm/{source_llm_id}", api_key, None)
    llm_create = _rewire_retell_tools(
        _without(
            source_llm,
            {"llm_id", "last_modification_timestamp", "version", "is_published"},
        ),
        context.tool_base_url,
    )
    created_llm = request("POST", f"{base}/create-retell-llm", api_key, llm_create)
    llm_id = str(created_llm.get("llm_id") or "").strip()
    if not llm_id:
        raise ProviderImportError("retell_llm_create_missing_id")
    agent_create = _without(
        original,
        {
            "agent_id",
            "last_modification_timestamp",
            "version",
            "base_version",
            "is_published",
        },
    )
    agent_create["agent_name"] = context.provider_resource_prefix
    agent_create["response_engine"] = {"type": "retell-llm", "llm_id": llm_id}
    agent_create["webhook_url"] = context.event_url
    try:
        created_agent = request("POST", f"{base}/create-agent", api_key, agent_create)
    except ProviderImportError:
        try:
            request("DELETE", f"{base}/delete-retell-llm/{llm_id}", api_key, None)
        except ProviderImportError:
            pass
        raise
    target_id = str(created_agent.get("agent_id") or "").strip()
    if not target_id:
        try:
            request("DELETE", f"{base}/delete-retell-llm/{llm_id}", api_key, None)
        except ProviderImportError:
            pass
        raise ProviderImportError("retell_agent_create_missing_id")
    return _CloneResult(
        target_id=target_id,
        target_kind="voice_agent",
        resources=(
            ProviderResource(kind="retell_llm", id=llm_id, owned=True),
            ProviderResource(kind="voice_agent", id=target_id, owned=True),
        ),
        metadata={
            "source_target_id": spec.source_target_id,
            "source_response_engine_id": source_llm_id,
            "clone_kind": "provider_import",
        },
    )


def clone_provider_target(
    spec: ProviderImportSpec,
    *,
    context: ProviderContext,
    api_key: str,
    request: JsonRequest = _request_json,
) -> ProviderProvisionReceipt:
    if not api_key:
        raise ProviderImportError(f"{spec.type.value}_api_key_missing")
    result = (
        _clone_vapi(spec, context, api_key, request)
        if spec.type is ProviderType.VAPI
        else _clone_retell(spec, context, api_key, request)
    )
    return ProviderProvisionReceipt(
        schema_version="1",
        provider=spec.type,
        attempt_id=context.attempt_id,
        world_id=context.world_id,
        target=ProviderTarget(kind=result.target_kind, id=result.target_id),
        resources=list(result.resources),
        cleanup=ProviderCleanupReceipt(idempotency_key=context.idempotency_key),
        metadata=result.metadata,
    )


def destroy_imported_target(
    spec: ProviderImportSpec,
    *,
    receipt: ProviderProvisionReceipt,
    api_key: str,
    request: JsonRequest = _request_json,
) -> None:
    base = (
        spec.api_base_url
        or (
            "https://api.vapi.ai"
            if spec.type is ProviderType.VAPI
            else "https://api.retellai.com"
        )
    ).rstrip("/")
    # Reverse dependency order: target first, then tools/response engine.
    for resource in reversed(receipt.resources):
        if not resource.owned:
            continue
        if spec.type is ProviderType.VAPI:
            path = "assistant" if resource.kind == "assistant" else "tool"
            try:
                request("DELETE", f"{base}/{path}/{resource.id}", api_key, None)
            except ProviderImportError as exc:
                if "HTTP 404" not in str(exc):
                    raise
        elif resource.kind == "voice_agent":
            try:
                request("DELETE", f"{base}/delete-agent/{resource.id}", api_key, None)
            except ProviderImportError as exc:
                if "HTTP 404" not in str(exc):
                    raise
        elif resource.kind == "retell_llm":
            try:
                request(
                    "DELETE", f"{base}/delete-retell-llm/{resource.id}", api_key, None
                )
            except ProviderImportError as exc:
                if "HTTP 404" not in str(exc):
                    raise


__all__ = [
    "ProviderImportError",
    "ProviderImportSpec",
    "clone_provider_target",
    "destroy_imported_target",
]
