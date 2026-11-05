"""World-side actors: people and systems that act in the world through its own actions.

An environment lists them once in `actors.json`. A scenario includes one by id with the actions it
takes in that scenario. What an actor may do is world data (`permitted_actions`), checked where
every action enters the world, never in a prompt.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .call_actor import NAME_MAX_LENGTH, ROLE_MAX_LENGTH

ACTORS_FILE = "actors.json"
ACTORS_SCHEMA = "futureagi.actors.v1"
# The agent under test in the action log; no actor may take its id.
AGENT = "agent"
# The environment's caller, an actor like any other; each scenario still writes its persona for itself.
CALLER = "caller"


class Constraints(BaseModel):
    """What an actor may agree to, must keep to itself, hands over on, and is done at."""

    model_config = ConfigDict(extra="forbid")

    may_agree: list[str] = Field(default_factory=list)
    private: list[str] = Field(default_factory=list)
    escalate_when: list[str] = Field(default_factory=list)
    stop_when: list[str] = Field(default_factory=list)


class ResponseTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["immediate", "delayed"] = "immediate"
    seconds: float = Field(default=0.0, ge=0)


class Behaviour(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: str = ""
    response_timing: ResponseTiming = Field(default_factory=ResponseTiming)


class Activation(BaseModel):
    """When an actor comes in by default, how long it waits, and what happens when it does not answer."""

    model_config = ConfigDict(extra="forbid")

    trigger: "Trigger" = Field(default_factory=lambda: Trigger())
    timeout_seconds: float = Field(default=0.0, ge=0)
    # `silent`, `decline`, or another actor's id who answers instead.
    fallback: str = "silent"


class Actor(BaseModel):
    id: str = Field(
        pattern=r"^[a-z0-9_]{1,64}$",
        description="1 to 64 lowercase letters, digits or underscores",
    )
    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    # An open set: an unknown kind is kept and shown.
    kind: str = "person"
    side: Literal["caller", "world"] = "world"
    role: str = Field(default="", max_length=ROLE_MAX_LENGTH)
    # Who brings this actor in.
    parent: str | None = None
    persona: dict[str, Any] = Field(default_factory=dict)
    goal: str = ""
    constraints: Constraints = Field(default_factory=Constraints)
    behaviour: Behaviour = Field(default_factory=Behaviour)
    # What it can read: `{"kind": "facts", "text"}`, `{"kind": "records", "store", "collection", "where"?}`,
    # `{"kind": "files", "paths"}`.
    knowledge: list[dict[str, Any]] = Field(default_factory=list)
    # The prompt it plays from, written for this environment; empty is the surface's default.
    prompt: str = ""
    surface: str = "world.records"
    # World actions, `<service>.<action>`; empty means this actor may do nothing in the world.
    permitted_actions: list[str] = Field(default_factory=list)
    # Who this actor is in each service, so the agent reaching one of these reaches this actor.
    identities: dict[str, str] = Field(default_factory=dict)
    on_behalf_of: str | None = None
    activation: Activation | None = None
    # How the actor is heard or seen on a call when it speaks in one: beside the person, taking over from them, or
    # answering before them. Empty: it does not speak on calls, it acts through the world.
    channel: str | None = None
    loudness_ratio: float | None = Field(default=None, gt=0, le=1)
    # Who last set the record and each field; written by the platform, never taken from a client.
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _not_the_agent(cls, value: str) -> str:
        if value == AGENT:
            raise ValueError(f"{AGENT!r} is the agent under test, never an actor")
        return value

    @model_validator(mode="after")
    def _one_contract(self) -> "Actor":
        """Every actor is checked the same way; one that speaks on calls also needs a known channel and a role."""
        from fi.simulate.simulation.call_actors import CHANNELS

        from .scenario import Persona

        if self.persona:
            Persona.model_validate(self.persona)
            named = str(self.persona.get("name") or "").strip()
            if named and named != self.name:
                raise ValueError(f"its persona is named {named!r} but the actor is {self.name!r}")
        hidden = set(EVALUATOR_ONLY["files"])
        for one in self.knowledge:
            named = [str(path) for path in one.get("paths") or []] if one.get("kind") == "files" else []
            if any(Path(path).name in hidden for path in named):
                raise ValueError("an actor never sees what only the evaluator reads: " + ", ".join(sorted(hidden)))
        if self.channel is not None:
            if self.channel not in CHANNELS:
                raise ValueError(f"channel {self.channel!r} is not one of {sorted(CHANNELS)}")
            if not self.role.strip():
                raise ValueError("an actor who speaks on calls needs a role: who they are to the person")
        return self


class ActorList(BaseModel):
    actors: list[Actor] = Field(default_factory=list)

    @field_validator("actors")
    @classmethod
    def _unique(cls, actors: list[Actor]) -> list[Actor]:
        ids = [one.id for one in actors]
        if len(ids) != len(set(ids)):
            raise ValueError("each actor id appears once")
        known = set(ids)
        for one in actors:
            if one.on_behalf_of is not None and one.on_behalf_of not in known - {
                one.id
            }:
                raise ValueError(
                    f"{one.id} acts on behalf of an actor that is not listed"
                )
            if one.parent is not None and one.parent not in known - {one.id}:
                raise ValueError(
                    f"{one.id} is brought in by an actor that is not listed"
                )
            fallback = (
                one.activation.fallback if one.activation is not None else "silent"
            )
            if fallback not in ("silent", "decline") and fallback not in known - {
                one.id
            }:
                raise ValueError(f"{one.id} falls back to an actor that is not listed")
        parents = {one.id: one.parent for one in actors}
        for one in actors:
            seen, at = {one.id}, one.parent
            while at is not None:
                if at in seen:
                    raise ValueError(
                        f"{one.id} is in a cycle of actors bringing each other in"
                    )
                seen.add(at)
                at = parents.get(at)
        return actors

    def by_id(self) -> dict[str, Actor]:
        return {one.id: one for one in self.actors}


class Trigger(BaseModel):
    # A misspelt field would otherwise leave an action firing at the start of the call.
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "always",
        "after_seconds",
        "on_tool",
        "contacted",
        "probability",
        "mid_journey",
        "on_keyword",
    ] = "always"
    seconds: float = Field(default=0.0, ge=0)
    tool: str = ""
    on_call: int = Field(default=1, ge=1)
    p: float = Field(default=1.0, ge=0, le=1)
    # mid_journey: the person's turn from which the actor may speak; on_keyword: the words that bring it in.
    turn: int = Field(default=0, ge=0)
    words: list[str] = Field(default_factory=list)


class ScheduledAction(BaseModel):
    action: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    trigger: Trigger = Field(default_factory=Trigger)


class ScenarioActor(BaseModel):
    """An actor in one scenario: what it says there and when it comes in to say it (for an actor that speaks on
    calls), what it knows there, and the actions it takes in the world, in order."""

    id: str
    instruction: str = ""
    knowledge: list[dict[str, Any]] = Field(default_factory=list)
    # Overrides the actor's own activation for this scenario.
    trigger: Trigger | None = None
    actions: list[ScheduledAction] = Field(default_factory=list)


def load_actors(root: Path) -> ActorList:
    path = Path(root) / ACTORS_FILE
    if not path.is_file():
        return ActorList()
    return ActorList.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save_actors(root: Path, listing: ActorList) -> None:
    body = {"schema": ACTORS_SCHEMA, **listing.model_dump(exclude_none=True)}
    (Path(root) / ACTORS_FILE).write_text(
        json.dumps(body, indent=2) + "\n", encoding="utf-8"
    )


def _literal(value: str | None) -> str:
    return "NULL" if value is None else "'" + value.replace("'", "''") + "'"


def actors_seed(listing: ActorList) -> str:
    """The world's own table of who may do what, read by the tool proxy on every actor request."""
    rows = "".join(
        "INSERT INTO _alk_actors(id, kind, side, role, permitted_actions, identities, on_behalf_of) "
        f"VALUES ({_literal(one.id)}, {_literal(one.kind)}, {_literal(one.side)}, {_literal(one.role)}, "
        f"{_literal(json.dumps(one.permitted_actions))}::jsonb, {_literal(json.dumps(one.identities))}::jsonb, "
        f"{_literal(one.on_behalf_of)}) ON CONFLICT (id) DO NOTHING;\n"
        for one in listing.actors
    )
    return ACTORS_DDL + rows


ACTORS_DDL = (
    "CREATE TABLE IF NOT EXISTS _alk_actors (id text PRIMARY KEY, kind text, side text, role text, "
    "permitted_actions jsonb NOT NULL DEFAULT '[]', identities jsonb NOT NULL DEFAULT '{}', on_behalf_of text);\n"
)


Activation.model_rebuild()


ACTOR_RULES_FILE = "actor_rules.json"
ACTOR_RULES_SCHEMA = "futureagi.actor_rules.v1"
# What only the grader may read: no actor is ever given it.
EVALUATOR_ONLY = {
    "scenario_fields": [
        "solution",
        "alternatives",
        "sub_goals",
        "tests",
        "setup_code",
        "ready_code",
        "coverage",
    ],
    "files": ["sub_goals.json", "certification.json", "runtime-validation.json"],
}
# What an actor's brief is built from: its own record, its entry in the scenario, and the caller's situation without
# the caller's own details.
BRIEF_INPUTS = [
    ["actor", "name"],
    ["actor", "role"],
    ["actor", "persona"],
    ["actor", "goal"],
    ["actor", "constraints"],
    ["actor", "behaviour"],
    ["actor", "prompt"],
    ["actor", "surface"],
    ["actor", "knowledge"],
    ["scenario", "instruction"],
    ["scenario", "knowledge"],
    ["caller", "situation"],
]
# Fields of one actor that no other actor is given.
PRIVATE_TO_ACTOR = ["goal", "prompt", "instruction", "actions"]
# How the world names an action: the route a tool's entrypoint calls, with `/` read as `.` (the tool proxy's rule).
WORLD_ACTIONS = {
    "from": "tool_entrypoints",
    "route": "endpoint",
    "tool": "tool",
    "separator": ".",
    # Only routes an actor's action can be sent to: a method it sends, and no path part to fill in.
    "method": "method",
    "methods": ["POST"],
    "placeholder": "{",
}


def world_action_names(contract: dict[str, Any]) -> list[str]:
    """The world's actions an actor may be given, named by `WORLD_ACTIONS` from a contract's tool entrypoints."""
    names = set()
    for entry in contract.get(WORLD_ACTIONS["from"]) or []:
        route = str((entry or {}).get(WORLD_ACTIONS["route"]) or "").strip("/")
        method = str((entry or {}).get(WORLD_ACTIONS["method"]) or "POST").upper()
        if (
            route
            and method in WORLD_ACTIONS["methods"]
            and WORLD_ACTIONS["placeholder"] not in route
        ):
            names.add(route.replace("/", WORLD_ACTIONS["separator"]))
    return sorted(names)


def environment_actions(
    destination: Path, contract: dict[str, Any] | None = None
) -> list[str]:
    """The world's actions in the environment's written contract, or in `contract` before it is written."""
    path = Path(destination) / "contract.json"
    if path.is_file():
        return world_action_names(json.loads(path.read_text(encoding="utf-8")))
    return world_action_names(contract) if contract is not None else []


# Fields naming another listed actor, with the other values each also takes; `parent` may not form a cycle.
REFERENCES = {
    "parent": [],
    "on_behalf_of": [],
    "activation.fallback": ["silent", "decline"],
}
ACYCLIC = ["parent"]


def actor_brief(
    actor: dict[str, Any],
    *,
    entry: dict[str, Any] | None = None,
    scenario: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """What an actor is given, one item per `BRIEF_INPUTS` line with its source: the one view the runtime plays from
    (lines on a call, live turns, history) and the platform shows as what the actor sees. Evaluator-only fields are
    never among the inputs; the caller's situation reaches anyone else only up to its details."""
    from fi.simulate.simulation.call_actors import known_to_others

    items = []
    for source, field in BRIEF_INPUTS:
        if source == "actor":
            value = actor.get(field)
        elif source == "scenario":
            value = (entry or {}).get(field)
        else:
            value = (
                known_to_others(str(scenario.get("instruction") or ""))
                if scenario is not None and actor.get("id") != CALLER
                else None
            )
        if value in (None, "", [], {}):
            continue
        items.append({"input": f"{source}.{field}", "value": value, "source": source})
    return items


def brief_text(items: list[dict[str, Any]]) -> str:
    """An actor's brief as the lines it is given."""
    labels = {
        "actor.name": "You are",
        "actor.role": "Who you are to the others",
        "actor.persona": "How you come across",
        "actor.goal": "What you want",
        "actor.constraints": "What you will not do",
        "actor.behaviour": "How you behave",
        "actor.prompt": "Your instructions",
        "actor.surface": "Where you act",
        "actor.knowledge": "What you know",
        "scenario.instruction": "What you do here",
        "scenario.knowledge": "What you know here",
        "caller.situation": "What is going on (the other person's situation, not yours)",
    }
    lines = []
    for item in items:
        value = item["value"]
        if item["input"] in ("actor.knowledge", "scenario.knowledge"):
            value = "\n".join(_knowledge_line(one) for one in value)
        elif not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        lines.append(f"{labels.get(item['input'], item['input'])}: {value}")
    return "\n".join(lines)


def _knowledge_line(one: Any) -> str:
    if not isinstance(one, dict):
        return f"- {one}"
    kind = one.get("kind")
    if kind == "facts":
        return f"- {one.get('text') or ''}"
    if kind == "files":
        return "- You can read these files: " + ", ".join(map(str, one.get("paths") or []))
    if kind == "records":
        where = f" where {one['where']}" if one.get("where") else ""
        return f"- You can read the {one.get('collection') or 'records'} records in {one.get('store') or 'the world'}{where}"
    return "- " + json.dumps(one, ensure_ascii=False, sort_keys=True)


def with_caller_actor(doc: dict[str, Any], root: Path) -> dict[str, Any]:
    """A scenario as the caller plays it, when the environment lists the caller as an actor: its persona under the
    scenario's own, and its goal, constraints, behaviour and knowledge after the scenario's instruction, so they are
    the caller's alone (anyone else is given the situation only up to the caller's details)."""
    caller = load_actors(root).by_id().get(CALLER)
    if caller is None:
        return doc
    own = [
        item
        for item in actor_brief(caller.model_dump(exclude_none=True))
        if item["input"] in ("actor.goal", "actor.constraints", "actor.behaviour", "actor.knowledge", "actor.prompt")
    ]
    instruction = str(doc.get("instruction") or "")
    return {
        **doc,
        "persona": {**caller.persona, **(doc.get("persona") or {})},
        "instruction": instruction + ("\n\n" + brief_text(own) if own else ""),
    }


def actor_rules() -> dict[str, Any]:
    """Everything this release decides about actors, sealed into each bundle so the platform reads the rules of the
    release that built it instead of keeping its own copy."""
    from fi.simulate.simulation.call_actors import CHANNELS, DETAILS_MARK, LOUDNESS_RANGE

    from .call_actor import channel_modalities

    return {
        "schema": ACTOR_RULES_SCHEMA,
        "evaluator_only": EVALUATOR_ONLY,
        "brief_inputs": BRIEF_INPUTS,
        "private_to_actor": PRIVATE_TO_ACTOR,
        # The caller's situation as given to anyone else: its instruction up to this mark.
        "situation_before": DETAILS_MARK,
        "reserved_ids": [AGENT],
        "references": REFERENCES,
        "acyclic": ACYCLIC,
        "world_actions": WORLD_ACTIONS,
        # A background voice's level as a share of the caller's: which channels have one, and the range a call draws
        # from when none is set.
        "loudness": {
            "channels": ["audio_mix"],
            "automatic": [round(LOUDNESS_RANGE[0], 3), round(LOUDNESS_RANGE[1], 3)],
        },
        # Fields of an actor speaking on a call that may never equal the caller's in a scenario that includes it.
        "distinct_from_caller": ["name"],
        # The ways an actor can speak on a call, each with what it does.
        "channels": {
            name: str(one.get("about") or "") for name, one in CHANNELS.items()
        },
        # The kinds of agent each channel reaches; an actor on a channel that does not reach its agent is refused.
        "channel_modalities": channel_modalities(),
        # Fields of an actor that, when set, equal another of its fields.
        "equal_when_set": {"persona.name": "name"},
        # The format tag each actor file is written with.
        "formats": {ACTORS_FILE: ACTORS_SCHEMA},
        "records": {ACTORS_FILE: actor_record_schema()},
    }


def actor_record_schema() -> dict[str, Any]:
    """An actor's JSON schema, with the channels an actor can speak on named."""
    from fi.simulate.simulation.call_actors import CHANNELS

    from .scenario import Persona

    body = Actor.model_json_schema()
    fields = body["properties"]
    fields["channel"] = {
        "anyOf": [{"type": "string", "enum": sorted(CHANNELS)}, {"type": "null"}],
        "default": None,
    }
    persona = Persona.model_json_schema()
    fields["persona"] = {**persona, "additionalProperties": True}
    # One that speaks on calls says who they are to the person.
    body["if"] = {"properties": {"channel": {"type": "string"}}, "required": ["channel"]}
    body["then"] = {"properties": {"role": {"minLength": 1}}, "required": ["role"]}
    return body


def actor_input_schema() -> dict[str, Any]:
    """The builder chat's `add_actor` input: every field of the record, from the model, except who last set it."""
    body = actor_record_schema()
    body["properties"].pop("provenance", None)
    body["required"] = ["id", "name"]
    return body


def write_actor_rules(bundle: Path) -> None:
    (Path(bundle) / ACTOR_RULES_FILE).write_text(
        json.dumps(actor_rules(), indent=2) + "\n", encoding="utf-8"
    )
