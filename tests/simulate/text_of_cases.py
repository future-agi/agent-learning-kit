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
        [
            NS(type="thinking", thinking="x", signature="sig"),
            NS(type="text", text=ANSWER),
        ],
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
        [
            {"type": "text", "text": "The status "},
            {"type": "text", "text": "is complete."},
        ],
        ANSWER,
    ),
    (
        "untyped-text-block",
        [{"text": "Your order shipped today."}],
        "Your order shipped today.",
    ),
    ("untyped-text-object", [NS(text="obj")], "obj"),
    ("untyped-block-without-text", [{"foo": "bar"}, {"text": 3}], ""),
    (
        "message-with-untyped-text-blocks",
        {"role": "assistant", "content": [{"text": "Hi."}]},
        "Hi.",
    ),
    ("string", "plain", "plain"),
    ("none", None, ""),
    ("empty-list", [], ""),
    ("string-list", ["a", "b"], "a b"),
    (
        "strings-and-blocks-in-order",
        ["a", {"type": "text", "text": "b"}, THINKING, "c"],
        "a b c",
    ),
    (
        "sentences-in-a-list",
        ["Sure.", "Your order shipped today."],
        "Sure. Your order shipped today.",
    ),
    ("empty-strings-add-no-space", ["a", "", "b"], "a b"),
    (
        "history-gives-only-the-last-assistant-message",
        [
            NS(type="human", content="q"),
            NS(type="ai", content="Let me check."),
            NS(type="tool", content="found"),
            NS(type="ai", content="It shipped today."),
        ],
        "It shipped today.",
    ),
    (
        "blocks-of-one-message-joined-by-nothing-only-the-last-message-counts",
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "a"},
                    {"type": "text", "text": "b"},
                ],
            },
            {"role": "assistant", "content": [{"type": "text", "text": "c"}]},
        ],
        "c",
    ),
    ("output-text-block", [{"type": "output_text", "text": "out"}], "out"),
    ("output-text-object", [NS(type="output_text", text="out")], "out"),
    ("text-value-dict", [{"type": "text", "text": {"value": "v"}}], "v"),
    ("text-value-not-str", [{"type": "text", "text": {"value": {"a": 1}}}], ""),
    ("text-not-str-never-a-repr", [{"type": "text", "text": {"other": "x"}}], ""),
    (
        "refusal-block",
        [{"type": "refusal", "refusal": "I can't help."}],
        "I can't help.",
    ),
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
        {
            "messages": [
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": _blocks()},
            ]
        },
        ANSWER,
    ),
    ("state-empty-messages", {"messages": []}, "{'messages': []}"),
    ("number-falls-back-to-str", 42, "42"),
    ("plain-dict-falls-back-to-str", {"a": 1}, "{'a': 1}"),
]

CASES += [
    (
        "gemini-thought-part-dropped",
        [{"text": "THOUGHT", "thought": True}, {"text": "Shipped."}],
        "Shipped.",
    ),
    (
        "gemini-thought-object-dropped",
        [NS(text="THOUGHT", thought=True), NS(text="Shipped.")],
        "Shipped.",
    ),
    (
        "untyped-text-thought-false-kept",
        [{"text": "a", "thought": False}, NS(text="b", thought=False)],
        "a b",
    ),
    (
        "untyped-text-no-thought-kept",
        [{"text": "a"}, NS(text="b", thought=None)],
        "a b",
    ),
]

CASES += [
    (
        "text-around-thinking-joined-by-a-space",
        [
            {"type": "text", "text": "Let me check."},
            THINKING,
            {"type": "text", "text": "It shipped today."},
        ],
        "Let me check. It shipped today.",
    ),
    (
        "text-around-server-tool-joined-by-a-space",
        [
            {"type": "text", "text": "Let me check."},
            {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {}},
            {"type": "text", "text": "It shipped today."},
        ],
        "Let me check. It shipped today.",
    ),
]

CASES += [
    (
        "untyped-empty-part-adds-no-extra-space",
        [{"text": "a"}, {"text": ""}, {"text": "b"}],
        "a b",
    ),
]

CASES += [
    (
        "blocks-of-the-last-message-are-still-joined",
        [
            {"role": "assistant", "content": "old answer"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "a"},
                    THINKING,
                    {"type": "text", "text": "b"},
                ],
            },
        ],
        "a b",
    ),
    (
        "history-ending-in-a-user-message-gives-the-last-assistant-reply",
        [
            {"role": "assistant", "content": "A"},
            {"role": "user", "content": "q"},
        ],
        "A",
    ),
]

CASES += [
    (
        "tool-result-with-text-content-is-not-unwrapped",
        {"type": "tool_result", "content": [{"type": "text", "text": "leak"}]},
        "",
    ),
    (
        "thinking-with-content-is-not-unwrapped",
        {"type": "thinking", "content": "leak"},
        "",
    ),
    (
        "thinking-object-with-content-is-not-unwrapped",
        NS(type="thinking", content="leak"),
        "",
    ),
    (
        "tool-use-with-content-is-not-unwrapped",
        {"type": "tool_use", "content": "leak"},
        "",
    ),
    (
        "typed-block-with-an-assistant-role-is-not-a-message",
        [
            {"type": "tool_result", "role": "assistant", "content": "leak"},
            {"type": "text", "text": "ok"},
        ],
        "ok",
    ),
    (
        "thinking-block-with-an-assistant-role-is-not-a-message",
        [
            NS(type="thinking", role="assistant", content="leak"),
            NS(type="text", text="ok"),
        ],
        "ok",
    ),
]

CASES += [
    ("message-chunk-object", NS(type="AIMessageChunk", content=_blocks()), ANSWER),
    ("message-chunk-dict", {"type": "AIMessageChunk", "content": _blocks()}, ANSWER),
    (
        "history-ending-in-a-message-chunk",
        [NS(type="human", content="q"), NS(type="AIMessageChunk", content=_blocks())],
        ANSWER,
    ),
    (
        "message-chunk-in-a-block-list",
        [NS(type="AIMessageChunk", content=_blocks()), {"type": "text", "text": "x"}],
        ANSWER,
    ),
]

CASES += [
    (
        "untyped-text-blocks-of-a-converse-reply-join-with-a-space",
        [{"text": "Your order shipped."}, {"text": "Anything else?"}],
        "Your order shipped. Anything else?",
    ),
]
