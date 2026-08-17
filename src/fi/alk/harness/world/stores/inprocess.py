"""A store that is not a server: the agent's own data, held where the agent holds it.

Plenty of real agents keep their state in memory, loaded from files their repository ships --
tau-bench's environments are dictionaries built from JSON, and they are not unusual. There is
no engine to stand up for those, no port, no connection string. Standing up a database for
them and hoping the agent notices would be exactly the replication this path exists to avoid.

So the store is the structure itself, and the agent's own loader is what fills it. The tools
under test then run against this dict the same way they run in production, because it *is* the
thing they run against -- unmodified code, its real data, and a copy taken before each
scenario so the next one starts where the last one began.

This is also the honest test of whether ``Store`` describes stores or merely describes
containers. Nothing here starts a process, and the gate does not care.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

from . import Snapshot, StoreError, register_store


class InProcessStore:
    """The agent's own in-memory data, as a store.

    ``loader`` is the agent's function -- ``load_data`` or whatever it is called. It is
    imported from the agent's repository and called, never reimplemented, so what is held is
    what the agent would hold on a cold start.
    """

    engine = "inprocess"

    def __init__(
        self,
        loader: Callable[[], dict[str, Any]] | None = None,
        module: str = "",
        function: str = "load_data",
        root: str | Path | None = None,
    ) -> None:
        self.loader = loader
        self.module = module
        self.function = function
        self.root = str(root) if root else ""
        self.data: dict[str, Any] = {}
        self._started = False

    # -- lifecycle -------------------------------------------------------------------

    def start(self) -> None:
        """Load the agent's data by calling the agent's own loader."""
        if self._started:
            return
        if self.loader is None:
            self.loader = self._imported()
        loaded = self.loader()
        if not isinstance(loaded, dict):
            raise StoreError(
                f"{self.function} returned {type(loaded).__name__}, not a dict of named "
                "groups, so there is nothing a check could read by name"
            )
        self.data = loaded
        self._started = True

    def _imported(self) -> Callable[[], dict[str, Any]]:
        """The agent's loader, imported from the agent's repository.

        Deliberately an import of their code rather than a reimplementation of it. If it will
        not import, that is worth stopping for: the alternative is inventing data and grading
        the agent against a world it has never seen.
        """
        import importlib
        import sys

        if not self.module:
            raise StoreError("no loader and no module to import one from")
        if self.root and self.root not in sys.path:
            sys.path.insert(0, self.root)
        try:
            found = importlib.import_module(self.module)
        except ImportError as exc:
            raise StoreError(
                f"cannot import {self.module!r} from {self.root or 'sys.path'}: {exc}. The "
                "agent's own dependencies have to be importable for its loader to run."
            ) from exc
        loader = getattr(found, self.function, None)
        if not callable(loader):
            raise StoreError(f"{self.module}.{self.function} is not a function")
        return loader

    def stop(self) -> None:
        self.data = {}
        self._started = False

    def dsn(self) -> str:
        """Nothing connects to this, which is the point.

        Reported rather than raised: a store with no address is a fact about this kind of
        agent, not a failure, and the environment records it so nothing later goes looking for
        a connection string that was never going to exist.
        """
        return "inprocess://"

    # -- contents --------------------------------------------------------------------

    def apply(self, script: str) -> None:
        """Run a snippet against the data, with ``data`` in scope and nothing else.

        This is how migrations and seeds are expressed for a store with no query language: the
        same Python the agent's own code would use to reach into its structures.
        """
        if not script.strip():
            return
        namespace: dict[str, Any] = {"data": self.data, "json": json}
        try:
            exec(compile(script, "<seed>", "exec"), namespace)  # nosec B102
        except Exception as exc:  # noqa: BLE001 - the caller's snippet, reported as given
            raise StoreError(f"{type(exc).__name__}: {exc}") from exc

    def state(self) -> dict[str, list[dict[str, Any]]]:
        """Every group and its rows, in the shape the checks already expect.

        The agent's structures are usually keyed by id rather than listed, so a mapping is
        turned into rows with the key carried along as ``_id``. Without that a check counting
        rows in ``orders`` would be counting nothing, and the id it needs to name would have
        been thrown away.
        """
        out: dict[str, list[dict[str, Any]]] = {}
        for name, group in self.data.items():
            if isinstance(group, dict):
                out[name] = [
                    {"_id": key, **value} if isinstance(value, dict) else {"_id": key, "value": value}
                    for key, value in group.items()
                ]
            elif isinstance(group, list):
                out[name] = [
                    row if isinstance(row, dict) else {"value": row} for row in group
                ]
            else:
                out[name] = [{"value": group}]
        return out

    # -- freeze and restore ----------------------------------------------------------

    def freeze(self) -> Snapshot:
        """A deep copy. There is nothing behind the rows here, so no counters to carry."""
        return Snapshot(rows=copy.deepcopy(self.state()), counters={})

    def restore(self, snapshot: Snapshot) -> None:
        """Put the structure back the way the agent's loader left it.

        Rebuilt from the rows rather than kept as a second copy of ``data``, so restore is
        checked against exactly what ``state`` reports -- the thing the gate compares and the
        thing a check reads are then the same thing, and cannot drift apart.
        """
        rebuilt: dict[str, Any] = {}
        for name, rows in snapshot.rows.items():
            original = self.data.get(name)
            if isinstance(original, list):
                rebuilt[name] = [
                    dict(row) if "value" not in row or len(row) > 1 else row["value"]
                    for row in copy.deepcopy(rows)
                ]
                continue
            keyed: dict[str, Any] = {}
            for row in copy.deepcopy(rows):
                identifier = row.pop("_id", None)
                if identifier is None:
                    continue
                keyed[identifier] = row.get("value") if set(row) == {"value"} else row
            rebuilt[name] = keyed
        # A group the snapshot does not mention is emptied, not carried over: restore has to be
        # able to reproduce a snapshot that holds nothing, or the gate cannot empty the store to
        # find out whether the checks actually bite. The key itself stays, with its original
        # shape, because the agent's own code indexes into it and would not survive its absence.
        for name, group in self.data.items():
            if name not in rebuilt:
                rebuilt[name] = [] if isinstance(group, list) else {}
        self.data.clear()
        self.data.update(rebuilt)


register_store(InProcessStore.engine, InProcessStore)
