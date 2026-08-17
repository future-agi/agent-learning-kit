"""Breaking a world on purpose, to find out whether its checks would notice.

The checks that verify an environment are written by whoever built it. That is the right way
round: what makes a world usable is a judgement about this agent, and no fixed set of probes
written in advance can make it for every agent. But it leaves nothing independent confirming the
checks work, and a check that cannot fail reports a healthy world forever.

So the checks are put to a test they cannot talk their way out of. The world is damaged in ways
that are obviously wrong, and the checks have to go red. One that stays green through every
damaged world is not verifying anything, whatever it claims to inspect.

The damage is deliberately generic, because a mutation that needed to understand the agent would
need the same judgement the checks needed, and nothing would be gained:

- **emptied**: every collection loses its contents. A world with no data at all.
- **silenced**: every tool answers with nothing. Calls succeed and change nothing.

Any check worth keeping fails against at least one of those. Most fail against both.
"""

from __future__ import annotations

from typing import Any, Callable

from .runtime import GeneratedWorld

EMPTIED = "emptied"
SILENCED = "silenced"

# What a silenced tool answers. Deliberately a plain empty string: it is the shape a handler
# returns when it has done nothing, which is exactly the failure being simulated.
_MUTE = "def handle(args, db):\n    return ''\n"


def _empty(world: GeneratedWorld) -> None:
    """Take the contents out of the world, leaving its shape intact.

    Through the world's own vocabulary rather than a store's, so this works the same against a
    database, a mapping the agent's code owns, or whatever a later store turns out to be.
    """
    for name in list(world.state()):
        try:
            world.drop(name)
        except Exception:
            # A collection that cannot be emptied is not a reason to abandon the mutation: the
            # remaining ones still damage the world, and the checks still have to notice.
            continue


def _silence(world: GeneratedWorld) -> None:
    """Leave every tool answering with nothing, so no call has any effect."""
    for name in list(world.handlers):
        world.handlers[name] = _MUTE


def damage() -> dict[str, Callable[[GeneratedWorld], None]]:
    """Every way a world is broken on purpose, by name."""
    return {EMPTIED: _empty, SILENCED: _silence}


def unnoticed(
    world_root: Any,
    checks: list[tuple[str, str]],
    *,
    run: Callable[[str, GeneratedWorld], Any],
    restore: Callable[[Any], GeneratedWorld],
) -> dict[str, list[str]]:
    """Which checks fail to notice each kind of damage.

    Every mutation runs against its own restored copy, so one cannot inherit another's damage and
    a check is never blamed for a world some earlier mutation had already emptied.

    Returns damage name to the checks that stayed green through it. A check appearing under every
    kind of damage is one that cannot fail.
    """
    survived: dict[str, list[str]] = {}
    for name, apply in damage().items():
        broken = restore(world_root)
        try:
            apply(broken)
            still_green = []
            for check_name, source in checks:
                outcome = run(source, broken)
                # A check that raises has not verified anything either, but that is a broken
                # check rather than a blind one, and it is reported separately by the caller.
                if getattr(outcome, "held", False):
                    still_green.append(check_name)
            survived[name] = still_green
        finally:
            broken.close()
    return survived


def blind(survived: dict[str, list[str]]) -> list[str]:
    """Checks that stayed green through every kind of damage."""
    if not survived:
        return []
    kinds = list(survived.values())
    return sorted(set(kinds[0]).intersection(*kinds[1:])) if len(kinds) > 1 else sorted(kinds[0])
