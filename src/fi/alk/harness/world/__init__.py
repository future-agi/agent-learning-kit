"""Generated worlds: a real data store behind an agent's tools.

The pieces here are the parts that must be exact, so that what gets generated per agent stays
small: the runtime a world executes on, the snapshot every scenario restores from, and the probe
suite that decides whether a world is usable at all.
"""

from .probe import EDGE, HAPPY, SEQUENCE, ProbeReport, ProbeResult, probe
from .runtime import Call, Db, GeneratedWorld, ToolError, WorldSpec
from .snapshot import apply_overlay, read_manifest, restore, save

__all__ = [
    "Call",
    "Db",
    "EDGE",
    "GeneratedWorld",
    "HAPPY",
    "ProbeReport",
    "ProbeResult",
    "SEQUENCE",
    "ToolError",
    "WorldSpec",
    "apply_overlay",
    "probe",
    "read_manifest",
    "restore",
    "save",
]
