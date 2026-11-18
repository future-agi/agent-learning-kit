"""Generated replies: the transcript never holds text from a position that is not answer text.

A small generator builds nested replies from mappings, lists and plain objects. Every string
gets a unique marker. The generator knows, by construction, which markers sit in positions that
are never answer text (thinking, reasoning, signatures, tool input, the bookkeeping fields of a
message, a message printed whole). The test asserts none of them reaches the transcript. It
does not assert that answer text is found, apart from a floor on how many cases give some text,
so an always-empty wrapper cannot pass.

The kinds, roles and keys below are written out here from the rule, not imported from the code
under test.
"""

import asyncio
import random
import time
from types import SimpleNamespace as NS

from fi.simulate.agent.generic import wrap_agent
from fi.simulate.agent.wrapper import AgentInput, AgentResponse

SEED = 20260401
CASES = 3000
MAX_DEPTH = 6

SLOT_KEYS = [
    "output",
    "result",
    "response",
    "data",
    "final_output",
    "answer",
    "content",
    "text",
    "message",
]
INERT_KEYS = ["status", "run", "trace", "usage", "extra", "id", "meta", "info"]
PLAIN_KEYS = [
    "order",
    "name",
    "value",
    "note",
    "status",
    "output",
    "result",
    "data",
    "message",
    "text",
    "response",
    "answer",
    "reasoning",
    "thinking",
    "tool_calls",
    "arguments",
    "input",
    "items",
]
STRIPPED_KEYS = [
    "signature",
    "thought_signature",
    "encrypted_content",
    "reasoning_content",
    "reasoningContent",
]
ROLES = ["assistant", "model", "user", "system", "tool", "developer"]
MESSAGE_TYPES = [
    "ai",
    "AIMessageChunk",
    "human",
    "system",
    "tool",
    "assistant",
    "user",
    "message",
]
THINKING_TYPES = ["thinking", "redacted_thinking", "reasoning", "reasoning_content"]
TOOL_TYPES = ["tool_use", "server_tool_use", "tool_result", "image"]


class _Case:
    def __init__(self, index):
        self.index = index
        self.count = 0
        self.not_answer = []  # markers in positions that are never answer text
        self.answer = []
        self.message_like = False  # a part looks like a message or a content block

    def mark(self, kind=None):
        self.count += 1
        marker = f"@@{self.index}.{self.count}@@"
        if kind == "bad":
            self.not_answer.append(marker)
        elif kind == "answer":
            self.answer.append(marker)
        return marker


def _maybe_object(rng, value):
    return NS(**value) if rng.random() < 0.3 else value


def _bad_record(rng, case):
    """A record whose every string is a marker in a non-answer position."""
    return {
        rng.choice(["q", "x", "args", "city"]): case.mark("bad"),
        "nested": [{"k": case.mark("bad")}],
    }


def _text_block(rng, case):
    case.message_like = True
    block = {"type": rng.choice(["text", "output_text"]), "text": case.mark("answer")}
    if rng.random() < 0.3:
        block["annotations"] = [{"title": case.mark("bad")}]
    return _maybe_object(rng, block)


def _thinking_block(rng, case):
    case.message_like = True
    block = {"type": rng.choice(THINKING_TYPES)}
    for key in rng.sample(
        ["thinking", "text", "signature", "data", "encrypted_content", "content", "id"],
        4,
    ):
        block[key] = case.mark("bad")
    if rng.random() < 0.4:
        block["summary"] = [{"type": "summary_text", "text": case.mark("bad")}]
    return _maybe_object(rng, block)


def _thought_part(rng, case):
    case.message_like = True
    part = {"text": case.mark("bad"), "thought": True}
    if rng.random() < 0.6:
        part["thought_signature"] = case.mark("bad")
    return _maybe_object(rng, part)


def _tool_block(rng, case):
    case.message_like = True
    block = {
        "type": rng.choice(TOOL_TYPES),
        "id": case.mark("bad"),
        "name": case.mark("bad"),
    }
    block["input"] = _bad_record(rng, case)
    if rng.random() < 0.5:
        block["content"] = case.mark("bad")
    return _maybe_object(rng, block)


def _unknown_block(rng, case):
    case.message_like = True
    return _maybe_object(
        rng,
        {
            "type": "vendor_" + rng.choice(["a", "b", "c"]),
            "text": case.mark("bad"),
            "content": case.mark("bad"),
            "value": case.mark("bad"),
        },
    )


def _bedrock_reasoning(rng, case):
    return {
        "reasoningContent": {
            "reasoningText": {"text": case.mark("bad"), "signature": case.mark("bad")}
        }
    }


def _function_call_part(rng, case):
    return {
        "function_call": {"name": case.mark("bad"), "args": _bad_record(rng, case)},
        "thought_signature": case.mark("bad"),
    }


def _content_list(rng, case, depth):
    builders = [
        _text_block,
        _thinking_block,
        _thought_part,
        _tool_block,
        _unknown_block,
        _bedrock_reasoning,
        lambda r, c: {"text": c.mark("answer")},
        lambda r, c: c.mark("answer"),
    ]
    items = [rng.choice(builders)(rng, case) for _ in range(rng.randint(1, 4))]
    if depth < MAX_DEPTH and rng.random() < 0.15:
        items.append(_message(rng, case, depth + 1))
    return items


def _extras(rng, case, message):
    """Bookkeeping fields of a message: never answer text."""
    pool = {
        "id": lambda: case.mark("bad"),
        "name": lambda: case.mark("bad"),
        "tool_call_id": lambda: case.mark("bad"),
        "additional_kwargs": lambda: {
            "reasoning_content": case.mark("bad"),
            "signature": case.mark("bad"),
        },
        "response_metadata": lambda: {
            "model": case.mark("bad"),
            "usage": {"n": case.mark("bad")},
        },
        "usage_metadata": lambda: {"detail": case.mark("bad")},
        "tool_calls": lambda: [
            {
                "name": case.mark("bad"),
                "args": _bad_record(rng, case),
                "id": case.mark("bad"),
            }
        ],
        "function_call": lambda: {
            "name": case.mark("bad"),
            "arguments": case.mark("bad"),
        },
    }
    for key in rng.sample(sorted(pool), rng.randint(0, 4)):
        message[key] = pool[key]()


def _message(rng, case, depth=0):
    case.message_like = True
    message = {}
    if rng.random() < 0.5:
        message["role"] = rng.choice(ROLES)
        if rng.random() < 0.2:
            message["type"] = "message"
    else:
        message["type"] = rng.choice(MESSAGE_TYPES)
    pick = rng.random()
    if pick < 0.35:
        message["content"] = case.mark("answer")
    elif pick < 0.85:
        message["content"] = _content_list(rng, case, depth)
    elif pick < 0.92:
        message["content"] = None
    else:
        message["content"] = (
            _message(rng, case, depth + 1) if depth < MAX_DEPTH else case.mark("answer")
        )
    _extras(rng, case, message)
    return _maybe_object(rng, message)


def _gemini_content(rng, case):
    case.message_like = True
    parts = [
        _thought_part(rng, case),
        {"text": case.mark("answer")},
        _function_call_part(rng, case),
        {"inline_data": {"data": case.mark("bad")}},
    ]
    chosen = rng.sample(parts, rng.randint(1, len(parts)))
    return _maybe_object(
        rng, {"role": rng.choice(["model", "assistant"]), "parts": chosen}
    )


def _dumped(rng, case, depth):
    case.message_like = True
    return {
        "type": rng.choice(["ai", "AIMessageChunk", "assistant", "human"]),
        "data": {
            "content": case.mark("answer")
            if rng.random() < 0.4
            else _content_list(rng, case, depth),
            "additional_kwargs": {"reasoning_content": case.mark("bad")},
            "id": case.mark("bad"),
        },
    }


def _wrapper_shapes(rng, case, depth):
    kind = rng.choice(
        [
            "choices",
            "candidates",
            "messages",
            "responses",
            "history",
            "bare_blocks",
            "dumped",
        ]
    )
    case.message_like = case.message_like or kind in (
        "choices",
        "candidates",
        "messages",
        "responses",
    )
    if kind == "choices":
        return {
            "choices": [
                {
                    "index": 0,
                    rng.choice(["message", "delta"]): _message(rng, case, depth + 1),
                    "finish_reason": case.mark("bad"),
                    "logprobs": {"content": [{"token": case.mark("bad")}]},
                }
            ],
            "usage": {"total": case.mark("bad")},
        }
    if kind == "candidates":
        return {
            "candidates": [
                {
                    "content": _gemini_content(rng, case),
                    "finish_reason": case.mark("bad"),
                    "safety_ratings": [{"category": case.mark("bad")}],
                }
            ],
            "usage_metadata": {"n": case.mark("bad")},
        }
    if kind == "messages":
        return {
            "messages": [
                _message(rng, case, depth + 1) for _ in range(rng.randint(1, 3))
            ]
        }
    if kind == "responses":
        return [
            {
                "type": "reasoning",
                "id": case.mark("bad"),
                "summary": [{"text": case.mark("bad")}],
                "encrypted_content": case.mark("bad"),
            },
            {
                "type": "message",
                "role": "assistant",
                "content": _content_list(rng, case, depth),
            },
        ]
    if kind == "history":
        return [_message(rng, case, depth + 1) for _ in range(rng.randint(1, 4))]
    if kind == "bare_blocks":
        return _content_list(rng, case, depth)
    return _dumped(rng, case, depth)


def _message_node(rng, case, depth=0):
    pick = rng.random()
    if pick < 0.45:
        return _message(rng, case, depth)
    if pick < 0.6:
        return _gemini_content(rng, case)
    return _wrapper_shapes(rng, case, depth)


def _inert_island(rng, case):
    """A subtree under an inert key of a reply: never answer text.

    Plain data is printed as JSON when nothing in the reply looks like a message, so such a
    reply only gets objects here, which are never printed.
    """
    pick = rng.random()
    if not case.message_like:
        return NS(note=case.mark("bad"), extra=[case.mark("bad")])
    if pick < 0.3:
        return case.mark("bad")
    if pick < 0.6:
        return {"id": case.mark("bad"), "usage": {"n": case.mark("bad")}}
    if pick < 0.8:
        return NS(note=case.mark("bad"), extra=[case.mark("bad")])
    return [{"note": case.mark("bad")}, case.mark("bad")]


def _message_reply(rng, case):
    node = _message_node(rng, case)
    for _ in range(rng.choice([0, 0, 1, 2, 3])):
        node = {rng.choice(SLOT_KEYS[:6]): node}
    pick = rng.random()
    if pick < 0.45:
        return node
    if pick < 0.8:
        wrapped = {rng.choice(SLOT_KEYS[:6]): node}
        for key in rng.sample(INERT_KEYS, rng.randint(1, 3)):
            wrapped[key] = _inert_island(rng, case)
        return wrapped
    if pick < 0.9:
        return NS(
            **{
                rng.choice(["output", "response", "final_output"]): node,
                "meta": NS(x=case.mark("bad")),
            }
        )
    return {
        rng.choice(INERT_KEYS): _inert_island(rng, case),
        rng.choice(["a", "b"]): node,
    }


def _plain_tree(rng, case, depth=0):
    pick = rng.random()
    if depth >= MAX_DEPTH or pick < 0.3:
        return rng.choice(
            [
                lambda: case.mark(),
                lambda: rng.randint(0, 99),
                lambda: True,
                lambda: None,
            ]
        )()
    if pick < 0.5:
        return [_plain_tree(rng, case, depth + 1) for _ in range(rng.randint(0, 3))]
    record = {
        key: _plain_tree(rng, case, depth + 1)
        for key in rng.sample(PLAIN_KEYS, rng.randint(1, 4))
    }
    if rng.random() < 0.3:
        record["parts"] = [case.mark() for _ in range(rng.randint(1, 3))]
    if rng.random() < 0.5:
        record[rng.choice(STRIPPED_KEYS)] = case.mark("bad")
    if rng.random() < 0.1:
        record["type"] = rng.choice(["invoice", "event", "record"])
    return record


def _plain_reply(rng, case):
    tree = _plain_tree(rng, case)
    if not isinstance(tree, (dict, list)):
        tree = {rng.choice(PLAIN_KEYS): tree}
    if isinstance(tree, dict):
        tree[rng.choice(STRIPPED_KEYS)] = case.mark("bad")
    return tree


def _build(index):
    rng = random.Random(f"{SEED}:{index}")
    case = _Case(index)
    value = _plain_reply(rng, case) if rng.random() < 0.2 else _message_reply(rng, case)
    return case, value


def _agent_input():
    return AgentInput(
        thread_id="t",
        execution_id="t",
        messages=[{"role": "user", "content": "hi"}],
        new_message={"role": "user", "content": "hi"},
    )


async def _transcript(value):
    result = await wrap_agent(lambda i: value).call(_agent_input())
    return result.content if isinstance(result, AgentResponse) else result


def test_generated_replies_never_leak_a_non_answer_position():
    leaks = []
    non_empty = 0
    read_answers = 0

    async def run():
        nonlocal non_empty, read_answers
        for index in range(CASES):
            case, value = _build(index)
            text = await _transcript(value)
            non_empty += bool(text)
            read_answers += any(marker in text for marker in case.answer)
            for marker in case.not_answer:
                if marker in text:
                    leaks.append((index, marker, text, value))
                    break

    started = time.time()
    asyncio.run(run())
    elapsed = time.time() - started

    if leaks:
        index, marker, text, value = leaks[0]
        raise AssertionError(
            f"{len(leaks)} of {CASES} cases leaked; first: seed={SEED} case={index} "
            f"marker={marker}\nvalue={value!r}\ntranscript={text!r}"
        )
    assert non_empty > CASES * 0.6, f"only {non_empty} of {CASES} cases gave any text"
    assert read_answers > CASES * 0.45, (
        f"only {read_answers} of {CASES} cases showed an answer marker"
    )
    assert elapsed < 30, f"{CASES} cases took {elapsed:.1f}s"


def test_a_message_nested_past_the_depth_limit_gives_nothing():
    marker = "@@deep@@"
    value = {"type": "thinking", "thinking": marker}
    for _ in range(40):
        value = {"role": "assistant", "content": value}
    assert marker not in asyncio.run(_transcript(value))
    value = [{"type": "thinking", "thinking": marker}]
    for _ in range(40):
        value = [{"role": "assistant", "content": value}]
    assert marker not in asyncio.run(_transcript({"output": value}))


# The names that mean answer text in a stream, written out here from the rule: compared without
# regard to case, "-" or "_".
STREAM_ANSWER_TYPES = [
    "token",
    "text",
    "delta",
    "chunk",
    "content",
    "answer",
    "output",
    "output_text",
    "text_delta",
    "text_message_content",
]
STREAM_OTHER_TYPES = [
    "thought",
    "thinking",
    "reasoning",
    "reasoning-delta",
    "status",
    "tool_result",
    "tool_call",
    "done",
    "end",
    "vendor_event",
    "response.reasoning_text.delta",
    "token.delta",
    "vendor.text",
    "tool",
    "user",
    "human",
    "system",
]
STREAM_FIELDS = ["content", "text", "delta"]


def _spelled(rng, name):
    """The same type name in another case, with "-" or "_" for the other."""
    name = rng.choice([name, name.upper(), name.title()])
    return name.replace("_", rng.choice(["_", "-", ""]))


def _stream_chunk(rng, case):
    pick = rng.random()
    if pick < 0.5:
        marker = case.mark("answer")
        chunk = {
            "type": _spelled(rng, rng.choice(STREAM_ANSWER_TYPES)),
            rng.choice(STREAM_FIELDS): marker,
        }
        if rng.random() < 0.3:
            chunk["id"] = case.mark("bad")
        return chunk, marker
    if pick < 0.8:
        chunk = {"type": rng.choice(STREAM_OTHER_TYPES)}
        for field in rng.sample(STREAM_FIELDS, rng.randint(1, 3)):
            chunk[field] = case.mark("bad")
        return chunk, ""
    if pick < 0.9:
        # A listed type whose fields hold no string: nothing to read.
        chunk = {"type": _spelled(rng, rng.choice(STREAM_ANSWER_TYPES))}
        for field in rng.sample(STREAM_FIELDS, rng.randint(1, 3)):
            chunk[field] = rng.choice(
                [
                    [case.mark("bad")],
                    {"value": case.mark("bad")},
                    None,
                    7,
                ]
            )
        chunk["payload"] = {"note": case.mark("bad")}
        return chunk, ""
    return {"type": "done", "usage": {"n": case.mark("bad")}}, ""


def test_generated_streams_give_exactly_the_text_of_the_listed_chunk_types():
    async def run():
        for index in range(CASES):
            rng = random.Random(f"{SEED}:stream:{index}")
            case = _Case(index)
            chunks, expected = [], []
            for _ in range(rng.randint(1, 8)):
                chunk, answer = _stream_chunk(rng, case)
                chunks.append(_maybe_object(rng, chunk))
                expected.append(answer)

            def agent(i, chunks=chunks):
                yield from chunks

            result = await wrap_agent(agent).call(_agent_input())
            text = result.content
            assert text == "".join(expected), (
                f"seed={SEED} stream case={index}\nchunks={chunks!r}\ntranscript={text!r}"
            )
            if not text:
                assert "reply_unreadable" in result.metadata, (index, chunks)
            else:
                assert "reply_unreadable" not in result.metadata, (index, chunks)

    asyncio.run(run())
