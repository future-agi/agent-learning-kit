"""Actors: people on the person's side who are not the person.

The environment keeps one list of who might be around its users (`actors.json`, beside the
sub-goal list): each with a name, role, channel and persona, so the same person sounds the same in
every scenario. A scenario that includes one names its id and writes what that person does in this
scenario and when they enter.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Callable

from pydantic import BaseModel, Field, model_serializer, model_validator

from fi.simulate.simulation.call_actors import CHANNELS, TRIGGERS

# A name and a role are one-line labels.
NAME_MAX_LENGTH = 80
ROLE_MAX_LENGTH = 160
# An id every part of the platform can address.
ID_PATTERN = r"[a-z0-9_]{1,64}"


class CallActor(BaseModel):
    """One entry of the environment's list."""

    id: str
    name: str = Field(max_length=NAME_MAX_LENGTH)
    role: str = Field(default="", max_length=ROLE_MAX_LENGTH)
    channel: str
    # The caller persona's fields; the voice is chosen from them.
    persona: dict[str, Any] = Field(default_factory=dict)
    # Background voices only: its level as a ratio of the person's voice (RMS over RMS, per line).
    # Empty takes a draw from the default range, fixed by the run seed.
    loudness_ratio: float | None = Field(default=None, gt=0, le=1)

    @model_validator(mode="after")
    def _a_persona_the_caller_could_have(self) -> "CallActor":
        if self.persona:
            from .scenario import Persona

            self.persona = Persona.model_validate(self.persona).model_dump(
                exclude_defaults=True
            )
        return self


class CallActorList(BaseModel):
    call_actors: list[CallActor] = Field(default_factory=list)

    def get(self, actor_id: str) -> CallActor | None:
        return next((one for one in self.call_actors if one.id == actor_id), None)

    def summary(self) -> str:
        """One line per actor, for a writer's or a judge's brief."""
        return "\n".join(
            f"- {one.id}: {one.name}"
            + (f" ({one.role})" if one.role else "")
            + f", {one.channel}: {(CHANNELS.get(one.channel) or {}).get('about', '')}"
            for one in self.call_actors
        )


class Trigger(BaseModel):
    """When an actor enters a call."""

    kind: str = "always"
    turn: int = 0
    tool: str = ""
    on_call: int = Field(default=1, ge=1)
    words: list[str] = Field(default_factory=list)
    p: float = 0.0

    @model_serializer(mode="wrap")
    def _only_what_it_uses(self, handler: Any) -> dict[str, Any]:
        unused = {"turn": 0, "tool": "", "on_call": 1, "words": [], "p": 0.0}
        return {
            key: value
            for key, value in handler(self).items()
            if key == "kind" or unused.get(key) != value
        }


class ScenarioCallActor(BaseModel):
    """An actor a scenario includes: what they do in it, and when they enter."""

    id: str
    instruction: str = ""
    trigger: Trigger = Field(default_factory=Trigger)


def speaking_entries(
    entries: list[Any],
    listing: CallActorList,
    every_listed: set[str] | None = None,
    own: Mapping[str, Any] | None = None,
) -> list[ScenarioCallActor]:
    """A scenario's actors that speak on the call (listed with a channel), each with what it says and when. With
    `every_listed`, the ids of the whole actor list, an entry missing from the list is kept too, so the call's record
    can say it was not there. An entry with no trigger of its own comes in on the actor's own activation (`own`, the
listed records by id), else from the start."""
    speaking = []
    for entry in entries:
        missing = every_listed is not None and entry.id not in every_listed
        if listing.get(entry.id) is None and not missing:
            continue
        record = (own or {}).get(entry.id)
        default = record.activation.trigger if record is not None and record.activation is not None else None
        chosen = entry.trigger or default
        trigger = chosen.model_dump(exclude_unset=True) if chosen is not None else {}
        speaking.append(
            ScenarioCallActor(
                id=entry.id,
                instruction=entry.instruction,
                trigger=Trigger.model_validate(
                    {
                        key: trigger[key]
                        for key in ("kind", "turn", "tool", "on_call", "words", "p")
                        if key in trigger
                    }
                ),
            )
        )
    return speaking


def load_call_actors(root: Path) -> CallActorList:
    """The environment's actors who speak on calls (those with a channel), from its one actor list."""
    from .actors import load_actors

    return CallActorList(
        call_actors=[
            CallActor(
                id=one.id,
                name=one.name,
                role=one.role,
                channel=one.channel,
                persona=one.persona,
                loudness_ratio=one.loudness_ratio,
            )
            for one in load_actors(root).actors
            if one.channel
        ]
    )


def save_call_actors(root: Path, listing: CallActorList) -> None:
    """Write the actors who speak on calls back into the one actor list, keeping every other field of each."""
    from .actors import Actor, ActorList, load_actors, save_actors

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    current = load_actors(root).by_id()
    speaking = {one.id: one for one in listing.call_actors}
    kept = [
        one for one in current.values() if not one.channel or one.id in speaking
    ]
    merged = []
    for one in kept:
        entry = speaking.pop(one.id, None)
        merged.append(
            one
            if entry is None
            else one.model_copy(update=entry.model_dump(exclude_none=True))
        )
    merged += [
        Actor.model_validate({"side": "caller", **entry.model_dump(exclude_none=True)})
        for entry in speaking.values()
    ]
    save_actors(root, ActorList(actors=merged))


def entry_problems(actor: CallActor, *, modality: str) -> list[str]:
    """Why this list entry cannot be part of this agent's environment, or nothing."""
    problems = []
    if not actor.id.strip() or not actor.name.strip() or not actor.role.strip():
        problems.append("an actor needs an id, a name and a role")
    elif not re.fullmatch(ID_PATTERN, actor.id):
        problems.append(
            f"id {actor.id!r} must be 1 to 64 lowercase letters, digits or underscores"
        )
    channel = CHANNELS.get(actor.channel)
    if channel is None:
        problems.append(f"channel {actor.channel!r} is not one of {sorted(CHANNELS)}")
    elif modality not in channel["modalities"]:
        problems.append(f"a {modality} agent cannot be reached through {actor.channel}")
    named = str(actor.persona.get("name") or "").strip()
    if named and named != actor.name:
        problems.append(
            f"its persona is named {named!r} but the actor is {actor.name!r}"
        )
    return problems


def record_schema() -> dict[str, Any]:
    """A list entry's JSON schema with the rules `entry_problems` and loading apply, for checks made outside this
    package; whether a channel fits the agent is in `channel_modalities`."""
    from .scenario import Persona

    body = CallActor.model_json_schema()
    fields = body["properties"]
    fields["id"]["pattern"] = f"^{ID_PATTERN}$"
    for name in ("id", "name", "role"):
        fields[name]["minLength"] = 1
    fields["channel"]["enum"] = sorted(CHANNELS)
    fields["persona"] = Persona.model_json_schema()
    body["required"] = ["id", "name", "role", "channel"]
    return body


def channel_modalities() -> dict[str, list[str]]:
    """The kinds of agent each channel can reach."""
    return {name: sorted(one["modalities"]) for name, one in CHANNELS.items()}


def scenario_problems(
    entries: list[ScenarioCallActor],
    listing: CallActorList,
    *,
    modality: str,
    call_direction: str,
    person: str,
    tools: set[str] | None,
    runs_in_world: Callable[[str], bool],
) -> list[str]:
    """Why a scenario's actors cannot run, or nothing. `person` is the simulated user's name,
    which no actor may share."""
    problems = []
    seen: set[str] = set()
    for entry in entries:
        actor = listing.get(entry.id)
        if actor is None:
            known = ", ".join(one.id for one in listing.call_actors) or "none"
            problems.append(
                f"{entry.id} is not on the environment's list (it has: {known})"
            )
            continue
        if entry.id in seen:
            problems.append(f"{entry.id} is included twice")
        seen.add(entry.id)
        if person and actor.name.strip().lower() == person.strip().lower():
            problems.append(f"{entry.id} has the simulated user's name, {person}")
        channel = CHANNELS.get(actor.channel) or {}
        if modality not in channel.get("modalities", ()):
            problems.append(
                f"{entry.id} arrives through {actor.channel}, which a {modality} scenario cannot carry"
            )
        trigger = entry.trigger
        if trigger.kind not in TRIGGERS:
            problems.append(
                f"{entry.id}: trigger {trigger.kind!r} is not one of {list(TRIGGERS)}"
            )
        if actor.channel == "ivr":
            if call_direction.strip().lower() != "outbound":
                problems.append(
                    f"{entry.id} is a phone menu, which only answers a call the agent places"
                )
            if trigger.kind != "always":
                problems.append(
                    f"{entry.id} is a phone menu, which answers the call itself: use `always`"
                )
        if trigger.kind == "mid_journey" and trigger.turn < 1:
            problems.append(f"{entry.id}: mid_journey needs a turn of 1 or more")
        if trigger.kind == "on_tool":
            if not trigger.tool.strip():
                problems.append(f"{entry.id}: on_tool needs the `tool` it waits for")
            elif tools is not None and trigger.tool not in tools:
                problems.append(
                    f"{entry.id}: on_tool names {trigger.tool!r}, which is not one of the agent's tools"
                )
            elif not runs_in_world(trigger.tool):
                problems.append(
                    f"{entry.id}: {trigger.tool} runs only inside the agent, so no call can see it"
                )
        if trigger.kind == "on_keyword" and not [
            one for one in trigger.words if one.strip()
        ]:
            problems.append(f"{entry.id}: on_keyword needs the words that bring it in")
        if trigger.kind == "probability" and not 0 < trigger.p <= 1:
            problems.append(f"{entry.id}: probability needs p above 0 and at most 1")
    return ["actors: " + problem for problem in problems]


def logged_tool_name(contract: dict[str, Any], tool: str) -> str:
    """The name the world's tool trace logs a call of `tool` under: the last part of the route its entrypoint
    calls, or the tool's own name when it has no fixed route. `tool` may be the agent's tool name or the world
    action the platform names it by (its route with dots)."""
    for entry in contract.get("tool_entrypoints") or []:
        if not isinstance(entry, dict):
            continue
        route = str(entry.get("endpoint") or "").split("?", 1)[0].strip("/")
        if tool in (entry.get("tool"), route.replace("/", ".")):
            last = route.rsplit("/", 1)[-1]
            if last and "{" not in last:
                return last
    return tool


def scenarios_using(destination: Path, actor_id: str) -> list[str]:
    """The written scenarios that include this actor."""
    found = []
    for document in sorted((Path(destination) / "scenarios").glob("*/scenario.json")):
        try:
            body = json.loads(document.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if any(
            (one or {}).get("id") == actor_id for one in body.get("actors") or []
        ):
            found.append(document.parent.name)
    return found
