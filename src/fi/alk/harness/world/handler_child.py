"""The child process environment-authored Python runs in (see `handler_host`).

It is started with `python -I -c <this file's source>`, so it imports nothing from disk beyond
the standard library: in a sandbox the paths that hold the builder's files are not visible to it.
It runs a handler with a `db`, or setup and checks with a restricted `world`; every operation is a
request to the parent. Messages are JSON lines in both directions, with the codec below; the parent
never unpickles anything this process sends.

The codec is also what the parent imports, so both sides encode values the same way.
"""

from __future__ import annotations

import base64
import contextlib
import datetime
import decimal
import inspect
import json
import os
import random
import sys
import uuid
from dataclasses import dataclass
from typing import Any

# A value JSON cannot carry is tagged with its type, so a Decimal or a timestamp a store returns
# comes back as the same type and a check comparing it sees what it would have in process.
_TAGS = (
    "$bytes",
    "$decimal",
    "$datetime",
    "$date",
    "$time",
    "$uuid",
    "$tuple",
    "$dict",
)
_PROTOCOL_OUT = sys.stdout


def encode(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, bytes | bytearray | memoryview):
        return {"$bytes": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, decimal.Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, datetime.datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, datetime.date):
        return {"$date": value.isoformat()}
    if isinstance(value, datetime.time):
        return {"$time": value.isoformat()}
    if isinstance(value, uuid.UUID):
        return {"$uuid": str(value)}
    if isinstance(value, tuple):
        return {"$tuple": [encode(item) for item in value]}
    if isinstance(value, list | set | frozenset):
        return [encode(item) for item in value]
    if isinstance(value, dict):
        encoded = {str(key): encode(item) for key, item in value.items()}
        # A record that happens to look like a tag stays a record.
        return (
            {"$dict": encoded}
            if len(encoded) == 1 and next(iter(encoded)) in _TAGS
            else encoded
        )
    return str(value)


def decode(value: Any) -> Any:
    if isinstance(value, list):
        return [decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    if len(value) == 1:
        tag, inner = next(iter(value.items()))
        if tag == "$bytes":
            return base64.b64decode(inner)
        if tag == "$decimal":
            return decimal.Decimal(inner)
        if tag == "$datetime":
            return datetime.datetime.fromisoformat(inner)
        if tag == "$date":
            return datetime.date.fromisoformat(inner)
        if tag == "$time":
            return datetime.time.fromisoformat(inner)
        if tag == "$uuid":
            return uuid.UUID(inner)
        if tag == "$tuple":
            return tuple(decode(item) for item in inner)
        if tag == "$dict":
            return {key: decode(item) for key, item in inner.items()}
    return {key: decode(item) for key, item in value.items()}


def _send(message: dict[str, Any]) -> None:
    _PROTOCOL_OUT.write(json.dumps(message) + "\n")
    _PROTOCOL_OUT.flush()


def _receive() -> dict[str, Any] | None:
    line = sys.stdin.readline()
    return json.loads(line) if line else None


class ToolError(Exception):
    """A refusal. The parent rebuilds the world's own ToolError from it, category included."""

    def __init__(self, message: str, category: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.category = category


class _DatabaseError(Exception):
    pass


@dataclass(frozen=True)
class AuthoredOutcome:
    ok: bool
    said: str = ""
    broken: bool = False


def takes_reference(checker: object) -> bool:
    """Whether a check, or its source, is written `check(world, calls, reference)`."""
    import ast

    source = (
        checker if isinstance(checker, str) else getattr(checker, "_alk_source", None)
    )
    if source:
        entry = getattr(checker, "_alk_entry", "check")
        try:
            body = ast.parse(source).body
        except SyntaxError:
            return False
        for node in body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == entry
            ):
                return len(node.args.args) >= 3
        return False
    try:
        return len(inspect.signature(checker).parameters) >= 3
    except (TypeError, ValueError):
        return False


def evaluate_authored(
    source: str,
    world: Any,
    *,
    kind: str,
    name: str,
    calls: Any = (),
    reference: Any = (),
) -> AuthoredOutcome:
    """Run one environment-authored setup, readiness check, or deterministic check."""
    if kind in {"setup", "ready"} and not source.strip():
        return AuthoredOutcome(True)

    namespace: dict[str, Any] = {}
    filename = (
        f"<check:{name}>"
        if kind == "check"
        else f"<world-check:{name}>"
        if kind == "world-check"
        else f"<{name}>"
    )
    try:
        exec(compile(source, filename, "exec"), namespace)  # noqa: S102
    except Exception as failed:  # noqa: BLE001 - model-written code is reported as broken
        said = (
            f"the check would not compile: {failed}"
            if kind in {"check", "world-check"}
            else f"{name} would not compile: {failed}"
        )
        return AuthoredOutcome(False, said, broken=True)

    entry = "check" if kind in {"check", "world-check"} else kind
    function = namespace.get(entry)
    if not callable(function):
        said = (
            "the check defines no check(world, calls)"
            if kind == "check"
            else "the check defines no check(world)"
            if kind == "world-check"
            else f"{name} defines no {entry}()"
        )
        return AuthoredOutcome(False, said, broken=True)

    try:
        if kind == "check":
            value = (
                function(world, list(calls), list(reference))
                if takes_reference(function)
                else function(world, list(calls))
            )
        elif kind == "world-check":
            try:
                wants = len(inspect.signature(function).parameters)
            except (TypeError, ValueError):
                wants = 1
            value = function(world) if wants < 2 else function(world, [])
        else:
            value = function(world)
    except Exception as failed:  # noqa: BLE001 - model-written code is reported as broken
        said = (
            f"the check raised {type(failed).__name__}: {str(failed)[:200]}"
            if kind in {"check", "world-check"}
            else f"{name} raised {type(failed).__name__}: {failed}"
        )
        return AuthoredOutcome(False, said, broken=True)

    if kind == "world-check":
        return AuthoredOutcome(value is None, "" if value is None else str(value))
    if value is None or value is True or (isinstance(value, str) and not value.strip()):
        return AuthoredOutcome(True)
    if value is False:
        if kind == "ready":
            return AuthoredOutcome(
                False,
                f"{name} returned False without saying what is wrong. Return the sentence "
                "instead, or None if it holds.",
                broken=True,
            )
        return AuthoredOutcome(False, "False")
    if kind == "check" and not isinstance(value, str):
        return AuthoredOutcome(
            False,
            f"the check returned {type(value).__name__} {repr(value)[:200]}; a check returns a "
            "sentence naming what is wrong, or None when it held.",
            broken=True,
        )
    if kind == "ready" and not isinstance(value, str):
        return AuthoredOutcome(
            False,
            f"{name} returned {type(value).__name__} {repr(value)[:200]}. Return the sentence "
            "naming what is missing, or None if it holds.",
            broken=True,
        )
    return AuthoredOutcome(False, str(value))


class _Db:
    """The handler's `db`: the same methods as the world's, each answered by the parent."""

    def __init__(self) -> None:
        self.state = None

    def _ask(self, method: str, *args: Any, **kwargs: Any) -> Any:
        _send(
            {
                "db": {
                    "method": method,
                    "args": encode(list(args)),
                    "kwargs": encode(kwargs),
                }
            }
        )
        reply = _receive()
        if reply is None:
            raise SystemExit(0)
        if "error" in reply:
            raised = type(reply["error"]["type"], (_DatabaseError,), {})
            raise raised(reply["error"]["message"])
        return decode(reply["reply"])

    def query(self, sql: str, params: Any = ()) -> list[dict[str, Any]]:
        return self._ask("query", sql, list(params))

    def one(self, sql: str, params: Any = ()) -> dict[str, Any] | None:
        return self._ask("one", sql, list(params))

    def execute(self, sql: str, params: Any = ()) -> int:
        return self._ask("execute", sql, list(params))

    def collections(self) -> list[str]:
        return self._ask("collections")

    def records(self, collection: str) -> list[dict[str, Any]]:
        return self._ask("records", collection)

    def find(self, collection: str, **fields: Any) -> list[dict[str, Any]]:
        return self._ask("find", collection, **fields)

    def add(self, collection: str, record: dict[str, Any]) -> Any:
        return self._ask("add", collection, dict(record))


@dataclass(frozen=True)
class _Call:
    name: str
    arguments: dict[str, Any]
    result: Any = None
    ok: bool = True
    error: str = ""
    refused: bool = False
    at: float = 0.0


def _call(value: dict[str, Any]) -> _Call:
    return _Call(
        name=str(value.get("name") or ""),
        arguments=dict(value.get("arguments") or {}),
        result=value.get("result"),
        ok=bool(value.get("ok", True)),
        error=str(value.get("error") or ""),
        refused=bool(value.get("refused", False)),
        at=float(value.get("at") or 0.0),
    )


class _World:
    def __init__(self, authored: dict[str, Any]) -> None:
        if authored.get("has_world_index"):
            self.world_index = int(authored["world_index"])
        if "rng" in authored:
            self.rng = random.Random()
            self.rng.setstate(decode(authored["rng"]))

    def _ask(self, method: str, *args: Any, **kwargs: Any) -> Any:
        _send(
            {
                "world": {
                    "method": method,
                    "args": encode(list(args)),
                    "kwargs": encode(kwargs),
                }
            }
        )
        reply = _receive()
        if reply is None:
            raise SystemExit(0)
        if "error" in reply:
            raised = type(str(reply["error"]["type"]), (Exception,), {})
            raise raised(str(reply["error"]["message"]))
        value = decode(reply["reply"])
        return _call(value) if method == "call" else value

    def state(self, table: str | None = None) -> Any:
        return self._ask("state") if table is None else self._ask("state", table)

    def query(self, sql: str, params: Any = ()) -> Any:
        return self._ask("query", sql, params)

    def put(self, collection: str, record: dict[str, Any], *, key: str = "") -> Any:
        return self._ask("put", collection, dict(record), key=key)

    def change(
        self,
        collection: str,
        key: str,
        changes: dict[str, Any],
        *,
        by: str = "",
    ) -> Any:
        return self._ask("change", collection, key, dict(changes), by=by)

    def drop(self, collection: str, key: str = "", *, by: str = "") -> Any:
        return self._ask("drop", collection, key, by=by)

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> _Call:
        return self._ask("call", name, dict(arguments or {}))


def _raised(exc: BaseException) -> dict[str, Any]:
    return {
        "mro": [base.__name__ for base in type(exc).__mro__],
        "message": getattr(exc, "message", None)
        if isinstance(exc, ToolError)
        else str(exc),
        "category": getattr(exc, "category", None),
    }


def _limit_memory() -> None:
    try:
        import resource

        most = 2 * 1024 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (most, most))
    except (ImportError, ValueError, OSError):
        # Not every platform lets a process lower its address space (macOS); the namespace and
        # the timeout are the boundary, this is only a backstop against a runaway allocation.
        pass


def main() -> None:
    _limit_memory()
    while (request := _receive()) is not None:
        if "authored" in request:
            authored = request["authored"]
            calls = [_call(decode(one)) for one in authored.get("calls") or []]
            world = _World(authored)
            with (
                open(os.devnull, "w", encoding="utf-8") as discarded,
                contextlib.redirect_stdout(discarded),
            ):
                outcome = evaluate_authored(
                    str(authored.get("source") or ""),
                    world,
                    kind=str(authored.get("kind") or ""),
                    name=str(authored.get("name") or ""),
                    calls=calls,
                    reference=decode(authored.get("reference") or []),
                )
            result: dict[str, Any] = {
                "ok": outcome.ok,
                "said": outcome.said,
                "broken": outcome.broken,
            }
            if hasattr(world, "rng"):
                result["rng"] = encode(world.rng.getstate())
            _send({"outcome": result})
            continue
        call = request["call"]
        namespace: dict[str, Any] = {"ToolError": ToolError, "json": json}
        try:
            code = compile(call["source"], f"<handler:{call['name']}>", "exec")
            exec(code, namespace)  # noqa: S102 - the isolated child exists to run it
            handle = namespace.get("handle")
            if not callable(handle):
                # The same error the world raises in process, so the outcome reads the same.
                raise RuntimeError("handler defines no handle(args, db)")  # noqa: TRY004
            value = handle(decode(call["args"]), _Db())
        except Exception as exc:  # noqa: BLE001 - every failure is reported to the parent
            _send({"raised": _raised(exc)})
            continue
        try:
            _send({"result": encode(value)})
        except (TypeError, ValueError) as exc:
            _send({"raised": _raised(exc)})


if __name__ == "__main__":
    main()
