"""A scenario: a delta on the base environment, and what must hold afterwards.

The base is built once — the world, the simulator's prompt, the catalogue of sub-goals. A
scenario changes a few values in that world, fills the prompt's slots, and names which sub-goals
must hold. It is not a template with values slotted into it; the harness writes each one.

It also carries a **solution**: what a correct agent would do. That is not decoration. It is what
proves, before the scenario is ever used, that the scenario can be passed at all and that its
checks are not vacuous — the two gates in ``prove.py``. Terminal-bench keeps its tasks honest the
same way, and it needs no model to do it.

There is no persona and no opening line. Variability comes from real conditions — an item in
stock or not, a customer who exists or does not — which live in ``setup``, not from an invented
character.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .environment import Catalogue, variables_in


class Step(BaseModel):
    """One action in a reference solution."""

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Scenario(BaseModel):
    """One test: what changes, what is asked, what a correct agent does, what must hold."""

    name: str
    use_case: str = ""
    tests: str = ""

    # What this scenario changes about the world after it is reset. The base world stays the
    # shared starting point; this is the only sanctioned way a scenario differs from it.
    setup: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)

    # The task. For a conversational agent it fills the simulator prompt's instruction slot; for
    # a browser or coding agent it goes to the agent directly.
    instruction: str = ""
    # Anything else that prompt asks for, by slot name.
    variables: dict[str, str] = Field(default_factory=dict)

    # What a correct agent would do. Run by the gates, never by the agent under test.
    solution: list[Step] = Field(default_factory=list)

    # Which entries of the shared catalogue must hold. Named, not restated, so results roll up
    # across the suite: the same sub-goal failing in seven of twelve scenarios is one sentence.
    sub_goals: list[str] = Field(default_factory=list)

    max_turns: int = 10

    def slots(self) -> dict[str, str]:
        """Every value this scenario offers the simulator prompt."""
        return {"instruction": self.instruction, **self.variables}


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

    for table, rows in scenario.setup.items():
        if table not in world_state:
            problems.append(
                f"setup changes {table!r}, which this world does not have. It has: "
                f"{', '.join(sorted(world_state)) or 'nothing'}"
            )
            continue
        columns = set(world_state[table][0]) if world_state[table] else set()
        for row in rows or []:
            unknown_columns = sorted(set(row) - columns) if columns else []
            if unknown_columns:
                problems.append(
                    f"setup into {table} sets columns it does not have: "
                    f"{', '.join(unknown_columns)}"
                )

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
    return problems
