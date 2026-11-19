"""Values a rule or a tool used to state and no longer does, and the case texts still stating them.

When a rule changes (a 30-day refund window made 20, then 14), the cases and sub-goals written against it keep their
old words unless someone carries the new value into them. The contract records each changed rule and tool
description before and after (`value_changes`). A value is retired only where the two line up word for word and the
number in that place changed, with the word after it (`30 days`, `30-day`): so only the value the change replaced, and
`30` alone never matches `130`. Contracts saved before that record are read from their `rule removed` / `rule added`
amendments the same way.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from difflib import SequenceMatcher
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
    for tool in _field(contract, "tools") or []:
        texts += [str(_field(tool, "description") or ""), str(_field(tool, "refuses") or "")]
    return {value for text in texts for value in values_in(text)}


# A number, a word, or one other character: what two versions of a rule are lined up by.
_TOKEN = re.compile(r"\d+(?:\.\d+)?|[A-Za-z]+|\S")
_SAME_RULE = 0.8


def _tokens(text: str) -> tuple[list[str], list[str]]:
    tokens = _TOKEN.findall(text or "")
    return tokens, ["#" if token[0].isdigit() else token.casefold() for token in tokens]


def _likeness(before: str, after: str) -> float:
    first, second = _tokens(before)[1], _tokens(after)[1]
    return SequenceMatcher(None, first, second, autojunk=False).ratio() if first and second else 0.0


def same_rule(before: str, after: str) -> bool:
    """Whether `after` is `before` with its values changed: the two read alike with every number set aside."""
    return _likeness(before, after) >= _SAME_RULE


def _cuts(body: str) -> list[str]:
    """Where an amendment's rule may end: before each `: ` (amend.py writes `{rule}: {why}`, and a why can hold its
    own `: `), or the whole line."""
    return [body[: at.start()] for at in re.finditer(r": ", body)] + [body]


def _rule_pair(removed: str, added: str) -> tuple[str, str] | None:
    """The two rules a removed and an added amendment name, each without its why: the cuts that read most alike, the
    shortest on a tie. None when no cut makes them the same rule."""
    best = max(
        ((_likeness(one, two), -len(one) - len(two), one, two) for one in _cuts(removed) for two in _cuts(added)),
        default=None,
    )
    return (best[2], best[3]) if best is not None and best[0] >= _SAME_RULE else None


def changed_values(before: str, after: str) -> set[tuple[str, str]]:
    """The values `before` states where `after` says the same words around another number: what the change
    retired, each with its unit."""
    (old, old_masked), (new, new_masked) = _tokens(before), _tokens(after)
    retired = set()
    matcher = SequenceMatcher(None, old_masked, new_masked, autojunk=False)
    for tag, start, end, other, _ in matcher.get_opcodes():
        if tag != "equal":
            continue
        for offset in range(end - start):
            at = start + offset
            if old_masked[at] != "#" or old[at] == new[other + offset]:
                continue
            unit = at + 1
            while unit < len(old) and old[unit] in ("-", "_"):
                unit += 1
            if unit < len(old) and old[unit].isalpha():
                retired.add((old[at], _unit(old[unit])))
    return retired


_REMOVED, _ADDED = "rule removed — ", "rule added — "


def _changes(contract: Any) -> list[tuple[str, str]]:
    """Each changed rule or tool text, before and after: the recorded ones, else (a contract saved before they were
    recorded) each removed rule paired with the next added one that reads alike."""
    recorded = [
        (str(_field(one, "before") or ""), str(_field(one, "after") or ""))
        for one in _field(contract, "value_changes") or []
    ]
    if recorded:
        return [pair for pair in recorded if pair[0] and pair[1]]
    lines = [str(one) for one in _field(contract, "amendments") or []]
    pairs, used = [], set()
    for at, line in enumerate(lines):
        if not line.startswith(_REMOVED):
            continue
        for index in range(at + 1, len(lines)):
            if index in used or not lines[index].startswith(_ADDED):
                continue
            # Each line's why is cut off first: two whys never decide whether the rules are one.
            pair = _rule_pair(line[len(_REMOVED):], lines[index][len(_ADDED):])
            if pair is not None:
                used.add(index)
                pairs.append(pair)
                break
    return pairs


def retired_values(contract: Any) -> set[tuple[str, str]]:
    """The values a changed rule or tool replaced that no rule or tool states now."""
    if contract is None:
        return set()
    retired = {value for before, after in _changes(contract) for value in changed_values(before, after)}
    return retired - current_values(contract)


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
