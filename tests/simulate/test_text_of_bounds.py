"""Nested replies are read only to a fixed depth."""

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_text_of_untyped import _generic_reply

import fi.simulate.agent.content as content_module

SRC = Path(content_module.__file__).resolve().parents[2]
COPIES = {
    "content": SRC / "simulate/agent/content.py",
    "callback_http_adapter": SRC / "alk/harness/callback_http_adapter.py",
    "langgraph_http_adapter": SRC / "alk/harness/langgraph_http_adapter.py",
}
REGION = re.compile(r"^# --- begin text_of.*?^# --- end text_of ---$", re.S | re.M)
BOUND = 32
TOO_DEEP_FOR_THE_STACK = 3000


def _namespace(name):
    """Run one file's text_of region on its own, as a hosted adapter does (stdlib only)."""
    path = COPIES[name]
    match = REGION.search(path.read_text(encoding="utf-8"))
    assert match, f"{path} has no text_of region"
    namespace = {"Any": object}
    exec(compile(match.group(0), str(path), "exec"), namespace)
    return namespace


def _message_chain(depth, leaf="x"):
    value = leaf
    for _ in range(depth):
        value = {"role": "assistant", "content": value}
    return value


def _list_chain(depth, leaf="x"):
    value = leaf
    for _ in range(depth):
        value = [{"role": "assistant", "content": value}]
    return value


def _state_chain(depth, leaf="x"):
    value = {"messages": [{"role": "assistant", "content": leaf}]}
    for _ in range(depth - 2):
        value = {"messages": [value]}
    return value


def _object_chain(depth, leaf="x"):
    value = leaf
    for _ in range(depth):
        value = SimpleNamespace(type="ai", content=value)
    return value


CHAINS = {
    "message-dicts": _message_chain,
    "message-lists": _list_chain,
    "graph-states": _state_chain,
    "message-objects": _object_chain,
}
COPY_NAMES = list(COPIES)


@pytest.mark.parametrize("copy_name", COPY_NAMES)
def test_every_copy_names_the_same_depth_bound(copy_name):
    assert _namespace(copy_name)["_MOST_DEPTH"] == BOUND


@pytest.mark.parametrize("chain", CHAINS, ids=list(CHAINS))
@pytest.mark.parametrize("copy_name", COPY_NAMES)
def test_a_nested_reply_at_the_bound_keeps_its_text(copy_name, chain):
    assert _namespace(copy_name)["_text_of"](CHAINS[chain](BOUND)) == "x"


@pytest.mark.parametrize("chain", CHAINS, ids=list(CHAINS))
@pytest.mark.parametrize("copy_name", COPY_NAMES)
def test_a_nested_reply_one_past_the_bound_gives_nothing(copy_name, chain):
    assert _namespace(copy_name)["_text_of"](CHAINS[chain](BOUND + 1)) == ""


@pytest.mark.parametrize("chain", CHAINS, ids=list(CHAINS))
@pytest.mark.parametrize("copy_name", COPY_NAMES)
def test_a_reply_nested_past_the_stack_limit_gives_nothing_and_never_raises(
    copy_name, chain
):
    assert (
        _namespace(copy_name)["_text_of"](CHAINS[chain](TOO_DEEP_FOR_THE_STACK)) == ""
    )


@pytest.mark.parametrize("copy_name", COPY_NAMES)
def test_a_deep_reply_without_an_answer_never_shows_its_repr(copy_name):
    reply = _message_chain(BOUND + 5, leaf={"type": "image", "source": "SECRET"})
    assert "SECRET" not in _namespace(copy_name)["_text_of"](reply)


def test_a_reply_one_past_the_bound_through_a_generic_agent_gives_nothing():
    assert _generic_reply({"output": _list_chain(BOUND + 1)}) == ""
