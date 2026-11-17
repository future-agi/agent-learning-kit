"""One case table for every copy of ``text_of``; each row is ``(id, content, expected)``."""

from __future__ import annotations

from types import SimpleNamespace as NS

THINKING = {"type": "thinking", "thinking": "plan the reply", "signature": "sig-abc"}
ANSWER = "The status is complete."


def _blocks():
    return [THINKING, {"type": "text", "text": ANSWER}]


CASES = [
    ("dict-blocks-thinking-first", _blocks(), ANSWER),
    (
        "sdk-objects",
        [NS(type="thinking", thinking="x", signature="sig"), NS(type="text", text=ANSWER)],
        ANSWER,
    ),
    (
        "other-block-types-dropped",
        [
            {"type": "redacted_thinking", "data": "enc"},
            {"type": "tool_use", "id": "t1", "name": "f", "input": {}},
            {"type": "tool_result", "content": "r"},
            {"type": "image", "source": {}},
            {"type": "mystery", "text": "nope"},
            {"type": "text", "text": {"value": "kept"}},
            {"type": "text", "text": {"value": 3}},
            {"type": "text", "text": 7},
            {"type": "text", "text": None},
            {"type": "text", "text": "!"},
        ],
        "kept!",
    ),
    (
        "consecutive-text-no-separator",
        [{"type": "text", "text": "The status "}, {"type": "text", "text": "is complete."}],
        ANSWER,
    ),
    ("string", "plain", "plain"),
    ("none", None, ""),
    ("empty-list", [], ""),
    ("string-list", ["a", "b"], "ab"),
    ("strings-and-blocks-in-order", ["a", {"type": "text", "text": "b"}, THINKING, "c"], "abc"),
    ("output-text-block", [{"type": "output_text", "text": "out"}], "out"),
    ("output-text-object", [NS(type="output_text", text="out")], "out"),
    ("text-value-dict", [{"type": "text", "text": {"value": "v"}}], "v"),
    ("text-value-not-str", [{"type": "text", "text": {"value": {"a": 1}}}], ""),
    ("text-not-str-never-a-repr", [{"type": "text", "text": {"other": "x"}}], ""),
    ("refusal-block", [{"type": "refusal", "refusal": "I can't help."}], "I can't help."),
    ("refusal-object", [NS(type="refusal", refusal="no")], "no"),
    ("refusal-not-str", [{"type": "refusal", "refusal": 5}], ""),
    ("lone-text-dict", {"type": "text", "text": "one"}, "one"),
    ("lone-text-object", NS(type="text", text="two"), "two"),
    ("lone-output-text", {"type": "output_text", "text": "three"}, "three"),
    ("lone-refusal", {"type": "refusal", "refusal": "four"}, "four"),
    ("lone-thinking-dict", THINKING, ""),
    ("lone-thinking-object", NS(type="thinking", thinking="x", signature="s"), ""),
    ("lone-tool-use", {"type": "tool_use", "id": "t", "name": "f", "input": {}}, ""),
    ("lone-tool-result", {"type": "tool_result", "content": "r"}, ""),
    ("message-dict-blocks", {"role": "assistant", "content": _blocks()}, ANSWER),
    ("message-dict-string", {"role": "assistant", "content": "hi"}, "hi"),
    ("message-dict-none-content", {"role": "assistant", "content": None}, ""),
    ("message-object-blocks", NS(type="ai", content=_blocks()), ANSWER),
    ("message-object-string", NS(content="hi"), "hi"),
    (
        "message-list-answer-of-the-assistant",
        [{"role": "user", "content": "q"}, {"role": "assistant", "content": _blocks()}],
        ANSWER,
    ),
    (
        "message-object-list",
        [NS(type="human", content="q"), NS(type="ai", content=_blocks())],
        ANSWER,
    ),
    (
        "state-last-message",
        {"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": _blocks()}]},
        ANSWER,
    ),
    ("state-empty-messages", {"messages": []}, "{'messages': []}"),
    ("number-falls-back-to-str", 42, "42"),
    ("plain-dict-falls-back-to-str", {"a": 1}, "{'a': 1}"),
]
