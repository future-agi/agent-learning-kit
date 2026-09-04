"""The sub-goals this agent can be checked on, shared by every scenario that needs one.

Defined once for the agent rather than restated per scenario, which is what makes results roll
up: the same sub-goal failing in seven of twelve scenarios is one sentence rather than seven.

``check`` is Python written by the harness. It is given what the run left behind and returns
nothing if the sub-goal held, or a sentence saying what was wrong. Code rather than a mini
language because an environment can be a database, a filesystem or a page, and a language
invented here would fit only the first.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

CATALOGUE = "sub_goals.json"


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


class SuiteEval(BaseModel):
    """One built-in Future AGI eval applied to every compatible scenario."""

    name: str
    required_inputs: list[str] = Field(default_factory=lambda: ["conversation"])
    minimum_score: float | None = None


def default_suite_evals() -> list[SuiteEval]:
    """The two verified built-in evals initially run for every voice scenario."""
    return [
        SuiteEval(
            name="customer_agent_task_completion",
            required_inputs=["agent_prompt", "conversation"],
        ),
        SuiteEval(
            name="customer_agent_conversation_quality",
            minimum_score=4,
        ),
    ]


class Catalogue(BaseModel):
    """Every sub-goal this agent has, defined once."""

    sub_goals: list[SubGoal] = Field(default_factory=list)
    # Deliberately separate from sub-goals: these assess every scenario, while a sub-goal only
    # applies where a scenario names it.
    suite_evals: list[SuiteEval] = Field(default_factory=default_suite_evals)

    def named(self, name: str) -> SubGoal | None:
        return next((one for one in self.sub_goals if one.name == name), None)

    def names(self) -> set[str]:
        return {one.name for one in self.sub_goals}

    def merged(self, other: "Catalogue") -> "Catalogue":
        """This catalogue plus anything only `other` holds, this one winning on a shared name.

        Writers run at the same time and each reads the file once, when its tools are built, so
        every copy goes stale the moment a sibling adds a sub-goal. Whatever writes has to fold the
        file back in first, or it saves its own view over everybody else's.
        """
        mine = self.names()
        return Catalogue(
            sub_goals=[
                *self.sub_goals,
                *(one for one in other.sub_goals if one.name not in mine),
            ],
            suite_evals=self.suite_evals,
        )

    def suite_eval(self, name: str) -> SuiteEval | None:
        return next((one for one in self.suite_evals if one.name == name), None)


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
    # A refusal graded by demanding the forbidden call fails the agent that correctly declined and
    # passes the one that tried. Nine of thirty one scenarios in one run were graded this way, and
    # the suite would have told a customer that its better agent was worse. The shape gives it
    # away: the sub-goal is about something not happening, and the check errors when it did not
    # happen.
    about_refusal = re.search(
        r"\b(refus|declin|reject|must not|never|block|deny|denied|without|unverified|invalid)\b",
        f"{sub_goal.name} {sub_goal.what}",
        re.I,
    )
    demands_attempt = re.search(
        r"return\s+f?[\"'][^\"']*\b(was never called|not called|did not attempt|never attempted|"
        r"was not attempted|no \w+ calls?)\b",
        sub_goal.check,
        re.I,
    )
    if about_refusal and demands_attempt:
        problems.append(
            f"{sub_goal.name}: this grades a refusal by requiring the forbidden call to appear, so "
            "an agent that correctly declined fails and one that tried passes. Assert the end "
            "state instead: that the thing which must not happen did not happen"
        )
    return problems


def save_catalogue(catalogue: Catalogue, destination: Path) -> Path:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / CATALOGUE
    path.write_text(
        json.dumps(catalogue.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def load_catalogue(destination: Path) -> Catalogue:
    path = Path(destination) / CATALOGUE
    if not path.exists():
        return Catalogue()
    return Catalogue.model_validate(json.loads(path.read_text(encoding="utf-8")))
