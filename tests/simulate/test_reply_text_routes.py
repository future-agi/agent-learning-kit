"""A bare block list, a history or a stream returned by the agent keeps only the text."""

import asyncio
from types import SimpleNamespace as NS

import pytest
from test_text_of_untyped import _generic_reply
from text_of_cases import ANSWER, THINKING, _blocks

from fi.simulate.agent.generic import wrap_agent
from fi.simulate.agent.wrapper import AgentInput, AgentResponse


def _text(text):
    return {"type": "text", "text": text}


@pytest.mark.parametrize(
    "reply, expected",
    [
        (_blocks(), ANSWER),
        ([_text(ANSWER), THINKING], ANSWER),
        ([THINKING], ""),
        (
            [_text("The status"), THINKING, _text("is complete.")],
            "The status is complete.",
        ),
        ([{"text": "a"}, {"text": "b"}], "ab"),
        (tuple(_blocks()), ANSWER),
        (
            [
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "A1"},
                {"role": "user", "content": "q2"},
                {"role": "assistant", "content": [THINKING, _text("A2")]},
            ],
            "A2",
        ),
        (
            [
                NS(type="human", content="q"),
                NS(type="ai", content="Let me check."),
                NS(type="ai", content=_blocks()),
            ],
            ANSWER,
        ),
    ],
    ids=[
        "thinking-then-text",
        "text-then-thinking",
        "only-thinking",
        "text-around-thinking",
        "untyped-parts",
        "tuple",
        "dict-history-gives-the-last-reply-only",
        "object-history-gives-the-last-reply-only",
    ],
)
def test_a_bare_block_list_or_a_history_returned_by_the_agent_is_reduced_to_text(
    reply, expected
):
    text = _generic_reply(reply)
    assert text == expected
    assert "sig-abc" not in text and "plan the reply" not in text


def _agent_input():
    return AgentInput(
        thread_id="t",
        execution_id="t",
        messages=[{"role": "user", "content": "hi"}],
        new_message={"role": "user", "content": "hi"},
    )


def _stream_reply(chunks):
    class Agent:
        def invoke(self, inputs):
            yield from chunks

    result = asyncio.run(wrap_agent(Agent()).call(_agent_input()))
    return result.content if isinstance(result, AgentResponse) else result


@pytest.mark.parametrize(
    "chunks, expected",
    [
        (
            [
                NS(content=[THINKING, _text("The status ")]),
                NS(content=[_text("is complete.")]),
            ],
            ANSWER,
        ),
        (
            [{"content": [THINKING]}, {"delta": [_text("The status is complete.")]}],
            ANSWER,
        ),
        ([{"content": "The status "}, {"content": "is complete."}], ANSWER),
        (
            [
                {"message": {"role": "assistant", "content": [THINKING, _text("Hi")]}},
                NS(output=[THINKING]),
            ],
            "Hi",
        ),
        ([{"content": {"city": "Pune"}}], '{"city": "Pune"}'),
    ],
    ids=[
        "chunk-objects-with-blocks",
        "chunk-dicts-with-blocks",
        "string-deltas",
        "message-chunk-and-thinking-only-chunk",
        "record-chunk-stays-json",
    ],
)
def test_a_streamed_reply_keeps_only_the_text(chunks, expected):
    text = _stream_reply(chunks)
    assert text == expected
    assert "sig-abc" not in text and "plan the reply" not in text
