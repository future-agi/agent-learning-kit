"""Choose the caller-side ambient noise a scenario should be heard through.

A scenario that sets ``background_noise`` wants the agent to handle a caller phoning from somewhere
real: a car, a street, an office. The clip is chosen here and handed to the voice engine, which
mixes it under the simulated caller's audio.

Two sources, pooled. ``ALK_BACKGROUND_NOISE_CATALOG`` may carry a JSON list of clips, inline or as a
file path, each with an ``environment`` tag and a ``url`` or ``path``; the platform supplies its own.
LiveKit's builtin clips join the same pool, and need no external asset.
"""

from __future__ import annotations

import hashlib
import json
import re
import os
from pathlib import Path
from typing import Any

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
# One place name per builtin clip, for offering the places a caller can be heard from.
_PLACE_BY_BUILTIN: dict[str, str] = {
    "CITY_AMBIENCE": "street",
    "FOREST_AMBIENCE": "outdoors",
    "CROWDED_ROOM": "crowd",
    "OFFICE_AMBIENCE": "office",
}

# A scenario that names a quiet place is asking to be heard in the clear, not for a default bed.
_SILENT_ENVIRONMENTS = frozenset({"quiet", "silent", "silence", "none", "clear", "quiet_line"})


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


def _catalogue() -> list[tuple[str, str]]:
    """``(environment, location)`` for each clip in ``ALK_BACKGROUND_NOISE_CATALOG``, inline JSON or a path."""
    raw = os.environ.get("ALK_BACKGROUND_NOISE_CATALOG", "").strip()
    if not raw:
        return []
    try:
        if raw.startswith("["):
            entries = json.loads(raw)
        elif Path(raw).is_file():
            entries = json.loads(Path(raw).read_text(encoding="utf-8"))
        else:
            return []
    except (OSError, ValueError):
        return []
    if not isinstance(entries, list):
        return []
    return [
        (str(entry.get("environment", "")).strip().lower(), str(entry.get("url") or entry.get("path")).strip())
        for entry in entries
        if isinstance(entry, dict) and (entry.get("url") or entry.get("path"))
    ]


def places() -> dict[str, int]:
    """Each place a caller can be heard from on this deployment, with how many recordings it draws on."""
    clips = _catalogue()
    named = {tag for tag, _ in clips if tag and tag not in _SILENT_ENVIRONMENTS}
    counts = {}
    for place in sorted(named | set(_PLACE_BY_BUILTIN.values())):
        counts[place] = sum(1 for tag, _ in clips if tag == place) + (place in _BUILTIN_BY_ENVIRONMENT)
    return counts


def source_for(environment: str = "", seed: str = "") -> str:
    """A background-noise source for a scenario.

    Returns a ``url``/``path`` from the configured catalog or the name of a LiveKit builtin clip,
    drawn from every bed that matches the environment, or from all of them when none does. The
    choice is deterministic in ``seed`` so the same scenario hears the same place across runs.
    """
    env = (environment or "").strip().lower()
    if env in _SILENT_ENVIRONMENTS:
        return ""
    clips = _catalogue()
    pool = [location for tag, location in clips if tag == env]
    if env in _BUILTIN_BY_ENVIRONMENT:
        pool.append(_BUILTIN_BY_ENVIRONMENT[env])
    if not pool:
        pool = [location for _, location in clips] + sorted(set(_BUILTIN_BY_ENVIRONMENT.values()))
    return pool[_pick(seed or env or "x", len(pool))]


def _pick(seed: str, size: int) -> int:
    return int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16) % size


# Words in a situation that say where the caller is, for the places a deployment can play.
_SETTING_WORDS: dict[str, str] = {
    "office": r"office|desk|workplace|cubicle|meeting room",
    "street": r"street|sidewalk|pavement|walking|outside a|crosswalk",
    "vehicle": r"\bcar\b|driving|taxi|cab\b|in traffic|behind the wheel",
    "transit": r"airport|flight|boarding|gate\b|terminal|train|station|platform|metro|subway",
    "retail": r"\bstore\b|\bshop\b|shopping|mall|grocery|supermarket|checkout",
    "outdoors": r"\bpark\b|outdoors|garden|hiking|beach",
    "crowd": r"cafe|café|restaurant|coffee shop|\bbar\b|canteen|cafeteria|crowd",
}


def place_for(name: str, fixture: Any = None, situation: str = "") -> str:
    """Where a caller with noise on and no place named is heard from.

    The fixture's place first, then a place the situation itself describes, and only then one
    picked by the scenario's name, so the sound never contradicts what the scenario says.
    """
    if isinstance(fixture, dict):
        named = str(fixture.get("environment") or "").strip().lower()
        if named:
            return named
    options = list(places())
    text = (situation or "").lower()
    for place, words in _SETTING_WORDS.items():
        if place in options and re.search(words, text):
            return place
    return options[_pick(name or "x", len(options))] if options else ""


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
