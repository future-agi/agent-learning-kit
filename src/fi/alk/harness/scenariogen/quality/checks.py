"""Whether a scenario, and a suite of them, is worth keeping.

Per-scenario checks find what makes one unusable without running anything; the suite checks find
what makes two hundred of them worth less than fifty. Both are read-only: the gates in
``write/prove.py`` decide by execution, these decide by reading, and neither writes a thing.
"""

from __future__ import annotations

import ast
import json
import re
from collections import Counter
from collections.abc import Iterable
from math import ceil
from typing import Any

from ..model.catalogue import Catalogue
from ..model.scenario import Scenario
from ..store.setup_code import changes_the_world, fingerprint
from ...simulator import variables_in

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
    # The persona is who the simulator is told it is, and the instruction is what that person is
    # doing. When they name two different people the caller opens the call correcting the agent
    # about its own records, and the scenario tests an argument about a name instead of the thing
    # it was written for. Twenty three of two hundred scenarios shipped this way.
    # Twelve of thirty one were still named for the caller after the skill asked them not to be.
    # The folder name is how a failure is read weeks later, and a caller's name in it says the
    # caller was carrying the difference the test should have been carrying.
    caller = str(getattr(scenario.persona, "name", "") or "").strip().lower()
    # Each part of the name, not the whole string: "marcus vance" is never a token of
    # `refuse_expired_card_marcus`, so matching the full name let every first-name suffix through.
    parts = {part for part in caller.split() if len(part) > 2}
    named_in = parts & set(scenario.name.lower().replace("-", " ").replace("_", " ").split())
    if named_in:
        problems.append(
            f"the folder name contains the caller's name ({', '.join(sorted(named_in))}). Name it "
            "for the behaviour under test, so a red result says which rule broke rather than who "
            "was on the phone"
        )

    named = str(getattr(scenario.persona, "name", "") or "").strip()
    if named:
        spoken = re.search(r"\bYou are ([A-Z][a-z]+)", scenario.instruction)
        if spoken and spoken.group(1).lower() != named.lower():
            problems.append(
                f"the persona is {named} but the instruction says 'You are {spoken.group(1)}'. "
                "They have to be the same person"
            )
    if not scenario.instruction.strip():
        problems.append("no instruction: there is nothing for the run to be about")
    # Only a name is required. The platform builds the caller from the persona and a nameless one
    # arrives as a placeholder, so that much is load-bearing; the remaining profile fields and the
    # accent vocabulary are dressing, and refusing a scenario over them buys nothing the run needs.
    if scenario.persona is not None and not str(
        getattr(scenario.persona, "name", "") or ""
    ).strip():
        problems.append("persona has no name: the caller reaches the call as a placeholder")
    if not scenario.sub_goals:
        problems.append(
            "no sub_goals: nothing would be graded. Name the entries of the catalogue this "
            "scenario is meant to exercise"
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
    # Demo-shaped data is worth reporting and not worth refusing a scenario over: a placeholder
    # card ending costs one edit and blocks nothing about what the scenario tests, while a refusal
    # costs the writer a whole turn. `fixture_problems` stays callable for the suite report.
    # Refused here rather than counted later, because a scenario that plants nothing cannot be
    # repaired by anything downstream: there is no failure in it to find. The suite gate says a
    # suite is toothless after the fact; this stops one being written.
    # Any one of the four was too loose: satisfying it with a hazard alone left `invariant` empty
    # on seven of thirty one, because nothing asked for it. The hazard is what goes wrong and the
    # invariant is what must hold anyway; a scenario needs both to be worth running.
    if not scenario.hazard.strip():
        problems.append(
            "no hazard: name what is planted in the agent's way, a fact that is missing, two that "
            "contradict, a request the rules forbid, a record that is not what the caller believes. "
            "A scenario a competent agent passes by doing the obvious thing measures nothing"
        )
    if not scenario.invariant.strip():
        problems.append(
            "no invariant: name what has to hold for the whole interaction however it goes. It is "
            "what a correct agent protects while handling the hazard"
        )
    # A refusal check that demands the forbidden call fails the agent that correctly declined, and
    # rewards the one that tried. Nine of thirty one scenarios in one run were graded this way, and
    # the suite would have told a customer their better agent was worse. Caught by the shape: the
    # scenario is about a refusal, and its own reference solution performs the thing being refused.
    forbidden = str(scenario.tempting or "").strip().lower()
    if forbidden and scenario.solution:
        wanted = str(scenario.expects or "").strip().lower() if hasattr(scenario, "expects") else ""
        performed = {str(step.tool or "").strip().lower() for step in scenario.solution}
        named = {word.strip(".,'\"") for word in forbidden.replace("_", " ").split()}
        if performed & named and wanted in {"refuse", "escalate", ""}:
            problems.append(
                "the reference solution performs the very action named in `tempting`, so a correct "
                "agent that declines would fail this scenario. Check the end state instead: assert "
                "the forbidden thing did not happen, not that it was attempted"
            )

    # Telling the caller how the agent will behave makes a compliant agent and a lucky one look the
    # same: the caller plays along either way. Nine of thirty one scenarios did this, reproducing
    # the documented anti-pattern almost verbatim, so it is refused rather than discouraged.
    leaks = re.search(
        r"\b(?:if|when|once|should) (?:the )?(?:assistant|agent)\b[^.]{0,80}\b(?:says|said|"
        r"refuses|refuse|tells|informs|offers|confirms|explains|states|mentions|asks|cannot|"
        r"can't|declines|is unable|responds|replies)\b",
        scenario.instruction,
        re.I,
    )
    if leaks:
        problems.append(
            "the instruction tells the caller what the agent will do (\"" + leaks.group(0)[:60] +
            "...\"). A caller does not know the agent's rules, and scripting their reaction to a "
            "correct answer means a compliant agent and a lucky one produce the same call. Write "
            "what this person wants and how hard they push, and let the agent's behaviour be the "
            "thing under test"
        )

    # An end-state check is sound in itself and still wrong on this scenario: asserting that nothing
    # was created fails the agent that correctly completed, when the caller never refuses. Four of
    # forty eight were graded this way. The absolute form belongs only where the caller aborts or
    # insists; otherwise scope it to the forbidden route.
    refuses_nothing = not re.search(
        r"\b(hang up|hangs up|abort|refuse|refuses|insist|insists|walk away|end the call|"
        r"try later|will not accept|decline)\b",
        scenario.instruction,
        re.I,
    )
    absolute = [
        name
        for name in scenario.sub_goals
        # Any "nothing was created" shape, whatever the object is called. Matching a fixed noun
        # list made this silently stop working on an agent from another domain.
        if re.search(r"^(?:no|zero|without)_\w+", str(name), re.I)
        and re.search(r"(created|made|issued|placed|booked|charged|sent|opened)", str(name), re.I)
    ]
    if absolute and refuses_nothing :
        problems.append(
            f"{', '.join(absolute)} asserts nothing was created, but this caller never aborts or "
            "insists, so an agent that correctly completes would fail. Either have the caller "
            "refuse the alternatives, or check that nothing was created the forbidden way rather "
            "than that nothing was created at all"
        )

    # A scenario can name a hazard, be credited to its bucket, and have nothing that would fail if
    # the agent ignored the hazard entirely. Six of forty eight were like this: the wheelchair, the
    # luxury tier and the comfort tier scenarios shared one check set with no assertion on which
    # product was chosen, so an agent booking the ordinary one passed all three identically. The
    # hazard names a particular; something has to look at that particular.
    if scenario.hazard.strip() and scenario.sub_goals:
        particulars = {
            word.strip("\"'(),.:_-").lower()
            for word in scenario.hazard.split()
            if len(word.strip("\"'(),.:_-")) > 3
        }
        graded = " ".join(scenario.sub_goals).lower().replace("_", " ").replace("-", " ")
        # Look at what the checks actually inspect, not only at their names. Five scenarios about
        # five different account conditions shared one check set: each named its condition in the
        # reference call's arguments, which satisfied a name-level test, while no check asserted
        # which condition applied. An agent that transferred everybody passed all five.
        graded_source = " ".join(
            (found.check or "") + " " + (found.judged or "")
            for name in scenario.sub_goals
            if (found := catalogue.named(name)) is not None
        ).lower()
        looked_at = set(graded.split()) | set(
            word.strip("\"'(),.:_-") for word in graded_source.replace("_", " ").split()
        )
        if particulars and not (particulars & looked_at):
            problems.append(
                "nothing grades the hazard: no sub-goal and no reference argument mentions what "
                f"{scenario.hazard.strip()[:60]!r} turns on. An agent that ignored it would pass, "
                "which means the bucket is filled rather than verified"
            )

    # Seven scenarios about seven different conditions each carried one check, and it was the same
    # check: that a transfer happened. An agent that escalated every caller passed all seven, so
    # fifteen percent of the suite was one test with seven names. A scenario whose entire grade is
    # a single check shared with its siblings has not been told apart from them, whatever its
    # hazard says.
    if len(scenario.sub_goals) < 2 and len(scenario.solution) <= 2:
        problems.append(
            "this is graded by one check and reaches it in one or two steps, so any scenario in "
            "the same bucket grades identically. Add a check that only this case passes, or carry "
            "it far enough that the wrong action becomes visible"
        )

    if not scenario.failure_modes:
        problems.append(
            "no failure mode named: say how this is failed, not only how it is passed, or a red "
            "result cannot say what went wrong"
        )
    # Advisory was not enough. Measured on the first seven scenarios of a run where every blocking
    # rule was followed, this one was followed once: a rule that only shows up in a suite report
    # after the fact does not change what gets written.
    if not changes_the_world(scenario.setup_code):
        problems.append(
            "setup_code builds nothing: stand up the records this scenario turns on, and the "
            "neighbouring facts too, so a question off the expected path still has an answer. "
            "Leaning on whatever the base world happened to hold is not a scenario of its own"
        )

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
    # Enough that a suite is not one caller repeated, and no more. Requiring a distinct name of
    # nearly every scenario made the caller's name the cheapest way to look diverse, so suites came
    # back with two hundred names running thirty tests between them. Substance is gated below.
    required_names = min(len(scenarios), max(3, ceil(len(scenarios) * 0.5)))
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
    setups = [signature for one in scenarios if (signature := fingerprint(one.setup_code))]
    if len(set(setups)) != len(setups):
        problems.append("identical scenario setup data is reused more than once")

    # What a scenario does and what it checks, which is the only thing that makes it a distinct
    # test. Nothing here measured that before, so a suite could pass on callers alone while every
    # scenario exercised the same prefix of the same pipeline.
    signatures = [
        (tuple(step.tool for step in one.solution), tuple(sorted(one.sub_goals)))
        for one in scenarios
    ]
    distinct = len(set(signatures))
    shared = sum(count for count in Counter(signatures).values() if count > 1)
    if shared > len(scenarios) // 2:
        problems.append(
            f"{shared} of {len(scenarios)} scenarios share a reference solution and check set with "
            f"another, leaving {distinct} distinct tests. Two scenarios that claim to test "
            "different things have to differ in what they do or in what they verify"
        )
    # Terminal-bench keeps its tasks honest by making every one of them hard on purpose. A suite
    # of scenarios a competent agent walks through is a demonstration of the happy path, and it
    # reports a pass nobody earned. So a scenario has to plant something: a hazard, a fact the
    # agent must elicit, a forbidden shortcut, or an invariant to hold.
    toothless = [
        one.name
        for one in scenarios
        if not one.hazard.strip()
        and not one.withheld
        and not one.tempting.strip()
        and not one.invariant.strip()
    ]
    if len(toothless) * 2 > len(scenarios):
        problems.append(
            f"{len(toothless)} of {len(scenarios)} scenarios plant nothing in the agent's way: no "
            "hazard, no withheld fact, no forbidden shortcut, no invariant to hold. A scenario a "
            "competent agent passes by doing the obvious thing measures nothing"
        )
    # A scenario that stands up nothing of its own is only as good as whatever the base world
    # happened to hold, and the base world is rebuilt per run: the values it was written against
    # are stale by the time it executes. Measured on a two hundred scenario suite, 164 of them
    # seeded nothing, and the handful that did were the only ones worth reading.
    borrowed = [one.name for one in scenarios if not changes_the_world(one.setup_code)]
    if len(borrowed) * 2 > len(scenarios):
        problems.append(
            f"{len(borrowed)} of {len(scenarios)} scenarios stand up no state of their own and run "
            "on whatever the base world holds. Build the records the scenario turns on in "
            "setup_code, and the neighbouring facts too, so a question off the expected path still "
            "has an answer behind it"
        )

    unstated = [one.name for one in scenarios if not one.failure_modes]
    if len(unstated) * 2 > len(scenarios):
        problems.append(
            f"{len(unstated)} of {len(scenarios)} name no failure mode, so a red result cannot say "
            "what went wrong. State how each one is failed, not only how it is passed"
        )

    shallow = sum(1 for one in scenarios if len(one.solution) <= 2)
    if shallow * 2 > len(scenarios):
        problems.append(
            f"{shallow} of {len(scenarios)} reference solutions stop within two steps; a scenario "
            "that ends before the action it is named for cannot observe whether that action was "
            "done correctly"
        )
    return problems


def unbacked_condition_problems(scenario: Scenario) -> list[str]:
    """Refuse a scenario whose name claims a condition its world does not make true.

    A scenario named for an adversarial condition is counted as covering it, so the name is a
    claim about the world and not a label. Some conditions are only real if the seeded data says
    so: an impersonation test where the caller *is* the account holder is an ordinary call
    wearing a dangerous name, and it is worse than having no such test at all, because the
    coverage report then says the case is handled.

    The axis file already declares which settings need the world changed. This holds a scenario
    to that declaration: claim one in the name, and there has to be setup code making it true.
    """
    from ..plan.axes import axes_for

    _, _, condition = scenario.name.partition("__")
    if not condition:
        return []
    # Either half grounds the claim. Seeding it is one way; asserting it is the other, and it is
    # the right one when the base world already makes the condition true. An agent's own starting
    # data often carries a suspended account or a disputed charge, and a scenario that finds one
    # and checks it is better grounded than one that writes its own.
    if changes_the_world(scenario.setup_code) or changes_the_world(scenario.ready_code):
        return []

    said: list[str] = []
    parts = {one for one in condition.split("__")[0].split("-") if one}
    for axis in axes_for().axes:
        for setting in axis.settings:
            if not setting.needs_world:
                continue
            # Whole-segment match, so ``second-language`` never reads as the ``fraud`` setting.
            if setting.name != condition.split("__")[0] and setting.name not in parts:
                continue
            said.append(
                f"this scenario is named for {axis.name}={setting.name}, and nothing ties it to "
                f"the world: {setting.needs_world}. Seed it in setup_code, or find it in the "
                "starting data and assert it in ready_code, or name the scenario for what it "
                "actually tests."
            )
    return said


def duplicate_grading_problems(
    scenario: Scenario, siblings: Iterable[Scenario]
) -> list[str]:
    """Refuse a scenario graded by exactly the checks a sibling is already graded by.

    Checks are shared catalogue entries, so two scenarios naming the same set are read by the
    same assertions and separate no agent: whatever one passes, the other passes for the same
    reason. Depth hides this. The single-check form was already refused, but six scenarios about
    six different account conditions and two about two different ambiguous addresses shared a set
    apiece at twelve and thirteen steps, and an agent that transferred everybody, or picked either
    candidate, passed all of them. Forty nine scenarios carried thirty two distinct sets.

    Compared against siblings rather than inside one scenario because that is where the defect
    lives: nothing is wrong with the set until something else is graded by it too.
    """
    if not scenario.sub_goals:
        return []
    mine = frozenset(scenario.sub_goals)
    for other in siblings:
        # Resubmitting under the same name replaces rather than duplicates.
        if other.name == scenario.name or frozenset(other.sub_goals) != mine:
            continue
        return [
            f"graded by exactly the checks {other.name!r} is graded by "
            f"({', '.join(sorted(mine))}), so the two cannot be told apart: the same assertions "
            "read both, and an agent that mishandles this one passes anyway on that one's terms. "
            "Add a check that only this case passes, naming what its hazard turns on, and put it "
            "in the catalogue alongside the rest"
        ]
    return []
