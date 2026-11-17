"""A single message (not a list) under a reply key keeps only its text."""

import pytest
from test_text_of_untyped import _generic_reply
from text_of_cases import ANSWER, _blocks
from types import SimpleNamespace as NS


class _Message:  # an AIMessage-like object with model_dump
    def __init__(self, content):
        self.type, self.content = "ai", content

    def model_dump(self):
        return {"type": self.type, "content": self.content, "additional_kwargs": {}}


@pytest.mark.parametrize(
    "reply, expected",
    [
        ({"output": _Message(_blocks())}, ANSWER),
        ({"response": {"role": "assistant", "content": _blocks()}}, ANSWER),
        (
            {"result": {"type": "message", "role": "assistant", "content": _blocks()}},
            ANSWER,
        ),
        (NS(output=_Message(_blocks())), ANSWER),
        (
            {"result": [{"order_id": "A1", "status": "shipped"}]},
            '[{"order_id": "A1", "status": "shipped"}]',
        ),
        ({"data": {"name": "Ann", "role": "admin"}}, '{"name": "Ann", "role": "admin"}'),
    ],
    ids=[
        "message-object-under-output",
        "message-dict-under-response",
        "responses-message-under-result",
        "message-object-as-output-attribute",
        "control-records-stay-json",
        "control-record-dict-stays-json",
    ],
)
def test_a_message_under_a_reply_key_never_shows_thinking(reply, expected):
    text = _generic_reply(reply)
    assert text == expected
    assert "sig-abc" not in text and "plan the reply" not in text
