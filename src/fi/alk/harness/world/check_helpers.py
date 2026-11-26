"""Read-only helpers available to authored checks in the parent and the isolated interpreter."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

if __package__:
    from ..world_door import (
        AGENT,
        is_committed,
        is_pre_run,
        is_read,
        logged_as,
        named_alike,
        row_arguments,
        same_write,
    )
else:
    from world_door import (
        AGENT,
        is_committed,
        is_pre_run,
        is_read,
        logged_as,
        named_alike,
        row_arguments,
        same_write,
    )


_ID_NOISE = re.compile(r"[\s\-_.:/#]")


def same_id(first: Any, second: Any) -> bool:
    """Whether two identifiers name the same record however they were typed or spoken.

    Only separators and case are ignored, so a different number is still different."""
    if first is None or second is None:
        return False
    mine, theirs = (
        _ID_NOISE.sub("", str(first)).casefold(),
        _ID_NOISE.sub("", str(second)).casefold(),
    )
    # An empty id names no record, so it never matches, not even another empty one.
    return bool(mine) and mine == theirs


# A number as text: an optional sign, a currency (a symbol, a code or a word, before or after), thousands commas, a
# fraction. A leading zero (`007`) is an identifier's, never a number's.
_NUMBER_TEXT = re.compile(
    r"^(?P<sign>[-+]?)(?P<before>US\$|[$€£₹]|[A-Za-z]{3}(?=\s?\d))?\s?"
    r"(?P<digits>(?:0|[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d*)(?:\.\d+)?)\s?(?P<after>[A-Za-z]+\.?)?$"
)
# A currency symbol and the ISO 4217 code it stands for; any other currency is written as its code.
_CURRENCIES = {"$": "USD", "us$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}


def _number(value: Any) -> tuple[str, Decimal] | None:
    """A number and the currency it is written in ("" for none), by symbol or ISO code: `$89` and `89 USD` are one
    currency. A word after the number (`89 dollars`, `89 items`) makes it something other than an amount."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return "", Decimal(str(value))
    found = _NUMBER_TEXT.match(value.strip()) if isinstance(value, str) else None
    if not found or (found["before"] and found["after"]):
        return None
    marker = found["before"] or found["after"] or ""
    currency = _CURRENCIES.get(
        marker.casefold(), marker.upper() if len(marker) in (0, 3) else None
    )
    if currency is None:
        return None
    return currency, Decimal(found["sign"] + found["digits"].replace(",", ""))


def _same_amount(mine: tuple[str, Decimal], theirs: tuple[str, Decimal]) -> bool:
    """One amount: the same number, and never two different currencies. A bare number matches an amount in one
    currency, since the world often holds the number and the person says the currency."""
    return mine[1] == theirs[1] and (
        not mine[0] or not theirs[0] or mine[0] == theirs[0]
    )


def _iso(value: Any) -> date | time | datetime | None:
    """A date, time or date and time written in ISO 8601, read by the standard library; anything else is None."""
    if not isinstance(value, str) or not value.strip() or value.strip().isdigit():
        return None
    for kind in (date, datetime, time):
        try:
            return kind.fromisoformat(value.strip())
        except ValueError:
            continue
    return None


def same_value(first: Any, second: Any) -> bool:
    """Whether two values are the same value however they were written: a number by its value (`89`, `89.0`,
    `"$89.00"`, `"89 USD"`; two different currencies never match), an ISO 8601 date or time by its value
    (`14:00` and `14:00:00`), and any other text as `same_id` reads it (case and separators ignored). A date said in
    words is compared as text: write the reference in the form the world holds. Lists and objects compare item by
    item.

    It never makes two different values the same: another number, moment or id is still different."""
    if first is None or second is None:
        return first is None and second is None
    if isinstance(first, bool) or isinstance(second, bool):
        return type(first) is type(second) and first == second
    if isinstance(first, Mapping) or isinstance(second, Mapping):
        return (
            isinstance(first, Mapping)
            and isinstance(second, Mapping)
            and first.keys() == second.keys()
            and all(same_value(first[key], second[key]) for key in first)
        )
    if isinstance(first, (list, tuple)) or isinstance(second, (list, tuple)):
        return (
            isinstance(first, (list, tuple))
            and isinstance(second, (list, tuple))
            and len(first) == len(second)
            and all(same_value(one, two) for one, two in zip(first, second))
        )
    amounts = _number(first), _number(second)
    if amounts[0] is not None and amounts[1] is not None:
        return _same_amount(*amounts)
    moments = _iso(first), _iso(second)
    if moments[0] is not None and moments[1] is not None:
        return moments[0] == moments[1]
    return same_id(first, second)


# What the action log says of a call the world timed out on: refused before its commit, or committed with the answer
# lost (`world_door._faulted`).
TIMED_OUT = ("timeout_before_commit", "response_lost")


def _agent_writes(world: Any) -> list[Mapping[str, Any]]:
    """The agent's attempts to change the world this trial, in the order the world saw them."""
    return [
        row
        for row in world.events(actor_id=AGENT)
        if not is_read(row) and not is_pre_run(row.get("idempotency_key"))
    ]


def _fixed(reference: Sequence[Mapping[str, Any]], action: str) -> set[str]:
    """The arguments the reference fixes for this action: a check's reference keeps only those of each step."""
    return {
        field
        for step in reference or ()
        if named_alike(action, str(step.get("tool") or ""))
        for field in step.get("arguments") or {}
    }


def _repeats(
    first: Mapping[str, Any],
    later: Mapping[str, Any],
    reference: Sequence[Mapping[str, Any]],
    own_words: Sequence[str] = (),
) -> bool:
    """The same write of one record, as duplicate commits read it: rewording an argument the check names as the
    agent's own words (`own_words`) is still the same write."""
    action = logged_as(first)
    return logged_as(later) == action and same_write(
        first, later, _fixed(reference, action), own_words
    )


def _pair(first: Mapping[str, Any], later: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "action": logged_as(first),
        "arguments": row_arguments(first.get("arguments")),
        "first_action_id": first.get("action_id"),
        "later_action_id": later.get("action_id"),
        "later_outcome": later.get("outcome"),
    }


def retried_timeouts(
    world: Any,
    reference: Sequence[Mapping[str, Any]] = (),
    own_words: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Each write of the agent's the world timed out on that the agent then attempted again on the same record.
    `own_words` names arguments the agent may reword on a retry (a note); every other argument has to match."""
    rows = _agent_writes(world)
    found = []
    for index, first in enumerate(rows):
        if first.get("error") not in TIMED_OUT:
            continue
        retry = next(
            (
                later
                for later in rows[index + 1 :]
                if _repeats(first, later, reference, own_words)
            ),
            None,
        )
        if retry is not None:
            found.append(_pair(first, retry))
    return found


def repeated_commits(
    world: Any,
    reference: Sequence[Mapping[str, Any]] = (),
    own_words: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Each write of the agent's the world committed again on the same record: one entry per extra commit.
    `own_words` names arguments the agent may reword on a retry (a note); every other argument has to match."""
    committed = [row for row in _agent_writes(world) if is_committed(row)]
    found = []
    for index, later in enumerate(committed):
        first = next(
            (
                row
                for row in committed[:index]
                if _repeats(row, later, reference, own_words)
            ),
            None,
        )
        if first is not None:
            found.append(_pair(first, later))
    return found


def committed_writes(world: Any) -> list[dict[str, Any]]:
    """The agent's writes the world committed. None means any change the reply reports never happened."""
    return [
        {
            "action": logged_as(row),
            "arguments": row_arguments(row.get("arguments")),
            "action_id": row.get("action_id"),
        }
        for row in _agent_writes(world)
        if is_committed(row)
    ]


def _with(call: Any, arguments: Mapping[str, Any]) -> bool:
    given = getattr(call, "arguments", None) or {}
    return all(
        same_value(given.get(field), value) for field, value in arguments.items()
    )


def answered(calls: Sequence[Any], name: str, **arguments: Any) -> list[Any]:
    """The calls of tool `name` the world answered: each that went through and each its own rule turned down, a
    not-found included; never one that crashed or a fault failed. What "the agent looked X up" or "tried X" reads, so a
    lookup that correctly finds nothing still counts. `arguments` keeps only the calls made with those values."""
    return [
        call
        for call in calls
        if call.name == name
        and (
            getattr(call, "ok", False) is True
            or getattr(call, "refusal_code", "") == "world_rule"
        )
        and _with(call, arguments)
    ]


def succeeded(calls: Sequence[Any], name: str, **arguments: Any) -> list[Any]:
    """The calls of tool `name` that went through: what "the agent did X" reads. `arguments` keeps only the calls
    made with those values."""
    return [
        call
        for call in calls
        if call.name == name
        and getattr(call, "ok", False) is True
        and not getattr(call, "refused", False)
        and _with(call, arguments)
    ]


def came_before(
    calls: Sequence[Any], earlier: str, later: str, **arguments: Any
) -> bool:
    """Whether the world answered a call of `earlier` (`answered`, with these `arguments`) before the agent's first
    attempt at `later`, whatever came of that attempt. True when the agent never attempted `later`: a step that must
    come before something only fails when that something happened without it."""
    first = next((at for at, call in enumerate(calls) if call.name == later), None)
    return first is None or bool(answered(list(calls)[:first], earlier, **arguments))


def exactly_once(calls: Sequence[Any], name: str, **arguments: Any) -> bool:
    """Whether exactly one call of tool `name` with these `arguments` took effect: each that went through
    (`succeeded`), and each other one the world committed although its answer was lost (`response_lost`), each call
    counted once, so a retry after a lost answer counts twice. A try the world refused is not counted. Where the world keeps an action log,
    `repeated_commits(world, reference)` reads the commits themselves."""
    lost = [
        call
        for call in calls
        if call.name == name
        and getattr(call, "error", "") == "response_lost"
        and getattr(call, "ok", False) is not True
        and not getattr(call, "refused", False)
        and _with(call, arguments)
    ]
    return len(succeeded(calls, name, **arguments)) + len(lost) == 1


# Read-only helpers every check is given beside `world` and `calls`, each with whether it reads what a call came to
# (went through, answered, committed, timed out): a check that uses one is never proof of a step that did not run, as
# a call recorded but not run came to nothing. One table, so a new helper is never given without saying which.
_HELPERS: dict[str, tuple[Callable[..., Any], bool]] = {
    "answered": (answered, True),
    "succeeded": (succeeded, True),
    "came_before": (came_before, True),
    "exactly_once": (exactly_once, True),
    "same_id": (same_id, False),
    "same_value": (same_value, False),
    "retried_timeouts": (retried_timeouts, True),
    "repeated_commits": (repeated_commits, True),
    "committed_writes": (committed_writes, True),
}
CHECK_HELPERS = {name: helper for name, (helper, _reads) in _HELPERS.items()}
# The helpers that read what a call came to.
OUTCOME_HELPERS = frozenset(
    name for name, (_helper, reads) in _HELPERS.items() if reads
)


def check_namespace() -> dict[str, Any]:
    """The names every check is given when it runs, wherever it runs (authoring, a hosted trial's phase worker, an
    injected local world): the helpers authoring tells checks to use, such as ``same_id``. One set, so a check that
    proves while authoring never fails on a name a trial does not give it."""
    return dict(CHECK_HELPERS)
