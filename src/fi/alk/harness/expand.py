"""Turning one proved scenario into many, without calling a model.

A scenario that has passed its three gates carries a working environment: setup code that runs,
checks that hold with the reference solution and fail without it. Changing who is calling does
not touch any of that. The account is the same account, the ride is the same ride, the fare is
the same fare; what differs is the person on the phone and how they say it.

So the axes that leave the seeded data alone can be varied by copying. Each copy reuses the
setup, the checks and the reference solution byte for byte, which is why it needs no re-proving:
the thing that was proved is unchanged. That is the whole reason a suite can be large without
costing a model call per scenario.

Nothing here decides *whether* a scenario should be expanded. The scenario says so itself, in
``varies``, because only whoever wrote it knows if its point survives a different caller.
"""

from __future__ import annotations

import logging
from typing import Any

from .axes import Axis, AxisSet, Setting
from .scenariogen.model.scenario import Persona, Scenario

logger = logging.getLogger(__name__)

# Where a setting's guidance is written on the copy. The simulator renders persona metadata into
# its prompt, so this is what actually reaches the call rather than sitting in the file unread.
CONDITION = "caller condition"


def _place(scenario: Scenario, path: str, value: Any) -> bool:
    """Write one ``applies`` entry onto a scenario, by dotted path.

    Dotted rather than a fixed set of fields, so an axis file can target something the code here
    has never heard of. A path naming a field that does not exist is logged and skipped: an axis
    file is data and may be edited by someone who cannot see this module, and a typo there should
    cost one setting rather than the whole suite.
    """
    head, _, tail = path.partition(".")
    if not tail:
        if not hasattr(scenario, head):
            logger.warning("axis setting targets %r, which a scenario has no field for", path)
            return False
        setattr(scenario, head, value)
        return True
    if head != "persona":
        logger.warning("axis setting targets %r, and only persona has fields beneath it", path)
        return False
    if scenario.persona is None:
        scenario.persona = Persona()
    if not hasattr(scenario.persona, tail):
        logger.warning("axis setting targets %r, which a persona has no field for", path)
        return False
    setattr(scenario.persona, tail, value)
    return True


def _varied(scenario: Scenario, setting: Setting, axis: Axis) -> Scenario | None:
    """One copy of a scenario, under one setting. ``None`` when it would be the same scenario."""
    copy = scenario.model_copy(deep=True)
    # The identity has to be new, and the two derived keys have to be cleared rather than
    # carried: a copy holding its parent's key would collide with it wherever results are stored.
    # A scenario planned at baseline is named for it, and a copy is no longer at baseline, so the
    # marker is replaced rather than stacked: ``cancel-ride__senior``, not
    # ``cancel-ride__baseline__senior``.
    stem = scenario.name[: -len("__baseline")] if scenario.name.endswith("__baseline") else scenario.name
    copy.name = f"{stem}__{setting.name}"
    copy.scenario_key = ""
    copy.scenario_id = ""
    # A copy is not itself expandable. Expanding an expansion would move two dials at once and
    # lose the one property that makes a failure attributable.
    copy.varies = []

    changed = False
    for path, value in setting.applies.items():
        changed = _place(copy, path, value) or changed

    if setting.guidance:
        if copy.persona is None:
            copy.persona = Persona()
        copy.persona.metadata = {**copy.persona.metadata, CONDITION: setting.guidance}
        changed = True

    if not changed:
        # Nothing about the run would differ. A copy like this reads as coverage in the index and
        # is the same test twice, which is worse than not having it.
        logger.debug("%s.%s changes nothing on %s, so no copy was made", axis.name, setting.name, scenario.name)
        return None

    if copy.branch:
        copy.branch = f"{copy.branch}, {setting.name}"
    else:
        copy.branch = f"the same request from a {setting.name} caller"
    return copy


def axes_to_vary(scenario: Scenario, axes: AxisSet, env: dict[str, str] | None = None) -> list[Axis]:
    """Which axes this scenario may be copied across.

    Named axes are honoured as a filter, not as a licence: an axis the scenario asks for whose
    settings would change the seeded data is still refused, because copying across it would make
    the copy's setup a lie about its own world.
    """
    free = [axis for axis in axes.free_axes(env)]
    if not scenario.varies:
        return free
    wanted = {one.strip().lower() for one in scenario.varies if one.strip()}
    kept = [axis for axis in free if axis.name.lower() in wanted]
    unknown = wanted - {axis.name.lower() for axis in axes.axes}
    if unknown:
        logger.info(
            "%s asks to vary across %s, which is not an axis of this agent",
            scenario.name,
            ", ".join(sorted(unknown)),
        )
    return kept


def expand(
    scenario: Scenario,
    axes: AxisSet,
    *,
    env: dict[str, str] | None = None,
    limit: int = 0,
) -> list[Scenario]:
    """Every caller variation of one proved scenario. The original is not included.

    One dial moves per copy. Combining them would multiply faster and cost the ability to say
    which condition caused a failure, which is the only reason a large suite is worth reading.
    """
    made: list[Scenario] = []
    for axis in axes_to_vary(scenario, axes, env):
        for setting in axis.copyable_settings(env):
            copy = _varied(scenario, setting, axis)
            if copy is None:
                continue
            made.append(copy)
            if limit and len(made) >= limit:
                return made
    return made


def expand_all(
    scenarios: list[Scenario],
    axes: AxisSet,
    *,
    env: dict[str, str] | None = None,
    wanted: int = 0,
) -> list[Scenario]:
    """A whole suite expanded, originals first, then their copies.

    ``wanted`` caps the result at a total count. The cap is spread across the suite rather than
    taken from the front, because stopping at the first scenario's copies would expand one
    scenario twelve ways and leave the rest at one apiece.
    """
    kept: list[Scenario] = list(scenarios)
    if not scenarios:
        return kept

    per_scenario = [expand(one, axes, env=env) for one in scenarios]
    if not wanted:
        for batch in per_scenario:
            kept.extend(batch)
        return kept

    room = max(0, wanted - len(kept))
    # Round-robin, one copy from each scenario before any scenario gets a second. Every original
    # then gains its variations at the same rate and the suite stays even wherever the cap falls.
    depth = 0
    while room > 0 and any(len(batch) > depth for batch in per_scenario):
        for batch in per_scenario:
            if room <= 0:
                break
            if len(batch) > depth:
                kept.append(batch[depth])
                room -= 1
        depth += 1
    return kept


def summarise(originals: int, expanded: list[Scenario], axes: AxisSet, env: dict[str, str] | None = None) -> str:
    """What expansion produced, for the line a person reads after a run."""
    copies = len(expanded) - originals
    free = axes.free_axes(env)
    names = ", ".join(axis.name for axis in free) or "none"
    return (
        f"{originals} proved scenarios expanded to {len(expanded)} by varying the caller "
        f"({names}); {copies} copies reuse a proved environment unchanged and cost no model call."
    )
