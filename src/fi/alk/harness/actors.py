"""World-side actors: people and systems that act in the world through its own actions.

An environment lists them once in `actors.json`. A scenario includes one by id with the actions it
takes in that scenario. What an actor may do is world data (`permitted_actions`), checked where
every action enters the world, never in a prompt.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ACTORS_FILE = "actors.json"
ACTORS_SCHEMA = "futureagi.actors.v1"
# The agent under test in the action log; no actor may take its id.
AGENT = "agent"
# Same switch as the caller-side sub-actors: off, no actor acts.
SWITCH = "HARNESS_SUB_ACTORS"


def actors_enabled() -> bool:
    return os.environ.get(SWITCH, "").strip().lower() in {"1", "true", "yes", "on"}


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
    id: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
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


def actor_problems(
    listing: ActorList,
    *,
    surfaces: set[str],
    actions: set[str],
    collections: set[str] = frozenset(),
) -> list[str]:
    """Every structural problem with an environment's actors against what it publishes: the surfaces this agent can
    be reached on, the world's action catalogue (`<service>.<action>`) and its record collections
    (`<store>.<collection>`). Each is one sentence naming the fix; none judges what an actor should do."""
    problems = []
    by_id = listing.by_id()
    for one in listing.actors:
        if one.surface not in surfaces:
            problems.append(
                f"{one.id} reaches the agent through {one.surface!r}, which this agent does not have; "
                f"use one of {', '.join(sorted(surfaces)) or 'none'}."
            )
        for action in one.permitted_actions:
            if action not in actions:
                problems.append(f"{one.id} may take {action!r}, which is not a world action; name one from the catalogue.")
        for source in one.knowledge:
            if source.get("kind") == "records":
                collection = f"{source.get('store')}.{source.get('collection')}"
                if collection not in collections:
                    problems.append(f"{one.id} reads {collection!r}, which the world does not hold.")
        trigger = one.activation.trigger if one.activation is not None else None
        if trigger is not None and trigger.kind == "on_tool" and trigger.tool not in actions:
            problems.append(f"{one.id} comes in on {trigger.tool!r}, which is not a world action.")
    # A fallback chain that comes back to where it started never answers.
    for one in listing.actors:
        seen, at = {one.id}, one
        while at.activation is not None and at.activation.fallback in by_id:
            nxt = at.activation.fallback
            if nxt in seen:
                problems.append(f"{one.id}'s fallbacks come back to {nxt}; end the chain with silent or decline.")
                break
            seen.add(nxt)
            at = by_id[nxt]
    return problems


EVALUATOR_ONLY_FILE = "evaluator_only.json"
# What only the grader may read: no actor brief and no actor view ever includes it. One declaration, read by the brief
# and the view alike, so a field added here is hidden everywhere at once.
EVALUATOR_ONLY = {
    "scenario_fields": ["solution", "alternatives", "sub_goals", "tests", "setup_code", "ready_code", "coverage"],
    "files": ["sub_goals.json", "certification.json", "runtime-validation.json"],
}
# What an actor's brief is built from, and nothing else: its own record, the surface it is on, its knowledge, its
# scenario entry, and the part of the caller's instruction before the caller's own details.
BRIEF_INPUTS = (
    ("actor", "name"),
    ("actor", "role"),
    ("actor", "persona"),
    ("actor", "goal"),
    ("actor", "constraints"),
    ("actor", "behaviour"),
    ("actor", "prompt"),
    ("actor", "surface"),
    ("actor", "knowledge"),
    ("scenario", "instruction"),
    ("scenario", "knowledge"),
    ("caller", "situation"),
)
# Another actor's private material, never in this one's brief.
_OTHERS_PRIVATE = ("goal", "prompt", "instruction", "actions")


def write_evaluator_only(bundle: Path) -> None:
    (Path(bundle) / EVALUATOR_ONLY_FILE).write_text(json.dumps(EVALUATOR_ONLY, indent=2) + "\n", encoding="utf-8")


def actor_view(
    listing: ActorList,
    actor_id: str,
    *,
    scenario: dict[str, Any] | None = None,
    known_to_others: Any = None,
) -> dict[str, Any]:
    """What one actor is given (each input with its value and where it comes from) and what it never sees. Built from
    `BRIEF_INPUTS` alone, so the view and the brief cannot disagree. `known_to_others` cuts the caller's instruction
    before the caller's own details."""
    actor = listing.by_id()[actor_id]
    record = actor.model_dump()
    entry = next(
        (
            ScenarioActor.model_validate(one).model_dump()
            for one in (scenario or {}).get("actors") or []
            if isinstance(one, dict) and one.get("id") == actor_id
        ),
        None,
    )
    sees = []
    for source, field in BRIEF_INPUTS:
        if source == "actor":
            value = record.get(field)
        elif source == "scenario":
            if entry is None:
                continue
            value = entry.get(field)
        else:
            if scenario is None or actor.side == "caller" or known_to_others is None:
                continue
            value = known_to_others(str(scenario.get("instruction") or ""))
        if value in (None, "", [], {}):
            continue
        sees.append({"input": f"{source}.{field}", "value": value, "source": source})
    never = [f"scenario.{name}" for name in EVALUATOR_ONLY["scenario_fields"]]
    never += [f"file.{name}" for name in EVALUATOR_ONLY["files"]]
    never += [f"{other.id}.{name}" for other in listing.actors if other.id != actor_id for name in _OTHERS_PRIVATE]
    return {"actor_id": actor_id, "sees": sees, "never_sees": never}
