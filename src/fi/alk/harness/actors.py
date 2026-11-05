"""World-side actors: people and systems that act in the world through its own actions.

An environment lists them once in `actors.json`. A scenario includes one by id with the actions it
takes in that scenario. What an actor may do is world data (`permitted_actions`), checked where
every action enters the world, never in a prompt.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
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
# The idempotency key of a message replayed before the run: `<trial>:<actor>:h<n>`. Matched at its end only, so an
# actor whose id starts with `h` is never read as one.
_PRE_RUN_KEY = re.compile(r":h[0-9]+$")


def is_pre_run(key: object) -> bool:
    """Whether an action-log row was written before the run started (a pre-run session's message)."""
    return bool(_PRE_RUN_KEY.search(str(key or "")))


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
    # `silent`, `decline`, one of the actor's own permitted actions taken for it, or another actor's id who answers
    # instead.
    fallback: str = "silent"


class Limits(BaseModel):
    """Every limit on actors, as a setting: on the environment's list, overridable per scenario. `None` is no limit.
    A value left unset takes the default for the channel the run uses (a phone line carries at most two voices)."""

    model_config = ConfigDict(extra="forbid")

    max_active: int | None = Field(default=None, ge=1)
    caller_takes_back_after_turns: int | None = Field(default=None, ge=1)
    actor_seconds_per_call: float | None = Field(default=None, gt=0)
    max_messages: int | None = Field(default=None, ge=1)
    # The model actors are played on; unset is the caller simulator's own.
    model: str | None = Field(default=None, max_length=120)

    def merged(self, override: "Limits | None") -> "Limits":
        """These limits with a scenario's own values on top."""
        if override is None:
            return self
        return self.model_copy(update=override.model_dump(exclude_unset=True))

    def resolved(self, modality: str) -> dict[str, Any]:
        """The limits in force for one run: what is set, else the channel's default."""
        from fi.simulate.simulation.call_actors import LIMIT_DEFAULTS

        spoken = modality in ("voice", "audio")
        defaults = {
            "max_active": LIMIT_DEFAULTS["max_active"] if spoken else None,
            "caller_takes_back_after_turns": LIMIT_DEFAULTS["caller_takes_back_after_turns"] if spoken else None,
            "actor_seconds_per_call": LIMIT_DEFAULTS["actor_seconds_per_call"] if spoken else None,
            "max_messages": LIMIT_DEFAULTS["max_messages"],
            "model": None,
        }
        given = self.model_dump(exclude_unset=True)
        return {key: given.get(key, value) for key, value in defaults.items()}



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
    # How the actor is played in the world: `mocked` takes only the actions each scenario schedules for it, `live`
    # decides its own actions from its brief when the agent reaches it (or its activation trigger fires), `both` does
    # the two.
    play: Literal["mocked", "live", "both"] = "mocked"
    # How the actor is heard or seen on a call when it speaks in one: beside the person, taking over from them, or
    # answering before them. Empty: it does not speak on calls, it acts through the world.
    channel: str | None = None
    loudness_ratio: float | None = Field(default=None, gt=0, le=1)
    # The model this actor is played on; unset is the environment's choice, else the caller simulator's.
    model: str | None = Field(default=None, max_length=120)
    # How many turns a live actor takes and lines a speaking one says in a trial; unset is no limit.
    max_turns: int | None = Field(default=None, ge=1)
    max_lines: int | None = Field(default=None, ge=1)
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
        for one in self.knowledge:
            named = [str(path) for path in one.get("paths") or []] if one.get("kind") == "files" else []
            refused = [path for path in named if not knowledge_file_allowed(path)]
            if refused:
                raise ValueError(
                    "an actor never sees what only the evaluator reads or the harness's own files: " + ", ".join(refused)
                )
        if self.channel is not None:
            if self.channel not in CHANNELS:
                raise ValueError(f"channel {self.channel!r} is not one of {sorted(CHANNELS)}")
            if not self.role.strip():
                raise ValueError("an actor who speaks on calls needs a role: who they are to the person")
        return self


class ActorList(BaseModel):
    actors: list[Actor] = Field(default_factory=list)
    limits: Limits = Field(default_factory=Limits)

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
            if (
                fallback not in ("silent", "decline")
                and fallback not in known - {one.id}
                and fallback not in one.permitted_actions
            ):
                raise ValueError(
                    f"{one.id} falls back to {fallback!r}, which is neither an actor on the list nor one of its own "
                    "permitted actions"
                )
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
        "conditional",
    ] = "always"
    seconds: float = Field(default=0.0, ge=0)
    tool: str = ""
    on_call: int = Field(default=1, ge=1)
    p: float = Field(default=1.0, ge=0, le=1)
    # mid_journey: the person's turn from which the actor may speak; on_keyword: the words that bring it in.
    turn: int = Field(default=0, ge=0)
    words: list[str] = Field(default_factory=list)
    # conditional: present once a record of `collection` matching every `where` field exists in the world.
    collection: str = ""
    where: dict[str, Any] = Field(default_factory=dict)


class ScheduledAction(BaseModel):
    action: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    trigger: Trigger = Field(default_factory=Trigger)


class Reference(BaseModel):
    """An argument filled at replay from an earlier message's result: the id a thread reply needs is only known once
    the world has taken the message it replies to."""

    model_config = ConfigDict(extra="forbid")

    message: int = Field(ge=1)
    field: str = Field(min_length=1)


class ThreadMessage(BaseModel):
    """One message an actor wrote before the run (a post, a reply, an email, a note): one of its permitted actions
    with its arguments, and the arguments that refer to an earlier message of the same session."""

    model_config = ConfigDict(extra="forbid")

    actor: str = Field(min_length=1)
    action: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    refers: dict[str, Reference] = Field(default_factory=dict)


# Kept for scenarios written before pre-run sessions: one typed entry is one message.
HistoryEntry = ThreadMessage


class PreRunSession(BaseModel):
    """A conversation that happened before the run, in one place of the world, generated by the actors in it from
    their briefs. `about` is the situation; `thread` the messages the actors wrote, replayed into the world before
    every trial (or written afresh inside each trial when `per_trial`)."""

    model_config = ConfigDict(extra="forbid")

    actors: list[str] = Field(default_factory=list)
    # The action that writes into the place and the arguments naming the place: a channel, a thread, a ticket.
    where: dict[str, Any] = Field(default_factory=dict)
    about: str = ""
    per_trial: bool = False
    max_messages: int | None = Field(default=None, ge=1)
    thread: list[ThreadMessage] = Field(default_factory=list)
    # What the thread was generated from; a different fingerprint means it is stale and is written again.
    fingerprint: str = ""


def session_fingerprint(session: PreRunSession, actors: dict[str, "Actor"]) -> str:
    """What a session's thread depends on: its situation, its place and the records of the actors in it."""
    material = {
        "about": session.about,
        "where": session.where,
        "actors": [
            actors[one].model_dump(mode="json", exclude={"provenance"}) if one in actors else {"id": one}
            for one in sorted(session.actors)
        ],
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()[:16]


class Fault(BaseModel):
    """A timeout the world gives the agent on one call of one action: before commit the world never sees it; after
    commit it does and the agent never hears back. Graders read it; no actor or agent ever does."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1)
    mode: Literal["timeout_before_commit", "timeout_after_commit"]
    on_call: int = Field(default=1, ge=1)
    delay_seconds: float = Field(default=30.0, ge=0, le=30)


class StartsWith(BaseModel):
    """What starts an event-started run: one actor's message to the agent, with no caller at all."""

    model_config = ConfigDict(extra="forbid")

    actor: str = Field(min_length=1)
    text: str = Field(min_length=1)


class ScenarioActor(BaseModel):
    """An actor in one scenario: what it says there and when it comes in to say it (for an actor that speaks on
    calls), what it knows there, and the actions it takes in the world, in order."""

    id: str
    instruction: str = ""
    knowledge: list[dict[str, Any]] = Field(default_factory=list)
    # Overrides the actor's own activation for this scenario.
    trigger: Trigger | None = None
    actions: list[ScheduledAction] = Field(default_factory=list)


def cycles(actors: dict[str, "Actor"], entries: list["ScenarioActor"]) -> list[list[str]]:
    """Actors that bring each other in: each one comes in once a record exists that only the next may write. A cycle
    is a scenario problem refused when the scenario is written, never a loop discovered during a run."""
    triggered_by: dict[str, set[str]] = {}
    for entry in entries:
        actor = actors.get(entry.id)
        if actor is None:
            continue
        trigger = entry.trigger or (actor.activation.trigger if actor.activation else None)
        # Only a record can bring an actor in off another actor's action: `on_tool` and `contacted` read the agent's
        # own actions alone, so they never chain from one actor to the next.
        if trigger is None or trigger.kind != "conditional" or not trigger.collection:
            continue
        names = {f"{trigger.collection}.change", f"{trigger.collection}.put"}
        triggered_by[entry.id] = {
            other.id
            for other in actors.values()
            if other.id != entry.id
            and any(name in (one, one.rsplit(".", 1)[-1]) for one in other.permitted_actions for name in names)
        }
    found: list[list[str]] = []
    for start in triggered_by:
        path, at = [start], start
        while True:
            nexts = sorted(triggered_by.get(at, set()) & set(triggered_by))
            if not nexts:
                break
            at = nexts[0]
            if at == start:
                if sorted(path) not in [sorted(one) for one in found]:
                    found.append(path)
                break
            if at in path:
                break
            path.append(at)
    return found


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
        "faults",
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
    # Who the actor is in each service, so a live actor can name itself where a world's tool asks (`actor_id`).
    ["actor", "identities"],
    ["scenario", "instruction"],
    ["scenario", "knowledge"],
    ["caller", "situation"],
]
# Fields of one actor that no other actor is given.
PRIVATE_TO_ACTOR = ["goal", "prompt", "instruction", "actions"]
# Files an actor's knowledge may never name: what only the evaluator reads, and the harness's own files, which are how
# the environment is built rather than anything a person in it would read.
NOT_KNOWLEDGE = {
    "files": [
        "contract.json",
        "manifest.json",
        "actors.json",
        "actor_rules.json",
        "credentials.json",
        "world.py",
        "world.sqlite",
        "simulator_prompt.md",
        "source-data-invariants.json",
        "scenarios.json",
    ],
    "prefixes": ["generic-harness/", "handlers/", "seed/", "scenarios/", "checks/", "."],
}


def knowledge_file_allowed(path: str) -> bool:
    """Whether an actor may be given this environment file to read."""
    name = str(path).strip().lstrip("/")
    return bool(name) and ".." not in Path(name).parts and Path(name).name not in EVALUATOR_ONLY["files"] and (
        name not in NOT_KNOWLEDGE["files"] and not any(name.startswith(prefix) for prefix in NOT_KNOWLEDGE["prefixes"])
    )
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


# How much of one file, and how many records of one collection, an actor's brief carries.
KNOWLEDGE_FILE_CHARS = 4000
KNOWLEDGE_RECORDS = 20


def read_knowledge(
    knowledge: list[Any],
    root: Path,
    records: Callable[[str], list[dict[str, Any]]] | None = None,
) -> list[Any]:
    """An actor's knowledge with the environment's files and records it may see read in: a `files` entry gets each
    file's text (bounded), a `records` entry the collection's rows matching its `where` (bounded) when the world's
    records can be read here. Never an evaluator-only file, and never a path outside the environment."""
    base = Path(root).resolve()
    out = []
    for one in knowledge:
        if not isinstance(one, dict) or one.get("kind") not in ("files", "records"):
            out.append(one)
            continue
        if one["kind"] == "files":
            read = []
            for name in one.get("paths") or []:
                path = (base / str(name)).resolve()
                if not knowledge_file_allowed(str(name)) or base not in path.parents or not path.is_file():
                    continue
                read.append({"path": str(name), "text": path.read_text(encoding="utf-8", errors="replace")[:KNOWLEDGE_FILE_CHARS]})
            out.append({**one, "read": read})
            continue
        rows = []
        if records is not None:
            where = one.get("where") if isinstance(one.get("where"), dict) else {}
            try:
                rows = [
                    row
                    for row in records(str(one.get("collection") or ""))
                    if all(row.get(key) == value for key, value in where.items())
                ][:KNOWLEDGE_RECORDS]
            except Exception:  # noqa: BLE001 - an unreadable store leaves the entry named, never a failed call
                rows = []
        out.append({**one, "read": rows} if records is not None else one)
    return out


def with_knowledge_read(
    record: dict[str, Any] | None,
    root: Path,
    records: Callable[[str], list[dict[str, Any]]] | None = None,
) -> dict[str, Any] | None:
    """An actor's record, or its scenario entry, with its `knowledge` read in."""
    if not record or not record.get("knowledge"):
        return record
    return {**record, "knowledge": read_knowledge(record["knowledge"], root, records)}


# A record's fields at their defaults: nothing anyone wrote, so never part of a brief.
_UNSET_FIELDS: dict[str, tuple[Any, ...]] = {
    "constraints": (Constraints().model_dump(),),
    "behaviour": (Behaviour().model_dump(), Behaviour().model_dump(mode="json")),
}


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
        if value in (None, "", [], {}) or value in _UNSET_FIELDS.get(field, ()):
            # A field left at its default says nothing about the actor, so it is never given as if it did.
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
        "actor.identities": "Who you are in each service (use this as your id where an action asks for one)",
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
        if one.get("read"):
            return "\n".join(f"- The file {item['path']}:\n{item['text']}" for item in one["read"])
        return "- You can read these files: " + ", ".join(map(str, one.get("paths") or []))
    if kind == "records":
        where = f" where {one['where']}" if one.get("where") else ""
        named = f"the {one.get('collection') or 'records'} records in {one.get('store') or 'the world'}{where}"
        if "read" in one:
            rows = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) for row in one["read"])
            return f"- {named[0].upper()}{named[1:]}:\n{rows or '(none)'}"
        return f"- You can read {named}"
    return "- " + json.dumps(one, ensure_ascii=False, sort_keys=True)


def acting_fields(actor: "Actor | None") -> dict[str, Any]:
    """What a speaking actor's turn needs from its record beyond its brief, on any channel: what it may do in the
    world, its own line limit and its model."""
    if actor is None:
        return {}
    activation = actor.activation
    return {
        "permitted_actions": list(actor.permitted_actions),
        "max_lines": actor.max_lines,
        "model": actor.model,
        # How long the run waits for it once due, and what happens if it does not answer.
        "timeout_seconds": activation.timeout_seconds if activation else 0,
        "fallback": activation.fallback if activation else "silent",
    }


def ensure_caller(root: Path) -> bool:
    """Lists the caller as an actor when the environment does not yet: the record the editor shows and edits, empty
    so every scenario plays exactly as before until someone gives it a goal, knowledge or behaviour. True when
    written."""
    listing = load_actors(Path(root))
    if CALLER in listing.by_id():
        return False
    caller = Actor(id=CALLER, name="The caller", side="caller", role="the person the agent serves in each scenario")
    save_actors(Path(root), listing.model_copy(update={"actors": [caller, *listing.actors]}))
    return True


def with_caller_actor(doc: dict[str, Any], root: Path) -> dict[str, Any]:
    """A scenario as the caller plays it, when the environment lists the caller as an actor: its persona under the
    scenario's own, and its goal, constraints, behaviour and knowledge after the scenario's instruction, so they are
    the caller's alone (anyone else is given the situation only up to the caller's details)."""
    caller = load_actors(root).by_id().get(CALLER)
    if caller is None:
        return doc
    own = [
        item
        for item in actor_brief(with_knowledge_read(caller.model_dump(exclude_none=True), root) or {})
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
        # Files an actor's knowledge may never name, beside the evaluator-only ones.
        "not_knowledge": NOT_KNOWLEDGE,
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
        "description": "How the actor speaks in the conversation, if it does; empty: it never speaks there",
    }
    fields["surface"]["description"] = "Where the actor acts in the world; never how it speaks (that is `channel`)"
    fields["side"]["description"] = "Whose side the actor is on, for reading the cast; it changes no rule"
    fields["play"]["description"] = (
        "mocked: only the actions each scenario gives it; live: decides its own actions from what it sees when "
        "reached; both"
    )
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
