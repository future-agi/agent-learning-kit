from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fi.alk.harness.provider_import import (
    ProviderImportError,
    ProviderImportSpec,
    clone_provider_target,
    destroy_imported_target,
)
from fi.alk.harness.provider_lifecycle import ProviderContext


def _context(provider: str) -> ProviderContext:
    return ProviderContext(
        attempt_id="attempt-1",
        world_id="0",
        provider=provider,
        public_base_url="https://world.example",
        event_url="https://world.example/provider/events",
        tool_base_url="https://world.example/provider/tools",
        provider_resource_prefix="alk-attempt-1-w0",
        idempotency_key=f"attempt-1:0:{provider}",
        expires_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_vapi_import_clones_tools_rewires_urls_and_cleans_up() -> None:
    calls: list[tuple[str, str, object]] = []

    def request(method, url, api_key, body):
        assert api_key == "vapi-secret"
        calls.append((method, url, body))
        if method == "GET" and "/assistant/" in url:
            return {
                "id": "source-assistant",
                "orgId": "org",
                "name": "Production",
                "server": {"url": "https://prod.example/events"},
                "model": {
                    "provider": "openai",
                    "toolIds": ["source-tool"],
                    "tools": [
                        {
                            "type": "function",
                            "function": {"name": "lookup"},
                            "server": {"url": "https://prod.example/tools/lookup"},
                        }
                    ],
                },
            }
        if method == "GET" and "/tool/" in url:
            return {
                "id": "source-tool",
                "type": "apiRequest",
                "name": "lookup",
                "url": "https://prod.example/api/orders",
            }
        if method == "POST" and url.endswith("/tool"):
            return {"id": "cloned-tool"}
        if method == "POST" and url.endswith("/assistant"):
            return {"id": "cloned-assistant"}
        return {}

    spec = ProviderImportSpec(
        type="vapi",
        source_target_id="source-assistant",
        public_capability="tools",
        environment_tools=["lookup"],
    )
    receipt = clone_provider_target(
        spec, context=_context("vapi"), api_key="vapi-secret", request=request
    )

    assistant_body = next(
        body
        for method, url, body in calls
        if method == "POST" and url.endswith("/assistant")
    )
    assert assistant_body["server"]["url"] == "https://world.example/provider/events"
    assert assistant_body["model"]["toolIds"] == ["cloned-tool"]
    assert (
        assistant_body["model"]["tools"][0]["server"]["url"]
        == "https://world.example/provider/tools/tools/lookup"
    )
    tool_body = next(
        body
        for method, url, body in calls
        if method == "POST" and url.endswith("/tool")
    )
    assert tool_body["url"] == "https://world.example/provider/tools/api/orders"
    assert receipt.target.id == "cloned-assistant"
    assert [resource.id for resource in receipt.resources] == [
        "cloned-tool",
        "cloned-assistant",
    ]

    destroy_imported_target(
        spec, receipt=receipt, api_key="vapi-secret", request=request
    )
    deletes = [(method, url) for method, url, _body in calls if method == "DELETE"]
    assert deletes == [
        ("DELETE", "https://api.vapi.ai/assistant/cloned-assistant"),
        ("DELETE", "https://api.vapi.ai/tool/cloned-tool"),
    ]


def test_vapi_import_cleans_up_tools_when_later_tool_copy_fails() -> None:
    calls: list[tuple[str, str]] = []

    def request(method, url, _api_key, _body):
        calls.append((method, url))
        if url.endswith("/assistant/source"):
            return {"id": "source", "model": {"toolIds": ["one", "two"]}}
        if url.endswith("/tool/one"):
            return {"id": "one", "type": "function", "function": {"name": "one"}}
        if method == "POST" and url.endswith("/tool"):
            return {"id": "cloned-one"}
        if url.endswith("/tool/two"):
            raise ProviderImportError("provider_api_error: GET returned HTTP 500")
        if method == "DELETE" and url.endswith("/tool/cloned-one"):
            return {}
        raise AssertionError((method, url))

    with pytest.raises(ProviderImportError, match="HTTP 500"):
        clone_provider_target(
            ProviderImportSpec(
                type="vapi",
                source_target_id="source",
                public_capability="api",
                environment_tools=["one", "two"],
            ),
            context=_context("vapi"),
            api_key="secret",
            request=request,
        )

    assert calls[-1] == ("DELETE", "https://api.vapi.ai/tool/cloned-one")


def test_retell_import_clones_llm_rewires_custom_tools_then_agent() -> None:
    calls: list[tuple[str, str, object]] = []

    def request(method, url, api_key, body):
        assert api_key == "retell-secret"
        calls.append((method, url, body))
        if method == "GET" and "/get-agent/" in url:
            return {
                "agent_id": "source-agent",
                "version": 7,
                "voice_id": "retell-Cimo",
                "response_engine": {"type": "retell-llm", "llm_id": "source-llm"},
                "webhook_url": "https://prod.example/events",
            }
        if method == "GET" and "/get-retell-llm/" in url:
            return {
                "llm_id": "source-llm",
                "version": 3,
                "general_prompt": "Help the caller.",
                "general_tools": [
                    {
                        "type": "custom",
                        "name": "lookup",
                        "url": "https://prod.example/tools/lookup",
                    },
                    {"type": "end_call", "name": "end_call"},
                ],
            }
        if method == "POST" and url.endswith("/create-retell-llm"):
            return {"llm_id": "cloned-llm"}
        if method == "POST" and url.endswith("/create-agent"):
            return {"agent_id": "cloned-agent"}
        return {}

    spec = ProviderImportSpec(
        type="retell",
        source_target_id="source-agent",
        public_capability="tools",
        environment_tools=["lookup"],
    )
    receipt = clone_provider_target(
        spec, context=_context("retell"), api_key="retell-secret", request=request
    )

    llm_body = next(
        body
        for method, url, body in calls
        if method == "POST" and url.endswith("/create-retell-llm")
    )
    assert (
        llm_body["general_tools"][0]["url"]
        == "https://world.example/provider/tools/tools/lookup"
    )
    assert "url" not in llm_body["general_tools"][1]
    agent_body = next(
        body
        for method, url, body in calls
        if method == "POST" and url.endswith("/create-agent")
    )
    assert agent_body["response_engine"] == {
        "type": "retell-llm",
        "llm_id": "cloned-llm",
    }
    assert agent_body["webhook_url"] == "https://world.example/provider/events"
    assert receipt.target.id == "cloned-agent"

    destroy_imported_target(
        spec, receipt=receipt, api_key="retell-secret", request=request
    )
    deletes = [(method, url) for method, url, _body in calls if method == "DELETE"]
    assert deletes == [
        ("DELETE", "https://api.retellai.com/delete-agent/cloned-agent"),
        ("DELETE", "https://api.retellai.com/delete-retell-llm/cloned-llm"),
    ]


def test_retell_import_rejects_an_opaque_response_engine() -> None:
    def request(method, url, api_key, body):
        return {
            "agent_id": "source-agent",
            "response_engine": {
                "type": "custom-llm",
                "llm_websocket_url": "wss://opaque",
            },
        }

    with pytest.raises(ProviderImportError, match="response_engine_unsupported"):
        clone_provider_target(
            ProviderImportSpec(
                type="retell",
                source_target_id="source-agent",
                public_capability="tools",
            ),
            context=_context("retell"),
            api_key="retell-secret",
            request=request,
        )


def test_retell_import_rejects_tool_without_environment_implementation() -> None:
    def request(method, url, api_key, body):
        if "/get-agent/" in url:
            return {
                "agent_id": "source-agent",
                "response_engine": {"type": "retell-llm", "llm_id": "source-llm"},
            }
        return {
            "llm_id": "source-llm",
            "general_tools": [
                {"type": "custom", "name": "refund_order", "url": "https://prod"}
            ],
        }

    with pytest.raises(ProviderImportError, match="tool_implementation_missing"):
        clone_provider_target(
            ProviderImportSpec(
                type="retell",
                source_target_id="source-agent",
                public_capability="tools",
                environment_tools=["lookup_order"],
            ),
            context=_context("retell"),
            api_key="retell-secret",
            request=request,
        )
