"""The runtime a generated world runs on.

A generated world is a database plus one handler per tool. The handler decides what a call does;
this decides what a handler is allowed to be, what happens when one fails, and what the world
looks like afterwards. Keeping that here means a generated file stays small enough to read and
correct, and the parts that must be exact are not regenerated every time.

The contract with the rest of the platform is ``EnvironmentAdapter``: ``reset`` publishes the
tools and the starting state, ``handle_tool_call`` executes one call, and the state afterwards is
what the checks grade. A world is therefore drivable by any loop that already drives an
environment, which is the whole reason we generate against this interface rather than inventing
one.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from fi.simulate.environment import (
    EnvironmentAdapter,
    EnvironmentSnapshot,
    ToolExecutionResult,
)


class ToolError(Exception):
    """A tool refusing for a real reason the agent should see and recover from.

    Distinct from a crash. A refusal is the world working: the id does not exist, the item is
    unavailable, the argument is outside what the tool accepts. A crash is our bug, and the two
    must never look the same to a caller deciding whether the agent behaved correctly.
    """


@dataclass
class Db:
    """The handle a handler gets. Deliberately small: query, execute, one.

    Handlers get a database, not a filesystem and not a network. Anything a handler can reach is
    something a generated world could depend on, and a world that depends on the outside is not
    reproducible.
    """

    connection: sqlite3.Connection

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        cursor = self.connection.execute(sql, tuple(params))
        columns = [column[0] for column in (cursor.description or [])]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        cursor = self.connection.execute(sql, tuple(params))
        self.connection.commit()
        return cursor.rowcount


def _is_refusal(raised: BaseException) -> bool:
    """Whether an exception is the world saying no, rather than the world falling over.

    Matched by name as well as by identity. A generated handler often declares its own
    ``ToolError`` rather than using the one already in scope, which is defensive and sensible
    from where it sits, and would otherwise turn every deliberate refusal into a reported crash.
    Relying on an invisible convention being followed is not a way to decide something this
    load-bearing.
    """
    if isinstance(raised, ToolError):
        return True
    return any(base.__name__ == "ToolError" for base in type(raised).__mro__)


@dataclass
class Call:
    """One tool call and what the world did with it."""

    name: str
    arguments: dict[str, Any]
    result: Any = None
    ok: bool = True
    error: str = ""
    refused: bool = False


class GeneratedWorld(EnvironmentAdapter):
    """A database-backed world whose tools are generated per agent.

    Subclasses declare ``name``, ``tools`` and ``handlers``. Everything about execution,
    refusal, and state reporting is here so that a generated subclass carries only the parts
    that are specific to one agent.
    """

    name = "generated"
    tools: list[dict[str, Any]] = []
    handlers: dict[str, str] = {}

    def __init__(self, database: str | Path = ":memory:") -> None:
        self.database = str(database)
        self.connection = sqlite3.connect(self.database, check_same_thread=False)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.calls: list[Call] = []

    # -- EnvironmentAdapter ----------------------------------------------------------

    def reset(self, **_context: Any) -> EnvironmentSnapshot:
        self.calls = []
        return EnvironmentSnapshot(tools=list(self.tools), state=self.state())

    def observe(self, **_context: Any) -> EnvironmentSnapshot:
        return EnvironmentSnapshot(tools=list(self.tools), state=self.state())

    def handle_tool_call(
        self, tool_call: Mapping[str, Any], **_context: Any
    ) -> ToolExecutionResult | None:
        name = str(
            tool_call.get("name") or (tool_call.get("function") or {}).get("name") or ""
        )
        call_id = tool_call.get("id") or tool_call.get("tool_call_id")
        arguments = tool_call.get("arguments") or tool_call.get("args") or {}
        if not isinstance(arguments, Mapping):
            arguments = {}

        call = self.call(name, arguments)
        content = (
            json.dumps(call.result, default=str)
            if not isinstance(call.result, str)
            else call.result
        )
        return ToolExecutionResult(
            tool_call_id=call_id,
            tool_name=name or "unknown",
            content=call.error if not call.ok else content,
            result=call.result,
            success=call.ok,
            error=call.error or None,
            state_updates=self.state(),
        )

    # -- execution -------------------------------------------------------------------

    def call(self, name: str, arguments: Mapping[str, Any] | None = None) -> Call:
        """Execute one call and record it. Never raises: a failure is an outcome, not an event.

        An unknown tool is a refusal rather than a silent success. An agent reaching for a tool
        that does not exist is a finding, and answering it with an acknowledgement is how a test
        passes something it should have caught.
        """
        args = dict(arguments or {})
        if name not in self.handlers:
            return self._record(
                Call(
                    name=name,
                    arguments=args,
                    ok=False,
                    refused=True,
                    error=(
                        f"no such tool {name!r}; this agent has "
                        f"{', '.join(sorted(self.handlers)) or 'none'}"
                    ),
                )
            )

        namespace: dict[str, Any] = {"ToolError": ToolError, "json": json}
        try:
            exec(compile(self.handlers[name], f"<handler:{name}>", "exec"), namespace)
            handle = namespace.get("handle")
            if not callable(handle):
                raise RuntimeError("handler defines no handle(args, db)")
            value = handle(args, Db(self.connection))
        except Exception as raised:
            if _is_refusal(raised):
                return self._record(
                    Call(
                        name=name,
                        arguments=args,
                        ok=False,
                        refused=True,
                        error=str(raised),
                    )
                )
            # Our bug, not the agent's. Labelled differently so a run is never scored
            # against a world that fell over.
            return self._record(
                Call(
                    name=name,
                    arguments=args,
                    ok=False,
                    error=f"{type(raised).__name__}: {raised}",
                )
            )
        return self._record(Call(name=name, arguments=args, result=value))

    def _record(self, call: Call) -> Call:
        self.calls.append(call)
        return call

    # -- state -----------------------------------------------------------------------

    def checkpoint(self) -> sqlite3.Connection:
        """A copy of the current data, to come back to.

        Probes mutate: ordering an item inserts a row. Without a way back, each probe runs
        against the debris of the ones before it, and a check expecting three rows finds seven.
        The same restore-a-fresh-copy discipline scenarios use, applied to the gate itself.
        """
        copy = sqlite3.connect(":memory:")
        with copy:
            self.connection.backup(copy)
        return copy

    def revert(self, checkpoint: sqlite3.Connection) -> None:
        """Put the data back as it was when the checkpoint was taken."""
        with self.connection:
            checkpoint.backup(self.connection)

    def state(self) -> dict[str, Any]:
        """Every table and its rows: what the checks compare against after a run."""
        tables = Db(self.connection).query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        db = Db(self.connection)
        return {row["name"]: db.query(f"SELECT * FROM {row['name']}") for row in tables}

    def close(self) -> None:
        self.connection.close()


@dataclass
class WorldSpec:
    """What a generated world is, before it is written out."""

    agent: str
    schema_sql: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)
    handlers: dict[str, str] = field(default_factory=dict)
    notes: str = ""
