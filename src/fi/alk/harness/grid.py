"""The space of everything an agent can be asked, derived from its own contract.

A cell is one ``operation x object``. The operations are fixed and come from the axis file; the
objects are read out of the agent, so the grid is the agent's own surface rather than whatever a
writer happened to think of. Enumerating it first is what makes "what did we not test" a question
with an answer.

Deriving is deliberately generous. A cell is kept unless the agent plainly cannot serve it, and
the operations that hand-written suites always miss, diagnose, compare, explain, configure and
navigate, are exactly the ones that need no dedicated tool: an agent can be asked why it charged
twice whether or not it has a ``diagnose_charge``. Being strict here would prune away the reason
for doing any of this.

Nothing about the derivation is final. It is a starting point the model can correct, because it
reads the agent's source and this only reads the contract.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .axes import AxisSet, Operation
from .contract import AgentContract

logger = logging.getLogger(__name__)

# Word-shaped fragments that are never the thing a tool acts on, so they are dropped when a
# noun is read out of a tool name. Not a stop-word list: every one of these is a connector or a
# qualifier that would otherwise become an object in its own right.
_NOISE = {
    "by", "for", "from", "to", "with", "of", "the", "a", "an", "and", "or",
    "id", "ids", "info", "information", "detail", "details", "data", "record",
    "current", "new", "all", "any", "my", "user_input", "async", "sync",
}

# Trailing fragments that qualify an object rather than name one: ``lookup_rider_by_phone`` is
# about a rider, not about a phone.
_QUALIFIER = re.compile(r"_(by|for|from|to|with|via|using|in|on|at)_.*$")

# How many declared collections make a data schema worth trusting on its own. Below this the
# schema is a fragment rather than a model of the agent's world.
_SCHEMA_IS_ENOUGH = 3

# How many tools have to act on a noun the schema never declared before it counts as an object
# in its own right rather than an action named as one.
_EARNS_ITS_PLACE = 2


# Words whose final ``s`` is part of the word. Stripping it turns address into addres and
# status into statu, which then read as different objects from the ones they duplicate.
_KEEPS_S = ("ss", "us", "is", "sms", "as")


def _singular(word: str) -> str:
    """Enough singularisation for object names, and no more.

    Objects become folder names and coverage rows, so ``ride`` and ``rides`` must not read as two
    different things. Full inflection is not worth a dependency here, but the endings that break
    ordinary nouns are, because a mangled object silently becomes a second object.
    """
    if len(word) < 4 or word.endswith(_KEEPS_S):
        return word
    for suffix, replacement in (("ies", "y"), ("xes", "x"), ("ches", "ch"), ("shes", "sh"), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + replacement
    return word


def _collapsed(names: list[str]) -> tuple[str, ...]:
    """Near-duplicate objects folded into the one they qualify.

    Tool names name the same thing at several grain sizes: ``place`` and ``saved_place``,
    ``booking`` and ``booking_status``, ``otp`` and ``otp_code``. Left apart they multiply the
    object count and split one object's coverage across rows that each look thin.

    First seen wins, and the caller passes the data schema's own nouns first, so the object keeps
    the name the agent's storage gives it rather than whichever name happens to be shortest.
    """
    result: list[str] = []
    for name in names:
        if name in result:
            continue
        parts = name.split("_")
        absorbed = False
        for other in result:
            kept = other.split("_")
            short, long = (parts, kept) if len(parts) <= len(kept) else (kept, parts)
            if short == long[: len(short)] or short == long[-len(short):]:
                absorbed = True
                break
        if not absorbed:
            result.append(name)
    return tuple(result)


def _words(name: str) -> list[str]:
    """A tool name broken into its parts, camelCase and snake_case alike."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return [one for one in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if one]


def object_of(tool_name: str, verbs: set[str]) -> str:
    """The thing a tool acts on, read out of its name.

    The verb is stripped because it names the operation, not the object, and a trailing
    qualifier is stripped because it names how the object was found. What is left is the noun.
    """
    trimmed = _QUALIFIER.sub("", tool_name)
    parts = _words(trimmed)
    # Never strip the last word. A verb list is a set of hints, and several of them are also
    # perfectly good nouns: ``otp`` hints at authenticate and is the thing ``send_otp`` acts on.
    # Stripping every hint leaves nothing and the tool is orphaned from its object in silence.
    while len(parts) > 1 and (parts[0] in verbs or parts[0] in _NOISE):
        parts.pop(0)
    while len(parts) > 1 and parts[-1] in _NOISE:
        parts.pop()
    if not parts or (len(parts) == 1 and parts[0] in _NOISE):
        return ""
    return _singular("_".join(parts))


@dataclass(frozen=True)
class Cell:
    """One thing the agent can be asked: an operation applied to one of its objects."""

    operation: str
    obj: str
    kind: str = ""
    # Tools that plausibly serve this cell. Empty is allowed and common for the reading
    # operations, which is the point.
    tools: tuple[str, ...] = ()
    weight: float = 1.0

    @property
    def name(self) -> str:
        return f"{self.operation}-{self.obj}".replace("_", "-")

    def described(self) -> str:
        served = f", tools: {', '.join(self.tools)}" if self.tools else ", no dedicated tool"
        return f"{self.operation} x {self.obj}{served}"


@dataclass
class Grid:
    """Every cell of one agent, and what was left out of it."""

    objects: tuple[str, ...] = ()
    operations: tuple[Operation, ...] = ()
    cells: tuple[Cell, ...] = ()
    # Cells enumerated and then removed because the agent has no way to serve them.
    dropped: tuple[str, ...] = ()
    # Said out loud when the contract was too thin to derive a real grid.
    thin: str = ""

    def by_kind(self) -> dict[str, list[Cell]]:
        found: dict[str, list[Cell]] = {}
        for cell in self.cells:
            found.setdefault(cell.kind or "other", []).append(cell)
        return found

    def named(self, name: str) -> Cell | None:
        for cell in self.cells:
            if cell.name == name:
                return cell
        return None

    def report(self) -> str:
        """The grid as a writer needs to see it: every cell, grouped, with its tools."""
        lines = [
            f"{len(self.objects)} objects x {len(self.operations)} operations = "
            f"{len(self.objects) * len(self.operations)} cells, "
            f"{len(self.dropped)} dropped, {len(self.cells)} valid.",
            f"Objects: {', '.join(self.objects)}",
        ]
        if self.thin:
            lines.append(f"NOTE: {self.thin}")
        for kind, cells in self.by_kind().items():
            lines.append(f"\n{kind.upper()} ({len(cells)})")
            for cell in cells:
                lines.append(f"  {cell.name}  ({cell.described()})")
        return "\n".join(lines)


def objects_in(contract: AgentContract, axes: AxisSet) -> tuple[str, ...]:
    """The nouns this agent acts on, from every part of the contract that names one.

    Read from several places and unioned rather than taken from the best available one. The data
    schema names them most reliably, tool names cover what the schema left implicit, and the
    starting data catches a store whose shape was never declared. A grid built from one source
    inherits that source's blind spot.
    """
    verbs = {verb for operation in axes.operations for verb in operation.verbs}
    declared: list[str] = []
    for source in (contract.data_schema, contract.base_environment):
        for key in source or {}:
            name = _singular(str(key).strip().lower())
            if name and name not in _NOISE and name not in declared:
                declared.append(name)

    derived: list[str] = []
    for tool in contract.tools:
        name = object_of(tool.name, verbs)
        if name and name not in _NOISE and name not in derived:
            derived.append(name)

    # An agent that declares its storage has already said what its objects are, and said it
    # better than a tool name can. Tool names carry the action as well as the thing, so they
    # yield entries like ``confirmation_sms`` and ``cancellation_quote``, which are things the
    # agent does rather than things it has, and every one becomes a column of odd cells.
    #
    # Below the threshold the schema is too thin to stand on and both sources are used, because
    # a short object list costs more than a noisy one.
    if len(declared) < _SCHEMA_IS_ENOUGH:
        return _collapsed(declared + derived)

    # The schema does not always use the tools' word for the same thing: a schema of ``trips``
    # and ``bookings`` sits under tools that all say ``ride``. Dropping every noun the schema
    # does not name would orphan those tools and leave the agent's main object untested, so a
    # derived noun is kept when several tools act on it.
    #
    # One tool is the giveaway for the other case: ``send_confirmation_sms`` yields
    # ``confirmation_sms``, which is something the agent does, not something it has.
    settled = _collapsed(declared)
    counts: dict[str, int] = {}
    for tool in contract.tools:
        name = object_of(tool.name, verbs)
        if name and not _canonical(name, settled):
            counts[name] = counts.get(name, 0) + 1
    # Fewest words first, then most tools. ``ride`` and ``ride_option`` are the same object seen
    # at two grains; folding is first-seen-wins, so the plainer noun has to be offered first or
    # the object ends up named after one of its own attributes.
    earned = sorted(
        (name for name in derived if counts.get(name, 0) >= _EARNS_ITS_PLACE),
        key=lambda name: (len(name.split("_")), -counts.get(name, 0), name),
    )
    ignored = [name for name in counts if name not in earned]
    if ignored:
        logger.info(
            "read %s noun(s) out of tool names that the schema does not declare and only one "
            "tool touches, so they name an action rather than an object: %s",
            len(ignored),
            ", ".join(sorted(ignored)),
        )
    return _collapsed(declared + earned)


def _canonical(raw: str, objects: tuple[str, ...]) -> str:
    """The object a tool belongs to once near-duplicates have been folded together.

    Collapsing has to be applied to the tools as well as to the list, or a tool whose own noun
    was the one folded away belongs to nothing: ``send_otp`` reads as ``otp`` while the schema
    calls it ``otp_code``, and the cell it should have served is silently dropped. That is how
    an agent with a dozen state-changing tools produced five state-changing cells.
    """
    if not raw:
        return ""
    if raw in objects:
        return raw
    parts = raw.split("_")
    # Longest first, so ``payment_link`` wins over ``payment`` when both are objects.
    ranked = sorted(objects, key=lambda one: -len(one.split("_")))
    for candidate in ranked:
        other = candidate.split("_")
        short, long = (parts, other) if len(parts) <= len(other) else (other, parts)
        # Whole-word prefix or suffix in either direction: a tool may name the object more
        # coarsely than the schema does (``send_otp`` against ``otp_codes``) or more finely
        # (``get_saved_places`` against ``places``). Both are the same object.
        if short == long[: len(short)] or short == long[-len(short):]:
            return candidate
    return ""


def _by_tool(contract: AgentContract, objects: tuple[str, ...], verbs: set[str]) -> dict[str, str]:
    """Which object each tool acts on, canonically."""
    return {
        tool.name: _canonical(object_of(tool.name, verbs), objects) for tool in contract.tools
    }


def _serving(
    cell_object: str, operation: Operation, owned: dict[str, str]
) -> tuple[str, ...]:
    """Tools that plausibly serve this operation on this object."""
    served: list[str] = []
    for name, obj in owned.items():
        if obj != cell_object:
            continue
        words = set(_words(name))
        if not operation.verbs or words & set(operation.verbs):
            served.append(name)
    return tuple(served)


def derive(
    contract: AgentContract, axes: AxisSet, objects: tuple[str, ...] | None = None
) -> Grid:
    """The grid for one agent.

    Never returns nothing. An agent with no readable objects still gets a grid built around a
    single one, because a caller who asked for scenarios needs scenarios, and the stage that
    runs next has a model and a copy of the source with which to do better than this.

    ``objects`` replaces the derivation entirely. Derivation reads a contract, which is a summary
    of an agent; the stage reading the agent's own source can see what the summary missed, and
    correcting it there is better than guessing harder here.
    """
    operations = axes.operations
    if not operations:
        return Grid(thin="the axis file declares no operations, so no grid could be derived")

    objects = tuple(objects) if objects else objects_in(contract, axes)
    thin = ""
    if not objects:
        # Nothing named a noun. Fall back to the agent itself so the stage still has somewhere
        # to start, and say so, because every count downstream is affected by it.
        objects = (_singular(_words(contract.agent or "request")[-1] if contract.agent else "request"),)
        thin = (
            "the contract named no data collections and no tools, so the grid was built around "
            f"{objects[0]!r} alone. Read the agent's source and name its real objects before "
            "trusting any coverage number."
        )

    verbs = {verb for operation in operations for verb in operation.verbs}
    owned = _by_tool(contract, objects, verbs)
    cells: list[Cell] = []
    dropped: list[str] = []

    # Operations about the conversation rather than about a thing get one cell each, whatever
    # the agent's object count is.
    for operation in operations:
        if operation.scope != "agent":
            continue
        served = tuple(
            name for name in owned
            if not operation.verbs or set(_words(name)) & set(operation.verbs)
        )
        if operation.needs_own_tool and not served:
            dropped.append(operation.name)
            continue
        cells.append(
            Cell(operation=operation.name, obj="caller", kind=operation.kind,
                 tools=served, weight=1.0 if served else 0.8)
        )

    for obj in objects:
        touching = [name for name, held in owned.items() if held == obj]
        for operation in operations:
            if operation.scope == "agent":
                continue
            served = _serving(obj, operation, owned)
            if operation.needs_own_tool and not served:
                # The agent has no way to do this to this thing. Not a gap in the suite, a
                # fact about the agent, so it is recorded rather than silently missing.
                dropped.append(f"{operation.name}-{obj}".replace("_", "-"))
                continue
            # A contract too thin to name a single tool still has to yield somewhere to start,
            # so the no-tool pruning is skipped for it. Pruning is about what an agent plainly
            # cannot do, and about an agent this poorly described nothing is plain.
            if not thin and operation.kind in ("read", "manage") and not touching:
                dropped.append(f"{operation.name}-{obj}".replace("_", "-"))
                continue
            cells.append(
                Cell(
                    operation=operation.name,
                    obj=obj,
                    kind=operation.kind,
                    tools=served or tuple(touching),
                    # A cell the agent has a dedicated tool for is what it is mostly asked to
                    # do; one it has no tool for is rarer and usually harder. Both are worth
                    # testing, so the difference is a weight rather than a filter.
                    weight=1.0 if served else 0.6,
                )
            )

    return Grid(
        objects=objects,
        operations=operations,
        cells=tuple(cells),
        dropped=tuple(dropped),
        thin=thin,
    )
