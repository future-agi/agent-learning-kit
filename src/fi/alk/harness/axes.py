"""The axes a scenario varies along, read from data rather than written in code.

A scenario is a coordinate: one cell of ``operation x object``, plus the conditions the person
wants it under. The operations and those conditions are the same shape for every agent, so they
are declared once as data and instantiated per modality. Adding a dial, adding a setting to one,
or onboarding a new kind of agent is an edit to a JSON file; nothing here has to change.

Two properties of a setting decide what it costs, and both are declared rather than inferred:

``needs_world``  the setting is only true if the seeded data says so. An impersonation test where
                 the caller really is the account holder tests nothing. These are authored.
``unwired``      the setting reaches nothing in the run today. Copying a scenario across it would
                 produce a duplicate wearing a different name, which reads as coverage and is not.

Everything else changes only who is calling, so a proved scenario can be copied across it with no
model call and no re-proving. That split is the whole reason a suite can be large.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Points at a file or a directory of them, so a deployment can carry its own axes without a
# rebuild. A directory is read the same way the bundled one is: ``<modality>.json`` over
# ``universal.json``.
AXES_ENV = "HARNESS_SCENARIO_AXES"

BUNDLED = Path(__file__).parent / "data" / "axes"
UNIVERSAL = "universal"

# What a setting can require of the world. Ordered from cheapest to most expensive, because the
# sampler spends its budget in that order.
NEVER, SOMETIMES, ALWAYS = "never", "sometimes", "always"


@dataclass(frozen=True)
class Setting:
    """One value a dial can take, and what it actually does to a scenario."""

    name: str
    applies: dict[str, Any] = field(default_factory=dict)
    guidance: str = ""
    # Set when the world has to make this true. Authored, never copied.
    needs_world: str = ""
    # Set when nothing in the run consumes this yet. Enumerated for coverage, never generated.
    unwired: str = ""
    # Environment the run needs before this setting reaches anything.
    needs_env: tuple[str, ...] = ()

    def live(self, env: dict[str, str] | None = None) -> bool:
        """Whether choosing this setting would change the run at all."""
        if self.unwired:
            return False
        source = env if env is not None else os.environ
        return all(source.get(name, "").strip() not in ("", "0", "off", "false") for name in self.needs_env)

    def copyable(self, env: dict[str, str] | None = None) -> bool:
        """Whether a proved scenario can be copied across this setting without re-proving."""
        return self.live(env) and not self.needs_world


@dataclass(frozen=True)
class Axis:
    """One dial: what it varies, where it sits by default, and what it may be moved to."""

    name: str
    label: str = ""
    of: str = ""
    baseline: str = ""
    changes_world: str = NEVER
    weight: float = 1.0
    settings: tuple[Setting, ...] = ()
    # Set on an axis whose every value is rare enough that sampling would lose it.
    force_every_setting: bool = False

    def free(self) -> bool:
        """Whether moving this dial leaves the seeded data untouched."""
        return self.changes_world == NEVER

    def copyable_settings(self, env: dict[str, str] | None = None) -> tuple[Setting, ...]:
        """The settings a proved scenario can be expanded across, for nothing."""
        if not self.free():
            return ()
        return tuple(one for one in self.settings if one.copyable(env))

    def authored_settings(self, env: dict[str, str] | None = None) -> tuple[Setting, ...]:
        """The settings that have to be written, because the world has to carry them."""
        return tuple(
            one
            for one in self.settings
            if one.live(env) and (one.needs_world or not self.free())
        )

    def named(self, name: str) -> Setting | None:
        for one in self.settings:
            if one.name == name:
                return one
        return None


@dataclass(frozen=True)
class Operation:
    """One of the things a person can want done, independent of what it is done to."""

    name: str
    kind: str = ""
    asks: str = ""
    # Tool-name fragments that suggest a tool serves this operation. Hints for building the
    # grid, not a taxonomy: a tool matching none of them still counts toward its object.
    verbs: tuple[str, ...] = ()
    # Whether a cell is only real when a tool exists for it. True for the operations that
    # change state, because an agent cannot cancel something it has no way to cancel. False
    # for reading and for managing the conversation, which an agent can be asked to do about
    # anything it can see, and which is exactly where hand-written suites never go.
    needs_own_tool: bool = False
    # ``object`` crosses this operation with every object the agent has. ``agent`` makes it one
    # cell for the whole agent, which is right for the operations that are about the
    # conversation rather than about a thing: proving who you are, being walked through a flow,
    # being handed to a human. Crossing those with every object produces cells like
    # "authenticate a market config", which are noise the sampler then has to spend budget on.
    scope: str = "object"


@dataclass(frozen=True)
class AxisSet:
    """Every axis in play for one agent, plus the operations its grid is built from."""

    modality: str
    operations: tuple[Operation, ...] = ()
    axes: tuple[Axis, ...] = ()
    # The ordered list a small suite is filled from, so asking for four scenarios yields four
    # worth running rather than four happy paths. Data, so what a small suite contains is tuned
    # by editing the axis file.
    priorities: tuple[dict[str, Any], ...] = ()
    # What this kind of agent calls the party on the other side. Conversation-scoped operations
    # are named for it, so a voice agent gets ``authenticate-caller`` and a coding agent does not
    # get a cell about authenticating a caller it has never had.
    counterparty: str = "person"

    def axis(self, name: str) -> Axis | None:
        for one in self.axes:
            if one.name == name:
                return one
        return None

    def free_axes(self, env: dict[str, str] | None = None) -> tuple[Axis, ...]:
        """Dials a proved scenario expands across, cheapest coverage there is."""
        return tuple(one for one in self.axes if one.copyable_settings(env))

    def authored_axes(self, env: dict[str, str] | None = None) -> tuple[Axis, ...]:
        """Dials whose settings each cost a scenario to write."""
        return tuple(one for one in self.axes if one.authored_settings(env))

    def versions_per_scenario(self, env: dict[str, str] | None = None) -> int:
        """How many callers one proved scenario turns into, the baseline included.

        One dial moves at a time, so this is a sum and not a product: combining them would
        multiply faster and cost the ability to say which condition caused a failure.
        """
        return 1 + sum(len(one.copyable_settings(env)) for one in self.free_axes(env))

    def problems(self, env: dict[str, str] | None = None) -> list[str]:
        """What is declared here but would not reach a run, said plainly.

        Reported rather than raised. A setting nothing consumes is worth knowing about and is
        not a reason to refuse to generate scenarios.
        """
        found: list[str] = []
        for axis in self.axes:
            for one in axis.settings:
                if one.unwired:
                    found.append(f"{axis.name}.{one.name} is enumerated but not generated: {one.unwired}")
                elif not one.live(env):
                    needs = ", ".join(one.needs_env)
                    found.append(f"{axis.name}.{one.name} needs {needs} set before it reaches a call")
        return found


def _setting(raw: dict[str, Any]) -> Setting | None:
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    applies = raw.get("applies")
    needs_env = raw.get("needs_env") or []
    return Setting(
        name=name,
        applies=dict(applies) if isinstance(applies, dict) else {},
        guidance=str(raw.get("guidance") or "").strip(),
        needs_world=str(raw.get("needs_world") or "").strip(),
        unwired=str(raw.get("unwired") or "").strip(),
        needs_env=tuple(str(one) for one in needs_env if str(one).strip()),
    )


def _axis(raw: dict[str, Any]) -> Axis | None:
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    settings = tuple(
        one
        for one in (_setting(each) for each in raw.get("settings") or [] if isinstance(each, dict))
        if one is not None
    )
    changes = str(raw.get("changes_world") or NEVER).strip().lower()
    if changes not in (NEVER, SOMETIMES, ALWAYS):
        logger.warning("axis %s declares changes_world=%r; treating it as %r", name, changes, ALWAYS)
        changes = ALWAYS
    try:
        weight = float(raw.get("weight", 1.0))
    except (TypeError, ValueError):
        weight = 1.0
    return Axis(
        name=name,
        label=str(raw.get("label") or name.title()).strip(),
        of=str(raw.get("of") or "").strip(),
        baseline=str(raw.get("baseline") or "").strip(),
        changes_world=changes,
        weight=weight,
        settings=settings,
        force_every_setting=bool(raw.get("force_every_setting")),
    )


def _read(path: Path) -> dict[str, Any]:
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as broke:
        logger.warning("axis file %s is unreadable, skipping it: %s", path, broke)
        return {}
    return held if isinstance(held, dict) else {}


def _merged(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """A modality file laid over the universal one, axis by axis, matched on name.

    Whole-axis replacement rather than a deep merge of settings. A modality that redefines a
    dial almost always means a different set of values, and a merge would leave the universal
    ones behind alongside them, which is how a suite ends up varying something the modality
    does not have.
    """
    if not over:
        return dict(base)
    result = dict(base)
    result["modality"] = over.get("modality") or base.get("modality") or UNIVERSAL
    if over.get("operations"):
        result["operations"] = over["operations"]
    if over.get("priorities"):
        result["priorities"] = over["priorities"]
    if over.get("counterparty"):
        result["counterparty"] = over["counterparty"]
    by_name = {
        str(one.get("name") or ""): one
        for one in base.get("axes") or []
        if isinstance(one, dict)
    }
    order = [str(one.get("name") or "") for one in base.get("axes") or [] if isinstance(one, dict)]
    for one in over.get("axes") or []:
        if not isinstance(one, dict):
            continue
        name = str(one.get("name") or "")
        if not name:
            continue
        if name not in by_name:
            order.append(name)
        by_name[name] = one
    result["axes"] = [by_name[name] for name in order if name in by_name]
    return result


def _roots() -> list[Path]:
    """Where axis files are looked for, most specific first."""
    found: list[Path] = []
    configured = os.environ.get(AXES_ENV, "").strip()
    if configured:
        path = Path(configured)
        if path.is_dir():
            found.append(path)
        elif path.is_file():
            found.append(path.parent)
        else:
            logger.warning("%s points at %s, which does not exist; using the bundled axes", AXES_ENV, configured)
    found.append(BUNDLED)
    return found


@lru_cache(maxsize=8)
def axes_for(modality: str = "") -> AxisSet:
    """The axes for one kind of agent, with the universal skeleton underneath.

    Never raises and never returns nothing usable. An unknown modality falls back to the
    universal file, which is agent-agnostic by construction, so a kind of agent nobody has
    onboarded yet still gets a grid rather than an error.
    """
    wanted = (modality or "").strip().lower() or UNIVERSAL
    base: dict[str, Any] = {}
    over: dict[str, Any] = {}
    for root in _roots():
        if not base:
            base = _read(root / f"{UNIVERSAL}.json")
        if not over and wanted != UNIVERSAL:
            candidate = root / f"{wanted}.json"
            if candidate.is_file():
                over = _read(candidate)
    if not base and not over:
        logger.warning("no axis definitions found for %r; scenarios will vary on nothing", wanted)
        return AxisSet(modality=wanted)
    if wanted != UNIVERSAL and not over:
        logger.info("no axis file for modality %r; using the universal axes", wanted)

    held = _merged(base, over)
    operations = tuple(
        Operation(
            name=str(one.get("name") or "").strip(),
            kind=str(one.get("kind") or "").strip(),
            asks=str(one.get("asks") or "").strip(),
            verbs=tuple(str(each).strip().lower() for each in one.get("verbs") or [] if str(each).strip()),
            needs_own_tool=bool(one.get("needs_own_tool")),
            scope=str(one.get("scope") or "object").strip().lower(),
        )
        for one in held.get("operations") or []
        if isinstance(one, dict) and str(one.get("name") or "").strip()
    )
    axes = tuple(
        one
        for one in (_axis(each) for each in held.get("axes") or [] if isinstance(each, dict))
        if one is not None and one.settings
    )
    priorities = tuple(
        dict(one) for one in held.get("priorities") or [] if isinstance(one, dict)
    )
    return AxisSet(
        modality=str(held.get("modality") or wanted),
        operations=operations,
        axes=axes,
        priorities=priorities,
        counterparty=str(held.get("counterparty") or "person").strip() or "person",
    )


def unrecognised_persona_values(axes: AxisSet) -> list[str]:
    """Axis settings that set a persona value the platform would not act on.

    The same failure the persona vocabulary exists to prevent, one level up: a setting that maps
    to an accent nothing recognises renders correctly and then selects no voice, so the suite
    varies on paper and not in the calls.
    """
    from .persona_guides import ENFORCED, vocabulary

    known = vocabulary()
    if not known:
        return []
    problems: list[str] = []
    for axis in axes.axes:
        for setting in axis.settings:
            for path, value in setting.applies.items():
                if not path.startswith("persona."):
                    continue
                field_name = path.split(".", 1)[1]
                if field_name not in ENFORCED:
                    continue
                allowed = known.get(field_name) or []
                values = value if isinstance(value, list) else [value]
                lowered = {str(one).strip().lower() for one in allowed}
                for one in values:
                    if str(one).strip().lower() not in lowered:
                        problems.append(
                            f"{axis.name}.{setting.name} sets persona {field_name}={one!r}, which the "
                            f"platform does not know. Use one of: {', '.join(allowed)}."
                        )
    return problems
