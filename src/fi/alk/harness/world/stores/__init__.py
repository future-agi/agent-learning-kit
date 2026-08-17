"""The stores the harness can stand up for an agent, and what every one of them must do.

The world an agent is tested in is not a replica of its tools. It is the thing underneath
them: the database its queries really run against. The agent keeps its own code, its own
client and its own SQL, and the only thing that changes is what its connection string points
at. So a store is not asked to execute a tool. It is asked to exist, to hold data, to say what
it holds, and to go back to how it was.

Which engine gets stood up is read off the agent, never chosen for it. Postgres and ClickHouse
disagree about dialect, types and transactions, so testing a Postgres agent against anything
else grades it on SQL it never runs. A store the harness cannot stand up is an answer -- say so
and stop -- not a reason to fall back to something that merely resembles it.
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

    Rows are kept in the same shape ``state()`` reports, so a check written against a world's
    state reads a snapshot without knowing which engine produced it. ``sequences`` is carried
    separately because restoring rows without restoring the counters behind them hands the next
    scenario ids that continue from the last one, and a check naming a specific id then fails
    for a reason that has nothing to do with the agent.
    """

    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    sequences: dict[str, int] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {table: len(rows) for table, rows in self.rows.items()}


@runtime_checkable
class Store(Protocol):
    """A running data store the agent under test is pointed at.

    The lifecycle is deliberately coarse. A store is started once for a whole suite and reset
    between scenarios, because standing up an engine costs seconds and putting its data back
    costs milliseconds. Anything that makes ``restore`` expensive belongs in ``start``.
    """

    engine: str

    def start(self) -> None:
        """Stand the store up and block until it answers. Raise ``StoreError`` if it will not."""

    def dsn(self) -> str:
        """The connection string to hand the agent. This is the whole point of the store."""

    def apply_sql(self, sql: str) -> None:
        """Run statements against the store: the agent's own migrations, or its seed."""

    def state(self) -> dict[str, list[dict[str, Any]]]:
        """Every table and its rows, which is what a check compares against after a run."""

    def freeze(self) -> Snapshot:
        """Capture the state to come back to between scenarios."""

    def restore(self, snapshot: Snapshot) -> None:
        """Put the data back exactly as the snapshot found it, counters included."""

    def stop(self) -> None:
        """Tear the store down. Safe to call when it never started."""


_REGISTRY: dict[str, Callable[..., Store]] = {}


def register_store(engine: str, factory: Callable[..., Store]) -> None:
    """Add an engine the harness can stand up. A class and this line."""
    _REGISTRY[engine] = factory


def resolve(engine: str, **options: Any) -> Store:
    """The store for an engine, or a refusal naming what there is.

    Deliberately not a fallback. An agent on an engine nobody has taught the harness to run is
    a gap worth reporting, and quietly handing it a different database would produce a green
    suite about SQL the agent never executes.
    """
    if engine not in _REGISTRY:
        raise StoreError(
            f"no store for engine {engine!r}; the harness can stand up "
            f"{', '.join(sorted(_REGISTRY)) or 'nothing yet'}"
        )
    return _REGISTRY[engine](**options)


def supported() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


from .postgres import PostgresStore  # noqa: E402  (registration on import)

register_store(PostgresStore.engine, PostgresStore)

__all__ = [
    "PostgresStore",
    "Snapshot",
    "Store",
    "StoreError",
    "register_store",
    "resolve",
    "supported",
]
