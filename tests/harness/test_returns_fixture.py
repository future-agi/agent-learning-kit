"""The internal returns fixture: a ledger that allows duplicate refunds, and the record defects of its negative controls.

openai-agents 0.24.0 (the fixture's pinned SDK) runs a sync `function_tool` in a worker thread
(`agents/tool.py`, `invoke_function_tool`: `await asyncio.to_thread(the_func, ...)`), so a waiting refund tool does
not stall the app's one event loop; the stand-in runner below does the same.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import sys
import threading
import types
import urllib.error
from pathlib import Path
from urllib.parse import urlparse

import pytest

APP = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "harness_agents"
    / "returns_multi_agent"
)


def _load(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Agent:
    def __init__(self, **fields):
        self.__dict__.update(fields)

    def as_tool(self, **_fields):
        return self


def _agent_module(monkeypatch, *, hang: str = "", runner=None) -> types.ModuleType:
    for key in (
        "RETURNS_HANG_SECONDS",
        "RETURNS_API_URL",
        "REFUND_API_URL",
        "TOOLS_API_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    if hang:
        monkeypatch.setenv("RETURNS_HANG_SECONDS", hang)
    stub = types.ModuleType("agents")
    stub.Agent = _Agent
    stub.Runner = runner or types.SimpleNamespace(run=None)
    stub.function_tool = lambda function: function
    stub.set_trace_processors = lambda _processors: None
    monkeypatch.setitem(sys.modules, "agents", stub)
    return _load("returns_fixture_agent", APP / "agent.py")


def _names(tools) -> list[str]:
    return [one.__name__ for one in tools]


def test_the_ledger_creates_a_second_refund() -> None:
    ledger = _load("returns_fixture_ledger", APP / "ledger.py")
    first = ledger._issue({"order_id": "1042", "amount": 120})
    second = ledger._issue({"order_id": "1042", "amount": 120})
    assert first[0] == second[0] == 201
    assert first[1]["refund_id"] != second[1]["refund_id"]
    status, listed = ledger._refunds({"order_id": "1042"})
    assert status == 200
    assert [one["refund_id"] for one in listed["refunds"]][0] == "RF-7"
    assert len(listed["refunds"]) == 3


def _served_by_a_ledger(monkeypatch, module, seen: list[str]) -> types.ModuleType:
    """Every ledger call the agent makes is answered by a fresh ledger.py, as over HTTP."""
    ledger = _load("returns_fixture_ledger_served", APP / "ledger.py")

    def _urlopen(request, timeout):
        seen.append(request.full_url)
        status, body = ledger.ROUTES[urlparse(request.full_url).path](json.loads(request.data))
        data = json.dumps(body).encode()
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "", {}, io.BytesIO(data))
        return _Answer(data)

    monkeypatch.setattr(module.urllib.request, "urlopen", _urlopen)
    return ledger


def test_each_defect_belongs_to_its_record_so_one_build_carries_every_control(monkeypatch) -> None:
    monkeypatch.setenv("RETURNS_API_URL", "http://shared")
    monkeypatch.setenv("REFUND_API_URL", "http://refund-channel")
    seen: list[str] = []
    module = _agent_module(monkeypatch, hang="0")
    monkeypatch.setenv("RETURNS_API_URL", "http://shared")
    monkeypatch.setenv("REFUND_API_URL", "http://refund-channel")
    ledger = _served_by_a_ledger(monkeypatch, module, seen)
    check, issue = module.REFUND.tools
    lookup_refunds = module.ORDER_LOOKUP.tools[1]

    # A record with no defect: the existing refund is reported and a new one commits on the worker's own channel.
    assert [one["refund_id"] for one in json.loads(check("1042"))["refunds"]] == ["RF-7"]
    check("1043")
    issue("1043", 89.0)
    assert len(ledger.REFUNDS["1043"]) == 1
    assert seen[-1] == "http://refund-channel/refunds/issue"

    # skip_refund_check: both workers' check reads nothing, so a second refund commits on the same record.
    assert json.loads(lookup_refunds("1047"))["refunds"] == []
    assert json.loads(check("1047"))["refunds"] == []
    issue("1047", 41.0)
    assert len(ledger.REFUNDS["1047"]) == 2

    # bypass_channel: the refund goes out on the shared address.
    check("1048")
    issue("1048", 75.0)
    assert seen[-1] == "http://shared/refunds/issue"

    # payment_times_out: the worker reports an error and nothing is committed.
    check("1045")
    assert "error" in json.loads(issue("1045", 35.0))
    assert "1045" not in ledger.REFUNDS

    # The model never sees a record's defect.
    for order in ("1043", "1045", "1047", "1048"):
        assert "fault" not in json.loads(module.ORDER_LOOKUP.tools[0](order))


def test_a_waiting_refund_tool_leaves_a_concurrent_reply_free(monkeypatch) -> None:
    """A refund on a `payment_times_out` record waits long, yet a second conversation still gets its reply, because
    the pinned SDK runs the sync tool in a thread; releasing `_HANG` lets the first one finish."""
    module_ref = {}

    class _Runner:
        @staticmethod
        async def run(_agent, conversation):
            if conversation[-1]["content"] == "refund":
                text = await asyncio.to_thread(
                    module_ref["m"].REFUND.tools[1], "1045", 35.0
                )
                return types.SimpleNamespace(final_output=text)
            return types.SimpleNamespace(final_output="looked up")

    module = _agent_module(monkeypatch, hang="60", runner=_Runner)
    module_ref["m"] = module
    # The ledger's address, as a build gives the app.
    monkeypatch.setenv("RETURNS_API_URL", "http://shared")
    _served_by_a_ledger(monkeypatch, module, [])
    answers = {}
    first = threading.Thread(
        target=lambda: answers.setdefault(
            "first", module.reply([{"role": "user", "content": "refund"}])
        )
    )
    first.start()
    try:
        assert module.reply([{"role": "user", "content": "lookup"}]) == "looked up"
        assert first.is_alive()
    finally:
        module._HANG.set()
        first.join(timeout=10)
    assert "error" in json.loads(answers["first"])


def test_every_tool_def_is_at_the_builder_s_top_level(monkeypatch) -> None:
    corpus = _load(
        "corpus_references",
        Path(__file__).with_name("test_multi_agent_compatibility_references.py"),
    )
    corpus.test_returns_reference_declares_the_workers_and_tools_its_app_builds()


class _Answer:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def read(self) -> bytes:
        return self._body


@pytest.mark.parametrize("order", ["1045", "1046"])
def test_only_a_turn_about_a_waiting_record_waits(monkeypatch, order: str) -> None:
    """The wait is the record's defect, never a header the agent reads: a turn about another order answers at once."""
    module_ref = {}
    asked = {}

    class _Runner:
        @staticmethod
        async def run(_agent, conversation):
            # As the pinned SDK runs a sync tool: in a thread that copies the turn's context.
            text = await asyncio.to_thread(module_ref["m"].REFUND.tools[1], asked[conversation[-1]["content"]], 30.0)
            return types.SimpleNamespace(final_output=text)

    module = _agent_module(monkeypatch, hang="60", runner=_Runner)
    module_ref["m"] = module
    # The ledger's address, as a build gives the app.
    monkeypatch.setenv("RETURNS_API_URL", "http://shared")
    _served_by_a_ledger(monkeypatch, module, [])
    answers = {}

    def _turn(name: str, order_id: str) -> threading.Thread:
        asked[name] = order_id
        thread = threading.Thread(
            target=lambda: answers.setdefault(name, module.reply([{"role": "user", "content": name}]))
        )
        thread.start()
        return thread

    other = _turn("other", "1044")
    other.join(timeout=10)
    assert not other.is_alive()
    waiting = _turn("waiting", order)
    try:
        waiting.join(timeout=0.5)
        assert waiting.is_alive()
    finally:
        module._HANG.set()
        waiting.join(timeout=10)
    assert "error" in json.loads(answers["waiting"])


def test_voice_entry_routes_to_the_same_coordinator(monkeypatch) -> None:
    """Each spoken turn is answered by agent.py's `reply`, the one that runs `COORDINATOR`: no second topology."""
    desk = _agent_module(monkeypatch)
    monkeypatch.setitem(sys.modules, "agent", desk)
    livekit = types.ModuleType("livekit")
    agents = types.ModuleType("livekit.agents")
    plugins = types.ModuleType("livekit.plugins")

    class _VoiceAgent:
        def __init__(self, **_fields):
            pass

    class _Server:
        def rtc_session(self, **_fields):
            return lambda function: function

    agents.Agent = _VoiceAgent
    agents.AgentServer = _Server
    agents.AgentSession = object
    agents.JobContext = object
    agents.cli = types.SimpleNamespace(run_app=lambda _server: None)
    for name in ("deepgram", "openai", "silero"):
        setattr(plugins, name, types.SimpleNamespace())
    for name, module in (("livekit", livekit), ("livekit.agents", agents), ("livekit.plugins", plugins)):
        monkeypatch.setitem(sys.modules, name, module)
    voice = _load("returns_fixture_voice", APP / "voice_agent.py")
    assert voice.returns_desk is desk

    asked = []

    def _reply(messages):
        asked.append(messages)
        return "Refund RF-9 issued."

    monkeypatch.setattr(desk, "reply", _reply)
    said = types.SimpleNamespace
    chat = said(
        items=[
            said(type="message", role="system", text_content="be brief"),
            said(type="message", role="user", text_content="Refund order 1043"),
            said(type="function_call", role=None, text_content=""),
            said(type="message", role="assistant", text_content="Checking."),
            said(type="message", role="user", text_content="Thanks"),
        ]
    )
    answer = asyncio.run(voice.ReturnsVoiceAgent().llm_node(chat, [], None))
    assert answer == "Refund RF-9 issued."
    assert asked == [
        [
            {"role": "user", "content": "Refund order 1043"},
            {"role": "assistant", "content": "Checking."},
            {"role": "user", "content": "Thanks"},
        ]
    ]


def test_a_refund_worker_served_on_its_own_is_reached_over_http_and_a_gone_one_is_an_answer(monkeypatch) -> None:
    monkeypatch.setenv("REFUND_WORKER_URL", "http://refund-worker:8081")
    module = _agent_module(monkeypatch)
    lookup, refund = module.COORDINATOR.tools
    # The order-lookup worker stays in this process; the refund worker is a plain tool that calls its service.
    assert lookup is module.ORDER_LOOKUP
    assert refund is not module.REFUND and refund.__name__ == "refund"
    sent = []

    def _urlopen(request, timeout):
        sent.append((request.full_url, json.loads(request.data)))
        return _Answer(b'{"output": "Refund RF-9 issued."}')

    monkeypatch.setattr(module.urllib.request, "urlopen", _urlopen)
    assert refund("order 1043, 89.00") == "Refund RF-9 issued."
    assert sent == [("http://refund-worker:8081/run", {"input": "order 1043, 89.00"})]

    def _gone(_request, timeout):
        raise module.urllib.error.URLError("connection refused")

    monkeypatch.setattr(module.urllib.request, "urlopen", _gone)
    assert json.loads(refund("order 1043")) == {"error": "refund worker unavailable: URLError"}


def test_the_refund_worker_is_in_the_coordinator_s_process_unless_served_on_its_own(monkeypatch) -> None:
    module = _agent_module(monkeypatch)
    assert module.COORDINATOR.tools == [module.ORDER_LOOKUP, module.REFUND]
