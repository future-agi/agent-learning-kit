"""A tool call streamed in parts is one call (TH-10449, ADR-002 amended 19 Nov).

Google ADK streams each tool call as events marked unfinished (``partial: true`` on the event,
``willContinue: true`` on the call) that carry the call's id and name but no arguments, then one
finished event with the arguments. Only the finished copy is a call. A call never disappears: an
id seen only in unfinished events keeps its last copy. Only one reply is ever considered.
"""

import copy
import json
from pathlib import Path

import pytest

from fi.simulate.agent.wrappers.http import (
    HTTPAgentWrapper,
    _decode_json_or_sse,
    _nested_tool_evidence,
)


FIXTURE = Path(__file__).parent / "fixtures" / "google_adk_streamed_replies.json"
REPLIES = json.loads(FIXTURE.read_text(encoding="utf-8"))["replies"]
EXPECTED = [
    ("look_up_order", {"order_id": "A1001"}),
    ("start_return", {"order_id": "A1001", "reason": "too small"}),
    ("return_policy", {}),
]


def _sse(events):
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events)


def _calls(events):
    calls, responses = _nested_tool_evidence(_decode_json_or_sse(_sse(events)))
    return (
        [
            (c["id"], c["function"]["name"], json.loads(c["function"]["arguments"]))
            for c in calls
        ],
        [(r["tool_call_id"], r["name"]) for r in responses],
    )


def _without_markers(events):
    stripped = copy.deepcopy(events)
    for event in stripped:
        event.pop("partial", None)
        for part in (event.get("content") or {}).get("parts") or []:
            (part.get("functionCall") or {}).pop("willContinue", None)
    return stripped


@pytest.mark.parametrize("turn", range(3))
def test_an_adk_streamed_call_is_one_call_with_its_arguments_and_its_response(turn):
    calls, responses = _calls(REPLIES[turn])

    name, arguments = EXPECTED[turn]
    assert [(n, a) for _, n, a in calls] == [(name, arguments)]
    call_id = calls[0][0]
    assert responses == [(call_id, name)]


def test_the_adk_reply_reads_as_the_agent_text_and_one_call_through_the_wrapper():
    wrapper = HTTPAgentWrapper(
        endpoint="http://localhost:8080/run_sse",
        protocol="json_template",
        request_template={"new_message": "{{new_message_content}}"},
        response_path="content.parts.0.text",
    )

    response = wrapper._agent_response_from_payload(
        _decode_json_or_sse(_sse(REPLIES[1]))
    )

    assert response.content.startswith("Done. Tool said:")
    assert [
        (c["function"]["name"], json.loads(c["function"]["arguments"]))
        for c in response.tool_calls
    ] == [EXPECTED[1]]
    assert [r["tool_call_id"] for r in response.tool_responses] == [
        response.tool_calls[0]["id"]
    ]


def test_a_call_seen_only_unfinished_is_kept_as_its_last_copy():
    """The guard: no finished copy arrived, so the call is not lost."""
    def calls_in(event):
        parts = (event.get("content") or {}).get("parts") or []
        return [part["functionCall"] for part in parts if "functionCall" in part]

    events = [
        event
        for event in copy.deepcopy(REPLIES[0])
        if not (calls_in(event) and not event.get("partial"))
    ]
    unfinished = [event for event in events if calls_in(event)]
    assert len(unfinished) == 2 and all(event["partial"] for event in unfinished)
    calls_in(unfinished[-1])[0]["args"] = {"order_id": "A1"}

    calls, _ = _calls(events)

    assert [(n, a) for _, n, a in calls] == [("look_up_order", {"order_id": "A1"})]


def test_will_continue_on_the_call_alone_marks_it_unfinished():
    events = copy.deepcopy(REPLIES[0])
    for event in events:
        event.pop("partial", None)

    calls, _ = _calls(events)

    assert [(n, a) for _, n, a in calls] == [EXPECTED[0]]


def test_partial_on_the_event_alone_marks_its_calls_unfinished():
    events = copy.deepcopy(REPLIES[0])
    for event in events:
        for part in (event.get("content") or {}).get("parts") or []:
            (part.get("functionCall") or {}).pop("willContinue", None)

    calls, _ = _calls(events)

    assert [(n, a) for _, n, a in calls] == [EXPECTED[0]]


def test_a_reply_with_no_markers_reads_as_before():
    """No marker, no change: the copy with no arguments is still read, as today."""
    calls, responses = _calls(_without_markers(REPLIES[0]))

    assert [(n, a) for _, n, a in calls] == [
        ("look_up_order", {}),
        ("look_up_order", {"order_id": "A1001"}),
    ]
    assert len({call_id for call_id, _, _ in calls}) == 1
    assert len(responses) == 1


def test_two_finished_calls_that_share_an_id_both_stay():
    """An id is only as unique as the agent makes it: ids that restart inside one reply are
    two calls, and only a copy the stream marks unfinished is ever dropped."""
    finished = [
        {
            "content": {
                "role": "model",
                "parts": [
                    {
                        "functionCall": {
                            "id": "call_0",
                            "name": "look_up_order",
                            "args": {"order_id": order_id},
                        }
                    }
                ],
            }
        }
        for order_id in ("A1001", "A1002")
    ]

    calls, _ = _calls(finished)

    assert [(i, n, a) for i, n, a in calls] == [
        ("call_0", "look_up_order", {"order_id": "A1001"}),
        ("call_0", "look_up_order", {"order_id": "A1002"}),
    ]


def test_an_unfinished_copy_is_dropped_only_for_its_own_id():
    """A finished call under one id does not remove an unfinished-only call under another."""
    events = copy.deepcopy(REPLIES[0])
    orphan = copy.deepcopy(events[0])
    orphan["content"]["parts"][0]["functionCall"].update(
        id="call_other", name="return_policy", args={}
    )
    events.insert(3, orphan)

    calls, _ = _calls(events)

    assert [(i, n) for i, n, _ in calls] == [
        (events[2]["content"]["parts"][0]["functionCall"]["id"], "look_up_order"),
        ("call_other", "return_policy"),
    ]
