"""Values a rule or a tool used to state and no longer does, and the case texts still stating them.

When a rule changes (a 30-day refund window made 20, then 14), the cases and sub-goals written against it keep their
old words unless someone carries the new value into them. The contract records every change in `amendments`, so the
values a changed rule or tool once stated are known across conversations. A value is a number with the word after it
(`30 days`, `30-day`): the word scopes it to what the rule governed, so `delivered 41 days ago` is never taken for
the old window, and `30` alone never matches `130`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

# A number and the word that follows it, joined by a space, a hyphen or an underscore: `30 days`, `30-day`,
# `outside_30_day_window`, `4.5 percent`.
_VALUE = re.compile(r"(?<![\d.,])(\d+(?:\.\d+)?)(?:\s+|[-_])([A-Za-z]+)")


def _unit(word: str) -> str:
    word = word.casefold()
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def values_in(text: str) -> set[tuple[str, str]]:
    """Each number in `text` with the word after it, the word singular: `{("30", "day")}`."""
    return {(number, _unit(word)) for number, word in _VALUE.findall(text or "")}


def _field(source: Any, name: str) -> Any:
    return source.get(name) if isinstance(source, Mapping) else getattr(source, name, None)


def current_values(contract: Any) -> set[tuple[str, str]]:
    """The values the agent's rules and tool descriptions state now. Not its prompt: a rule changed here overrides
    what the agent's own source says."""
    if contract is None:
        return set()
    texts = [str(one) for one in _field(contract, "hard_constraints") or []]
    texts += [str(_field(tool, "description") or "") for tool in _field(contract, "tools") or []]
    return {value for text in texts for value in values_in(text)}


def retired_values(contract: Any) -> set[tuple[str, str]]:
    """The values a changed rule or tool stated before (from the contract's amendments) that no rule or tool states
    now."""
    if contract is None:
        return set()
    stated = {value for line in _field(contract, "amendments") or [] for value in values_in(str(line))}
    return stated - current_values(contract)


def scenario_texts(scenario: Any) -> list[tuple[str, str]]:
    """Where a scenario states things in words: its name, use case, tests, instruction, branch and each control's
    `why`."""
    name = str(_field(scenario, "name") or "")
    texts = [(f"{name}.name", name)]
    texts += [(f"{name}.{field}", str(_field(scenario, field) or "")) for field in ("use_case", "tests", "instruction", "branch")]
    texts += [
        (f"{name}.negative_controls.{_field(control, 'name')}.why", str(_field(control, "why") or ""))
        for control in _field(scenario, "negative_controls") or []
    ]
    return texts


def sub_goal_texts(goal: Any) -> list[tuple[str, str]]:
    name = str(_field(goal, "name") or "")
    return [
        (f"sub-goal {name}.{field}", str(_field(goal, field) or ""))
        for field in ("what", "judged", "applicability", "reference")
    ]


def old_value_mentions(contract: Any, texts: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Each `(where, value)` whose text still states a retired value, the value as `30 day`."""
    retired = retired_values(contract)
    if not retired:
        return []
    return [
        (where, f"{number} {unit}")
        for where, text in texts
        for number, unit in sorted(values_in(text) & retired)
    ]


def now_stated(contract: Any, unit: str) -> list[str]:
    """What the rules and tools state now for this unit (`14 day`), so a note can say what to write instead."""
    return sorted(f"{number} {one}" for number, one in current_values(contract) if one == unit)


def old_value_notes(contract: Any, scenarios: Iterable[Any], sub_goals: Iterable[Any]) -> list[str]:
    """One line per text still stating a value a changed rule or tool no longer states."""
    texts = [text for one in scenarios for text in scenario_texts(one)]
    texts += [text for one in sub_goals for text in sub_goal_texts(one)]
    notes = []
    for where, value in old_value_mentions(contract, texts):
        now = now_stated(contract, value.split(" ", 1)[1])
        notes.append(
            f"{where} still says {value}, which a changed rule or tool no longer states"
            + (f" (it now says {', '.join(now)})" if now else "")
        )
    return notes
