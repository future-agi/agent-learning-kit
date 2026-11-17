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


def test_a_callback_that_returns_a_message_list_keeps_the_assistant_text(message_list_adapter):
    assert _ask(message_list_adapter) == "The status is complete."
