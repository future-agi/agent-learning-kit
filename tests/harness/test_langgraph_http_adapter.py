from __future__ import annotations

import importlib
import sys
from pathlib import Path


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
