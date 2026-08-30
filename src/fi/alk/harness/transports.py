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

import hashlib
import importlib
import logging
import importlib.util
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


logger = logging.getLogger(__name__)

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
    except (OSError, ValueError) as broke:
        # `is_file` already separated "nothing was declared" from this, so reaching here means a
        # declaration exists and could not be read. Returning {} silently makes it read as the
        # first: the runner the build stage wrote is ignored, resolution falls through to
        # recognition or fails naming no declaration, and the evidence contract goes with it.
        logger.warning(
            "%s exists but could not be read (%s: %s), so it is being treated as if the "
            "environment declared nothing about how its agent is reached.",
            path,
            type(broke).__name__,
            broke,
        )
        return {}
    if not isinstance(body, dict):
        logger.warning(
            "%s is a %s, not an object, so nothing in it is being used.",
            path,
            type(body).__name__,
        )
        return {}
    return body


def _bundle_namespace(bundle_dir: Path | None) -> str:
    """A module-name prefix unique to one bundle.

    `import_module` caches by module name, so two bundles in one job that both call their runner
    something conventional -- and they will, because a skill teaches one name -- would resolve to
    whichever was imported first. Every later world would then run the earlier world's runner,
    silently, producing plausible receipts that belong to the wrong environment. Namespacing by
    the bundle's own path is what keeps them apart.
    """
    seed = str(bundle_dir.resolve()) if bundle_dir is not None else "no-bundle"
    return "_alk_runner_" + hashlib.sha256(seed.encode()).hexdigest()[:12]


def _module_file(module_name: str, bundle_dir: Path | None) -> Path | None:
    """Where a dotted module name sits inside the bundle, if it is a file there at all."""
    if bundle_dir is None:
        return None
    parts = module_name.split(".")
    candidate = bundle_dir.joinpath(*parts).with_suffix(".py")
    if candidate.is_file():
        return candidate
    package = bundle_dir.joinpath(*parts, "__init__.py")
    return package if package.is_file() else None


def _load_written_runner(
    spec: str, bundle_dir: Path | None
) -> Callable[[Any, Any], Any]:
    """Import a runner the build stage wrote, named ``module:Attribute``.

    Loaded from its file under a name unique to this bundle rather than by bare module name, so a
    second world cannot be served the first world's runner out of the module cache.

    Every failure here is a declaration problem and is reported as one. Model-written code is
    exactly the code most likely to carry a module-level mistake, and a `SyntaxError` reaching the
    scheduler as an untyped crash tells the operator nothing about what to fix; a
    `TransportUnresolved` naming the file and the error tells them everything.
    """
    if ":" not in spec:
        raise TransportUnresolved(
            f"runner {spec!r} is not in module:Attribute form, so it cannot be imported"
        )
    module_name, _, attribute = spec.partition(":")
    if not module_name or not attribute:
        raise TransportUnresolved(
            f"runner {spec!r} needs both a module and an attribute, as module:Attribute"
        )

    importlib.invalidate_caches()
    unique_name = f"{_bundle_namespace(bundle_dir)}.{module_name}"
    source = _module_file(module_name, bundle_dir)
    # The bundle goes on sys.path only while the module executes, so a runner may import its own
    # siblings, and is taken off again so it cannot shadow the next bundle's imports.
    added = False
    if bundle_dir is not None and str(bundle_dir) not in sys.path:
        sys.path.insert(0, str(bundle_dir))
        added = True
    try:
        if source is not None:
            spec_obj = importlib.util.spec_from_file_location(unique_name, source)
            if spec_obj is None or spec_obj.loader is None:
                raise TransportUnresolved(
                    f"runner {spec!r} names {source}, which python cannot load as a module"
                )
            module = importlib.util.module_from_spec(spec_obj)
            # Registered before execution so a module that refers to itself, or uses dataclasses,
            # resolves; removed again if it fails, so a broken module is never left cached.
            sys.modules[unique_name] = module
            try:
                spec_obj.loader.exec_module(module)
            except Exception as exc:
                sys.modules.pop(unique_name, None)
                raise TransportUnresolved(
                    f"runner {spec!r} was found at {source} but failed while loading: "
                    f"{type(exc).__name__}: {exc}. Fix the module so it imports cleanly on its "
                    "own, then declare it again."
                ) from exc
        else:
            try:
                module = importlib.import_module(module_name)
            except ImportError as exc:
                raise TransportUnresolved(
                    f"runner {spec!r} could not be imported from "
                    f"{bundle_dir or 'sys.path'}: {exc}. The module has to sit in the bundle and "
                    "import cleanly on its own."
                ) from exc
            except Exception as exc:
                raise TransportUnresolved(
                    f"runner {spec!r} was found but failed while loading: "
                    f"{type(exc).__name__}: {exc}. Fix the module so it imports cleanly on its "
                    "own, then declare it again."
                ) from exc
    finally:
        if added:
            try:
                sys.path.remove(str(bundle_dir))
            except ValueError:
                pass

    try:
        found = getattr(module, attribute)
    except Exception as exc:
        # A module can raise from __getattr__ as readily as from its body.
        raise TransportUnresolved(
            f"reading {attribute!r} from {module_name} failed: {type(exc).__name__}: {exc}"
        ) from exc
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
        key = str(declaration.get("transport") or "declared")
        # A written runner for a transport we already implement still owes what that transport
        # owes. Writing your own LiveKit runner does not make a voice call without audio complete;
        # it is the same call, reached the same way, rendered by the same platform. Dropping the
        # default here is what made the evidence gate inert for the only thing it was built to
        # police, since a runner is exactly what nothing else guarantees the shape of.
        known = _REGISTRY.get(key)
        if known is None and not isinstance(declaration.get("requires"), list):
            # Nothing to inherit and nothing declared. The run continues, because refusing would
            # block every genuinely new transport, but "no evidence is owed" and "we could not
            # work out what is owed" are different states and only one of them is true here.
            logger.warning(
                "transport %r is written for this environment and declares no 'requires', and no "
                "built-in default exists for that name, so nothing its runner returns will be "
                "checked. Declare requires in transport.json to be held to it.",
                key,
            )
        return Transport(
            key=key,
            build=factory,
            summary=f"written for this environment ({written})",
            requires=known.requires if known is not None else (),
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
