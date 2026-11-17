from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


def test_declared_graph_receives_chat_and_returns_report(
    tmp_path: Path, monkeypatch,
) -> None:
    source = tmp_path / "source"
    package = source / "src" / "fixture_graph"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "graph.py").write_text(
        "class Graph:\n"
        "    async def ainvoke(self, state):\n"
        "        assert state['messages'][-1]['content'] == 'Research this'\n"
        "        return {'final_report': 'Finished report'}\n"
        "graph = Graph()\n"
    )
    monkeypatch.chdir(source)
    monkeypatch.setenv("ALK_LANGGRAPH_ENTRYPOINT", "src/fixture_graph/graph.py:graph")
    sys.modules.pop("fi.alk.harness.langgraph_http_adapter", None)
    adapter = importlib.import_module("fi.alk.harness.langgraph_http_adapter")

    try:
        assert adapter._invoke({"new_message": {"content": "Research this"}}) == {
            "content": "Finished report"
        }
    finally:
        sys.modules.pop("fi.alk.harness.langgraph_http_adapter", None)
        sys.modules.pop("fixture_graph.graph", None)
        sys.modules.pop("fixture_graph", None)
        sys.path.remove(str(source / "src"))


def _load_adapter(tmp_path: Path, monkeypatch, graph_source: str):
    source = tmp_path / "source"
    package = source / "src" / "fixture_graph"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "graph.py").write_text(graph_source)
    monkeypatch.chdir(source)
    monkeypatch.setenv("ALK_LANGGRAPH_ENTRYPOINT", "src/fixture_graph/graph.py:graph")
    sys.modules.pop("fi.alk.harness.langgraph_http_adapter", None)
    adapter = importlib.import_module("fi.alk.harness.langgraph_http_adapter")

    def cleanup() -> None:
        sys.modules.pop("fi.alk.harness.langgraph_http_adapter", None)
        sys.modules.pop("fixture_graph.graph", None)
        sys.modules.pop("fixture_graph", None)
        sys.path.remove(str(source / "src"))

    return adapter, cleanup


def test_a_block_list_reply_keeps_only_its_text(tmp_path: Path, monkeypatch) -> None:
    adapter, cleanup = _load_adapter(
        tmp_path,
        monkeypatch,
        "class Message:\n"
        "    content = [\n"
        "        {'type': 'thinking', 'thinking': 'hmm', 'signature': 'sig-abc'},\n"
        "        {'type': 'text', 'text': 'The status '},\n"
        "        {'type': 'text', 'text': 'is complete.'},\n"
        "    ]\n"
        "class Graph:\n"
        "    async def ainvoke(self, state):\n"
        "        return {'messages': [Message()]}\n"
        "graph = Graph()\n",
    )
    try:
        assert adapter._invoke({"new_message": {"content": "hi"}}) == {
            "content": "The status is complete."
        }
    finally:
        cleanup()


@pytest.mark.parametrize(
    "content",
    ["[]", "[{'type': 'thinking', 'thinking': 'hmm', 'signature': 'sig-abc'}]", "''"],
    ids=["empty-list", "only-thinking", "empty-string"],
)
def test_a_reply_with_no_text_is_an_empty_answer_whatever_its_form(tmp_path: Path, monkeypatch, content) -> None:
    adapter, cleanup = _load_adapter(
        tmp_path,
        monkeypatch,
        "class Message:\n"
        f"    content = {content}\n"
        "class Graph:\n"
        "    async def ainvoke(self, state):\n"
        "        return {'messages': [Message()]}\n"
        "graph = Graph()\n",
    )
    try:
        assert adapter._invoke({"new_message": {"content": "hi"}}) == {"content": ""}
    finally:
        cleanup()
