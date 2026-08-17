"""The stores the harness can stand up for an agent, and what every one of them must do.

The world an agent is tested in is not a replica of its tools. It is the thing underneath
them: the store its queries really run against. The agent keeps its own code, its own client
and its own queries, and the only thing that changes is what its connection string points at.
So a store is never asked to execute a tool. It is asked to exist, to hold data, to say what
it holds, and to go back to how it was.

Which engine gets stood up is read off the agent, never chosen for it. If the agent uses
Postgres it gets Postgres; if it uses ClickHouse it gets ClickHouse. They disagree about
dialect, types and what a transaction even means, so testing one against the other grades an
agent on queries it never runs. An engine the harness cannot stand up is an answer -- say so
and stop -- not a reason to substitute something that merely resembles it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable


class StoreError(RuntimeError):
    """The store could not be stood up, or could not answer.

    Distinct from anything the agent did. A store that will not start is our problem and
    should stop the run loudly, because every result after it would be measured against
    something that is not there.
    """


@dataclass
class Snapshot:
    """Everything a store held at one moment, and what it takes to put it back.

    ``rows`` is kept in the same shape ``state()`` reports, so a check written against a
    world's state reads a snapshot without knowing which engine produced it.

    ``counters`` is whatever an engine hands out that is not itself a row: a Postgres sequence,
    a MySQL auto-increment, anything that keeps counting after the rows are gone. Restoring
    rows without restoring these gives the next scenario ids that continue from the last one,
    and a check naming a specific id then fails for a reason that has nothing to do with the
    agent. Engines that hand out nothing of the sort leave it empty, which is not a gap.
    """

    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {table: len(rows) for table, rows in self.rows.items()}


@runtime_checkable
class Store(Protocol):
    """A running store the agent under test is pointed at.

    The lifecycle is deliberately coarse. A store is started once for a whole suite and reset
    between scenarios, because standing up an engine costs seconds and putting its data back
    costs milliseconds. Anything that makes ``restore`` expensive belongs in ``start``.
    """

    engine: str

    def start(self) -> None:
        """Stand the store up and block until it answers. Raise ``StoreError`` if it will not."""

    def dsn(self) -> str:
        """The connection string to hand the agent. This is the whole point of the store."""

    def apply(self, script: str) -> None:
        """Run statements against the store: the agent's own migrations, or its seed.

        Whatever language this engine speaks. Not necessarily SQL -- what makes it a store is
        that the agent's own setup can be replayed into it, not which dialect it accepts.
        """

    def state(self) -> dict[str, list[dict[str, Any]]]:
        """Everything the store holds, grouped by name, which is what a check compares against.

        Tables for a relational engine, collections for a document one. The grouping is the
        contract; what an engine calls the groups is its own business.
        """

    def freeze(self) -> Snapshot:
        """Capture the state to come back to between scenarios."""

    def restore(self, snapshot: Snapshot) -> None:
        """Put the data back exactly as the snapshot found it, counters included."""

    def stop(self) -> None:
        """Tear the store down. Safe to call when it never started."""


_REGISTRY: dict[str, Callable[..., Store]] = {}


def register_store(engine: str, factory: Callable[..., Store]) -> None:
    """Teach the harness an engine. A class and this line.

    The cost of this line is what decides whether "whatever the agent uses" is real or an
    aspiration, which is why the shared work lives in ``ContainerStore`` and an engine
    contributes only what genuinely differs.
    """
    _REGISTRY[engine] = factory


def resolve(engine: str, **options: Any) -> Store:
    """The store for an engine, or a refusal naming what there is.

    Deliberately not a fallback. An agent on an engine nobody has taught the harness to run is
    a gap worth reporting, and quietly handing it a different store would produce a green suite
    about queries the agent never executes.
    """
    if engine not in _REGISTRY:
        raise StoreError(
            f"no store for engine {engine!r}; the harness can stand up "
            f"{', '.join(sorted(_REGISTRY)) or 'nothing yet'}"
        )
    return _REGISTRY[engine](**options)


def supported() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


from .container import ContainerStore, strays  # noqa: E402
from .postgres import PostgresStore  # noqa: E402

# Postgres is registered as the worked example, not as the supported list. An engine the
# harness has never seen is meant to be written at build time against ``ContainerStore`` and
# proved by the gates, rather than waiting for someone to ship a class for it.
register_store(PostgresStore.engine, PostgresStore)

__all__ = [
    "ContainerStore",
    "PostgresStore",
    "Snapshot",
    "Store",
    "StoreError",
    "register_store",
    "resolve",
    "strays",
    "supported",
]
