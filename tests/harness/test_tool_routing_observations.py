from __future__ import annotations

import json
from types import SimpleNamespace

from fi.alk.harness.chat_readiness import TOOL_ROUTING, record_tool_routing


def _call(name, *, ok=True, refused=False):
    return SimpleNamespace(name=name, ok=ok, refused=refused)


def test_each_tool_that_reached_the_world_is_recorded_once_with_its_outcomes(tmp_path):
    record_tool_routing(
        tmp_path,
        "refund-1",
        [
            _call("lookup_order"),
            _call("issue_refund", ok=False),
            _call("lookup_order"),
            _call("cancel", refused=True),
        ],
    )

    body = json.loads((tmp_path / "generic-harness" / TOOL_ROUTING).read_text())

    assert body["scenario_key"] == "refund-1"
    assert body["tools"] == [
        {"tool": "cancel", "routed": True, "calls": 1, "outcomes": ["refused"]},
        {"tool": "issue_refund", "routed": True, "calls": 1, "outcomes": ["error"]},
        {"tool": "lookup_order", "routed": True, "calls": 2, "outcomes": ["ok"]},
    ]


def test_no_calls_records_no_tool(tmp_path):
    record_tool_routing(tmp_path, "chat-1", [_call("")])

    assert (
        json.loads((tmp_path / "generic-harness" / TOOL_ROUTING).read_text())["tools"]
        == []
    )


import asyncio

from fi.alk.harness import call_runner, chat_readiness
from fi.alk.harness.contract import AgentContract


def _contract():
    return AgentContract.model_validate(
        {
            "agent": "shop",
            "tools": [
                {
                    "name": "lookup_order",
                    "args": ["order_id"],
                    "arg_values": {"order_id": ["A-1"]},
                },
                {"name": "track_parcel", "args": ["parcel_id"]},
                {
                    "name": "issue_refund",
                    "args": ["order_id"],
                    "effects": "creates a refund row",
                },
                {"name": "get_hours"},
            ],
        }
    )


def _requests(tmp_path, monkeypatch, *, answers, fails=(), elsewhere=()):
    trace: list = []
    sent: list = []

    class Wrapper:
        async def call(self, agent_input):
            text = agent_input.new_message["content"]
            sent.append(text)
            for tool in answers:
                if f"your {tool} tool" in text:
                    trace.append(_call(tool))
            if any(f"your {tool} tool" in text for tool in fails):
                raise RuntimeError("agent down")
            said = [tool for tool in elsewhere if f"your {tool} tool" in text]
            return SimpleNamespace(
                tool_calls=[
                    {"type": "function", "function": {"name": one}} for one in said
                ]
            )

    monkeypatch.setattr(
        chat_readiness, "create_chat_wrapper", lambda *a, **k: Wrapper()
    )
    monkeypatch.setattr(
        call_runner, "_collect_file_tool_calls", lambda runtime: tuple(trace)
    )
    record_tool_routing(tmp_path, "s-1", [_call("lookup_order")])
    asyncio.run(
        chat_readiness.request_uncovered_tools(
            interface=None,
            endpoint="http://agent",
            runtime=None,
            contract=_contract(),
            authoring=tmp_path,
            secret_values={},
        )
    )
    return json.loads((tmp_path / "generic-harness" / TOOL_ROUTING).read_text()), sent


def test_each_uncalled_read_only_tool_is_asked_once_and_counted_when_the_world_sees_it(
    tmp_path, monkeypatch
):
    body, sent = _requests(tmp_path, monkeypatch, answers=("track_parcel",))

    assert [one["tool"] for one in body["tools"]] == ["lookup_order", "track_parcel"]
    assert body["tools"][1]["source"] == "tool_request"
    assert body["unobserved"] == [
        {"tool": "get_hours", "reason": "not_called"},
        {"tool": "issue_refund", "reason": "changes_state"},
    ]
    assert len(sent) == 2
    assert not any("issue_refund" in text for text in sent)
    assert not any("lookup_order" in text for text in sent)


def test_a_request_the_agent_cannot_answer_leaves_the_tool_unobserved(
    tmp_path, monkeypatch
):
    body, _ = _requests(tmp_path, monkeypatch, answers=(), fails=("track_parcel",))

    assert {"tool": "track_parcel", "reason": "request_failed"} in body["unobserved"]


def test_the_request_names_the_tool_and_an_example_from_its_contract():
    text = chat_readiness._tool_request(_contract().tools[0])

    assert "lookup_order" in text and "order_id='A-1'" in text and "read-only" in text


def test_a_call_the_agent_reports_but_the_world_never_saw_went_elsewhere(
    tmp_path, monkeypatch
):
    body, _ = _requests(tmp_path, monkeypatch, answers=(), elsewhere=("track_parcel",))

    entry = next(one for one in body["tools"] if one["tool"] == "track_parcel")
    assert (entry["routed"], entry["reason"]) == (False, "reported_not_seen")
    assert {"tool": "get_hours", "reason": "not_called"} in body["unobserved"]
