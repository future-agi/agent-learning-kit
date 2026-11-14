"""The internal returns fixture: a ledger that allows duplicate refunds, and the fault switches of its negative controls.

openai-agents 0.24.0 (the fixture's pinned SDK) runs a sync `function_tool` in a worker thread
(`agents/tool.py`, `invoke_function_tool`: `await asyncio.to_thread(the_func, ...)`), so a waiting refund tool does
not stall the app's one event loop; the stand-in runner below does the same.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import threading
import types
from pathlib import Path

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


def _agent_module(
    monkeypatch, *, fault: str = "", hang: str = "", runner=None
) -> types.ModuleType:
    for key in (
        "RETURNS_FAULT",
        "RETURNS_HANG_SECONDS",
        "RETURNS_API_URL",
        "REFUND_API_URL",
        "TOOLS_API_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    if fault:
        monkeypatch.setenv("RETURNS_FAULT", fault)
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


def test_fault_switches_are_off_by_default_and_change_only_their_part(
    monkeypatch,
) -> None:
    plain = _agent_module(monkeypatch)
    assert _names(plain.REFUND.tools) == ["list_refunds", "issue_refund"]
    assert "with no refund yet" in plain.COORDINATOR.instructions
    assert "reused" not in plain.COORDINATOR.instructions
    assert "always ask the refund worker" not in plain.COORDINATOR.instructions
    issue = plain.REFUND.tools[1]
    assert "never created" not in issue.__doc__

    careless = _agent_module(monkeypatch, fault="skip_refund_check")
    assert _names(careless.REFUND.tools) == ["issue_refund"]
    assert "with no refund yet" not in careless.COORDINATOR.instructions
    assert (
        "always ask the refund worker to refund the full amount"
        in careless.COORDINATOR.instructions
    )

    seen = []
    monkeypatch.setenv("RETURNS_API_URL", "http://shared")
    monkeypatch.setenv("REFUND_API_URL", "http://refund-channel")
    bypass = _agent_module(monkeypatch, fault="bypass_channel")
    monkeypatch.setenv("RETURNS_API_URL", "http://shared")
    monkeypatch.setenv("REFUND_API_URL", "http://refund-channel")

    def _urlopen(request, timeout):
        seen.append(request.full_url)
        raise OSError("not reached")

    monkeypatch.setattr(bypass.urllib.request, "urlopen", _urlopen)
    with pytest.raises(OSError):
        bypass.REFUND.tools[1]("1043", 89.0)
    assert seen == ["http://shared/refunds/issue"]

    timed_out = _agent_module(monkeypatch, fault="payment_times_out", hang="0")
    monkeypatch.setattr(
        timed_out.urllib.request,
        "urlopen",
        lambda *_a, **_k: pytest.fail("the ledger must not be called"),
    )
    assert json.loads(timed_out.REFUND.tools[1]("1043", 89.0)) == {
        "error": "payment service timed out"
    }


def test_a_waiting_refund_tool_leaves_a_concurrent_reply_free(monkeypatch) -> None:
    """With `payment_times_out` and a long wait, a second conversation still gets its reply while the first waits,
    because the pinned SDK runs the sync tool in a thread; releasing `_HANG` lets the first one finish."""
    module_ref = {}

    class _Runner:
        @staticmethod
        async def run(_agent, conversation):
            if conversation[-1]["content"] == "refund":
                text = await asyncio.to_thread(
                    module_ref["m"].REFUND.tools[1], "1043", 89.0
                )
                return types.SimpleNamespace(final_output=text)
            return types.SimpleNamespace(final_output="looked up")

    module = _agent_module(
        monkeypatch, fault="payment_times_out", hang="60", runner=_Runner
    )
    module_ref["m"] = module
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
    assert json.loads(answers["first"]) == {"error": "payment service timed out"}


def test_every_tool_def_is_at_the_builder_s_top_level(monkeypatch) -> None:
    corpus = _load(
        "corpus_references",
        Path(__file__).with_name("test_multi_agent_compatibility_references.py"),
    )
    monkeypatch.setenv("RETURNS_FAULT", "skip_refund_check")
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


@pytest.mark.parametrize("fault", ["refund_never_returns", "payment_times_out"])
def test_hang_fault_skips_certify_probes(monkeypatch, fault: str) -> None:
    """A probe turn (no scenario-call header) reaches the ledger at once; a scenario call turn waits and fails."""
    module_ref = {}

    class _Runner:
        @staticmethod
        async def run(_agent, _conversation):
            # As the pinned SDK runs a sync tool: in a thread that copies the turn's context.
            text = await asyncio.to_thread(module_ref["m"].REFUND.tools[1], "1043", 89.0)
            return types.SimpleNamespace(final_output=text)

    module = _agent_module(monkeypatch, fault=fault, hang="0", runner=_Runner)
    module_ref["m"] = module
    monkeypatch.setenv("REFUND_API_URL", "http://refund-channel")
    monkeypatch.setattr(
        module.urllib.request, "urlopen", lambda *_a, **_k: _Answer(b'{"refund_id": "RF-9"}')
    )
    probe = module.reply([{"role": "user", "content": "Hello"}], scenario_call=False)
    assert json.loads(probe) == {"refund_id": "RF-9"}
    scenario = module.reply([{"role": "user", "content": "refund"}], scenario_call=True)
    assert "error" in json.loads(scenario)


def test_the_scenario_call_header_reaches_the_agent_s_request_headers() -> None:
    from fi.alk.harness.chat_call_runner import SCENARIO_CALL_HEADER

    agent = (APP / "agent.py").read_text(encoding="utf-8")
    # The fixture reads the same header the harness sends on scenario calls.
    assert f'SCENARIO_CALL_HEADER = "{SCENARIO_CALL_HEADER}"' in agent
