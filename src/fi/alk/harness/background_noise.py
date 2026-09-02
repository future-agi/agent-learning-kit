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

# LiveKit ships these four; they are the reliable default when no custom catalog is configured.
#
# Whoever writes a scenario picks the word for where its caller is, so this cannot be a closed
# list: a suite measured here used `city`, `airport` and `hotel`, none of which were mapped, and
# every one of them fell through to an office. Matching is on words rather than the whole string,
# so "in a moving vehicle" and "vehicle" reach the same clip and an unfamiliar phrase still lands
# somewhere defensible.
_BUILTIN_BY_WORD: dict[str, str] = {
    # outdoors and moving
    "street": "CITY_AMBIENCE",
    "city": "CITY_AMBIENCE",
    "transit": "CITY_AMBIENCE",
    "traffic": "CITY_AMBIENCE",
    "vehicle": "CITY_AMBIENCE",
    "car": "CITY_AMBIENCE",
    "driving": "CITY_AMBIENCE",
    "bus": "CITY_AMBIENCE",
    "train": "CITY_AMBIENCE",
    "outdoors": "FOREST_AMBIENCE",
    "outside": "FOREST_AMBIENCE",
    "park": "FOREST_AMBIENCE",
    # busy indoor places
    "retail": "CROWDED_ROOM",
    "shop": "CROWDED_ROOM",
    "store": "CROWDED_ROOM",
    "cafe": "CROWDED_ROOM",
    "restaurant": "CROWDED_ROOM",
    "bar": "CROWDED_ROOM",
    "airport": "CROWDED_ROOM",
    "station": "CROWDED_ROOM",
    "hotel": "CROWDED_ROOM",
    "lobby": "CROWDED_ROOM",
    "crowd": "CROWDED_ROOM",
    "event": "CROWDED_ROOM",
    # quiet indoor places
    "office": "OFFICE_AMBIENCE",
    "home": "OFFICE_AMBIENCE",
    "indoors": "OFFICE_AMBIENCE",
    "desk": "OFFICE_AMBIENCE",
}
_DEFAULT_BUILTIN = "OFFICE_AMBIENCE"


def _builtin_for(environment: str) -> str:
    """The clip for an environment, matched on any word in it."""
    words = "".join(c if c.isalnum() else " " for c in environment).split()
    for word in words:
        found = _BUILTIN_BY_WORD.get(word)
        if found:
            return found
    return _DEFAULT_BUILTIN


def enabled() -> bool:
    """Whether any scenario may be heard through background noise on this run.

    Off unless ``ALK_BACKGROUND_NOISE`` opts in, so a run needs no environment at all to be
    silent. Continuous ambient audio under the caller competes with endpoint detection, and calls
    carrying it end earlier and on fewer turns, so silence is the setting a run should fall into
    rather than the one it has to ask for. Opting in still only permits noise: a scenario that
    asked for none stays silent either way.
    """
    return os.environ.get("ALK_BACKGROUND_NOISE", "0").strip().lower() in (
        "1",
        "on",
        "true",
        "yes",
    )


def source_for(environment: str = "", seed: str = "") -> str:
    """A background-noise source for a scenario.

    Returns a ``url``/``path`` from the configured catalog when one matches the environment, else the
    name of a LiveKit builtin clip. The choice is deterministic in ``seed`` so the same scenario
    hears the same place across runs.
    """
    env = (environment or "").strip().lower()
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
    return _builtin_for(env)


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
