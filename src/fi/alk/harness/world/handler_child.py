"""The child process an environment-authored handler runs in (see `handler_host`).

It is started with `python -I -c <this file's source>`, so it imports nothing from disk beyond
the standard library: in a sandbox the paths that hold the builder's files are not visible to it.
It reads one call at a time from stdin, runs the handler with a `db` whose every operation is a
request to the parent, and writes the outcome to stdout. Messages are JSON lines in both
directions, with the codec below; the parent never unpickles anything this process sends.

The codec is also what the parent imports, so both sides encode values the same way.
"""

from __future__ import annotations

import base64
import datetime
import decimal
import json
import sys
import uuid
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
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


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
