from __future__ import annotations

import gc
import importlib
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


def test_declared_graph_receives_chat_and_returns_report(
    tmp_path: Path, monkeypatch,
) -> None:
    source = tmp_path / "source"
    package = source / "src" / "fixture_graph"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "graph.py").write_text(
        "class Graph:\n"
        "    async def ainvoke(self, state):\n"
        "        assert state['messages'][-1]['content'] == 'Research this'\n"
        "        return {'final_report': 'Finished report'}\n"
        "graph = Graph()\n"
    )
    monkeypatch.chdir(source)
    monkeypatch.setenv("ALK_LANGGRAPH_ENTRYPOINT", "src/fixture_graph/graph.py:graph")
    sys.modules.pop("fi.alk.harness.langgraph_http_adapter", None)
    adapter = importlib.import_module("fi.alk.harness.langgraph_http_adapter")

    try:
        assert adapter._invoke({"new_message": {"content": "Research this"}}) == {
            "content": "Finished report"
        }
    finally:
        adapter.close()
        sys.modules.pop("fi.alk.harness.langgraph_http_adapter", None)
        sys.modules.pop("fixture_graph.graph", None)
        sys.modules.pop("fixture_graph", None)
        sys.path.remove(str(source / "src"))


def _load_adapter(tmp_path: Path, monkeypatch, graph_source: str, name: str):
    source = tmp_path / "source"
    package = source / "src" / name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "graph.py").write_text(graph_source)
    monkeypatch.chdir(source)
    monkeypatch.setenv("ALK_LANGGRAPH_ENTRYPOINT", f"src/{name}/graph.py:graph")
    sys.modules.pop("fi.alk.harness.langgraph_http_adapter", None)
    adapter = importlib.import_module("fi.alk.harness.langgraph_http_adapter")
    return adapter, source, name


def _unload(adapter, source: Path, name: str) -> None:
    adapter.close()
    sys.modules.pop("fi.alk.harness.langgraph_http_adapter", None)
    sys.modules.pop(f"{name}.graph", None)
    sys.modules.pop(name, None)
    if str(source / "src") in sys.path:
        sys.path.remove(str(source / "src"))


@pytest.fixture
def load(tmp_path: Path, monkeypatch):
    loaded = []

    def _load(graph_source: str, name: str = "loop_graph"):
        item = _load_adapter(tmp_path, monkeypatch, graph_source, name)
        loaded.append(item)
        return item[0]

    yield _load
    for item in loaded:
        _unload(*item)


def _turn(adapter, text: str = "hi") -> str:
    return adapter._invoke({"new_message": {"content": text}})["content"]


BOUND_CLIENT_GRAPH = """
import asyncio


class Client:
    # Like an SDK client built at import: it keeps the first loop it ran on.
    def __init__(self):
        self.loop = None

    async def call(self):
        if self.loop is None:
            self.loop = asyncio.get_running_loop()
        done = asyncio.get_running_loop().create_future()
        self.loop.call_soon(done.set_result, "ok")
        return await done


client = Client()


class Graph:
    async def ainvoke(self, state):
        return {"final_report": await client.call()}


graph = Graph()
"""


def test_client_bound_to_its_first_loop_survives_later_turns(load) -> None:
    adapter = load(BOUND_CLIENT_GRAPH)

    assert [_turn(adapter) for _ in range(3)] == ["ok", "ok", "ok"]


def test_httpx_client_built_at_import_answers_two_turns(load) -> None:
    class Echo(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # keep-alive, so the pooled connection outlives the first turn

        def do_GET(self) -> None:  # noqa: N802
            body = b"pong"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Echo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        adapter = load(
            "import httpx\n"
            f"client = httpx.AsyncClient(base_url='http://127.0.0.1:{server.server_address[1]}')\n"
            "class Graph:\n"
            "    async def ainvoke(self, state):\n"
            "        return {'final_report': (await client.get('/')).text}\n"
            "graph = Graph()\n",
            "httpx_graph",
        )

        assert [_turn(adapter), _turn(adapter)] == ["pong", "pong"]
    finally:
        server.shutdown()
        server.server_close()


def test_two_requests_in_flight_overlap(load) -> None:
    adapter = load(
        """
import asyncio
import threading

arrived = []
both = threading.Event()


class Graph:
    async def ainvoke(self, state):
        arrived.append(1)
        if len(arrived) == 2:
            both.set()
        for _ in range(500):
            if both.is_set():
                return {"final_report": "met"}
            await asyncio.sleep(0.01)
        return {"final_report": "alone"}


graph = Graph()
""",
        "overlap_graph",
    )
    results: list[str] = []
    threads = [threading.Thread(target=lambda: results.append(_turn(adapter))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert results == ["met", "met"]


SLOW_GRAPH = """
import asyncio
import threading

release = threading.Event()


class Graph:
    async def ainvoke(self, state):
        while not release.is_set():
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                if MODE == "swallow":
                    continue
                raise
        return {"final_report": "late"}


MODE = "%s"
graph = Graph()
"""


def _post(url: str) -> tuple[int, dict]:
    request = urllib.request.Request(
        url, data=json.dumps({"new_message": {"content": "hi"}}).encode(), method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _serve(adapter):
    server = ThreadingHTTPServer(("127.0.0.1", 0), adapter.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/invoke"


def test_inner_timeout_answers_500(load, monkeypatch) -> None:
    monkeypatch.setenv("ALK_LANGGRAPH_TIMEOUT_SECONDS", "0.2")
    adapter = load(SLOW_GRAPH % "plain", "slow_graph")
    server, url = _serve(adapter)
    try:
        status, body = _post(url)
    finally:
        server.shutdown()
        server.server_close()

    assert status == 500
    assert "RequestTimeout" in body["error"]
    assert body["content"] == ""


def test_node_that_ignores_cancellation_still_answers_500(load, monkeypatch) -> None:
    monkeypatch.setenv("ALK_LANGGRAPH_TIMEOUT_SECONDS", "0.2")
    adapter = load(SLOW_GRAPH % "swallow", "stubborn_graph")
    monkeypatch.setattr(adapter, "_WAIT_MARGIN_SECONDS", 0.3)
    server, url = _serve(adapter)
    try:
        status, body = _post(url)
    finally:
        sys.modules["stubborn_graph.graph"].release.set()
        server.shutdown()
        server.server_close()

    assert status == 500
    assert "RequestTimeout" in body["error"]


def test_close_stops_and_joins_the_loop_thread_for_good(load) -> None:
    adapter = load(
        "class Graph:\n"
        "    async def ainvoke(self, state):\n"
        "        return {'final_report': 'ok'}\n"
        "graph = Graph()\n",
        "close_graph",
    )
    _turn(adapter)
    thread = adapter._LOOP_THREAD
    assert thread is not None and thread.is_alive()

    adapter.close()

    assert not thread.is_alive()
    assert adapter._LOOP is None
    adapter.close()
    # Clients built at import may be bound to the closed loop, so no new one is started.
    with pytest.raises(RuntimeError, match="closed"):
        _turn(adapter)


def test_a_graph_that_raises_its_own_timeout_error_answers_500_with_its_message(
    load, monkeypatch
) -> None:
    adapter = load(
        "class Graph:\n"
        "    async def ainvoke(self, state):\n"
        "        raise TimeoutError('upstream tool timed out')\n"
        "graph = Graph()\n",
        "own_timeout_graph",
    )
    server, url = _serve(adapter)
    try:
        status, body = _post(url)
    finally:
        server.shutdown()
        server.server_close()

    assert status == 500
    assert "upstream tool timed out" in body["error"]
    assert "RequestTimeout" not in body["error"]


def test_the_deadline_fires_without_waiting_for_the_margin(load, monkeypatch) -> None:
    monkeypatch.setenv("ALK_LANGGRAPH_TIMEOUT_SECONDS", "0.2")
    adapter = load(SLOW_GRAPH % "plain", "deadline_graph")
    monkeypatch.setattr(adapter, "_WAIT_MARGIN_SECONDS", 30.0)
    server, url = _serve(adapter)
    started = time.monotonic()
    try:
        status, body = _post(url)
    finally:
        server.shutdown()
        server.server_close()

    assert status == 500
    assert "RequestTimeout" in body["error"]
    assert time.monotonic() - started < 5


SECOND_CANCEL_GRAPH = """
import asyncio

cancels = []


class Graph:
    async def ainvoke(self, state):
        while True:
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                cancels.append(1)
                if len(cancels) == 2:
                    raise


graph = Graph()
"""


def test_a_graph_that_outlives_its_deadline_is_cancelled_again(load, monkeypatch, caplog) -> None:
    monkeypatch.setenv("ALK_LANGGRAPH_TIMEOUT_SECONDS", "0.2")
    adapter = load(SECOND_CANCEL_GRAPH, "second_cancel_graph")
    monkeypatch.setattr(adapter, "_WAIT_MARGIN_SECONDS", 0.3)
    graph_module = sys.modules["second_cancel_graph.graph"]

    with pytest.raises(adapter.RequestTimeout):
        _turn(adapter)
    deadline = time.monotonic() + 5
    while len(graph_module.cancels) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)

    # The first cancel is the deadline's, which the node swallows; the second is the
    # abandoned request's.
    assert graph_module.cancels == [1, 1]
    # Nobody is waiting for that request any more, so its end must not be logged as an
    # exception never retrieved.
    time.sleep(0.2)
    gc.collect()
    assert "never retrieved" not in caplog.text


ABANDON_GRAPH = """
import threading

runs = []
unblock = threading.Event()


class Graph:
    async def ainvoke(self, state):
        text = state["messages"][-1]["content"]
        runs.append(text)
        if text == "block":
            unblock.wait(10)
        return {"final_report": text}


graph = Graph()
"""


def test_a_request_answered_with_a_timeout_never_starts_its_graph(load, monkeypatch) -> None:
    adapter = load(ABANDON_GRAPH, "abandon_graph")
    graph_module = sys.modules["abandon_graph.graph"]
    blocker = threading.Thread(target=lambda: _turn(adapter, "block"))
    blocker.start()
    while graph_module.runs != ["block"]:
        time.sleep(0.01)
    monkeypatch.setenv("ALK_LANGGRAPH_TIMEOUT_SECONDS", "0.1")
    monkeypatch.setattr(adapter, "_WAIT_MARGIN_SECONDS", 0.1)

    with pytest.raises(adapter.RequestTimeout):
        _turn(adapter, "late")
    graph_module.unblock.set()
    blocker.join(timeout=10)
    time.sleep(0.2)

    assert graph_module.runs == ["block"]


def test_close_answers_a_request_in_flight_at_once(load, monkeypatch) -> None:
    monkeypatch.setenv("ALK_LANGGRAPH_TIMEOUT_SECONDS", "30")
    adapter = load(SLOW_GRAPH % "plain", "close_in_flight_graph")
    outcome: list[BaseException] = []

    def ask() -> None:
        try:
            _turn(adapter)
        except BaseException as exc:  # noqa: BLE001 - the test inspects it
            outcome.append(exc)

    asker = threading.Thread(target=ask)
    asker.start()
    while adapter._LOOP is None:
        time.sleep(0.01)
    time.sleep(0.2)
    started = time.monotonic()
    adapter.close()
    asker.join(timeout=10)

    assert not asker.is_alive()
    assert time.monotonic() - started < 5
    (error,) = outcome
    assert not isinstance(error, adapter.RequestTimeout)
    assert "closed" in str(error)


def test_close_from_two_threads_returns_only_once_the_loop_has_stopped(load) -> None:
    adapter = load(
        "class Graph:\n"
        "    async def ainvoke(self, state):\n"
        "        return {'final_report': 'ok'}\n"
        "graph = Graph()\n",
        "double_close_graph",
    )
    _turn(adapter)
    thread = adapter._LOOP_THREAD
    stopped: list[bool] = []

    def close_it() -> None:
        adapter.close()
        stopped.append(not thread.is_alive())

    closers = [threading.Thread(target=close_it) for _ in range(2)]
    for closer in closers:
        closer.start()
    for closer in closers:
        closer.join(timeout=10)

    assert stopped == [True, True]


def test_a_graph_that_raises_system_exit_answers_500_and_the_loop_lives_on(
    load, monkeypatch
) -> None:
    monkeypatch.setenv("ALK_LANGGRAPH_TIMEOUT_SECONDS", "2")
    adapter = load(
        "class Graph:\n"
        "    async def ainvoke(self, state):\n"
        "        if state['messages'][-1]['content'] == 'exit':\n"
        "            raise SystemExit(3)\n"
        "        return {'final_report': 'ok'}\n"
        "graph = Graph()\n",
        "exit_graph",
    )
    started = time.monotonic()

    with pytest.raises(RuntimeError, match="SystemExit"):
        _turn(adapter, "exit")

    assert time.monotonic() - started < 2
    assert _turn(adapter) == "ok"


def _runner_code_for(tmp_path: Path, monkeypatch, status: int) -> str:
    """The failure code the hosted chat runner gives an HTTP failure with `status`."""
    import asyncio
    from types import SimpleNamespace

    from test_chat_call_runner import _Adapter, _context, _ToolWorld

    from fi.alk.harness import chat_call_runner as chat
    from fi.alk.harness.hosted_scheduler import CallAborted
    from fi.alk.harness.process_runtime import (
        EnvironmentRuntime,
        RuntimeEndpoint,
        RuntimeState,
    )
    from fi.simulate.agent.wrappers.http import HTTPCallFailure

    async def failed(*_args, **_kwargs):
        raise HTTPCallFailure({"success": False, "status_code": status, "failure_stage": "http"})

    monkeypatch.setattr(chat, "_tool_world", lambda *_args: _ToolWorld())
    monkeypatch.setattr(chat, "_drive_conversation", failed)
    runtime = EnvironmentRuntime(
        runtime_id="runtime",
        world_index=0,
        bundle_digest="sha256:" + "a" * 64,
        state=RuntimeState.READY,
        endpoints={
            "target_http": RuntimeEndpoint(
                capability="target_http", protocol="http", address="http://localhost:18080"
            )
        },
    )
    with pytest.raises(CallAborted) as caught:
        asyncio.run(
            chat.HostedChatCallRunner(_Adapter(), _context(tmp_path)).run(
                SimpleNamespace(scenario_key="one", scenario_id="scenario-1"), runtime
            )
        )
    return caught.value.code


@pytest.mark.parametrize("graph", ["slow", "own_timeout"])
def test_a_slow_graph_is_the_agents_failure_and_is_not_retried(
    load, monkeypatch, tmp_path: Path, graph: str
) -> None:
    from fi.alk.harness import hosted_scheduler
    from fi.alk.harness.hosted_scheduler import FailureDomain

    monkeypatch.setenv("ALK_LANGGRAPH_TIMEOUT_SECONDS", "0.2")
    source = (
        SLOW_GRAPH % "plain"
        if graph == "slow"
        else "class Graph:\n"
        "    async def ainvoke(self, state):\n"
        "        raise TimeoutError('upstream tool timed out')\n"
        "graph = Graph()\n"
    )
    adapter = load(source, f"classified_{graph}_graph")
    server, url = _serve(adapter)
    try:
        status, _ = _post(url)
    finally:
        server.shutdown()
        server.server_close()

    # Followed through the runner's status mapping to the scheduler's class: a 504 would be
    # `call_failed`, an infrastructure fault retried once on a fresh world.
    code = _runner_code_for(tmp_path / "runner", monkeypatch, status)
    assert code == "target_agent_failed"
    assert hosted_scheduler._domain_for(code) is FailureDomain.AGENT
    assert not hosted_scheduler._is_retryable(code)


def test_a_graph_that_cancels_itself_is_not_reported_as_a_close(load) -> None:
    adapter = load(
        "import asyncio\n"
        "class Graph:\n"
        "    async def ainvoke(self, state):\n"
        "        asyncio.current_task().cancel()\n"
        "        await asyncio.sleep(1)\n"
        "graph = Graph()\n",
        "self_cancel_graph",
    )

    with pytest.raises(RuntimeError, match="cancelled") as raised:
        _turn(adapter)

    assert "closed" not in str(raised.value)


def test_a_loop_stopped_by_the_graph_is_refused_and_health_says_so(load, monkeypatch) -> None:
    monkeypatch.setenv("ALK_LANGGRAPH_TIMEOUT_SECONDS", "0.3")
    adapter = load(
        "import asyncio, sys\n"
        "class Graph:\n"
        "    async def ainvoke(self, state):\n"
        "        asyncio.get_running_loop().call_soon(sys.exit, 3)\n"
        "        await asyncio.sleep(5)\n"
        "graph = Graph()\n",
        "stopping_graph",
    )
    monkeypatch.setattr(adapter, "_WAIT_MARGIN_SECONDS", 0.3)
    with pytest.raises(adapter.RequestTimeout):
        _turn(adapter)
    adapter._LOOP_THREAD.join(timeout=5)
    server = ThreadingHTTPServer(("127.0.0.1", 0), adapter.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        health = f"http://127.0.0.1:{server.server_address[1]}/health"
        with pytest.raises(urllib.error.HTTPError) as unhealthy:
            urllib.request.urlopen(health, timeout=10)
    finally:
        server.shutdown()
        server.server_close()

    # Clients built at import may be bound to the stopped loop, so no new one is started.
    with pytest.raises(RuntimeError, match="stopped"):
        _turn(adapter)
    assert unhealthy.value.code == 503


def test_close_refuses_to_run_on_the_adapters_own_loop(load) -> None:
    adapter = load(
        "import sys\n"
        "class Graph:\n"
        "    async def ainvoke(self, state):\n"
        "        sys.modules['fi.alk.harness.langgraph_http_adapter'].close()\n"
        "graph = Graph()\n",
        "closing_graph",
    )

    with pytest.raises(RuntimeError, match="own event loop"):
        _turn(adapter)
