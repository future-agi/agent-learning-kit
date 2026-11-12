from __future__ import annotations

import json
from pathlib import Path

import pytest

from fi.alk.harness.job import HarnessJob
from fi.alk.harness.microsoft_import import (
    FOUNDRY_TOKEN_SCOPE,
    MICROSOFT_CLIENT_SECRET,
    entra_token,
    inspect_copilot_studio_agent,
    inspect_foundry_agent,
)
from fi.alk.harness.provider_import import ProviderImportError

DATAVERSE = "https://org123.crm.dynamics.com"
FOUNDRY = "https://acme.services.ai.azure.com/api/projects/support"
BOT_ID = "11111111-2222-3333-4444-555555555555"


def _token_request(calls: list[tuple[str, dict[str, str]]]):
    def request(url, form):
        calls.append((url, dict(form)))
        return {"access_token": "entra-token", "token_type": "Bearer"}

    return request


def test_entra_token_requires_the_client_secret() -> None:
    with pytest.raises(ProviderImportError, match="azure_client_secret_missing"):
        entra_token(
            tenant_id="tenant", client_id="client", client_secret=" ", scope="x"
        )


def test_copilot_studio_reads_draft_instructions_topics_and_tools() -> None:
    token_calls: list[tuple[str, dict[str, str]]] = []
    requested: list[str] = []
    components = [
        {
            "schemaname": "agent.gpt.default",
            "componenttype": 15,
            "componentstate": 0,
            "data": "instructions: Old published prompt.",
        },
        {
            "schemaname": "agent.gpt.default",
            "componenttype": 15,
            "componentstate": 1,
            "data": "kind: GptComponentMetadata\ninstructions: Help with orders and invoices.",
        },
        {
            "name": "Track order",
            "schemaname": "agent.topic.track",
            "componenttype": 9,
            "componentstate": 1,
            "data": (
                "kind: AdaptiveDialog\n"
                "beginDialog:\n"
                "  actions:\n"
                "    - kind: InvokeExternalAgentTaskAction\n"
                "      id: call_bc\n"
                "      connectionReference: cr_businesscentral\n"
            ),
        },
        {
            "name": "Returns policy",
            "schemaname": "agent.knowledge.returns",
            "componenttype": 16,
            "componentstate": 1,
            "data": "",
        },
    ]

    def request(method, url, token, body):
        assert method == "GET"
        assert token == "entra-token"
        assert body is None
        requested.append(url)
        if "/bots(" in url:
            return {"name": "Order Assistant", "schemaname": "agent", "publishedon": None}
        return {"value": components}

    profile = inspect_copilot_studio_agent(
        tenant_id="tenant-1",
        client_id="client-1",
        client_secret="client-secret",
        dataverse_url=DATAVERSE + "/",
        agent_id=BOT_ID,
        token_request=_token_request(token_calls),
        request=request,
    )

    assert token_calls[0][0] == (
        "https://login.microsoftonline.com/tenant-1/oauth2/v2.0/token"
    )
    assert token_calls[0][1]["scope"] == f"{DATAVERSE}/.default"
    assert requested[0] == (
        f"{DATAVERSE}/api/data/v9.2/bots({BOT_ID})"
        "?$select=name,schemaname,language,publishedon,template,configuration"
    )
    assert "componentstate%20eq%201" in requested[1]
    assert profile["provider"] == "copilot_studio"
    assert profile["name"] == "Order Assistant"
    assert profile["published"] is False
    # The saved draft wins over its published copy.
    assert profile["instructions"] == "Help with orders and invoices."
    assert [tool["kind"] for tool in profile["tools"]] == [
        "InvokeExternalAgentTaskAction"
    ]
    assert profile["tools"][0]["component"] == "Track order"
    assert profile["topics"][0]["name"] == "Track order"
    assert profile["knowledge_sources"] == [
        {"name": "Returns policy", "description": None}
    ]
    assert "client-secret" not in json.dumps(profile)


def test_copilot_studio_follows_paging_only_on_the_same_host() -> None:
    def request(method, url, token, body):
        if "/bots(" in url:
            return {"name": "Agent"}
        return {
            "value": [],
            "@odata.nextLink": "https://attacker.example/api/data/v9.2/botcomponents",
        }

    with pytest.raises(ProviderImportError, match="next_link_host_changed"):
        inspect_copilot_studio_agent(
            tenant_id="t",
            client_id="c",
            client_secret="s",
            dataverse_url=DATAVERSE,
            agent_id=BOT_ID,
            token_request=_token_request([]),
            request=request,
        )


def test_copilot_studio_with_nothing_readable_names_the_likely_cause() -> None:
    def request(method, url, token, body):
        return {"name": "Agent"} if "/bots(" in url else {"value": []}

    with pytest.raises(ProviderImportError, match="copilot_studio_agent_empty"):
        inspect_copilot_studio_agent(
            tenant_id="t",
            client_id="c",
            client_secret="s",
            dataverse_url=DATAVERSE,
            agent_id=BOT_ID,
            token_request=_token_request([]),
            request=request,
        )


def test_foundry_reads_the_latest_prompt_agent_version() -> None:
    token_calls: list[tuple[str, dict[str, str]]] = []
    requested: list[str] = []

    def request(method, url, token, body):
        requested.append(url)
        return {
            "name": "order-assistant",
            "versions": {
                "latest": {
                    "name": "order-assistant",
                    "version": "3",
                    "definition": {
                        "kind": "prompt",
                        "model": "gpt-5-mini",
                        "instructions": "Look up the order before answering.",
                        "tools": [
                            {
                                "type": "function",
                                "name": "get_sales_order",
                                "description": "Fetch one sales order",
                                "parameters": {"type": "object"},
                            }
                        ],
                    },
                }
            },
        }

    profile = inspect_foundry_agent(
        tenant_id="tenant-1",
        client_id="client-1",
        client_secret="client-secret",
        project_endpoint=FOUNDRY + "/",
        agent_name="order-assistant",
        token_request=_token_request(token_calls),
        request=request,
    )

    assert token_calls[0][1]["scope"] == FOUNDRY_TOKEN_SCOPE
    assert requested == [f"{FOUNDRY}/agents/order-assistant?api-version=v1"]
    assert profile["provider"] == "foundry"
    assert profile["version"] == "3"
    assert profile["instructions"] == "Look up the order before answering."
    assert profile["tools"][0]["name"] == "get_sales_order"


def test_foundry_falls_back_to_the_version_list() -> None:
    requested: list[str] = []

    def request(method, url, token, body):
        requested.append(url)
        if "/versions?" in url:
            return {
                "data": [
                    {
                        "version": "1",
                        "definition": {"kind": "prompt", "instructions": "Hi."},
                    }
                ]
            }
        return {"name": "agent"}

    profile = inspect_foundry_agent(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        project_endpoint=FOUNDRY,
        agent_name="agent",
        token_request=_token_request([]),
        request=request,
    )

    assert profile["instructions"] == "Hi."
    assert "/agents/agent/versions?" in requested[1]


def test_foundry_rejects_agents_that_are_not_prompt_agents() -> None:
    def request(method, url, token, body):
        return {"versions": {"latest": {"definition": {"kind": "hosted"}}}}

    with pytest.raises(ProviderImportError, match="foundry_agent_kind_unsupported"):
        inspect_foundry_agent(
            tenant_id="t",
            client_id="c",
            client_secret="s",
            project_endpoint=FOUNDRY,
            agent_name="agent",
            token_request=_token_request([]),
            request=request,
        )


def _job(connector: str, config: dict[str, str], mode: str = "connect_only") -> HarnessJob:
    return HarnessJob(
        job_id=f"job-{connector}",
        run_id=f"run-{connector}",
        execution="hosted",
        source={"kind": "provider"},
        agent={
            "connector": connector,
            "mode": mode,
            "config": config,
            "secret_refs": {
                MICROSOFT_CLIENT_SECRET: {
                    "manager": "platform-vault",
                    "key": "secret-id",
                    "purpose": "target_provider",
                }
            },
        },
        scenario_count=1,
        runtime={"isolation": "dedicated_vm"},
    )


COPILOT_CONFIG = {
    "tenant_id": "tenant-1",
    "client_id": "client-1",
    "dataverse_url": DATAVERSE,
    "agent_id": BOT_ID,
}
FOUNDRY_CONFIG = {
    "tenant_id": "tenant-1",
    "client_id": "client-1",
    "project_endpoint": FOUNDRY,
    "agent_name": "order-assistant",
}


@pytest.mark.parametrize(
    ("connector", "config", "message"),
    [
        (
            "copilot_studio",
            {**COPILOT_CONFIG, "agent_id": ""},
            "copilot_studio_connect_only_requires: agent_id",
        ),
        (
            "copilot_studio",
            {**COPILOT_CONFIG, "dataverse_url": "https://evil.example.com"},
            "copilot_studio_dataverse_url_must_be_https_on",
        ),
        (
            "foundry",
            {**FOUNDRY_CONFIG, "project_endpoint": "http://acme.services.ai.azure.com"},
            "foundry_project_endpoint_must_be_https_on",
        ),
    ],
)
def test_microsoft_connect_only_config_is_validated(
    connector: str, config: dict[str, str], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _job(connector, config)


def test_microsoft_connectors_support_connect_only_only() -> None:
    with pytest.raises(ValueError, match="foundry_supports_connect_only_only"):
        _job("foundry", FOUNDRY_CONFIG, mode="provider_import")


@pytest.mark.parametrize(
    ("connector", "config"),
    [("copilot_studio", COPILOT_CONFIG), ("foundry", FOUNDRY_CONFIG)],
)
def test_authoring_reads_microsoft_agents_and_drops_the_secret_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    connector: str,
    config: dict[str, str],
) -> None:
    from fi.alk.harness import authoring_entrypoint

    secrets = tmp_path / "target-secrets.json"
    secrets.write_text(
        json.dumps({MICROSOFT_CLIENT_SECRET: "client-secret"}), encoding="utf-8"
    )
    cache = tmp_path / "provider-profile.json"
    calls: list[tuple[str, dict[str, object]]] = []

    def inspect(name: str, **kwargs: object) -> dict[str, object]:
        calls.append((name, kwargs))
        return {"provider": name, "instructions": "Be helpful."}

    monkeypatch.setattr(authoring_entrypoint, "inspect_microsoft_agent", inspect)

    profile = authoring_entrypoint._load_provider_import_profile(
        _job(connector, config), secrets, cache
    )

    assert profile == {"provider": connector, "instructions": "Be helpful."}
    assert calls[0][0] == connector
    assert calls[0][1]["client_secret"] == "client-secret"
    assert calls[0][1]["config"] == config
    assert not secrets.exists()
    assert "client-secret" not in cache.read_text(encoding="utf-8")


def test_copilot_studio_reads_the_current_agent_format() -> None:
    # Shape recorded from a real Copilot Studio agent (template cliagent-1.0.0): the
    # instructions sit on the bot's configuration, a tool is its own WorkflowTool component,
    # and Work IQ leaves IQCapability components behind.
    configuration = json.dumps(
        {
            "$kind": "BotConfiguration",
            "agentSettings": {
                "$kind": "AgentSettings",
                "instructions": {
                    "$kind": "Instructions",
                    "segments": [
                        {"$kind": "StaticSegment", "value": "You are Ava. Ask for the order number first."}
                    ],
                },
            },
        }
    )
    components = [
        {
            "name": "get_order_status",
            "schemaname": "agent.tool.get_order_status",
            "componenttype": 9,
            "componentstate": 0,
            "data": (
                "kind: WorkflowTool\n"
                "workflowId: b9db5ccc-5be5-408b-c9bd-9b8b5c92ac96\n"
                "toolInputs:\n"
                "  - name: text\n"
                "    displayName: order_number\n"
                '    description: "Order number in the format AC-12345"\n'
                "toolOutputs:\n"
                "  - name: text\n"
                "    displayName: status\n"
            ),
        },
        {
            "name": "mail",
            "schemaname": "draft.iqCapability.mail",
            "componenttype": 9,
            "componentstate": 0,
            "data": "kind: IQCapability\ncapability: mail\naccess: Write\n",
        },
    ]

    def request(method, url, token, body):
        if "/bots(" in url:
            assert "configuration" in url
            return {"name": "Ava", "template": "cliagent-1.0.0", "configuration": configuration}
        return {"value": components}

    profile = inspect_copilot_studio_agent(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        dataverse_url=DATAVERSE,
        agent_id=BOT_ID,
        token_request=_token_request([]),
        request=request,
    )

    assert profile["instructions"] == "You are Ava. Ask for the order number first."
    assert profile["template"] == "cliagent-1.0.0"
    assert profile["tools"] == [
        {
            "kind": "WorkflowTool",
            "component": "get_order_status",
            "name": "get_order_status",
            "description": None,
            "inputs": [
                {"name": "order_number", "description": "Order number in the format AC-12345"}
            ],
            "outputs": [{"name": "status", "description": None}],
        }
    ]
    assert profile["topics"] == []
    assert {"name": "mail", "type": "work_iq_capability", "capability": "mail", "access": "Write"} in profile["other_components"]
