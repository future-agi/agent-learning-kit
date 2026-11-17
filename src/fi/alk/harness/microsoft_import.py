"""Read-only inspection of Microsoft-hosted agents: Copilot Studio and Foundry.

Both are reached with one Entra app (tenant ID, client ID, client secret) using the OAuth2
client-credentials grant. The token is scoped to the one resource being read, so it can do
nothing outside it. Nothing here creates, updates or deletes anything on the customer's side.

Copilot Studio has no "get agent" API; an agent is rows in Dataverse. The ``bot`` row is the
agent, and ``botcomponent`` rows are its parts. Saved but unpublished parts are rows too (their
``componentstate`` is 1), so an agent on a trial licence, which cannot publish, can still be read.

Foundry has an agent API: the latest version's ``definition`` holds the instructions, the model
and the tool list.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

import yaml

from .provider_import import ProviderImportError, _request_json, _safe_profile_value

MICROSOFT_CONNECTORS = frozenset({"copilot_studio", "foundry"})

# The one secret both connectors need. Every other value (tenant, client ID, URLs, agent ID)
# is public configuration and travels in ``agent.config``.
MICROSOFT_CLIENT_SECRET = "AZURE_CLIENT_SECRET"

MICROSOFT_LOGIN_HOST = "login.microsoftonline.com"
FOUNDRY_TOKEN_SCOPE = "https://ai.azure.com/.default"
FOUNDRY_DEFAULT_API_VERSION = "v1"
DATAVERSE_API_PATH = "api/data/v9.2"

# Microsoft's botcomponent table reference.
_COMPONENT_STATE_PUBLISHED = 0
_COMPONENT_STATE_UNPUBLISHED = 1
_COMPONENT_TYPE_LEGACY_TOPIC = 0
_COMPONENT_TYPE_TOPIC = 9
_COMPONENT_TYPE_CUSTOM_GPT = 15
_COMPONENT_TYPE_KNOWLEDGE = 16
_COMPONENT_TYPE_NAMES = {
    0: "topic_legacy",
    9: "topic",
    15: "custom_gpt",
    16: "knowledge_source",
    17: "external_trigger",
    18: "copilot_settings",
}

# Action steps inside a component's YAML that call something outside the agent. Copilot Studio
# has no separate "tool" row; a tool exists only as one of these steps.
_TOOL_ACTION_KINDS = frozenset(
    {
        "InvokeExternalAgentTaskAction",  # an MCP server
        "TaskDialog",  # a Power Platform connector action
        "HttpRequestAction",  # an HTTP tool
        "InvokeConnectorAction",
        "InvokeFlowAction",
    }
)

# A single component's YAML is bounded so one huge topic cannot crowd out the rest of the
# definition the authoring model reads.
_MAX_COMPONENT_DATA_CHARS = 20_000
_MAX_DATAVERSE_PAGES = 20

TokenRequest = Callable[[str, Mapping[str, str]], dict[str, Any]]
JsonRequest = Callable[[str, str, str, Mapping[str, Any] | None], dict[str, Any]]


def _post_form(url: str, form: Mapping[str, str]) -> dict[str, Any]:
    request = Request(
        url,
        data=urlencode(form).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "FutureAGI-ALK/1.0",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed Microsoft host
            raw = response.read()
    except HTTPError as exc:
        # Entra answers with a safe error code and description (for example AADSTS7000215,
        # an invalid client secret). Keep a bounded excerpt; never the form, which holds it.
        try:
            detail = json.loads(exc.read(2048).decode("utf-8", errors="replace"))
            reason = str(detail.get("error_description") or detail.get("error") or "")
        except (AttributeError, OSError, ValueError):
            reason = ""
        reason = " ".join(reason.split())[:300]
        suffix = f": {reason}" if reason else ""
        raise ProviderImportError(
            f"microsoft_token_rejected: HTTP {exc.code}{suffix}"
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise ProviderImportError(
            f"microsoft_token_unavailable: {type(exc).__name__}"
        ) from exc
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ProviderImportError("microsoft_token_response_invalid_json") from exc
    if not isinstance(value, dict):
        raise ProviderImportError("microsoft_token_response_must_be_object")
    return value


def entra_token(
    *,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    scope: str,
    request: TokenRequest | None = None,
) -> str:
    """Exchange the Entra app's credentials for an access token scoped to one resource."""
    if not tenant_id.strip():
        raise ProviderImportError("microsoft_tenant_id_missing")
    if not client_id.strip():
        raise ProviderImportError("microsoft_client_id_missing")
    if not client_secret.strip():
        raise ProviderImportError(f"{MICROSOFT_CLIENT_SECRET.lower()}_missing")
    request = request or _post_form
    url = (
        f"https://{MICROSOFT_LOGIN_HOST}/{quote(tenant_id.strip(), safe='')}"
        "/oauth2/v2.0/token"
    )
    body = request(
        url,
        {
            "grant_type": "client_credentials",
            "client_id": client_id.strip(),
            "client_secret": client_secret.strip(),
            "scope": scope,
        },
    )
    token = str(body.get("access_token") or "")
    if not token:
        raise ProviderImportError("microsoft_token_missing_from_response")
    return token


def _origin(url: str) -> str:
    parsed = urlsplit(url.strip())
    return f"{parsed.scheme}://{parsed.netloc}"


def dataverse_scope(dataverse_url: str) -> str:
    return f"{_origin(dataverse_url)}/.default"


def _yaml(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


def _tool_actions(node: Any, topic: str) -> list[dict[str, Any]]:
    """Every action step in a component's YAML that calls a tool, with where it sits."""
    found: list[dict[str, Any]] = []
    if isinstance(node, Mapping):
        kind = str(node.get("kind") or "")
        if kind in _TOOL_ACTION_KINDS:
            found.append({"kind": kind, "component": topic, "step": dict(node)})
        for value in node.values():
            found.extend(_tool_actions(value, topic))
    elif isinstance(node, list):
        for item in node:
            found.extend(_tool_actions(item, topic))
    return found


def _configuration_instructions(configuration: Any) -> str:
    """The instructions of an agent in the current format, kept on the bot row itself.

    Agents made with the current Copilot Studio (template ``cliagent-*``) store them in
    ``bot.configuration`` at ``agentSettings.instructions.segments[].value``; older agents keep
    them in a Custom GPT component instead, which is read separately.
    """
    if isinstance(configuration, str):
        try:
            configuration = json.loads(configuration)
        except ValueError:
            return ""
    if not isinstance(configuration, Mapping):
        return ""
    settings = configuration.get("agentSettings")
    instructions = settings.get("instructions") if isinstance(settings, Mapping) else None
    if isinstance(instructions, str):
        return instructions
    if not isinstance(instructions, Mapping):
        return ""
    segments = instructions.get("segments")
    return "".join(
        str(segment.get("value") or "")
        for segment in segments if isinstance(segment, Mapping)
    ) if isinstance(segments, list) else ""


def _component_tool(parsed: Any, name: str) -> dict[str, Any] | None:
    """A tool stored as its own component (``kind: WorkflowTool`` and the like)."""
    if not isinstance(parsed, Mapping):
        return None
    kind = str(parsed.get("kind") or "")
    if not kind.endswith("Tool"):
        return None

    def fields(entries: Any) -> list[dict[str, Any]]:
        return [
            {
                "name": entry.get("displayName") or entry.get("name"),
                "description": str(entry.get("description") or "").strip() or None,
            }
            for entry in entries or []
            if isinstance(entry, Mapping)
        ]

    return {
        "kind": kind,
        "component": name,
        "name": name,
        "description": parsed.get("description") or parsed.get("modelDescription"),
        "inputs": fields(parsed.get("toolInputs") or parsed.get("inputs")),
        "outputs": fields(parsed.get("toolOutputs") or parsed.get("outputs")),
    }


def _bounded(text: str) -> str:
    if len(text) <= _MAX_COMPONENT_DATA_CHARS:
        return text
    return text[:_MAX_COMPONENT_DATA_CHARS] + "\n# [truncated]"


def _latest_components(rows: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """One row per component. A saved draft (unpublished) wins over its published copy.

    Microsoft does not document whether editing a published part makes a second row or
    updates the first. Keying on ``schemaname`` and preferring the unpublished row is right in
    both cases.
    """
    chosen: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        key = str(row.get("schemaname") or row.get("botcomponentid") or id(row))
        current = chosen.get(key)
        if current is None or (
            row.get("componentstate") == _COMPONENT_STATE_UNPUBLISHED
            and current.get("componentstate") != _COMPONENT_STATE_UNPUBLISHED
        ):
            chosen[key] = row
    return list(chosen.values())


def inspect_copilot_studio_agent(
    *,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    dataverse_url: str,
    agent_id: str,
    token_request: TokenRequest | None = None,
    request: JsonRequest | None = None,
) -> dict[str, Any]:
    """Read a Copilot Studio agent's instructions, topics and tools from Dataverse."""
    if not agent_id.strip():
        raise ProviderImportError("copilot_studio_agent_id_missing")
    request = request or _request_json
    token = entra_token(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
        scope=dataverse_scope(dataverse_url),
        request=token_request,
    )
    base = f"{_origin(dataverse_url)}/{DATAVERSE_API_PATH}"
    bot_id = quote(agent_id.strip(), safe="")
    bot = request(
        "GET",
        f"{base}/bots({bot_id})"
        "?$select=name,schemaname,language,publishedon,template,configuration",
        token,
        None,
    )
    query = urlencode(
        {
            "$select": "name,schemaname,componenttype,componentstate,description,data",
            "$filter": (
                f"_parentbotid_value eq {agent_id.strip()} and "
                f"(componentstate eq {_COMPONENT_STATE_PUBLISHED} or "
                f"componentstate eq {_COMPONENT_STATE_UNPUBLISHED})"
            ),
        },
        quote_via=quote,
    )
    rows: list[Mapping[str, Any]] = []
    next_url: str | None = f"{base}/botcomponents?{query}"
    for _ in range(_MAX_DATAVERSE_PAGES):
        if not next_url:
            break
        page = request("GET", next_url, token, None)
        rows.extend(item for item in page.get("value") or [] if isinstance(item, Mapping))
        next_url = page.get("@odata.nextLink")
        # Paging must stay on the same Dataverse host the token was issued for.
        if next_url and _origin(next_url) != _origin(dataverse_url):
            raise ProviderImportError("copilot_studio_next_link_host_changed")

    instructions: list[str] = []
    configured = _configuration_instructions(bot.get("configuration"))
    if configured.strip():
        instructions.append(configured)
    topics: list[dict[str, Any]] = []
    knowledge: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for row in _latest_components(rows):
        component_type = row.get("componenttype")
        name = str(row.get("name") or row.get("schemaname") or "")
        data = str(row.get("data") or "")
        parsed = _yaml(data) if data else None
        kind = str(parsed.get("kind") or "") if isinstance(parsed, Mapping) else ""
        tool = _component_tool(parsed, name)
        if tool is not None:
            tools.append(tool)
            continue
        if kind == "IQCapability":
            # Work IQ (Microsoft 365) access. These can outlive removing Work IQ in the
            # designer, so they are listed as capabilities, never as topics or tools.
            other.append(
                {
                    "name": name,
                    "type": "work_iq_capability",
                    "capability": parsed.get("capability"),
                    "access": parsed.get("access"),
                }
            )
            continue
        tools.extend(_tool_actions(parsed, name))
        if component_type == _COMPONENT_TYPE_CUSTOM_GPT:
            prompt = (
                parsed.get("instructions") if isinstance(parsed, Mapping) else None
            )
            instructions.append(str(prompt) if prompt else _bounded(data))
        elif component_type in {_COMPONENT_TYPE_TOPIC, _COMPONENT_TYPE_LEGACY_TOPIC}:
            topics.append(
                {
                    "name": name,
                    "description": row.get("description"),
                    "unpublished": row.get("componentstate")
                    == _COMPONENT_STATE_UNPUBLISHED,
                    "yaml": _bounded(data),
                }
            )
        elif component_type == _COMPONENT_TYPE_KNOWLEDGE:
            knowledge.append({"name": name, "description": row.get("description")})
        else:
            other.append(
                {
                    "name": name,
                    "type": _COMPONENT_TYPE_NAMES.get(
                        component_type, str(component_type)
                    ),
                }
            )
    if not instructions and not topics and not tools:
        raise ProviderImportError(
            "copilot_studio_agent_empty: no instructions or topics were readable; check "
            "the agent ID and that the app user's security role can read botcomponent rows"
        )
    profile = {
        "provider": "copilot_studio",
        "modality": "chat",
        "source_target_id": agent_id.strip(),
        "name": bot.get("name"),
        "schema_name": bot.get("schemaname"),
        "published": bool(bot.get("publishedon")),
        "template": bot.get("template"),
        "instructions": "\n\n".join(instructions),
        "tools": tools,
        "topics": topics,
        "knowledge_sources": knowledge,
        "other_components": other,
    }
    return _safe_profile_value(profile)


def _foundry_latest_version(
    base: str, agent: str, api_version: str, token: str, request: JsonRequest
) -> dict[str, Any]:
    params = urlencode({"api-version": api_version})
    body = request("GET", f"{base}/agents/{agent}?{params}", token, None)
    versions = body.get("versions")
    latest = versions.get("latest") if isinstance(versions, Mapping) else None
    if isinstance(latest, Mapping):
        return dict(latest)
    # ``versions.latest`` is required by the documented response, so this is a guard, not a
    # path the docs promise. Only the documented query parameter is sent.
    listing = request(
        "GET",
        f"{base}/agents/{agent}/versions?" + urlencode({"api-version": api_version}),
        token,
        None,
    )
    data = listing.get("data")
    if isinstance(data, list) and data and isinstance(data[-1], Mapping):
        return dict(data[-1])
    raise ProviderImportError("foundry_agent_has_no_versions")


def inspect_foundry_agent(
    *,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    project_endpoint: str,
    agent_name: str,
    api_version: str = FOUNDRY_DEFAULT_API_VERSION,
    token_request: TokenRequest | None = None,
    request: JsonRequest | None = None,
) -> dict[str, Any]:
    """Read a Foundry prompt agent's latest version: instructions, model and tools."""
    if not agent_name.strip():
        raise ProviderImportError("foundry_agent_name_missing")
    request = request or _request_json
    token = entra_token(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
        scope=FOUNDRY_TOKEN_SCOPE,
        request=token_request,
    )
    base = project_endpoint.strip().rstrip("/")
    agent = quote(agent_name.strip(), safe="")
    version = _foundry_latest_version(base, agent, api_version, token, request)
    definition = version.get("definition")
    if not isinstance(definition, Mapping):
        raise ProviderImportError("foundry_agent_definition_missing")
    kind = str(definition.get("kind") or definition.get("type") or "").strip()
    if kind and kind != "prompt":
        raise ProviderImportError(
            f"foundry_agent_kind_unsupported: {kind!r}; only prompt agents are supported"
        )
    profile = {
        "provider": "foundry",
        "modality": "chat",
        "source_target_id": agent_name.strip(),
        "name": version.get("name") or agent_name.strip(),
        "version": version.get("version"),
        "description": version.get("description"),
        "model": definition.get("model"),
        "instructions": definition.get("instructions"),
        "tools": definition.get("tools") or [],
    }
    return _safe_profile_value(profile)


def inspect_microsoft_agent(
    connector: str,
    *,
    config: Mapping[str, Any],
    client_secret: str,
    token_request: TokenRequest | None = None,
    request: JsonRequest | None = None,
) -> dict[str, Any]:
    """Dispatch one connect-only Microsoft connector to its read-only inspector."""
    connector = connector.strip().lower()
    common = {
        "tenant_id": str(config.get("tenant_id") or ""),
        "client_id": str(config.get("client_id") or ""),
        "client_secret": client_secret,
        "token_request": token_request,
        "request": request,
    }
    if connector == "copilot_studio":
        return inspect_copilot_studio_agent(
            dataverse_url=str(config.get("dataverse_url") or ""),
            agent_id=str(config.get("agent_id") or ""),
            **common,
        )
    if connector == "foundry":
        return inspect_foundry_agent(
            project_endpoint=str(config.get("project_endpoint") or ""),
            agent_name=str(config.get("agent_name") or ""),
            api_version=str(config.get("api_version") or "")
            or FOUNDRY_DEFAULT_API_VERSION,
            **common,
        )
    raise ProviderImportError(f"microsoft_connector_unknown: {connector!r}")
