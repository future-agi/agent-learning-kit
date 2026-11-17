"""Untyped provider thinking blocks (Gemini, Bedrock, langchain-aws) never reach the reply."""

import pytest
from test_text_of_untyped import _generic_reply
from text_of_cases import ANSWER

BEDROCK_REASONING = {
    "reasoningContent": {
        "reasoningText": {"text": "plan the reply", "signature": "sig-abc"}
    }
}
AWS_REASONING = {
    "type": "reasoning_content",
    "reasoning_content": {"text": "plan the reply", "signature": "sig-abc"},
}


@pytest.mark.parametrize(
    "reply, expected",
    [
        ({"content": [{"text": "plan the reply", "thought": True}]}, ""),
        (
            {
                "role": "assistant",
                "content": [
                    BEDROCK_REASONING,
                    {"toolUse": {"toolUseId": "t", "name": "f", "input": {}}},
                ],
            },
            "",
        ),
        ({"output": [{"text": ANSWER}, BEDROCK_REASONING]}, ANSWER),
        ({"content": [AWS_REASONING]}, ""),
        (
            {
                "message": {
                    "role": "model",
                    "parts": [
                        {"text": "plan the reply", "thought": True},
                        {"text": ANSWER},
                    ],
                }
            },
            ANSWER,
        ),
    ],
    ids=[
        "gemini-thought-only",
        "bedrock-reasoning-tool-turn",
        "bedrock-text-and-reasoning-under-output",
        "langchain-aws-reasoning-only",
        "gemini-content-parts-in-message",
    ],
)
def test_an_untyped_thinking_block_never_shows(reply, expected):
    text = _generic_reply(reply)
    assert text == expected
    assert "sig-abc" not in text and "plan the reply" not in text
