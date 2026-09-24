"""Choose the caller-side ambient noise a scenario should be heard through.

A scenario that sets ``background_noise`` wants the agent to handle a caller phoning from somewhere
real: a car, a street, an office. The clip is chosen here and handed to the voice engine, which
mixes it under the simulated caller's audio.

Two sources, in order. A run may point ``ALK_BACKGROUND_NOISE_CATALOG`` at a JSON file of clips
(each with an ``environment`` tag and a ``url`` or ``path``); the catalog stays a local file so its
asset locations are never committed here. When no catalog matches, a LiveKit builtin clip is used,
which needs no external asset and always works.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# LiveKit ships these; they are the reliable default when no custom catalog is configured.
_BUILTIN_BY_ENVIRONMENT: dict[str, str] = {
    "street": "CITY_AMBIENCE",
    "transit": "CITY_AMBIENCE",
    "vehicle": "CITY_AMBIENCE",
    "in-car": "CITY_AMBIENCE",
    "in_car": "CITY_AMBIENCE",
    "car": "CITY_AMBIENCE",
    "metro": "CITY_AMBIENCE",
    "train": "CITY_AMBIENCE",
    "bus": "CITY_AMBIENCE",
    "traffic": "CITY_AMBIENCE",
    "outdoors": "FOREST_AMBIENCE",
    "park": "FOREST_AMBIENCE",
    "retail": "CROWDED_ROOM",
    "airport": "CROWDED_ROOM",
    "restaurant": "CROWDED_ROOM",
    "cafe": "CROWDED_ROOM",
    "coffee_shop": "CROWDED_ROOM",
    "bar": "CROWDED_ROOM",
    "hotel": "CROWDED_ROOM",
    "crowd": "CROWDED_ROOM",
    "office": "OFFICE_AMBIENCE",
    "home": "OFFICE_AMBIENCE",
}
# A scenario that names a quiet place is asking to be heard in the clear, not for a default bed.
_SILENT_ENVIRONMENTS = frozenset({"quiet", "silent", "silence", "none", "clear", "quiet_line"})
_DEFAULT_BUILTIN = "OFFICE_AMBIENCE"


def distinct_beds(environments) -> dict[str, list[str]]:
    """The clips a set of place names actually produces, keyed by clip."""
    grouped: dict[str, list[str]] = {}
    for environment in environments:
        named = str(environment or "").strip().lower()
        if not named or named in _SILENT_ENVIRONMENTS:
            continue
        grouped.setdefault(source_for(named), []).append(named)
    return {clip: sorted(set(places)) for clip, places in grouped.items()}


def enabled() -> bool:
    """Whether any scenario may be heard through background noise on this run.

    **On unless ``ALK_BACKGROUND_NOISE`` turns it off.** A real caller is somewhere, and an agent
    tested only against studio silence has not been tested against its callers, so noise is what a
    run should fall into rather than something it has to ask for. It was opt-in and every deployment
    forgot: a switch nobody sets is a feature nobody has.

    The tradeoff is real and is why an opt-out exists. Continuous ambience under the caller competes
    with endpoint detection, and calls carrying it end a little earlier and on fewer turns. Set
    ``ALK_BACKGROUND_NOISE=0`` (or ``off``, ``false``, ``no``) for a run that needs a clean line.

    Permission, not compulsion: a scenario whose own ``background_noise`` says none stays silent
    either way.
    """
    return os.environ.get("ALK_BACKGROUND_NOISE", "1").strip().lower() not in (
        "0",
        "off",
        "false",
        "no",
    )


def source_for(environment: str = "", seed: str = "") -> str:
    """A background-noise source for a scenario.

    Returns a ``url``/``path`` from the configured catalog when one matches the environment, else the
    name of a LiveKit builtin clip. The choice is deterministic in ``seed`` so the same scenario
    hears the same place across runs.
    """
    env = (environment or "").strip().lower()
    if env in _SILENT_ENVIRONMENTS:
        return ""
    catalog = os.environ.get("ALK_BACKGROUND_NOISE_CATALOG", "").strip()
    if catalog and Path(catalog).is_file():
        try:
            entries = json.loads(Path(catalog).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            entries = []
        if isinstance(entries, list) and entries:
            pool = [
                entry
                for entry in entries
                if str(entry.get("environment", "")).strip().lower() == env
            ] or entries
            chosen = pool[sum(ord(character) for character in (seed or env or "x")) % len(pool)]
            located = str(chosen.get("url") or chosen.get("path") or "").strip()
            if located:
                return located
    return _BUILTIN_BY_ENVIRONMENT.get(env, _DEFAULT_BUILTIN)


def scenario_source(
    background_noise, fixture, seed: str = ""
) -> str:
    """The noise source for one scenario, or "" when it should be heard in the clear.

    The scenario names the place when it cares which one; otherwise the fixture says where the
    caller is, and failing that any noise will do.
    """
    if not background_noise or not enabled():
        return ""
    environment = background_noise if isinstance(background_noise, str) else ""
    if not environment and isinstance(fixture, dict):
        environment = str(fixture.get("environment") or fixture.get("location") or "")
    return source_for(environment, seed=seed)
