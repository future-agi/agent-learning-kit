"""What the environment step produces: everything common to every test of one agent.

Three artifacts, and the harness writes all three. Nothing here decides their content.

- **the world** — whatever this agent acts on, subclassing ALK's ``EnvironmentAdapter`` so the
  runners that already exist can drive it
- **the simulator prompt** — for a conversational agent only, with variables left open for each
  scenario to fill
- **the sub-goal catalogue** — the named things this agent can be checked on, each carrying its
  own check as code

Kept together because they share a property: they are written once and every scenario is only a
delta on them. A scenario changes a few values, substitutes the simulator's variables, and says
which sub-goals must hold. That is what makes results roll up across a suite — a sub-goal is the
same sub-goal in all twelve scenarios, so "order confirmation fails in 7 of 12" is sayable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

CATALOGUE = "sub_goals.json"
SIMULATOR = "simulator_prompt.md"


class SubGoal(BaseModel):
    """One named thing the agent can be checked on, shared across every scenario that needs it.

    ``check`` is Python, written by the harness. It is given what the run left behind and returns
    nothing if the sub-goal held, or a sentence saying what was wrong. Code rather than a mini
    language because an environment can be a database, a filesystem or a page, and a language
    invented here would fit only the first.

    ``judged`` marks the ones nothing observable can settle — whether a refusal was explained,
    whether a price was invented. Those go to a model, and are the exception.
    """

    name: str
    what: str = ""
    check: str = ""
    judged: str = ""

    def deterministic(self) -> bool:
        return bool(self.check.strip())


class Catalogue(BaseModel):
    """Every sub-goal this agent has, defined once."""

    sub_goals: list[SubGoal] = Field(default_factory=list)

    def named(self, name: str) -> SubGoal | None:
        return next((one for one in self.sub_goals if one.name == name), None)

    def names(self) -> set[str]:
        return {one.name for one in self.sub_goals}


def validate_sub_goal(sub_goal: SubGoal) -> list[str]:
    """Problems that make a sub-goal unusable.

    A sub-goal that settles nothing is the expensive kind of wrong: every scenario referencing it
    reports a result nobody should believe.
    """
    problems: list[str] = []
    if not sub_goal.name.strip():
        problems.append("no name")
    if not sub_goal.what.strip():
        problems.append(f"{sub_goal.name}: no description of what it means")
    if not sub_goal.check.strip() and not sub_goal.judged.strip():
        problems.append(
            f"{sub_goal.name}: settles nothing. Give a check in code, or say what a judge has "
            "to decide and why nothing observable can settle it"
        )
    if sub_goal.check.strip() and "def check(" not in sub_goal.check:
        problems.append(
            f"{sub_goal.name}: a check must define check(world, calls) and return a problem as "
            "a string, or None when the sub-goal held"
        )
    return problems


def save_catalogue(catalogue: Catalogue, destination: Path) -> Path:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / CATALOGUE
    path.write_text(
        json.dumps(catalogue.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


def load_catalogue(destination: Path) -> Catalogue:
    path = Path(destination) / CATALOGUE
    if not path.exists():
        return Catalogue()
    return Catalogue.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save_simulator_prompt(prompt: str, destination: Path) -> Path:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / SIMULATOR
    path.write_text(prompt, encoding="utf-8")
    return path


def load_simulator_prompt(destination: Path) -> str:
    path = Path(destination) / SIMULATOR
    return path.read_text(encoding="utf-8") if path.exists() else ""


def variables_in(prompt: str) -> set[str]:
    """The slots a scenario has to fill.

    Written ``{{ name }}``, so the prompt stays readable as prose and a missing value is caught
    before a call is placed rather than appearing verbatim in what the simulated caller says.
    """
    import re

    return set(re.findall(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", prompt))


def fill(prompt: str, values: dict[str, Any]) -> tuple[str, list[str]]:
    """The simulator prompt for one scenario, and anything it left unfilled."""
    import re

    missing = sorted(variables_in(prompt) - set(values))

    def swap(match: re.Match[str]) -> str:
        return str(values.get(match.group(1), match.group(0)))

    filled = re.sub(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", swap, prompt)
    return filled, missing


def validate_simulator_prompt(prompt: str) -> list[str]:
    """Problems that make a simulator prompt unusable.

    Deliberately thin. What a good simulator prompt says is judgement, and belongs in the skill;
    what can be checked here is that it exists and that a scenario has somewhere to put its
    instruction, since a prompt with no variables is the same prompt for every scenario.
    """
    problems: list[str] = []
    if len(prompt.strip()) < 80:
        problems.append("too short to be a simulator prompt")
    if not variables_in(prompt):
        problems.append(
            "no variables: without a slot for the scenario's instruction, every scenario would "
            "run the same conversation. Write them as {{ instruction }}"
        )
    return problems
