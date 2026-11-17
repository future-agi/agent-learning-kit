"""The hosted callback adapter's reply body: graph state and a returned message."""

import importlib.util
from pathlib import Path

import pytest

ADAPTER = (
    Path(__file__).resolve().parents[2] / "src/fi/alk/harness/callback_http_adapter.py"
)
THINKING = {"type": "thinking", "thinking": "plan the reply", "signature": "sig-abc"}


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    (tmp_path / "cb_mod.py").write_text("def reply(agent_input):\n    return ''\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ALK_CALLBACK_ENTRYPOINT", "cb_mod:reply")
    spec = importlib.util.spec_from_file_location("cb_review2", ADAPTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_graph_state_with_no_messages_is_an_empty_answer(adapter):
    body = adapter._json_body({"messages": [], "status": "done"})
    assert body == {"status": "done", "content": ""}


def test_a_callback_message_sends_only_its_text(adapter):
    message = {
        "role": "assistant",
        "content": [THINKING, {"type": "text", "text": "Done."}],
    }
    assert adapter._json_body({"message": message}) == {"content": "Done."}
