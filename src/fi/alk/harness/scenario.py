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

import ast
import hashlib
import json
import re
from collections import Counter
from math import ceil
from typing import Any, ClassVar

from pydantic import BaseModel, Field, model_validator

from .catalogue import Catalogue
from .simulator import variables_in


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

    # Whether the agent placed this call or answered it. Voice only: a chat is always started by
    # the person, so it stays inbound. An outbound caller has no opening request to make, which is
    # a different test of the agent rather than the same one with a reworded greeting.
    # Empty means defer to the run and then to the contract, which is where the agent's own
    # direction was identified. Defaulting it to "inbound" here would be written into the saved
    # document and silently outrank both of them.
    call_direction: str = ""
    # For an outbound call, how much this person already knows about why they are being rung:
    # "expecting", "partial" or "unaware". Unset means unaware, the case the agent must work
    # hardest for.
    caller_awareness: str = ""

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


def validate_scenario(
    scenario: Scenario,
    catalogue: Catalogue,
    world_state: dict[str, list[dict[str, Any]]],
    simulator_prompt: str = "",
) -> list[str]:
    """Problems that make a scenario unusable, found without running anything.

    Whether it can actually be passed is a different question, and no amount of reading settles
    it. That is what the gates are for.
    """
    problems: list[str] = []
    if not scenario.name.strip():
        problems.append("no name")
    if not scenario.instruction.strip():
        problems.append("no instruction: there is nothing for the run to be about")
    if scenario.persona is not None and not scenario.persona.described():
        problems.append("persona has no details")
    elif scenario.persona is not None and (
        missing := scenario.persona.missing_profile_fields()
    ):
        problems.append("persona is incomplete: " + ", ".join(missing))
    elif scenario.persona is not None:
        # A persona in words of its own renders fine and then does nothing: no behaviour guidance
        # attaches, and the accent it names selects no voice.
        from .persona_guides import unrecognised

        problems.extend(unrecognised(scenario.persona.model_dump()))
    if not scenario.sub_goals:
        problems.append(
            "no sub_goals: nothing would be graded. Name the entries of the catalogue this "
            "scenario is meant to exercise"
        )
    if world_state and not scenario.fixture:
        problems.append(
            "no fixture manifest: declare the seed/generated/mixed data this scenario relies on"
        )
    elif scenario.fixture and str(scenario.fixture.get("origin") or "").lower() not in {
        "seed",
        "generated",
        "mixed",
    }:
        problems.append("fixture.origin must be seed, generated, or mixed")
    elif (
        scenario.fixture
        and str(scenario.fixture.get("origin") or "").lower() in {"generated", "mixed"}
        and not (scenario.setup_code or "").strip()
    ):
        # A fixture claiming data it never creates is the whole "the OTP was never seeded" failure:
        # the scenario reads as self-sufficient, the world has none of it, and the agent has nothing
        # to answer with. Caught here because it is provable from the document alone.
        problems.append(
            f"fixture.origin is {scenario.fixture.get('origin')!r}, which claims this scenario "
            "creates data, but setup_code is empty. Either seed everything the fixture names, or "
            "declare origin 'seed' and use only records that already exist"
        )

    unknown = sorted(set(scenario.sub_goals) - catalogue.names())
    if unknown:
        problems.append(
            f"sub_goals not in the catalogue: {', '.join(unknown)}. Use the shared names, or add "
            f"them to the catalogue first. It has: {', '.join(sorted(catalogue.names())) or 'none'}"
        )

    # setup_code and ready_code are not read here. Whether they work is not a question reading
    # them can answer, and running them is exactly what the first gate does.
    if scenario.setup_code.strip() and "def setup(" not in scenario.setup_code:
        problems.append("setup_code must define setup(world)")
    if scenario.ready_code.strip() and "def ready(" not in scenario.ready_code:
        problems.append("ready_code must define ready(world)")

    if simulator_prompt:
        unfilled = sorted(variables_in(simulator_prompt) - set(scenario.slots()))
        if unfilled:
            problems.append(
                f"the simulator prompt asks for {', '.join(unfilled)}, which this scenario does "
                "not supply. An unfilled slot reaches the caller verbatim"
            )

    if not scenario.solution:
        problems.append(
            "no solution: without the actions a correct agent would take, there is no way to "
            "show this scenario can be passed at all"
        )
    problems.extend(fixture_problems(scenario))
    return problems


def contract_sequence_problems(
    scenario: Scenario, hard_constraints: list[str]
) -> list[str]:
    """Catch reference solutions that hide required same-call state in a fixture.

    A dependency can accept a pre-seeded identifier even when the public agent API cannot. For
    a rule such as ``cancel_ride requires a booking_ref from this call``, require a producer
    (``book_ride``) earlier in the same reference solution instead of allowing setup code or
    environment-only arguments to make an impossible scenario look solvable.
    """
    problems: list[str] = []
    names = [step.tool for step in scenario.solution]
    pattern = re.compile(
        r"\b(?P<consumer>[a-z][a-z0-9_]*)\b\s+requires\b.*?\b"
        r"(?P<resource>[a-z][a-z0-9_]*(?:_id|_ref))\s+from this call\b",
        re.IGNORECASE,
    )
    for constraint in hard_constraints:
        found = pattern.search(constraint)
        if found is None:
            continue
        consumer = found.group("consumer").lower()
        lowered = [name.lower() for name in names]
        if consumer not in lowered:
            continue
        resource = re.sub(r"_(?:id|ref)$", "", found.group("resource").lower())
        stems = {resource, resource.removesuffix("ing")}
        before = lowered[: lowered.index(consumer)]
        produced = any(
            any(stem and stem in tool for stem in stems)
            and not tool.startswith(("get_", "list_", "find_", "cancel_"))
            for tool in before
        )
        if not produced:
            problems.append(
                f"{consumer} requires {found.group('resource')} from this call, but the "
                "reference solution does not create it first; do not hide it in setup or "
                "environment_arguments"
            )
    return problems


_WEAK_CODES = {
    "000000",
    "111111",
    "222222",
    "333333",
    "444444",
    "555555",
    "666666",
    "777777",
    "888888",
    "999999",
    "012345",
    "123456",
    "234567",
    "345678",
    "456789",
    "987654",
    "876543",
    "765432",
    "654321",
}


def _six_digit_values(scenario: Scenario) -> list[str]:
    """Likely one-time codes declared by a scenario, without treating phone digits as OTPs."""
    found: list[str] = []

    def walk(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child, item in value.items():
                walk(item, str(child))
        elif isinstance(value, list):
            for item in value:
                walk(item, key)
        elif "otp" in key.lower() or key.lower() in {"code", "verification_code"}:
            found.extend(re.findall(r"(?<!\d)\d{6}(?!\d)", str(value)))

    walk(scenario.fixture)
    if scenario.persona:
        walk(scenario.persona.metadata)
        walk(scenario.persona.scripted_caller or {})
    for step in scenario.solution:
        walk(step.arguments)
        walk(step.environment_arguments)
    # Setup is code, so key-aware traversal is unavailable. Restrict matches to a nearby field
    # name instead of collecting six digits from a phone number or an unrelated identifier.
    found.extend(
        match.group(1)
        for match in re.finditer(
            r"(?:otp|verification[_ ]?code|['\"]code['\"])[^\n]{0,80}?(?<!\d)(\d{6})(?!\d)",
            scenario.setup_code,
            flags=re.IGNORECASE,
        )
    )
    return found


def fixture_problems(scenario: Scenario) -> list[str]:
    """Reject demo-shaped data before a paid run makes it look like production traffic."""
    problems: list[str] = []
    codes = _six_digit_values(scenario)
    weak = sorted({code for code in codes if code in _WEAK_CODES})
    if weak:
        problems.append(
            "fixture uses predictable verification code(s): "
            + ", ".join(weak)
            + ". Generate a different non-sequential six-digit value for this scenario"
        )
    written = json.dumps(
        {
            "instruction": scenario.instruction,
            "persona": scenario.persona.model_dump() if scenario.persona else {},
            "fixture": scenario.fixture,
            "setup": scenario.setup_code,
        },
        default=str,
    ).lower()
    clichés = [
        value
        for value in ("test user", "john doe", "jane doe", "123 main street")
        if value in written
    ]
    if clichés:
        problems.append("fixture contains placeholder demo data: " + ", ".join(clichés))
    card_endings = sorted(
        set(
            re.findall(
                r"(?:last4|card_last4|payment_last4)[^\n]{0,30}?[\"']?(0000|1111|1234|4242|4444)[\"']?",
                written,
            )
        )
    )
    if card_endings:
        problems.append(
            "fixture uses placeholder payment-card ending(s): "
            + ", ".join(card_endings)
        )
    spoken_card_endings = sorted(
        set(
            re.findall(
                r"(?:ending(?:\s+in)?|last\s+four(?:\s+digits)?(?:\s+are)?)\D{0,12}"
                r"(0000|1111|1234|4242|4444)",
                written,
            )
        )
    )
    if spoken_card_endings:
        problems.append(
            "fixture/instruction uses placeholder payment-card ending(s): "
            + ", ".join(spoken_card_endings)
        )
    demo_ids = sorted(
        value
        for value in ("ub12345678", "booking123", "booking_123", "test123")
        if value in written
    )
    demo_ids.extend(
        re.findall(r"\b(?:ub_[a-z]+_0*1|pay_[a-z]+(?:_[a-z]+)*0*1)\b", written)
    )
    demo_ids = sorted(set(demo_ids))
    if demo_ids:
        problems.append(
            "fixture uses placeholder transaction identifier(s): " + ", ".join(demo_ids)
        )
    return problems


def suite_diversity_problems(scenarios: list[Scenario]) -> list[str]:
    """Whether a conversational suite represents meaningfully different people and data."""
    if len(scenarios) < 4:
        return []
    problems: list[str] = []
    personas = [one.persona for one in scenarios if one.persona]
    names = [one.name.strip().lower() for one in personas if one and one.name.strip()]
    unique_names = len(set(names))
    required_names = min(len(scenarios), max(3, ceil(len(scenarios) * 0.9)))
    if unique_names < required_names:
        repeated = [name for name, count in Counter(names).items() if count > 2]
        problems.append(
            f"only {unique_names} distinct caller names across {len(scenarios)} scenarios; "
            f"need at least {required_names}"
            + (f". Overused: {', '.join(repeated)}" if repeated else "")
        )
    openings = [
        one.initial_message.strip().lower()
        for one in personas
        if one and one.initial_message.strip()
    ]
    if len(set(openings)) != len(openings):
        problems.append("caller opening messages repeat verbatim across scenarios")
    locations = {
        one.location.strip().lower() for one in personas if one and one.location.strip()
    }
    if len(scenarios) >= 8 and len(locations) < 3:
        problems.append(
            f"only {len(locations)} persona locations across {len(scenarios)} scenarios; need 3"
        )
    # A code naturally appears several times inside one scenario (fixture, caller script,
    # reference verify call). Diversity is about reuse *between* callers, not repeated mention
    # of the same fact inside one test.
    codes = [
        code for scenario in scenarios for code in set(_six_digit_values(scenario))
    ]
    duplicated_codes = sorted(
        code for code, count in Counter(codes).items() if count > 1
    )
    if duplicated_codes:
        problems.append(
            "verification codes are reused across scenarios: "
            + ", ".join(duplicated_codes)
        )
    setups = [signature for one in scenarios if (signature := _setup_signature(one))]
    if len(set(setups)) != len(setups):
        problems.append("identical scenario setup data is reused more than once")
    return problems


def _setup_signature(scenario: Scenario) -> str:
    """Comparable setup code, excluding the generated no-op function/documentation."""
    source = scenario.setup_code.strip()
    if not source:
        return ""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return " ".join(source.split())
    function = next(
        (node for node in tree.body if isinstance(node, ast.FunctionDef)), None
    )
    if function is None:
        return " ".join(source.split())
    meaningful = [
        node
        for node in function.body
        if not isinstance(node, ast.Pass)
        and not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
    ]
    return (
        "" if not meaningful else ast.dump(ast.Module(body=meaningful, type_ignores=[]))
    )
