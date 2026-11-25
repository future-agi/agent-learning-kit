"""What a scenario's grading must do beyond holding on its reference, shared by every vertical.

A run that stops early must fail: every strict prefix of the reference solution (nothing, the
lookups alone, everything but the last action) leaves some deterministic sub-goal unmet, unless
the steps it leaves out are listed as optional with a reason.

A check sees the world and the calls, never what the agent said, so a task whose outcome is an
answer or a reply is graded by its judged criterion. An answer control plays the reference, gives
the real judge the caller's opening and a scripted reply, and asks for one sub-goal's verdict: the
correct reply must pass, and a wrong one (a wrong figure, date or status, a false completion
claim, no answer at all) must fail. The judge needs a model, so the controls run only with
ALK_LIVE_JUDGE=1 and the harness model configured (ALK_HARNESS, its credentials).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from fi.alk.harness.catalogue import load_catalogue
from fi.alk.harness.checks import reference_of, run_check
from fi.alk.harness.folder import read_folder
from fi.alk.harness.judge import judge_sub_goal
from fi.alk.harness.prove import run_steps
from fi.alk.harness.releases import WORLD

live_judge = pytest.mark.skipif(
    os.environ.get("ALK_LIVE_JUDGE") != "1", reason="ALK_LIVE_JUDGE=1 runs the judge on a model"
)


def passing_prefixes(built: Path, suite: Path, key: str) -> list[int]:
    """The lengths of the strict prefixes of `key`'s reference after which every deterministic
    sub-goal holds. Empty is what a scenario that grades its outcome gives."""
    scenario = read_folder(suite, key)
    catalogue = load_catalogue(suite)
    reference = reference_of(scenario.solution)
    goals = [
        (name, goal)
        for name in scenario.sub_goals
        if (goal := catalogue.named(name)) is not None and goal.deterministic()
    ]
    passing = []
    for length in range(len(scenario.solution)):
        world, calls, _refused, _ = run_steps(
            scenario, built / WORLD, with_solution=True, steps=scenario.solution[:length]
        )
        try:
            if goals and all(
                run_check(goal.check, world, calls, name=name, reference=reference).held
                for name, goal in goals
            ):
                passing.append(length)
        finally:
            world.close()
    return passing


# The reads whose text a decision rests on: mail, Slack and documents.
EVIDENCE = ("get_thread", "get_message", "slack_read_thread", "read_file_content", "fetch_passage")


def read_last(steps, *tools: str) -> list:
    """`steps` with every step of `tools` moved to the end: the evidence read after acting on it."""
    return sorted(steps, key=lambda step: step.tool in tools)


def passing_read_last(built: Path, suite: Path, key: str, writes: set[str]) -> bool:
    """Whether every deterministic sub-goal of `key` still holds when its evidence is read last. A
    reference that fixes no evidence, or commits no write (`writes`), has no order to keep."""
    scenario = read_folder(suite, key)
    if not any(step.tool in EVIDENCE and step.fixed for step in scenario.solution):
        return False
    steps = read_last(scenario.solution, *EVIDENCE)
    catalogue = load_catalogue(suite)
    reference = reference_of(scenario.solution)
    world, calls, _refused, _ = run_steps(
        scenario, built / WORLD, with_solution=True, steps=steps
    )
    try:
        return any(call.ok and call.name in writes for call in calls) and all(
            run_check(goal.check, world, calls, name=name, reference=reference).held
            for name in scenario.sub_goals
            if (goal := catalogue.named(name)) is not None and goal.deterministic()
        )
    finally:
        world.close()


def judge_reply(built: Path, suite: Path, key: str, goal: str, reply: str) -> tuple[bool | None, str]:
    """The judge's verdict on `goal` for a run that made the reference's calls and then said
    `reply` to the caller's opening message."""
    scenario = read_folder(suite, key)
    sub_goal = load_catalogue(suite).named(goal)
    assert sub_goal is not None and sub_goal.judged.strip(), f"{key}: {goal} has no judged criterion"
    world, calls, _refused, _ = run_steps(scenario, built / WORLD, with_solution=True)
    try:
        messages = [
            {"role": "user", "content": scenario.persona.initial_message},
            {"role": "assistant", "content": reply},
        ]
        return asyncio.run(
            judge_sub_goal(sub_goal, _Inspected(world), calls, messages=messages, scenario=scenario)
        )
    finally:
        world.close()


class _Inspected:
    """A local world seen through the hosted world's read interface, which the judge's
    inspection tools use: one table or all of them, and a read-only query."""

    def __init__(self, world) -> None:
        self._world = world

    def state(self, table: str | None = None) -> dict:
        state = self._world.state()
        return {table: state.get(table, [])} if table else state

    def query(self, sql: str) -> list[dict]:
        return self._world.store.query(sql)

    def __getattr__(self, name: str):
        return getattr(self._world, name)
