"""Small HTTP boundary for a graph explicitly declared in ``langgraph.json``.

The graph remains submitted source code. This adapter only translates the harness chat
request into LangGraph's standard messages state; it never edits or substitutes the graph.

Every request runs on one event loop, on a daemon thread that lives as long as the process,
so a model client built when the graph's module is imported keeps a live loop across turns.
The kit runs one agent process per world and sends its turns one at a time, so sharing the
loop costs little, but:

- a node that blocks the loop (synchronous I/O inside an ``async`` node) stalls any other
  request in flight until it returns;
- synchronous nodes share the loop's default thread pool, and ``threading.local`` state in
  async node code is shared between requests (context variables stay per request);
- a graph still running at ``ALK_LANGGRAPH_TIMEOUT_SECONDS`` is cancelled and answers 500
  ``RequestTimeout``, as the subprocess adapter does: a slow graph is the agent's failure,
  not one to retry. A node that blocks the loop, or catches the cancel, can run past the
  deadline; if it is still running ``_WAIT_MARGIN_SECONDS`` later, the request answers 500
  ``RequestTimeout`` then. A request answered with a timeout before its graph started never
  starts it;
- tasks a turn starts and leaves running are not cancelled when the turn ends;
- if the graph's code stops the loop itself, every later turn is refused and ``/health``
  answers 503 instead of 200;
- ``close()`` is for tests: it ends the loop for good, because clients built at import may
  be bound to it.

Each request's ``thread_id`` reaches the graph as LangGraph's own thread,
``config={"configurable": {"thread_id": ...}}``, so a node or tool can tell conversations apart
with ``langgraph.config.get_config()``. The kit sends the whole history every turn, so what a
graph keeps of a thread would see it twice. So:

- a LangGraph graph compiled with a checkpointer of its own is refused once, when it is
  loaded, with a message that names the checkpointer: it would keep every turn and see the
  history again on each, and LangGraph refuses such a graph called without a thread. One bound
  to a fixed thread with ``with_config`` is refused too: every conversation would share that
  stored thread;
- any other object with a ``checkpointer`` attribute is called as it was before threads were
  passed: with no thread, and not refused;
- an object whose ``ainvoke`` takes no ``config`` (neither by name nor through ``**kwargs``) is
  called as before, with the state alone;
- not covered: a subgraph compiled with its own checkpointer under a parent with none, and a
  graph that keys a memory store of its own by the thread. Each keeps every turn of a
  conversation, and so sees the history again on each.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import inspect
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def _load_graph() -> Any:
    declaration = os.environ["ALK_LANGGRAPH_ENTRYPOINT"]
    relative, attribute = declaration.rsplit(":", 1)
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or path.suffix != ".py":
        raise ValueError("invalid LangGraph entrypoint path")
    parts = path.with_suffix("").parts
    if "src" in parts:
        index = parts.index("src")
        sys.path.insert(0, str(Path.cwd().joinpath(*parts[: index + 1])))
        parts = parts[index + 1 :]
    else:
        sys.path.insert(0, str(Path.cwd()))
    graph = getattr(importlib.import_module(".".join(parts)), attribute)
    if not callable(getattr(graph, "ainvoke", None)):
        raise TypeError("declared LangGraph object has no ainvoke method")
    compiled = _compiled_graph(graph)
    checkpointer = getattr(compiled, "checkpointer", None)
    if compiled is not None and _is_set(checkpointer):
        named = checkpointer if isinstance(checkpointer, bool) else type(checkpointer).__name__
        raise ValueError(
            f"declared LangGraph graph is compiled with a checkpointer ({named}); compile it "
            "without one: the kit sends the whole conversation every turn, so a checkpointer "
            "would keep each turn and the graph would see the history twice"
        )
    return graph


def _compiled_graph(graph: Any) -> Any:
    """The LangGraph compiled graph ``graph`` is, or wraps in a binding (``bind``,
    ``with_retry``); None for any other object, or when LangGraph is not installed.
    ``with_config`` on a compiled graph returns a compiled graph."""
    try:
        from langchain_core.runnables.base import RunnableBindingBase
        from langgraph.pregel import Pregel
    except ImportError:
        return None
    while isinstance(graph, RunnableBindingBase):
        graph = graph.bound
    return graph if isinstance(graph, Pregel) else None


def _is_set(checkpointer: Any) -> bool:
    return checkpointer is not None and checkpointer is not False


GRAPH = _load_graph()


def _response_text(state: Any) -> str:
    if isinstance(state, dict):
        report = state.get("final_report")
        if isinstance(report, str) and report.strip():
            return report
        messages = state.get("messages")
        if isinstance(messages, list) and messages:
            last = messages[-1]
            content = last.get("content") if isinstance(last, dict) else getattr(last, "content", None)
            if isinstance(content, str):
                return content
    raise ValueError("declared LangGraph graph returned no assistant content")


_WAIT_MARGIN_SECONDS = 5.0
_CLOSE_WAIT_SECONDS = 5.0
_LOOP_LOCK = threading.Lock()
_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_THREAD: threading.Thread | None = None
_CLOSED = False


class RequestTimeout(Exception):
    """The graph did not answer within the configured time."""


def _start_loop() -> None:
    # Called with the lock held.
    global _LOOP, _LOOP_THREAD
    if _CLOSED:
        raise RuntimeError("the LangGraph adapter is closed")
    if _LOOP_THREAD is not None and not _LOOP_THREAD.is_alive():
        # A node stopped the loop; clients built at import may be bound to it, so a new loop
        # would not help them.
        raise RuntimeError("the LangGraph adapter's event loop stopped; restart the agent")
    if _LOOP is None:
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, name="alk-langgraph-loop", daemon=True)
        thread.start()
        _LOOP, _LOOP_THREAD = loop, thread


def _thread_config(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The conversation as the graph's thread, or None: no thread named, an ``ainvoke`` that
    takes no config, or an object other than a LangGraph graph that has a checkpointer."""
    thread = payload.get("thread_id")
    if not isinstance(thread, str) or not thread.strip() or not _THREADED:
        return None
    return {"configurable": {"thread_id": thread}}


def _takes_config(graph: Any) -> bool:
    """Whether the graph's ``ainvoke`` accepts ``config``: by name, or through ``**kwargs``."""
    try:
        parameters = inspect.signature(graph.ainvoke).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "config" or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


_THREADED = _takes_config(GRAPH) and not _is_set(getattr(GRAPH, "checkpointer", None))


async def _run_graph(
    messages: list[dict[str, str]],
    timeout: float,
    abandoned: threading.Event,
    config: dict[str, Any] | None = None,
) -> Any:
    if abandoned.is_set():
        # Answered already, while the loop was busy: never start the graph.
        raise RequestTimeout(f"graph did not answer within {timeout:g}s")
    task = asyncio.current_task()
    expired = False

    def expire() -> None:
        nonlocal expired
        expired = True
        task.cancel()

    # The graph runs in this task, not a child one, so the deadline is told apart from the
    # graph's own TimeoutError on every Python version, and SystemExit can't stop the loop.
    deadline = asyncio.get_running_loop().call_later(timeout, expire)
    try:
        if config is None:
            return await GRAPH.ainvoke({"messages": messages})
        return await GRAPH.ainvoke({"messages": messages}, config=config)
    except asyncio.CancelledError:
        # Once the request is abandoned nobody reads this task's result, so it ends cancelled.
        if expired and not abandoned.is_set():
            raise RequestTimeout(f"graph did not answer within {timeout:g}s") from None
        raise
    except (SystemExit, KeyboardInterrupt) as exc:
        raise RuntimeError(f"the graph raised {type(exc).__name__}") from exc
    finally:
        deadline.cancel()


def _submit(
    messages: list[dict[str, str]],
    timeout: float,
    abandoned: threading.Event,
    config: dict[str, Any] | None = None,
):
    with _LOOP_LOCK:
        _start_loop()
        return asyncio.run_coroutine_threadsafe(
            _run_graph(messages, timeout, abandoned, config), _LOOP
        )


async def _shutdown() -> None:
    current = asyncio.current_task()
    tasks = {task for task in asyncio.all_tasks() if task is not current}
    for task in tasks:
        task.cancel()
    pending: set[asyncio.Task[Any]] = set()
    if tasks:
        _, pending = await asyncio.wait(tasks, timeout=_CLOSE_WAIT_SECONDS)
    loop = asyncio.get_running_loop()
    await loop.shutdown_asyncgens()
    if not pending:
        # A node that ignores the cancel may hold an executor thread; don't wait for it.
        await loop.shutdown_default_executor()


def close() -> None:
    """End the loop for good, cancelling any request in flight. For tests."""
    global _LOOP, _LOOP_THREAD, _CLOSED
    with _LOOP_LOCK:
        loop, thread = _LOOP, _LOOP_THREAD
        if thread is not None and thread is threading.current_thread():
            raise RuntimeError("close() can't run on the adapter's own event loop")
        _CLOSED = True
        _LOOP = _LOOP_THREAD = None
        if loop is None:
            return
        if thread is not None and thread.is_alive():
            try:
                asyncio.run_coroutine_threadsafe(_shutdown(), loop).result(
                    _CLOSE_WAIT_SECONDS + _WAIT_MARGIN_SECONDS
                )
            except Exception:  # noqa: BLE001 - a node that won't stop must not hold up close()
                pass
            loop.call_soon_threadsafe(loop.stop)
            thread.join(_CLOSE_WAIT_SECONDS)
        if not loop.is_running():
            loop.close()


def _invoke(payload: dict[str, Any]) -> dict[str, str]:
    raw_messages = payload.get("messages")
    raw_messages = raw_messages if isinstance(raw_messages, list) else []
    messages = [
        {"role": str(message.get("role") or "user"), "content": str(message.get("content") or "")}
        for message in raw_messages
        if isinstance(message, dict)
    ]
    new_message = payload.get("new_message")
    if isinstance(new_message, dict):
        current = {"role": "user", "content": str(new_message.get("content") or "")}
        if not messages or messages[-1] != current:
            messages.append(current)
    if not messages:
        raise ValueError("chat request contains no messages")
    timeout = float(os.environ.get("ALK_LANGGRAPH_TIMEOUT_SECONDS", "110"))
    abandoned = threading.Event()
    future = _submit(messages, timeout, abandoned, _thread_config(payload))
    try:
        # The margin lets the deadline fire first; this wait covers a node that ignores cancellation.
        state = future.result(timeout + _WAIT_MARGIN_SECONDS)
    except concurrent.futures.CancelledError as exc:
        if _CLOSED:
            raise RuntimeError("the LangGraph adapter closed while the graph ran") from exc
        raise RuntimeError("the graph was cancelled") from exc
    except concurrent.futures.TimeoutError:
        if not future.done():
            abandoned.set()
            future.cancel()
            raise RequestTimeout(f"graph did not answer within {timeout:g}s") from None
        # The graph finished, or raised a TimeoutError of its own, just as the wait ended.
        state = future.result()
    return {"content": _response_text(state)}


class Handler(BaseHTTPRequestHandler):
    def _respond(self, status: int, body: dict[str, str]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self._respond(404, {"error": "not_found"})
        elif _LOOP_THREAD is not None and not _LOOP_THREAD.is_alive():
            self._respond(503, {"status": "event_loop_stopped"})
        else:
            self._respond(200, {"status": "ok"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/invoke":
            self._respond(404, {"error": "not_found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(payload, dict):
                raise TypeError("chat request must be a JSON object")
            self._respond(200, _invoke(payload))
        except Exception as exc:  # noqa: BLE001 - source errors cross the transport boundary
            self._respond(500, {"error": f"{type(exc).__name__}: {exc}", "content": ""})

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()


if __name__ == "__main__":
    main()
