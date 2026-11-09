from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from fi.alk.harness import call_runner, chat_call_runner, chat_readiness
from fi.alk.harness.chat_readiness import TOOL_ROUTING, record_tool_routing
from fi.alk.harness.contract import AgentContract
from fi.alk.harness.world.runtime import Call


def _call(name, *, ok=True, refused=False, at=10.0):
    return Call(name=name, arguments={}, ok=ok, refused=refused, at=at)


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
                {"name": "send_sms", "args": ["to"]},
                {"name": "get_hours"},
            ],
            "tool_entrypoints": [
                {
                    "tool": "lookup_order",
                    "mode": "service",
                    "service": "tools-api",
                    "endpoint": "/orders/lookup",
                },
                {"tool": "track_parcel", "mode": "service", "service": "tools-api"},
                {"tool": "issue_refund", "mode": "service", "service": "tools-api"},
                {"tool": "get_hours", "mode": "import", "module": "m", "callable": "f"},
            ],
            "dependencies": [
                {
                    "kind": "service",
                    "name": "sms-gateway",
                    "what": "a hosted SMS API",
                    "used_by": ["send_sms"],
                }
            ],
        }
    )


PROXIED = SimpleNamespace(endpoints={"tool_proxy": object()})
NO_PROXY = SimpleNamespace(endpoints={})


def _body(tmp_path):
    return json.loads((tmp_path / "generic-harness" / TOOL_ROUTING).read_text())


def test_a_tool_the_world_received_is_routed_once_with_its_outcomes(tmp_path):
    record_tool_routing(
        tmp_path,
        "refund-1",
        _contract(),
        PROXIED,
        # The proxy logs a call by the last segment of its path.
        received=[_call("lookup"), _call("issue_refund", ok=False), _call("lookup")],
        reported=[_call("lookup_order"), _call("issue_refund")],
    )

    body = _body(tmp_path)

    assert body["scenario_key"] == "refund-1"
    assert body["tools"] == [
        {"tool": "issue_refund", "routed": True, "calls": 1, "outcomes": ["error"]},
        {"tool": "lookup_order", "routed": True, "calls": 2, "outcomes": ["ok"]},
    ]
    assert body["unobserved"] == []


def test_a_tool_the_agent_reports_but_the_world_never_received_is_not_routed(tmp_path):
    record_tool_routing(
        tmp_path,
        "refund-1",
        _contract(),
        PROXIED,
        received=[_call("lookup")],
        reported=[_call("lookup_order"), _call("track_parcel")],
    )

    assert _body(tmp_path)["tools"] == [
        {"tool": "lookup_order", "routed": True, "calls": 1, "outcomes": ["ok"]},
        {"tool": "track_parcel", "routed": False, "reason": "not_received"},
    ]


def test_a_tool_the_agent_runs_itself_is_never_counted_routed(tmp_path):
    # In-process tools, and service tools with no proxy in front: no world record exists.
    record_tool_routing(
        tmp_path,
        "refund-1",
        _contract(),
        NO_PROXY,
        received=[],
        reported=[_call("get_hours"), _call("track_parcel")],
    )

    body = _body(tmp_path)

    assert body["tools"] == []
    assert body["unobserved"] == [
        {"tool": "get_hours", "reason": "no_world_record"},
        {"tool": "track_parcel", "reason": "no_world_record"},
    ]


def test_a_tool_the_mcp_toolbox_serves_is_logged_by_the_world(tmp_path):
    served = tmp_path / "bundle" / "generated" / "mcp-toolbox"
    served.mkdir(parents=True)
    (served / "toolbox.json").write_text(json.dumps({"tools": {"get_hours": {}, "track_parcel": {}}}))
    runtime = SimpleNamespace(endpoints={"mcp_toolbox": object()})

    record_tool_routing(
        tmp_path,
        "s-1",
        _contract(),
        runtime,
        received=[_call("get_hours")],
        reported=[_call("get_hours"), _call("track_parcel")],
        bundle=tmp_path / "bundle",
    )

    assert _body(tmp_path)["tools"] == [
        {"tool": "get_hours", "routed": True, "calls": 1, "outcomes": ["ok"]},
        # Reported, and the toolbox would have logged it: the call went elsewhere.
        {"tool": "track_parcel", "routed": False, "reason": "not_received"},
    ]


class _World:
    def __init__(self):
        self.calls = []

    def handle_tool_call(self, call):
        self.calls.append(_call(call["name"]))
        return SimpleNamespace(content="{}")


def _requests(tmp_path, monkeypatch, *, runtime=PROXIED, bounds=None, **behaviour):
    """Each request is answered as ``behaviour`` says, per tool: ``world`` (the call goes
    through the proxy), ``reported`` (the agent says it ran it; nothing reached the world),
    ``returned`` (the agent returns the call for the harness to run), ``fail``."""
    world_log: list = []
    file_trace: list = []
    sent: list = []

    class Wrapper:
        def __init__(self):
            self.returned = False

        async def call(self, agent_input):
            text = agent_input.new_message["content"]
            sent.append(text)
            tool = next(
                (one for one in behaviour.get("names", ()) if f"your {one} tool" in text),
                None,
            )
            how = behaviour.get(tool) if tool else None
            if how == "fail":
                raise RuntimeError("agent down")
            if how == "world":
                world_log.append(_call(tool, at=1e12))
                file_trace.append(_call(tool))
            if how == "reported":
                file_trace.append(_call(tool))
            calls = []
            if how == "returned" and not self.returned:
                self.returned = True
                calls = [{"id": "c1", "name": tool, "arguments": {}}]
            return SimpleNamespace(
                content="done", tool_calls=calls, tool_responses=[], metadata={}
            )

    monkeypatch.setattr(
        chat_readiness, "create_chat_wrapper", lambda *a, **k: Wrapper()
    )
    monkeypatch.setattr(chat_call_runner, "_tool_world", lambda *a, **k: _World())
    monkeypatch.setattr(
        call_runner, "_collect_file_tool_calls", lambda runtime: tuple(file_trace)
    )
    monkeypatch.setattr(
        call_runner, "_collect_tool_trace_calls", lambda runtime: tuple(world_log)
    )
    record_tool_routing(
        tmp_path,
        "s-1",
        _contract(),
        runtime,
        received=[_call("lookup")],
        reported=[_call("lookup_order")],
    )
    asyncio.run(
        chat_readiness.request_uncovered_tools(
            interface=None,
            endpoint="http://agent",
            runtime=runtime,
            contract=_contract(),
            authoring=tmp_path,
            bundle=tmp_path,
            source=None,
            secret_values={},
            **(bounds or {}),
        )
    )
    return _body(tmp_path), sent


NAMES = ("track_parcel", "issue_refund", "get_hours")


def test_each_uncalled_tool_is_asked_once_and_routed_only_when_the_world_receives_it(
    tmp_path, monkeypatch
):
    body, sent = _requests(
        tmp_path,
        monkeypatch,
        names=NAMES,
        track_parcel="world",
        issue_refund="reported",
        get_hours="returned",
    )

    assert body["tools"] == [
        # The agent returned the call and the harness ran it at the world.
        {"tool": "get_hours", "routed": True, "calls": 1, "outcomes": ["ok"], "source": "tool_request"},
        {"tool": "issue_refund", "routed": False, "reason": "not_received", "source": "tool_request"},
        {"tool": "lookup_order", "routed": True, "calls": 1, "outcomes": ["ok"]},
        {"tool": "track_parcel", "routed": True, "calls": 1, "outcomes": ["ok"], "source": "tool_request"},
    ]
    assert body["unobserved"] == [{"tool": "send_sms", "reason": "reaches_outside"}]
    assert len(sent) == 4  # three tools, one continuation after the returned call
    assert not any("send_sms" in text or "lookup_order" in text for text in sent)


def test_a_request_the_agent_cannot_answer_or_ignores_leaves_the_tool_unobserved(
    tmp_path, monkeypatch
):
    body, _ = _requests(tmp_path, monkeypatch, names=NAMES, track_parcel="fail")

    assert {"tool": "track_parcel", "reason": "request_failed"} in body["unobserved"]
    assert {"tool": "get_hours", "reason": "not_called"} in body["unobserved"]


def test_requests_past_the_limit_are_recorded_as_not_asked(tmp_path, monkeypatch):
    body, sent = _requests(
        tmp_path, monkeypatch, bounds={"limit": 1}, names=NAMES, track_parcel="world"
    )

    assert len(sent) == 1
    asked = [one["tool"] for one in body["tools"] if one.get("source") == "tool_request"]
    left = sorted(one["tool"] for one in body["unobserved"] if one["reason"] == "not_asked")
    assert len(asked) + len(left) == 3
    assert len(left) == 2
    assert all(one.get("reason") != "not_asked" for one in body["tools"])


def test_no_request_is_sent_once_the_time_is_spent(tmp_path, monkeypatch):
    body, sent = _requests(
        tmp_path, monkeypatch, bounds={"budget": 0.0}, names=NAMES, track_parcel="world"
    )

    assert sent == []
    assert sorted(one["tool"] for one in body["unobserved"] if one["reason"] == "not_asked") == sorted(NAMES)


def test_the_request_names_the_tool_and_an_example_from_its_contract():
    text = chat_readiness._tool_request(_contract().tools[0])

    assert (
        "lookup_order" in text
        and "order_id='A-1'" in text
        and "disposable test world" in text
    )


def test_an_argument_without_an_example_takes_a_plain_value_of_its_type():
    tool = AgentContract.model_validate(
        {
            "agent": "a",
            "tools": [
                {
                    "name": "apply",
                    "args": ["item_id", "note"],
                    "arg_types": {"item_id": "integer"},
                }
            ],
        }
    ).tools[0]

    assert "item_id=1, note='test'" in chat_readiness._tool_request(tool)


def test_a_later_attempt_of_the_check_reuses_the_answers_and_stays_within_the_limit(
    tmp_path, monkeypatch
):
    first, sent_first = _requests(
        tmp_path, monkeypatch, bounds={"limit": 2}, names=NAMES, track_parcel="world"
    )
    # A retry writes the qualification's routing again, then asks for the rest.
    again, sent_again = _requests(
        tmp_path, monkeypatch, bounds={"limit": 2}, names=NAMES, track_parcel="world"
    )

    assert len(sent_first) == 2
    assert sent_again == []
    assert again["tools"] == first["tools"]
    assert again["unobserved"] == first["unobserved"]


def test_a_tool_that_changes_state_and_leaves_no_world_record_is_not_asked(
    tmp_path, monkeypatch
):
    body, sent = _requests(tmp_path, monkeypatch, runtime=NO_PROXY, names=NAMES)

    assert {"tool": "issue_refund", "reason": "not_observable"} in body["unobserved"]
    assert not any("issue_refund" in text for text in sent)
