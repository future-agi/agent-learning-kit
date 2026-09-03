"""A scenario: a delta on the base environment, and what must hold afterwards.

The base is built once — the world, the simulator's prompt, the catalogue of sub-goals. A
scenario changes a few values in that world, fills the prompt's slots, and names which sub-goals
must hold. It is not a template with values slotted into it; the harness writes each one.

It also carries a **solution**: what a correct agent would do. That is not decoration. It is what
proves, before the scenario is ever used, that the scenario can be passed at all and that its
checks are not vacuous — the two gates in ``prove.py``. Terminal-bench keeps its tasks honest the
same way, and it needs no model to do it.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from math import ceil
from typing import Any, ClassVar

from pydantic import BaseModel, Field, model_validator

from ..store.setup_code import fingerprint
from .catalogue import Catalogue
from ...simulator import variables_in


class Step(BaseModel):
    """One action in a reference solution."""

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Source-backed agents often add trusted session state between the model-facing function
    # and the dependency API: rider ids, resolved addresses, selected fares, and similar values
    # must never be exposed as arguments the model supposedly chose.  A reference proof still
    # has to drive the real dependency so its database effects can be checked, so it may carry
    # that dependency payload separately.  Agent runs never read this field.
    environment_arguments: dict[str, Any] = Field(default_factory=dict)


class Persona(BaseModel):
    """The simulated caller, in the same shape used by existing voice scenarios.

    A persona controls how the caller pursues a scenario's task. The task itself remains on
    ``Scenario.instruction`` so the harness can vary either one without conflating them.
    """

    name: str = ""
    gender: str = ""
    age_group: str = ""
    occupation: str = ""
    location: str = ""
    personality: str = ""
    communication_style: str = ""
    # The first thing this person actually says. Voice agents often greet immediately; leaving
    # this to the simulator model produced generic "Hello?" turns and avoidable silence races.
    initial_message: str = ""
    keywords: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    accent: str = ""
    multilingual: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Optional deterministic voice policy for transactional scenarios. It keeps
    # caller facts realistic and varied while avoiding LLM role drift during a
    # long tool-heavy phone flow.
    scripted_caller: dict[str, Any] | None = None

    def described(self) -> bool:
        return bool(
            self.name
            or self.gender
            or self.age_group
            or self.occupation
            or self.location
            or self.personality
            or self.communication_style
            or self.keywords
            or self.languages
            or self.accent
            or self.metadata
        )

    def missing_profile_fields(self) -> list[str]:
        """The minimum needed for a scenario to exercise caller variation intentionally."""
        missing = [
            name
            for name, value in (
                ("name", self.name),
                ("personality", self.personality),
                ("communication_style", self.communication_style),
                ("initial_message", self.initial_message),
                ("accent", self.accent),
            )
            if not value.strip()
        ]
        if not self.languages:
            missing.append("languages")
        if not self.keywords:
            missing.append("keywords")
        return missing

    def format_persona(self) -> str:
        """A stable, human-readable profile the simulator can consistently embody."""
        parts = []
        identity = []
        for label, value in (
            ("Name", self.name),
            ("Gender", self.gender),
            ("Age Group", self.age_group),
            ("Occupation", self.occupation),
            ("Location", self.location),
        ):
            if value:
                identity.append(f"- {label}: {value}")
        if identity:
            parts.append("# YOUR IDENTITY\n\n" + "\n".join(identity))

        behavior = []
        if self.personality:
            behavior.append(f"- Personality: {self.personality}")
        if self.communication_style:
            behavior.append(f"- Communication Style: {self.communication_style}")
        if self.keywords:
            behavior.append("- Key Traits: " + ", ".join(self.keywords))
        if behavior:
            parts.append("# YOUR PERSONALITY & COMMUNICATION\n\n" + "\n".join(behavior))

        speech = []
        if self.languages:
            speech.append("- Language(s): " + ", ".join(self.languages))
        if self.accent:
            speech.append(f"- Accent: {self.accent}")
        if self.multilingual:
            speech.append(
                "- Switch languages naturally when the conversation calls for it."
            )
        if speech:
            parts.append("# LANGUAGE & SPEECH PATTERNS\n\n" + "\n".join(speech))

        if self.metadata:
            characteristics = [
                f"- {key.replace('_', ' ').title()}: {value}"
                for key, value in self.metadata.items()
            ]
            parts.append(
                "# ADDITIONAL CHARACTERISTICS\n\n" + "\n".join(characteristics)
            )
        return "\n".join(parts)


def _slug(name: str) -> str:
    """An ASCII key for ``name``, safe to send as a header value.

    Falls back to a digest rather than an empty string: an empty key would collapse every
    scenario in a job onto one idempotency key on the receiving side.
    """
    cleaned = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return cleaned or "scenario-" + hashlib.sha256(name.encode()).hexdigest()[:12]


def _decided_by(name: str) -> bool:
    """Whether this scenario is noisy, decided by its name so a rerun decides the same."""
    return hashlib.sha256((name or "").encode()).digest()[0] % 2 == 0


class Scenario(BaseModel):
    """One test: what changes, what is asked, what a correct agent does, what must hold."""

    name: str
    # How this scenario is identified on the wire. Derived from ``name``, which is already unique
    # across a suite and already a slug because it is the folder name. It ships as a header, so
    # anything outside ASCII is dropped and an empty result falls back to a digest.
    scenario_key: str = ""
    # Assigned by the platform when the scenario is pre-allocated. Never written here.
    scenario_id: str = ""
    use_case: str = ""
    # What makes this row different from its siblings in the same use case. Coverage is counted
    # on the pair, so a use case can carry many scenarios without any reading as a duplicate.
    branch: str = ""
    tests: str = ""
    # Which caller conditions this scenario stays true under, by axis name. A proved scenario
    # carries a working environment, so an axis listed here can be varied by copying rather than
    # by writing and proving another one: the setup, the checks and the reference solution are
    # reused untouched and only the person changes.
    #
    # Empty means every axis whose settings leave the world alone, which is the ordinary case.
    # A scenario names axes explicitly only to *withhold* one, and it withholds one when the
    # scenario's own point would be lost: an accent test says nothing about a caller given a
    # different accent, and a scenario turning on somebody's impatience is not the same scenario
    # once they are calm.
    varies: list[str] = Field(default_factory=list)

    # What this scenario changes about the world after it is reset, as code: a file defining
    # ``setup(world)``. Rows in a table were enough while every world was a database, and they
    # are not enough now — a scenario may need a service to start returning errors, a file to be
    # missing, a queue to be backed up. Code can express all of that; a table of rows cannot.
    setup_code: str = ""

    # Whether the world is actually ready for this scenario, as code: a file defining
    # ``ready(world)`` that answers with nothing when the world holds what this scenario
    # presumes, or a sentence saying what is missing.
    #
    # This is the precondition, and it is the difference between a real finding and a wasted
    # run: a scenario about the last five chocolates is only a test of the agent if there really
    # are five. Otherwise the agent fails for something we got wrong, and it looks like the
    # agent's fault.
    ready_code: str = ""

    # The task. For a conversational agent it fills the simulator prompt's instruction slot; for
    # a browser or coding agent it goes to the agent directly.
    instruction: str = ""
    # Who is making the request. This is deliberately separate from the task so a caller's
    # communication needs do not get buried in an unstructured instruction.
    persona: Persona | None = None
    # Anything else that prompt asks for, by slot name.
    variables: dict[str, str] = Field(default_factory=dict)
    # A readable declaration of which data makes this scenario real. ``setup_code`` remains the
    # executable delta; this is the index a person and the UI can inspect without reverse-
    # engineering Python. Typical keys are origin (seed/generated/mixed), identity, credentials,
    # location and account_state. It is intentionally open-ended across agent domains.
    fixture: dict[str, Any] = Field(default_factory=dict)

    # What a correct agent would do. Run by the gates, never by the agent under test.
    solution: list[Step] = Field(default_factory=list)

    # Which entries of the shared catalogue must hold. Named, not restated, so results roll up
    # across the suite: the same sub-goal failing in seven of twelve scenarios is one sentence.
    sub_goals: list[str] = Field(default_factory=list)

    max_turns: int = 10

    # Where this call is made from. A string names the place ("street", "vehicle", "retail"), and
    # True asks for noise while leaving the place to the fixture. Left unset it is decided from
    # the name, so a suite still covers both conditions but the same suite decides the same way
    # twice; a coin flip here made a seeded run unreproducible.
    background_noise: bool | str = ""

    # Which way the call goes, from the tested agent's side. ``inbound`` is somebody ringing the
    # agent, which is every scenario written before this field existed and so is the default.
    # ``outbound`` is the agent ringing a person, which is a different situation and not a
    # different person: the persona is unchanged, but the person did not place the call, does not
    # know who is on the line, and has no errand of their own. An agent that collects information
    # is only tested honestly this way, because the whole conversation is it asking and them
    # answering rather than them arriving with something to do.
    direction: str = "inbound"

    # Slots the caller filled by the run rather than by the scenario. Listed so a template that
    # uses one is not rejected as unfillable at write time.
    RUNTIME_SLOTS: ClassVar[tuple[str, ...]] = ("channel", "situation")

    @model_validator(mode="after")
    def _identify(self) -> "Scenario":
        if not self.scenario_key:
            self.scenario_key = _slug(self.name)
        if self.background_noise == "":
            self.background_noise = _decided_by(self.name)
        return self

    @property
    def agent_speaks_first(self) -> bool:
        """Whether the tested agent opens the call.

        It does when it was rung: a service answers and greets. It does not when it placed the
        call, because a person picking up their own phone speaks first, and an agent that opens
        an outbound call with the greeting of one that was rung is not being tested on the call
        it actually makes.
        """
        return self.direction != "outbound"

    def slots(self) -> dict[str, str]:
        """Every value this scenario offers the simulator prompt."""
        persona = {"persona": self.persona.format_persona()} if self.persona else {}
        runtime = {name: "" for name in self.RUNTIME_SLOTS}
        return {
            "instruction": self.instruction,
            **runtime,
            **self.variables,
            **persona,
        }
