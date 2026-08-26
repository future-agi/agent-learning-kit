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
    "outdoors": "FOREST_AMBIENCE",
    "retail": "CROWDED_ROOM",
    "office": "OFFICE_AMBIENCE",
    "home": "OFFICE_AMBIENCE",
}
_DEFAULT_BUILTIN = "OFFICE_AMBIENCE"


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
    return _BUILTIN_BY_ENVIRONMENT.get(env, _DEFAULT_BUILTIN)
