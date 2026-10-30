"""Sub-actors: people on the person's side who are not the person.

The environment keeps one list of who might be around its users (`sub_actors.json`, beside the
sub-goal list): each with a name, role, channel and persona, so the same person sounds the same in
every scenario. A scenario that includes one names its id and writes what that person does in this
scenario and when they enter.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field, model_serializer, model_validator

from fi.simulate.simulation.sub_actors import CHANNELS, TRIGGERS

SUB_ACTORS_FILE = "sub_actors.json"
SWITCH = "HARNESS_SUB_ACTORS"


def sub_actors_enabled() -> bool:
    """Whether this sandbox may build or run sub-actors. Off unless the platform turns it on."""
    return os.environ.get(SWITCH, "").strip().lower() in {"1", "true", "yes", "on"}


class SubActor(BaseModel):
    """One entry of the environment's list."""

    id: str
    name: str
    role: str = ""
    channel: str
    # The caller persona's fields; the voice is chosen from them.
    persona: dict[str, Any] = Field(default_factory=dict)
    # Background voices only: its level as a ratio of the person's voice (RMS over RMS, per line).
    # Empty takes a draw from the default range, fixed by the run seed.
    loudness_ratio: float | None = Field(default=None, gt=0, le=1)

    @model_validator(mode="after")
    def _a_persona_the_caller_could_have(self) -> "SubActor":
        if self.persona:
            from .scenario import Persona

            self.persona = Persona.model_validate(self.persona).model_dump(
                exclude_defaults=True
            )
        return self


class SubActorList(BaseModel):
    sub_actors: list[SubActor] = Field(default_factory=list)

    def get(self, sub_actor_id: str) -> SubActor | None:
        return next((one for one in self.sub_actors if one.id == sub_actor_id), None)

    def summary(self) -> str:
        """One line per sub-actor, for a writer's or a judge's brief."""
        return "\n".join(
            f"- {one.id}: {one.name}"
            + (f" ({one.role})" if one.role else "")
            + f", {one.channel}: {(CHANNELS.get(one.channel) or {}).get('about', '')}"
            for one in self.sub_actors
        )


class Trigger(BaseModel):
    """When a sub-actor enters a call."""

    kind: str = "always"
    turn: int = 0
    tool: str = ""
    on_call: int = 1
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


class ScenarioSubActor(BaseModel):
    """A sub-actor a scenario includes: what they do in it, and when they enter."""

    id: str
    instruction: str = ""
    trigger: Trigger = Field(default_factory=Trigger)


def load_sub_actors(root: Path) -> SubActorList:
    """The environment's list; an environment without one has none."""
    path = Path(root) / SUB_ACTORS_FILE
    if not path.exists():
        return SubActorList()
    return SubActorList.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save_sub_actors(root: Path, listing: SubActorList) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / SUB_ACTORS_FILE).write_text(
        listing.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8"
    )


def entry_problems(actor: SubActor, *, modality: str) -> list[str]:
    """Why this list entry cannot be part of this agent's environment, or nothing."""
    problems = []
    if not actor.id.strip() or not actor.name.strip() or not actor.role.strip():
        problems.append("a sub-actor needs an id, a name and a role")
    channel = CHANNELS.get(actor.channel)
    if channel is None:
        problems.append(f"channel {actor.channel!r} is not one of {sorted(CHANNELS)}")
    elif modality not in channel["modalities"]:
        problems.append(f"a {modality} agent cannot be reached through {actor.channel}")
    named = str(actor.persona.get("name") or "").strip()
    if named and named != actor.name:
        problems.append(
            f"its persona is named {named!r} but the sub-actor is {actor.name!r}"
        )
    return problems


def scenario_problems(
    entries: list[ScenarioSubActor],
    listing: SubActorList,
    *,
    modality: str,
    call_direction: str,
    person: str,
    tools: set[str] | None,
    runs_in_world: Callable[[str], bool],
) -> list[str]:
    """Why a scenario's sub-actors cannot run, or nothing. `person` is the simulated user's name,
    which no sub-actor may share."""
    problems = []
    seen: set[str] = set()
    for entry in entries:
        actor = listing.get(entry.id)
        if actor is None:
            known = ", ".join(one.id for one in listing.sub_actors) or "none"
            problems.append(
                f"{entry.id} is not on the environment's list (it has: {known})"
            )
            continue
        if entry.id in seen:
            problems.append(f"{entry.id} is included twice")
        seen.add(entry.id)
        if person and actor.name.strip().lower() == person.strip().lower():
            problems.append(f"{entry.id} has the simulated user's name, {person}")
        if not entry.instruction.strip():
            problems.append(
                f"{entry.id} needs an instruction: who they are here and what they do"
            )
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
    return ["sub_actors: " + problem for problem in problems]


def scenarios_using(destination: Path, sub_actor_id: str) -> list[str]:
    """The written scenarios that include this sub-actor."""
    found = []
    for document in sorted((Path(destination) / "scenarios").glob("*/scenario.json")):
        try:
            body = json.loads(document.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if any(
            (one or {}).get("id") == sub_actor_id
            for one in body.get("sub_actors") or []
        ):
            found.append(document.parent.name)
    return found
