"""Apply a typed change-set to a saved suite, reworking only what the change actually affects.

A person editing a suite does not send prose. They send changes: this scenario's caller is now
sixty-two, drop that one, reword this instruction. Prose cannot be validated before it mutates
anything, cannot carry a batch, and cannot say afterwards what it did to each scenario. A
change-set can, so this is the transport and ``guidance`` is not.

The subtle part is knowing when a change is consequential. **The gates cannot tell us.** ``prove``
replays the reference solution against the world and runs the checks; the persona never enters it,
because a persona shapes what the simulated caller says rather than what the world holds. So
changing an age fails nothing, and a suite can pass every gate while the world seeds a
twenty-four-year-old, the caller claims sixty-two, and the oracle still takes the standard path.

Three steps, cheapest first:

1. ``bearing_on`` asks whether the edited field appears anywhere in the agent's contract at all. A
   weather agent never mentions age, so an age edit cannot matter and costs nothing to apply.
2. Where it might matter, one bounded question decides whether the seed, the oracle or the checks
   actually move.
3. Only then is anything rewritten, and the gates prove the rewrite the same way they prove a
   fresh scenario.

The gates are the safety net for a rewrite, never the detector for the need.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .scenario import Persona, Scenario

logger = logging.getLogger(__name__)

SCHEMA = "futureagi.scenario-changes.v1"

# Every operation a change-set may carry. Editing a whole scenario later is another entry here and
# nothing else: the transport, the prefilter and the receipt do not change with it.
OPERATIONS = ("set_persona", "set_field", "drop")

# Fields a `set_field` may touch. Everything else is derived, proved, or identity, and letting an
# edit write those would be letting it bypass the gates rather than satisfy them. `use_case` is not
# here: it is read off the contract and one use case covers many scenarios, so editing it on one
# only misfiles that scenario. A wrong use case means a wrong contract, which is a rebuild.
EDITABLE_FIELDS = ("instruction", "tests", "branch", "max_turns", "background_noise")

# Of those, the ones that describe a scenario rather than decide anything about it. `tests` belongs
# here despite reading like the claim: it is reported with the result and deliberately withheld from
# the simulated caller, and the checks are what actually grade. Editing it renames the claim without
# changing what is verified, so changing what is verified means changing sub-goals instead.
# `instruction` stays out, because it is what the person asks for and can leave the solution unable
# to solve it.
DESCRIPTIVE_FIELDS = ("tests", "branch", "max_turns", "background_noise")


@dataclass
class Change:
    """One edit, named by the scenario it applies to."""

    op: str
    scenario: str
    persona: dict[str, Any] = field(default_factory=dict)
    field_name: str = ""
    value: Any = None

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "Change":
        op = str(raw.get("op") or "").strip()
        if op not in OPERATIONS:
            raise ValueError(f"unknown op {op!r}; expected one of {', '.join(OPERATIONS)}")
        name = str(raw.get("scenario") or "").strip()
        if not name:
            raise ValueError("a change has to name the scenario it applies to")
        if op == "set_field":
            which = str(raw.get("field") or "").strip()
            if which not in EDITABLE_FIELDS:
                raise ValueError(
                    f"{which!r} is not editable; these are: {', '.join(EDITABLE_FIELDS)}"
                )
            return cls(op=op, scenario=name, field_name=which, value=raw.get("value"))
        if op == "set_persona":
            persona = raw.get("persona")
            if not isinstance(persona, dict) or not persona:
                raise ValueError("set_persona needs a persona object with at least one field")
            unknown = sorted(set(persona) - set(Persona.model_fields))
            if unknown:
                raise ValueError(f"not persona fields: {', '.join(unknown)}")
            return cls(op=op, scenario=name, persona=dict(persona))
        return cls(op=op, scenario=name)


@dataclass
class Receipt:
    """What happened to one change, in words a person can act on."""

    scenario: str
    op: str
    outcome: str  # applied | reworked | refused
    why: str = ""
    touched: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "op": self.op,
            "outcome": self.outcome,
            "why": self.why,
            "touched": self.touched,
        }


def parse_changes(document: dict[str, Any]) -> list[Change]:
    """Validate the whole document before any of it is applied.

    All or nothing on purpose: a batch that half-applies leaves a suite nobody can reason about,
    and the caller cannot tell which half.
    """
    if str(document.get("schema") or "") != SCHEMA:
        raise ValueError(f"expected schema {SCHEMA}")
    raw = document.get("changes")
    if not isinstance(raw, list) or not raw:
        raise ValueError("a change-set needs at least one change")
    return [Change.parse(one) for one in raw]


# Persona fields that describe how somebody sounds rather than what they are entitled to. An agent
# decides nothing from an accent, so these never reach the world, the oracle or the checks. The
# rest might, and are asked about.
NEVER_CONSEQUENTIAL = ("accent", "communication_style", "personality", "initial_message", "name")


def seeded_among(scenario: Any, persona_fields: list[str]) -> list[str]:
    """Which of these fields this scenario already wrote into the world.

    The contract says what an agent reasons about. It cannot say what a scenario seeded, and a
    value in the world is just as binding: a caller who gives a different name than the record the
    agent looks them up by is a caller the agent cannot find. The fixture is the record of what was
    seeded, so it settles this without guessing.
    """
    fixture = getattr(scenario, "fixture", None) or {}
    persona = getattr(scenario, "persona", None)
    seeded = {str(value).strip().lower() for value in fixture.values() if value not in (None, "")}
    if not seeded or persona is None:
        return []
    found: list[str] = []
    for name in persona_fields:
        value = getattr(persona, name, None)
        if isinstance(value, str) and value.strip() and value.strip().lower() in seeded:
            found.append(name)
    return found


def bearing_on(contract: Any, persona_fields: list[str]) -> list[str]:
    """Which of these edited fields the agent's contract could plausibly act on.

    The cheap half of the question, and it settles most of it. An agent whose tools, values, rules
    and use cases never mention age cannot behave differently for a sixty-two-year-old, so an age
    edit is cosmetic there and nothing is rewritten and no model is asked. Only what survives this
    is worth a question.

    Deliberately generous: it matches the field name and the words around it rather than trying to
    be clever, because a false "might matter" costs one bounded question and a false "cannot
    matter" ships an incoherent scenario.
    """
    haystack = " ".join(
        [
            str(getattr(contract, "one_liner", "") or ""),
            " ".join(str(one) for one in (getattr(contract, "hard_constraints", None) or [])),
            " ".join(str(one) for one in (getattr(contract, "real_use_cases", None) or [])),
            str(getattr(contract, "system_prompt_excerpt", "") or ""),
            json_of_tools(contract),
        ]
    ).lower()
    bearing: list[str] = []
    for name in persona_fields:
        if name in NEVER_CONSEQUENTIAL:
            continue
        for word in _words_for(name):
            # On word boundaries, because plain substring matching makes "age" a hit inside
            # "agent", "manage" and "package". Every contract says "agent" somewhere, so that
            # alone flagged an age edit as consequential on every agent there is.
            if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", haystack):
                bearing.append(name)
                break
    return bearing


def json_of_tools(contract: Any) -> str:
    """Every tool's name, arguments and offered values as one searchable string."""
    parts: list[str] = []
    for tool in getattr(contract, "tools", None) or []:
        parts.append(str(getattr(tool, "name", "") or ""))
        parts.append(str(getattr(tool, "description", "") or ""))
        parts.extend(str(one) for one in (getattr(tool, "args", None) or []))
        values = getattr(tool, "arg_values", None) or {}
        for key, allowed in values.items():
            parts.append(str(key))
            parts.extend(str(one) for one in (allowed or []))
    return " ".join(parts)


def _words_for(persona_field: str) -> tuple[str, ...]:
    """The words a contract would use for this persona field, not just the field's own name."""
    spellings = {
        "age_group": ("age", "birth", "dob", "senior", "minor", "adult", "year old", "18+", "60+"),
        "languages": ("language", "english", "spanish", "translat", "bilingual"),
        "location": ("location", "country", "region", "state", "address", "zip", "postcode"),
        "occupation": ("occupation", "employ", "job", "profession", "student", "retired"),
        "gender": ("gender", "male", "female", "title", "salutation"),
        "multilingual": ("language", "multilingual", "translat"),
        "scripted_caller": ("script",),
        "keywords": ("keyword",),
        "metadata": (),
    }
    return spellings.get(persona_field, (persona_field.replace("_", " "),))


def applied_to(scenario: Scenario, change: Change) -> Scenario:
    """One change, applied to one scenario, with nothing else touched.

    Returns a copy. The suite is only written once every change has been applied and proved, so a
    batch that fails partway leaves the saved suite as it was rather than half-edited.
    """
    if change.op == "set_persona":
        current = scenario.persona.model_dump() if scenario.persona else {}
        return scenario.model_copy(
            update={"persona": Persona.model_validate({**current, **change.persona})}
        )
    if change.op == "set_field":
        return scenario.model_copy(update={change.field_name: change.value})
    raise ValueError(f"{change.op} does not produce a scenario")


def amend(
    scenarios: list[Scenario],
    changes: list[Change],
    *,
    contract: Any,
    consider: Any = None,
) -> tuple[list[Scenario], list[Receipt]]:
    """Apply a change-set to a suite, asking about a change only when it could matter.

    ``consider`` is how the caller answers the one question code cannot: given this contract, does
    changing these fields move what the world seeds, what a correct agent does, or what is checked?
    It takes the scenario, the change and the fields the prefilter could not rule out, and returns
    the reworked scenario or None for "nothing moves". Left out, a change that might matter is
    refused rather than quietly applied, because applying it is how a suite becomes incoherent
    while still passing every gate.
    """
    by_name = {one.name: one for one in scenarios}
    kept = list(scenarios)
    receipts: list[Receipt] = []

    for change in changes:
        scenario = by_name.get(change.scenario)
        if scenario is None:
            receipts.append(
                Receipt(change.scenario, change.op, "refused", "no scenario by that name")
            )
            continue

        if change.op == "drop":
            kept = [one for one in kept if one.name != change.scenario]
            by_name.pop(change.scenario, None)
            receipts.append(Receipt(change.scenario, change.op, "applied", "removed"))
            continue

        edited = applied_to(scenario, change)
        if change.op == "set_persona":
            fields = sorted(change.persona)
            # Two independent reasons a persona edit matters: the agent reasons about the field, or
            # this scenario already wrote that value into the world.
            might = sorted(set(bearing_on(contract, fields)) | set(seeded_among(scenario, fields)))
        else:
            fields = [change.field_name]
            might = [] if change.field_name in DESCRIPTIVE_FIELDS else fields

        if not might:
            kept = [edited if one.name == change.scenario else one for one in kept]
            by_name[change.scenario] = edited
            # Two different reasons a change is free, and saying the right one matters: a
            # descriptive field cannot reach the proof at all, while a persona field reached it
            # for some other agent and not for this one.
            why = (
                f"{', '.join(fields)} says how this scenario is filed, not what it proves"
                if change.op == "set_field"
                else f"nothing in this agent's contract turns on {', '.join(fields)}"
            )
            receipts.append(
                Receipt(change.scenario, change.op, "applied", why, ["scenario.json"])
            )
            continue

        if consider is None:
            receipts.append(
                Receipt(
                    change.scenario,
                    change.op,
                    "refused",
                    f"{', '.join(might)} could change what this agent does, and there is no one "
                    "to work out whether the seed, the solution and the checks still hold",
                )
            )
            continue

        reworked = consider(scenario=edited, change=change, fields=might)
        if reworked is None:
            kept = [edited if one.name == change.scenario else one for one in kept]
            by_name[change.scenario] = edited
            receipts.append(
                Receipt(
                    change.scenario,
                    change.op,
                    "applied",
                    f"{', '.join(might)} could have mattered here and does not",
                    ["scenario.json"],
                )
            )
            continue

        kept = [reworked if one.name == change.scenario else one for one in kept]
        by_name[change.scenario] = reworked
        receipts.append(
            Receipt(
                change.scenario,
                change.op,
                "reworked",
                f"{', '.join(might)} changed what this scenario has to hold",
                _moved(scenario, reworked),
            )
        )

    return kept, receipts


def _moved(before: Scenario, after: Scenario) -> list[str]:
    """Which parts of a scenario a rework actually changed, for the receipt to name."""
    moved = ["scenario.json"]
    if before.setup_code != after.setup_code:
        moved.append("setup.py")
    if before.ready_code != after.ready_code:
        moved.append("ready.py")
    if [one.model_dump() for one in before.solution] != [
        one.model_dump() for one in after.solution
    ]:
        moved.append("solution")
    if sorted(before.sub_goals) != sorted(after.sub_goals):
        moved.append("checks")
    return moved


REWORK_TURNS = 30


def rework_brief(scenario: Scenario, change: Change, fields: list[str]) -> str:
    """What the session is told. Short, because the scenario and its tools are already in front of
    it and repeating them is how an instruction gets skimmed."""
    was = ", ".join(f"{one}={change.persona.get(one)!r}" for one in fields)
    return (
        f"The person in scenario {scenario.name!r} has changed: {was}.\n\n"
        "This agent's contract turns on that, so the scenario may no longer hold together. Read it "
        "with inspect_scenario, then work out whether any of these three still match the person "
        "it now describes:\n"
        "  - the world this scenario seeds, if it carries anything about them\n"
        "  - the reference solution, if a correct agent would now take a different path or send "
        "different arguments\n"
        "  - the sub-goals, if what must be true at the end has changed\n\n"
        "Change only what the new person actually moves, and leave the rest exactly as it is. If "
        "nothing moves, say so and submit nothing: a scenario that did not need changing must not "
        "be rewritten. Otherwise submit it under the same name, which replaces it, and the three "
        "gates will prove it the way they prove any scenario."
    )


def reworker_for(contract: Any, destination: Any, *, ask: Any = None) -> Any:
    """A ``consider`` that reworks one scenario in its own session, then lets the gates judge it.

    The session gets the scenario tools it already knows, so submitting is what re-proves: ready,
    solvable and not vacuous run exactly as they do for a fresh scenario. Nothing here re-implements
    proving, and nothing here can skip it.
    """
    import asyncio

    from .scenario_tools import scenario_tools, world_summary
    from .backends import SessionSpec
    from .config import chosen_model, load_skill
    from .session import Stage

    def consider(*, scenario: Scenario, change: Change, fields: list[str]) -> Scenario | None:
        server, kept = scenario_tools(
            contract,
            destination,
            destination,
            wanted=0,
            can_save=False,
            start_from=[scenario],
            # This session edits one named scenario and means to replace it. Renaming here would
            # leave the original beside a suffixed copy, which is two scenarios where there was one.
            rename_on_collision=False,
        )
        spec = SessionSpec(
            system_prompt=(
                f"## This agent\n\n{contract.brief(with_data=True)}"
                f"\n\n## Its world\n\n{world_summary(destination)}"
                f"\n\n{load_skill('write-scenarios')}"
                "\n\n## What you are doing\n\nOne scenario already exists and somebody has changed "
                "who it is about. You are not writing a new one and not widening the suite: you are "
                "making this one true again, changing as little as possible."
            ),
            servers={"scenarios": server},
            max_turns=REWORK_TURNS,
            model=chosen_model(),
            ask=ask,
            thinking=True,
        )
        stage = Stage(spec, name="amend-scenario")

        async def run() -> None:
            async with stage:
                await stage.say(rework_brief(scenario, change, fields))

        # `consider` is called from `amend`, which is ordinary synchronous code, but its callers
        # are not: the CLI runs its commands inside a loop and so does the platform worker.
        # `asyncio.run` refuses to nest, so where a loop is already turning the session gets a
        # thread of its own with its own loop, and the caller waits for it.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(run())
        else:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(lambda: asyncio.run(run())).result()
        # Submitting replaces by name, so the reworked scenario is whichever one came back under
        # it. Nothing submitted means the session judged that nothing moved.
        for one in kept:
            if one.name == scenario.name and one.model_dump() != scenario.model_dump():
                return one
        return None

    return consider


def amend_bundle(
    directory: Any,
    document: dict[str, Any],
    *,
    contract: Any = None,
    rework: bool = True,
    ask: Any = None,
) -> dict[str, Any]:
    """Apply a change-set to an authoring bundle on disk, and say what happened.

    The bundle is the unit a caller already has: a directory holding ``contract.json``, the world,
    and ``scenarios/``. Everything needed to decide and to re-prove is inside it, so this needs no
    sandbox and no network. Whoever owns the archive extracts it, calls this, and packs it back.

    ``contract`` is passed in where the caller already holds it, because the two bundle layouts
    disagree: a sealed archive carries ``contract.json`` at its root and a live authoring directory
    does not. Left out, it is read from the file, and its absence is said plainly rather than
    surfacing as a missing-file traceback.

    ``rework=False`` applies only what cannot matter and refuses the rest, which is how a caller
    with no model budget still gets its cosmetic edits without risking an incoherent suite.
    """
    from pathlib import Path

    from .catalogue import load_catalogue
    from .contract import AgentContract
    from .scenario_tools import load_scenarios, write_scenarios

    root = Path(directory)
    if contract is None:
        document_path = root / "contract.json"
        if not document_path.is_file():
            raise ValueError(
                f"no contract at {document_path}: a live authoring directory does not carry one, "
                "so pass the contract this bundle was authored from"
            )
        contract = AgentContract.model_validate_json(
            document_path.read_text(encoding="utf-8")
        )
    changes = parse_changes(document)
    before = load_scenarios(root)

    kept, receipts = amend(
        before,
        changes,
        contract=contract,
        consider=reworker_for(contract, root, ask=ask) if rework else None,
    )

    # Written once, after every change has resolved. `write_scenarios` regenerates the index over
    # what it is given, so a dropped scenario leaves the index and the folders agreeing rather than
    # leaving an entry pointing at nothing.
    if any(one.outcome != "refused" for one in receipts):
        write_scenarios(kept, root, load_catalogue(root))

    return {
        "schema": SCHEMA,
        "scenarios": len(kept),
        "receipts": [one.as_dict() for one in receipts],
    }
