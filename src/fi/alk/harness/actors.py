"""World-side actors: people and systems that act in the world through its own actions.

An environment lists them once in `actors.json`. A scenario includes one by id with the actions it
takes in that scenario. What an actor may do is world data (`permitted_actions`), checked where
every action enters the world, never in a prompt.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# One switch for every actor, on the caller's side or in the world.
from .sub_actor import sub_actors_enabled as actors_enabled  # noqa: F401

ACTORS_FILE = "actors.json"
ACTORS_SCHEMA = "futureagi.actors.v1"
# The agent under test in the action log; no actor may take its id.
AGENT = "agent"


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
    name: str = Field(min_length=1)
    # Open sets: an unknown kind or play is kept and shown; only running an unknown play is refused.
    kind: str = "person"
    side: Literal["caller", "world"] = "world"
    role: str = ""
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
    play: str = "scheduled"
    # World actions, `<service>.<action>`; empty means this actor may do nothing in the world.
    permitted_actions: list[str] = Field(default_factory=list)
    # Who this actor is in each service, so the agent reaching one of these reaches this actor.
    identities: dict[str, str] = Field(default_factory=dict)
    on_behalf_of: str | None = None
    activation: Activation | None = None
    loudness_ratio: float | None = Field(default=None, gt=0, le=1)
    # Who last set the record and each field; written by the platform, never taken from a client.
    provenance: dict[str, Any] = Field(default_factory=dict)
    # Reserved for a real peer agent standing in for a simulated one.
    card: dict[str, Any] | None = None

    @field_validator("id")
    @classmethod
    def _not_the_agent(cls, value: str) -> str:
        if value == AGENT:
            raise ValueError(f"{AGENT!r} is the agent under test, never an actor")
        return value


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
            if one.on_behalf_of is not None and one.on_behalf_of not in known - {one.id}:
                raise ValueError(f"{one.id} acts on behalf of an actor that is not listed")
            if one.parent is not None and one.parent not in known - {one.id}:
                raise ValueError(f"{one.id} is brought in by an actor that is not listed")
            fallback = one.activation.fallback if one.activation is not None else "silent"
            if fallback not in ("silent", "decline") and fallback not in known - {one.id}:
                raise ValueError(f"{one.id} falls back to an actor that is not listed")
        parents = {one.id: one.parent for one in actors}
        for one in actors:
            seen, at = {one.id}, one.parent
            while at is not None:
                if at in seen:
                    raise ValueError(f"{one.id} is in a cycle of actors bringing each other in")
                seen.add(at)
                at = parents.get(at)
        return actors

    def by_id(self) -> dict[str, Actor]:
        return {one.id: one for one in self.actors}


class Trigger(BaseModel):
    # A misspelt field would otherwise leave an action firing at the start of the call.
    model_config = ConfigDict(extra="forbid")

    kind: Literal["always", "after_seconds", "on_tool", "contacted", "probability"] = "always"
    seconds: float = Field(default=0.0, ge=0)
    tool: str = ""
    on_call: int = Field(default=1, ge=1)
    p: float = Field(default=1.0, ge=0, le=1)


class ScheduledAction(BaseModel):
    action: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    trigger: Trigger = Field(default_factory=Trigger)


class ScenarioActor(BaseModel):
    """An actor in one scenario: what it is told to do there, what it knows there, when it comes in, and the actions
    it takes, in order."""

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
    (Path(root) / ACTORS_FILE).write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


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
}


def world_action_names(contract: dict[str, Any]) -> list[str]:
    """The world's actions an actor may be given, named by `WORLD_ACTIONS` from a contract's tool entrypoints."""
    names = set()
    for entry in contract.get(WORLD_ACTIONS["from"]) or []:
        route = str((entry or {}).get(WORLD_ACTIONS["route"]) or "").strip("/")
        if route:
            names.add(route.replace("/", WORLD_ACTIONS["separator"]))
    return sorted(names)


# Fields naming another listed actor, with the other values each also takes; `parent` may not form a cycle.
REFERENCES = {
    "parent": [],
    "on_behalf_of": [],
    "activation.fallback": ["silent", "decline"],
}
ACYCLIC = ["parent"]


def actor_rules() -> dict[str, Any]:
    """Everything this release decides about actors, sealed into each bundle so the platform reads the rules of the
    release that built it instead of keeping its own copy."""
    from fi.simulate.simulation.sub_actors import CHANNELS, DETAILS_MARK, LOUDNESS_RANGE

    from .sub_actor import SubActor

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
        # Fields of a sub-actor that may never equal the caller's in a scenario that includes it.
        "distinct_from_caller": ["name"],
        # The call channels a sub-actor can speak on, each with what it does.
        "channels": {
            name: str(one.get("about") or "") for name, one in CHANNELS.items()
        },
        # The format tag each actor file is written with.
        "formats": {ACTORS_FILE: ACTORS_SCHEMA},
        "records": {
            "actors.json": Actor.model_json_schema(),
            "sub_actors.json": SubActor.model_json_schema(),
        },
    }


def actor_input_schema() -> dict[str, Any]:
    """The builder chat's `add_actor` input: every field of the record, from the model, except who last set it."""
    body = Actor.model_json_schema()
    body["properties"].pop("provenance", None)
    body["required"] = ["id", "name"]
    return body


def write_actor_rules(bundle: Path) -> None:
    (Path(bundle) / ACTOR_RULES_FILE).write_text(
        json.dumps(actor_rules(), indent=2) + "\n", encoding="utf-8"
    )
