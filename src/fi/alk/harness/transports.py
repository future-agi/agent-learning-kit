"""How a scenario reaches the agent, resolved from a declaration rather than enumerated here.

The rule this module exists to enforce: **the model decides and writes, the code executes.** The
build stage works out how the agent under test is reached, and either names a transport this repo
already implements or writes a runner of its own and declares where it lives. From then on every
scenario is executed by that runner, identically, with no model in the loop at call time. That
split is what keeps a run reproducible while leaving the harness free to meet an agent it has
never seen.

Adding a transport is therefore a declaration, never an edit here. A run stage that enumerated
connectors could only ever run the agents somebody had already anticipated, which is the treadmill
this replaces.
"""

from __future__ import annotations

import importlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

DECLARATION = "transport.json"


class TransportUnresolved(RuntimeError):
    """No transport could be resolved, said with everything needed to fix it.

    Deliberately not a runner that refuses at call time: a refusal reaches the operator as a
    failed scenario with an unhelpful message, tens of minutes in, while this reaches them before
    any world is leased and names what to declare.
    """


@dataclass(frozen=True)
class Evidence:
    """What is known about the agent when a transport has to be chosen."""

    connector: str = ""
    modality: str = ""
    bundle_dir: Path | None = None

    def has(self, name: str) -> bool:
        return bool(self.bundle_dir and (self.bundle_dir / name).is_file())


@dataclass(frozen=True)
class Transport:
    """One way of reaching an agent, and how to recognise that it is the right one.

    ``claims`` belongs to the transport rather than to a branch in the run stage: a transport is
    the thing that knows what its own agent looks like, and keeping that knowledge next to the
    runner is what allows a new one to arrive without the stage learning about it.
    """

    key: str
    build: Callable[[Any, Any], Any]
    claims: Callable[[Evidence], bool] | None = None
    summary: str = ""
    # What a runner for this transport owes the platform, checked after every call. Declared per
    # transport because it genuinely differs: a voice call without audio is missing evidence, a
    # text conversation without audio is simply a text conversation.
    requires: tuple[str, ...] = ()


_REGISTRY: dict[str, Transport] = {}


def register(transport: Transport) -> None:
    """Add a way of reaching an agent. A runner class and this line."""
    _REGISTRY[transport.key] = transport


def supported() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def declared(bundle_dir: Path | None) -> dict[str, Any]:
    """What the environment said about how its agent is reached, or nothing.

    Written by whoever built the environment, because that is the only stage that has read the
    repository and knows.
    """
    if bundle_dir is None:
        return {}
    path = bundle_dir / DECLARATION
    if not path.is_file():
        return {}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


def _load_written_runner(
    spec: str, bundle_dir: Path | None
) -> Callable[[Any, Any], Any]:
    """Import a runner the build stage wrote, named ``module:Attribute``.

    The bundle goes on ``sys.path`` first: a runner written for this environment lives with the
    environment, not in this package, and requiring it to be installed would mean no new transport
    could ever arrive without a release.
    """
    if ":" not in spec:
        raise TransportUnresolved(
            f"runner {spec!r} is not in module:Attribute form, so it cannot be imported"
        )
    module_name, _, attribute = spec.partition(":")
    if bundle_dir is not None and str(bundle_dir) not in sys.path:
        sys.path.insert(0, str(bundle_dir))
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise TransportUnresolved(
            f"runner {spec!r} could not be imported from {bundle_dir or 'sys.path'}: {exc}. "
            "The module has to sit in the bundle and import cleanly on its own."
        ) from exc
    found = getattr(module, attribute, None)
    if found is None:
        raise TransportUnresolved(
            f"{module_name} has no {attribute!r}; runner must name a class or factory that exists"
        )
    return found


def resolve(evidence: Evidence) -> Transport:
    """The transport for this agent: what was declared, else what recognises itself.

    Declaration wins over recognition, always. Recognition is a convenience for the agents this
    repo already knows; a declaration is the environment stating what it built, and second-guessing
    that would make the build stage's work advisory.
    """
    declaration = declared(evidence.bundle_dir)

    written = str(declaration.get("runner") or "").strip()
    if written:
        factory = _load_written_runner(written, evidence.bundle_dir)
        return Transport(
            key=str(declaration.get("transport") or "declared"),
            build=factory,
            summary=f"written for this environment ({written})",
        )

    named = str(declaration.get("transport") or "").strip().lower()
    if named:
        if named not in _REGISTRY:
            raise TransportUnresolved(
                f"the environment declared transport {named!r}, which nothing implements. "
                f"Registered: {', '.join(supported()) or 'none'}. Either name one of those or "
                'declare a runner you wrote, as {"runner": "module:Class"}.'
            )
        return _REGISTRY[named]

    for transport in _REGISTRY.values():
        if transport.claims is not None and transport.claims(evidence):
            return transport

    raise TransportUnresolved(
        "nothing declared how this agent is reached and no transport recognised it "
        f"(connector={evidence.connector or 'unset'!r}, modality={evidence.modality or 'unset'!r}). "
        f"Registered: {', '.join(supported()) or 'none'}. The environment stage should write "
        f"{DECLARATION} naming a transport, or a runner it wrote."
    )


def build_runner(adapter: Any, context: Any, evidence: Evidence) -> Any:
    """The runner that will execute every scenario of this run, resolved once."""
    return resolve(evidence).build(adapter, context)
