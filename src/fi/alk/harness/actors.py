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


class Actor(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    name: str = Field(min_length=1)
    kind: str = "person"
    side: Literal["caller", "world"] = "world"
    role: str = ""
    surface: str = "world.records"
    play: str = "scheduled"
    # World actions, `<service>.<action>`; empty means this actor may do nothing in the world.
    permitted_actions: list[str] = Field(default_factory=list)
    # Who this actor is in each service, so the agent reaching one of these reaches this actor.
    identities: dict[str, str] = Field(default_factory=dict)
    on_behalf_of: str | None = None

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
    """An actor in one scenario, with the actions it takes there, in order."""

    id: str
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
