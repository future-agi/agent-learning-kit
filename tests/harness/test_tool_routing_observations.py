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
