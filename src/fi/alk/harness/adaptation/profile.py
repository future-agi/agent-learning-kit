"""The agent being adapted: its inspected profile, per-tool admission and binding identity."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..json_schemas import schema_problems
from ..provider_import import (
    ProviderImportError,
    ToolAdmission,
    _safe_profile_value,
    admit_provider_tools,
    admitted_tool_definitions,
    inspect_provider_target,
    provider_profile_digest,
)
from ..provider_lifecycle import ProviderType
from ..releases import AgentRef, BindingMode, ToolDefinition, seal_agent_binding
from .declared import (
    DECLARED,
    MCP_CLIENT,
    declared_admission,
    declared_profile,
    declared_tool_definitions,
)
from .errors import AdaptationFailure

# Provider agents are inspected at their provider; an `mcp_client` agent is declared (`declared.py`).
ADAPTABLE_CONNECTORS = ("retell_chat", "retell", "vapi", MCP_CLIENT)
_TARGET_CREDENTIALS = {
    ProviderType.RETELL: "RETELL_API_KEY",
    ProviderType.VAPI: "VAPI_API_KEY",
}


@dataclass(frozen=True)
class AgentProfile:
    connector: str
    provider: ProviderType | None  # None: a declared agent, with no provider to clone
    target: dict[str, Any]
    admission: list[ToolAdmission]
    agent_tools: list[dict[str, Any]]
    agent: AgentRef

    def document(self) -> dict[str, Any]:
        return {
            "agent": self.agent.model_dump(mode="json"),
            "connector": self.connector,
            "profile": self.target,
            "admission": [one.model_dump(mode="json") for one in self.admission],
            "agent_tools": self.agent_tools,
        }

    @property
    def admitted(self) -> list[ToolAdmission]:
        return [one for one in self.admission if one.action in {"routed", "replaced"}]


def provider_of(connector: str) -> ProviderType | None:
    if connector not in ADAPTABLE_CONNECTORS:
        raise AdaptationFailure(
            "compose_unsupported",
            f"adaptation supports {', '.join(ADAPTABLE_CONNECTORS)} agents, not {connector}",
        )
    if connector == MCP_CLIENT:
        return None
    return ProviderType.VAPI if connector == "vapi" else ProviderType.RETELL


def build_profile(
    target: Mapping[str, Any], *, connector: str, agent: Mapping[str, Any] | None
) -> AgentProfile:
    """Admission over a (re-redacted) provider profile; `agent` is the platform's AgentRef.

    Without one (a local run from a profile file) the agent is identified by the profile itself:
    its configuration digest is the canonical digest of the redacted profile.
    """
    provider = provider_of(connector)
    if provider is None:
        # A declaration holds only what the customer wrote down: nothing to redact, and every
        # tool is routed, so its argument names stay exactly as the agent sends them.
        try:
            redacted = declared_profile(target)
        except ValueError as exc:
            raise AdaptationFailure("adaptation_profile_invalid", str(exc)) from exc
        admission = declared_admission(redacted)
        definitions = declared_tool_definitions(redacted)
    else:
        redacted = _safe_profile_value(dict(target))
        try:
            admission = admit_provider_tools(provider, redacted)
            definitions = admitted_tool_definitions(provider, redacted)
        except ProviderImportError as exc:
            code = str(exc).split(":", 1)[0]
            raise AdaptationFailure(code or "provider_import_failed", str(exc)) from exc
    target_id = str(redacted.get("source_target_id") or "").strip()
    if agent is None:
        local = f"local:{target_id or 'agent'}"
        digest = provider_profile_digest(redacted)
        agent = {
            "agent_definition_id": local,
            "agent_version_id": local,
            "configuration_digest": digest,
            "agent_config_digest": digest,
            "connector": connector,
        }
    try:
        reference = AgentRef.model_validate(
            {
                **dict(agent),
                "provider_target_id": target_id,
                "provider_digest": provider_profile_digest(redacted),
            }
        )
    except ValueError as exc:
        raise AdaptationFailure(
            "adaptation_job_invalid", f"agent reference: {exc}"
        ) from exc
    if reference.connector != connector:
        raise AdaptationFailure(
            "adaptation_job_invalid",
            f"agent reference connector {reference.connector} differs from {connector}",
        )
    return AgentProfile(
        connector=connector,
        provider=provider,
        target=redacted,
        admission=admission,
        agent_tools=definitions,
        agent=reference,
    )


def profile_from_file(body: Mapping[str, Any]) -> AgentProfile:
    """A saved `inspect_provider_target` result, optionally carrying `agent` (an AgentRef)."""
    target = dict(body)
    agent = target.pop("agent", None)
    provider = str(target.get("provider") or "")
    if provider == "vapi":
        connector = "vapi"
    elif provider == "retell":
        connector = "retell_chat" if target.get("modality") == "chat" else "retell"
    elif provider == DECLARED:
        connector = MCP_CLIENT
    else:
        raise AdaptationFailure(
            "adaptation_profile_invalid",
            f"profile provider {provider!r} is not retell, vapi or {DECLARED}",
        )
    return build_profile(target, connector=connector, agent=agent)


def profile_from_job(
    job: Any, *, credentials: Mapping[str, str], request: Any = None
) -> AgentProfile:
    """Inspect the job's provider target read-only, with the target's own API key; a declared
    agent (`mcp_client`) is read from the job's agent config, and no key is needed."""
    connector = job.agent.connector.strip().lower()
    provider = provider_of(connector)
    adaptation = (job.metadata or {}).get("adaptation") or {}
    agent = adaptation.get("agent") if isinstance(adaptation, Mapping) else None
    if not isinstance(agent, Mapping):
        raise AdaptationFailure(
            "adaptation_job_invalid", "job metadata.adaptation.agent is missing"
        )
    if provider is None:
        try:
            target = declared_profile(job.agent.config)
        except ValueError as exc:
            raise AdaptationFailure(
                "adaptation_job_invalid", f"agent.config is not a declared agent: {exc}"
            ) from exc
        return build_profile(target, connector=connector, agent=agent)
    target_key = "assistant_id" if provider is ProviderType.VAPI else "agent_id"
    target_id = str(job.agent.config.get(target_key) or "").strip()
    try:
        target = inspect_provider_target(
            provider,
            source_target_id=target_id,
            api_key=credentials.get(_TARGET_CREDENTIALS[provider], ""),
            target_modality="chat" if connector == "retell_chat" else "voice",
            request=request,
        )
    except ProviderImportError as exc:
        raise AdaptationFailure("provider_inspect_failed", str(exc)) from exc
    return build_profile(target, connector=connector, agent=agent)


def select_mode(
    profile: AgentProfile, environment_tools: Sequence[ToolDefinition]
) -> BindingMode:
    """§7.5 step 2: canonical when the admitted tools are exactly the environment's, each called
    as-is, same name and compatible arguments; anything else needs a routed binding.

    A canonical binding serves every environment tool, so an agent with fewer tools than the
    environment, even ones it calls as-is, is routed: its binding serves only its own tools
    (`identity_mapping`), and a call to any other is refused."""
    admitted = {one.name for one in profile.admitted}
    if not admitted:
        return BindingMode.CONVERSATION_ONLY
    if admitted == {tool.name for tool in environment_tools} and admitted <= _as_is(
        profile, environment_tools
    ):
        return BindingMode.CANONICAL
    return BindingMode.TOOL_ROUTED


def _as_is(
    profile: AgentProfile,
    environment_tools: Sequence[ToolDefinition],
    *,
    forwarded: bool = False,
) -> set[str]:
    """The agent's tools it can call as the environment's own: same name, compatible arguments.
    `forwarded` is through a mapping that passes on only the arguments the agent declares."""
    environment = {tool.name: tool.parameters for tool in environment_tools}
    return {
        name
        for one in profile.agent_tools
        if (name := str(one.get("name"))) in environment
        and not argument_mismatches(
            _forwarded(one.get("parameters") or {})
            if forwarded
            else one.get("parameters") or {},
            environment[name],
        )
    }


def _forwarded(parameters: Mapping[str, Any]) -> Mapping[str, Any]:
    """The calls that reach the environment when only the declared arguments are passed on: an
    agent whose schema is open to other names still never sends one."""
    if schema_problems(parameters):
        return parameters
    properties = parameters.get("properties") or {}
    return {
        **{
            keyword: parameters[keyword]
            for keyword in ("$defs", "definitions")
            if keyword in parameters
        },
        "type": "object",
        "properties": properties,
        "required": [
            name for name in parameters.get("required") or [] if name in properties
        ],
        "additionalProperties": False,
    }


def identity_mapping(
    profile: AgentProfile, environment_tools: Sequence[ToolDefinition]
) -> dict[str, Any] | None:
    """The routed mapping of an agent whose every tool is an environment tool it calls as-is: each
    one onto itself, every argument passed through. None when a tool needs a mapping written."""
    names = [str(one.get("name")) for one in profile.agent_tools]
    if not names or set(names) - _as_is(profile, environment_tools, forwarded=True):
        return None
    return {
        "tools": [
            {
                "agent_tool": str(one["name"]),
                "steps": [
                    {
                        "id": "call",
                        "tool": str(one["name"]),
                        "arguments": {
                            argument: {"arg": _argument_path(argument)}
                            for argument in (one.get("parameters") or {}).get(
                                "properties"
                            )
                            or {}
                        },
                    }
                ],
            }
            for one in profile.agent_tools
        ]
    }


def _argument_path(name: str) -> str:
    """The JMESPath of one top-level argument: quoted unless it is a plain identifier."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return name
    return json.dumps(name)


# Keywords that say nothing about which values a schema accepts. Definitions are among them: a
# `$ref` into them does (`_same`).
_ANNOTATIONS = frozenset(
    {
        "description",
        "title",
        "examples",
        "default",
        "$comment",
        "deprecated",
        "readOnly",
        "writeOnly",
        "$defs",
        "definitions",
    }
)
# Keywords `_implied` reads itself, together: what one accepts depends on its neighbours.
_COMPARED = frozenset(
    {
        "type",
        "enum",
        "const",
        "required",
        "properties",
        "items",
        "additionalProperties",
    }
)
# Keywords whose answer depends on nothing but their own value: the same one accepts the same
# values wherever it stands, and one only the agent sets can only accept fewer.
_ALONE = frozenset(
    {
        "pattern",
        "format",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minProperties",
        "maxProperties",
        "propertyNames",
        "dependentRequired",
        "dependentSchemas",
        "allOf",
        "anyOf",
        "oneOf",
        "not",
    }
)
# Keywords that make a schema's meaning depend on where it stands.
_PLACED = frozenset(
    {
        "$ref",
        "$dynamicRef",
        "$recursiveRef",
        "$id",
        "$anchor",
        "$dynamicAnchor",
        "$schema",
    }
)


def _json(value: Any) -> str:
    """Equal only for the same JSON: 1 is not true."""
    return json.dumps(value, sort_keys=True, default=str)


# Where a schema holds other schemas: as the keyword's value, or under names.
_ONE = frozenset({"items", "additionalProperties", "propertyNames", "not"})
_LISTED = frozenset({"allOf", "anyOf", "oneOf"})
_NAMED = frozenset({"properties", "dependentSchemas"})


def _plain(schema: Any) -> Any:
    """`schema` without its annotations, at every depth they are looked for."""
    if not isinstance(schema, Mapping):
        return schema
    plain = {}
    for keyword, value in schema.items():
        if keyword in _ANNOTATIONS:
            continue
        if keyword in _ONE:
            value = _plain(value)
        elif keyword in _LISTED and isinstance(value, list):
            value = [_plain(one) for one in value]
        elif keyword in _NAMED and isinstance(value, Mapping):
            value = {name: _plain(one) for name, one in value.items()}
        plain[keyword] = value
    return plain


def _placed(value: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for keyword, inner in value.items():
            if keyword in _PLACED:
                yield keyword, inner
            yield from _placed(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _placed(inner)


def _into_definitions(keyword: str, target: Any) -> bool:
    return keyword == "$ref" and str(target).startswith(("#/$defs/", "#/definitions/"))


def _same(agent: Any, environment: Any, shared: bool) -> bool:
    """One schema, annotations aside. With a reference in it, only where both roots hold the same
    definitions (`shared`) and that is where it leads."""
    return _json(_plain(agent)) == _json(_plain(environment)) and all(
        shared and _into_definitions(*one)
        for schema in (agent, environment)
        for one in _placed(schema)
    )


def _types(schema: Mapping[str, Any]) -> set[str]:
    declared = schema.get("type")
    return set(
        declared if isinstance(declared, list) else [declared] if declared else []
    )


def _implied(agent: Any, environment: Any, where: str, shared: bool) -> list[str]:
    """Why a value the agent's schema allows at `where` could be one the environment's refuses.

    Sound, not complete. The same schema is compatible. Otherwise types, enums, constants,
    required names, properties, additional properties and items are read at every depth, any
    other rule the environment sets (a pattern, a bound, a union) must be set the same in the
    agent's schema, and a keyword this does not know, on either side, is a mismatch. The agent's
    tool is then mapped rather than called as-is."""
    if agent is False or _same(agent, environment, shared):
        return []
    if environment is False:
        return [f"{where} may be sent, the environment accepts no value there"]
    if environment is True or not set(environment) - _ANNOTATIONS:
        return []
    if not isinstance(agent, Mapping):
        return [f"{where} has no schema, the environment constrains it"]
    known = _ANNOTATIONS | _COMPARED | _ALONE
    problems = [
        f"{where}: the {side} sets {keyword}, which cannot be compared"
        for side, schema in (("agent", agent), ("environment", environment))
        for keyword in schema
        if keyword not in known
    ]
    if taken := _types(environment):
        sent = _types(agent)
        # Every integer is a number.
        wider = {
            "number" if one == "integer" and "number" in taken else one for one in sent
        }
        if not sent:
            problems.append(
                f"{where} has no type, the environment takes {', '.join(sorted(taken))}"
            )
        elif wider - taken:
            problems.append(
                f"{where} is {', '.join(sorted(sent))}, the environment takes {', '.join(sorted(taken))}"
            )
    offered = [agent["const"]] if "const" in agent else agent.get("enum")
    for allowed in (
        [environment["const"]] if "const" in environment else None,
        environment.get("enum"),
    ):
        if allowed is not None and (
            not offered or {_json(one) for one in offered} - {*map(_json, allowed)}
        ):
            problems.append(f"{where} may carry values the environment does not accept")
    sent_properties = agent.get("properties") or {}
    taken_properties = environment.get("properties") or {}
    # What each side says of a property it does not name: nothing at all accepts any.
    sent_others = agent.get("additionalProperties", True)
    taken_others = environment.get("additionalProperties", True)
    inside = "" if where == "arguments" else f"{where}."
    problems += [
        f"{inside}{name} is not an environment argument"
        for name in sent_properties
        if name not in taken_properties
        and ("properties" in environment or taken_others is False)
    ]
    problems += [
        f"{inside}{name} is an environment argument the agent does not declare, and may carry "
        "a value the environment does not accept (additionalProperties is not false)"
        for name, schema in taken_properties.items()
        if name not in sent_properties and _implied(sent_others, schema, name, shared)
    ]
    if taken_others is False and sent_others is not False:
        problems.append(
            f"{where} may carry properties the environment forbids "
            "(additionalProperties is false there, not here)"
        )
    elif taken_others is not False:
        problems += _implied(sent_others, taken_others, f"{inside}*", shared)
    always = set(agent.get("required") or [])
    for name in environment.get("required") or []:
        if name not in sent_properties:
            problems.append(f"{inside}{name} is required but the agent never sends it")
        elif name not in always:
            problems.append(
                f"{inside}{name} is required but the agent may leave it out"
            )
    for name, schema in sent_properties.items():
        taken_schema = taken_properties.get(name, taken_others)
        if name in taken_properties or taken_others is not False:
            problems += _implied(schema, taken_schema, f"{inside}{name}", shared)
    problems += _implied(
        agent.get("items", True), environment.get("items", True), f"{where}[]", shared
    )
    problems += [
        f"{where}: the environment sets {keyword}, which the agent's schema does not match"
        for keyword in environment
        if keyword in _ALONE
        and not (
            keyword in agent
            and _same(
                {keyword: agent[keyword]}, {keyword: environment[keyword]}, shared
            )
        )
    ]
    return problems


def argument_mismatches(
    agent: Mapping[str, Any], environment: Mapping[str, Any]
) -> list[str]:
    """Why calls the agent makes with this schema would not be valid environment calls.

    The whole schema is compared, nested objects and array items included (`_implied`): every
    argument the agent may send must be one the environment takes, of a type and with only values
    it accepts; every argument the environment requires must be one the agent always sends.
    """
    if problems := schema_problems(agent):
        return [f"the agent's schema cannot be used: {one}" for one in problems]
    # Word for word the same, its references lead to the same places whatever they are.
    if _json(agent) == _json(environment):
        return []
    # Which draft a schema says it is written in changes nothing: both are read as 2020-12.
    agent, environment = (
        {keyword: value for keyword, value in one.items() if keyword != "$schema"}
        if isinstance(one, Mapping)
        else one
        for one in (agent, environment)
    )
    return _implied(
        agent, environment, "arguments", _shared_definitions(agent, environment)
    )


def _shared_definitions(agent: Any, environment: Any) -> bool:
    ours, theirs = (
        {one: schema[one] for one in ("$defs", "definitions") if one in schema}
        if isinstance(schema, Mapping)
        else {}
        for schema in (agent, environment)
    )
    return _json(ours) == _json(theirs) and all(
        _into_definitions(*one) for one in _placed(ours)
    )


def binding_fields(
    profile: AgentProfile, mode: BindingMode, mapping: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """A complete binding document minus what the sealer sets; mapping only for tool_routed."""
    mapping = mapping or {}
    routed = mode is BindingMode.TOOL_ROUTED
    return {
        "agent": profile.agent.model_dump(mode="json"),
        "mode": mode.value,
        "agent_tools": profile.agent_tools if routed else [],
        "tools": list(mapping.get("tools") or []) if routed else [],
        "fixtures": dict(mapping.get("fixtures") or {}) if routed else {},
        "aliases": dict(mapping.get("aliases") or {}) if routed else {},
        "rendering": dict(mapping.get("rendering") or {}),
        "prompt_changes": [],
    }


def seal_binding(
    profile: AgentProfile,
    mode: BindingMode,
    mapping: Mapping[str, Any] | None,
    *,
    environment_root: Any,
    environment_tools: Sequence[ToolDefinition] | None = None,
) -> Any:
    return seal_agent_binding(
        binding_fields(profile, mode, mapping),
        environment_root=environment_root,
        agent_tools=[one.name for one in profile.admitted],
        environment_tools=environment_tools,
    )
