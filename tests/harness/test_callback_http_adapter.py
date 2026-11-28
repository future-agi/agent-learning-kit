import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ADAPTER = (
    Path(__file__).resolve().parents[2] / "src/fi/alk/harness/callback_http_adapter.py"
)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


STUB_FI = {
    "fi/__init__.py": "",
    # What an older published agent-simulate offers: AgentInput, and no fi.simulate.agent.content.
    "fi/simulate/__init__.py": (
        "class AgentInput:\n"
        "    def __init__(self, **fields):\n"
        "        self.__dict__.update(fields)\n"
        "\n"
        "    @classmethod\n"
        "    def model_validate(cls, payload):\n"
        "        return cls(**payload)\n"
    ),
}


def _serve(tmp_path, entrypoint, stub_fi=False):
    (tmp_path / "chat_agent.py").write_text(
        "def reply(agent_input):\n"
        "    return {'content': 'thread ' + agent_input.thread_id}\n"
        "\n"
        "def state_reply(agent_input):\n"
        "    return {'messages': [\n"
        "        {'role': 'user', 'content': 'q'},\n"
        "        {'role': 'assistant', 'content': [\n"
        "            {'type': 'thinking', 'thinking': 'hmm', 'signature': 'sig-abc'},\n"
        "            {'type': 'text', 'text': 'The status is complete.'},\n"
        "        ]},\n"
        "    ]}\n"
        "\n"
        "def message_list_reply(agent_input):\n"
        "    return [\n"
        "        {'role': 'user', 'content': 'q'},\n"
        "        {'role': 'assistant', 'content': 'The status is complete.'},\n"
        "    ]\n"
        "\n"
        "def content_dict_reply(agent_input):\n"
        "    return {'content': [\n"
        "        {'type': 'thinking', 'thinking': 'hmm', 'signature': 'sig-abc'},\n"
        "        {'type': 'text', 'text': 'The status '},\n"
        "        {'type': 'text', 'text': 'is complete.'},\n"
        "    ], 'tool_calls': [{'id': 'c1'}]}\n"
        "\n"
        "import copy\n"
        "\n"
        "class Message:\n"
        "    def __init__(self, type, content):\n"
        "        self.type = type\n"
        "        self.content = content\n"
        "\n"
        "def object_state_reply(agent_input):\n"
        "    return {\n"
        "        'messages': [\n"
        "            Message('human', 'q'),\n"
        "            Message('ai', [\n"
        "                {'type': 'thinking', 'thinking': 'hmm', 'signature': 'sig-abc'},\n"
        "                {'type': 'text', 'text': 'The status is complete.'},\n"
        "            ]),\n"
        "        ],\n"
        "        'tool_calls': [{'id': 'c1'}],\n"
        "        'state': {'stage': 'done'},\n"
        "        'handle': object(),\n"
        "    }\n"
        "\n"
        "BLOCK_DICT = {\n"
        "    'content': [\n"
        "        {'type': 'thinking', 'thinking': 'hmm', 'signature': 'sig-abc'},\n"
        "        {'type': 'text', 'text': 'The status is complete.'},\n"
        "    ],\n"
        "    'tool_calls': [{'id': 'c1'}],\n"
        "}\n"
        "BARE_DICT = {'tool_calls': [{'id': 'c1'}]}\n"
        "ORIGINALS = {'block': copy.deepcopy(BLOCK_DICT), 'bare': copy.deepcopy(BARE_DICT)}\n"
        "CALLS = {'n': 0}\n"
        "\n"
        "def _same_dict_twice(name, shared):\n"
        "    CALLS['n'] += 1\n"
        "    if CALLS['n'] <= 2:\n"
        "        return shared\n"
        "    return {'content': 'intact' if shared == ORIGINALS[name] else 'mutated'}\n"
        "\n"
        "def block_dict_twice(agent_input):\n"
        "    return _same_dict_twice('block', BLOCK_DICT)\n"
        "\n"
        "def bare_dict_twice(agent_input):\n"
        "    return _same_dict_twice('bare', BARE_DICT)\n"
        "\n"
        "def thinking_reply(agent_input):\n"
        "    return [\n"
        "        {'type': 'thinking', 'thinking': 'hmm', 'signature': 'sig-abc'},\n"
        "        {'type': 'text', 'text': 'The status '},\n"
        "        {'type': 'text', 'text': 'is complete.'},\n"
        "    ]\n",
        encoding="utf-8",
    )
    # Bundles embed the adapter's source and run it from the bundle, not from the package.
    script = tmp_path / "adapter.py"
    script.write_text(ADAPTER.read_text(encoding="utf-8"), encoding="utf-8")
    if stub_fi:
        # The agent's own venv: the stub is the only `fi`, and the repo's `src` is not on the path.
        for name, text in STUB_FI.items():
            (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / name).write_text(text, encoding="utf-8")
        python_path = str(tmp_path)
    else:
        python_path = os.pathsep.join([str(tmp_path), os.environ.get("PYTHONPATH", "")])
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, str(script)],
        env={
            **os.environ,
            "ALK_CALLBACK_ENTRYPOINT": entrypoint,
            "PORT": str(port),
            "PYTHONPATH": python_path,
        },
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{base}/health", timeout=1)
            break
        except OSError:
            if process.poll() is not None:
                pytest.fail(process.stderr.read().decode())
            time.sleep(0.2)
    yield base
    process.terminate()
    process.wait(timeout=10)


@pytest.fixture
def adapter(tmp_path):
    yield from _serve(tmp_path, "chat_agent:reply")


@pytest.fixture
def thinking_adapter(tmp_path):
    yield from _serve(tmp_path, "chat_agent:thinking_reply")


@pytest.fixture
def state_adapter(tmp_path):
    yield from _serve(tmp_path, "chat_agent:state_reply")


@pytest.fixture
def message_list_adapter(tmp_path):
    yield from _serve(tmp_path, "chat_agent:message_list_reply")


def test_a_callback_answers_from_the_request_thread(adapter):
    """The request thread is not the main thread; nothing imported there may require it."""
    body = json.dumps(
        {
            "thread_id": "t1",
            "messages": [{"role": "user", "content": "hello"}],
            "new_message": {"role": "user", "content": "hello"},
        }
    ).encode()
    request = urllib.request.Request(
        f"{adapter}/invoke", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        assert response.status == 200
        assert json.loads(response.read())["content"] == "thread t1"


def _ask_body(base):
    body = json.dumps(
        {
            "thread_id": "t1",
            "execution_id": "e1",
            "messages": [{"role": "user", "content": "hello"}],
            "new_message": {"role": "user", "content": "hello"},
        }
    ).encode()
    request = urllib.request.Request(
        f"{base}/invoke", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def _ask(base):
    body = json.dumps(
        {
            "thread_id": "t1",
            "messages": [{"role": "user", "content": "hello"}],
            "new_message": {"role": "user", "content": "hello"},
        }
    ).encode()
    request = urllib.request.Request(
        f"{base}/invoke", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())["content"]


def test_a_block_list_reply_keeps_only_its_text(thinking_adapter):
    content = _ask(thinking_adapter)
    assert content == "The status is complete."
    assert "signature" not in content and "thinking" not in content


def test_the_adapter_runs_against_an_fi_simulate_without_agent_content(tmp_path):
    """The agent's own venv holds a published fi.simulate that only exports AgentInput."""
    served = _serve(tmp_path, "chat_agent:thinking_reply", stub_fi=True)
    try:
        assert _ask(next(served)) == "The status is complete."
    finally:
        next(served, None)


def test_a_callback_that_returns_graph_state_keeps_the_last_message_text(state_adapter):
    content = _ask(state_adapter)
    assert content == "The status is complete."


def test_a_callback_that_returns_a_message_list_keeps_the_assistant_text(
    message_list_adapter,
):
    assert _ask(message_list_adapter) == "The status is complete."


def test_a_callback_dict_with_block_content_sends_its_text_and_keeps_its_other_keys(
    tmp_path,
):
    served = _serve(tmp_path, "chat_agent:content_dict_reply")
    try:
        body = _ask_body(next(served))
        assert body["content"] == "The status is complete."
        assert body["tool_calls"] == [{"id": "c1"}]
    finally:
        next(served, None)


def test_a_callback_that_returns_graph_state_keeps_its_other_fields(tmp_path):
    served = _serve(tmp_path, "chat_agent:object_state_reply")
    try:
        body = _ask_body(next(served))
        assert body["content"] == "The status is complete."
        assert body["tool_calls"] == [{"id": "c1"}]
        assert body["state"] == {"stage": "done"}
        # Raw message objects and anything else that is not JSON stay out of the response.
        assert "messages" not in body and "handle" not in body
    finally:
        next(served, None)


@pytest.mark.parametrize("entrypoint", ["block_dict_twice", "bare_dict_twice"])
def test_a_callback_that_returns_the_same_dict_twice_is_answered_the_same_and_left_unchanged(
    tmp_path, entrypoint
):
    served = _serve(tmp_path, f"chat_agent:{entrypoint}")
    try:
        base = next(served)
        first, second = _ask_body(base), _ask_body(base)
        assert first == second
        assert first["tool_calls"] == [{"id": "c1"}]
        assert _ask_body(base)["content"] == "intact"
    finally:
        next(served, None)


# --- The adapter's reply body: graph state and a returned message, loaded as a module. ---

BLOCK_THINKING = {
    "type": "thinking",
    "thinking": "plan the reply",
    "signature": "sig-abc",
}


@pytest.fixture
def adapter_module(tmp_path, monkeypatch):
    (tmp_path / "cb_mod.py").write_text("def reply(agent_input):\n    return ''\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ALK_CALLBACK_ENTRYPOINT", "cb_mod:reply")
    spec = importlib.util.spec_from_file_location("cb_adapter_module", ADAPTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_graph_state_with_no_messages_is_an_empty_answer(adapter_module):
    body = adapter_module._json_body({"messages": [], "status": "done"})
    assert body == {"status": "done", "content": ""}


def test_a_callback_message_sends_only_its_text(adapter_module):
    message = {
        "role": "assistant",
        "content": [BLOCK_THINKING, {"type": "text", "text": "Done."}],
    }
    assert adapter_module._json_body({"message": message}) == {"content": "Done."}


class _Model:
    """A pydantic-like return value whose model_dump is a graph state."""

    def __init__(self, state):
        self._state = state

    def model_dump(self, **_):
        return self._state


def test_a_model_whose_dump_is_a_graph_state_gets_the_graph_state_answer(
    adapter_module,
):
    state = {
        "messages": [
            {"role": "user", "content": "q"},
            {
                "type": "ai",
                "content": [BLOCK_THINKING, {"type": "text", "text": "Done."}],
            },
        ],
        "status": "ok",
    }
    body = adapter_module._json_body(_Model(state))
    assert body == {"status": "ok", "content": "Done."}


def test_a_model_whose_dump_has_content_is_sent_as_it_is(adapter_module):
    body = adapter_module._json_body(_Model({"content": "Hi", "state": {"a": 1}}))
    assert body == {"content": "Hi", "state": {"a": 1}}
