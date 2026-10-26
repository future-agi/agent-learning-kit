from pathlib import Path

import pytest

from fi.alk.harness.python_tool_trace import TraceBindingError, trace_bindings


def resolve(
    root: Path, callable_name: str, module: str = "agent", mode: str = "import"
):
    return trace_bindings(
        {
            "tool_entrypoints": [
                {
                    "tool": "lookup",
                    "module": module,
                    "callable": callable_name,
                    "mode": mode,
                }
            ]
        },
        root,
        root,
    )


@pytest.mark.parametrize(
    "method,expected", [("invoke", "sync_impl"), ("ainvoke", "async_impl")]
)
def test_structured_tool_selects_execution_function(tmp_path, method, expected):
    (tmp_path / "agent.py").write_text(
        "from langchain_core.tools import StructuredTool\ndef sync_impl(): pass\nasync def async_impl(): pass\nlookup = StructuredTool.from_function(func=sync_impl, coroutine=async_impl)\n"
    )
    assert resolve(tmp_path, f"lookup.{method}")[0]["callable"] == expected


def test_async_structured_tool_falls_back_to_sync(tmp_path):
    (tmp_path / "agent.py").write_text(
        "from langchain_core.tools import StructuredTool\ndef sync_impl(): pass\nlookup = StructuredTool.from_function(func=sync_impl)\n"
    )
    assert resolve(tmp_path, "lookup.ainvoke")[0]["callable"] == "sync_impl"


def test_positional_async_structured_tool(tmp_path):
    (tmp_path / "agent.py").write_text(
        "from langchain_core.tools import StructuredTool\ndef sync_impl(): pass\nasync def async_impl(): pass\nlookup = StructuredTool.from_function(sync_impl, async_impl)\n"
    )
    assert resolve(tmp_path, "lookup.ainvoke")[0]["callable"] == "async_impl"


@pytest.mark.parametrize(
    "source",
    [
        "from langchain_core.tools import StructuredTool\ndef impl(): pass\nimpl = lambda: None\nlookup = StructuredTool.from_function(impl)\n",
        "from langchain_core.tools import tool\ntool = lambda f: object()\n@tool\ndef lookup(): pass\n",
    ],
)
def test_rebound_factory_or_function_is_not_proven(tmp_path, source):
    (tmp_path / "agent.py").write_text(source)
    with pytest.raises(TraceBindingError, match="unobservable"):
        resolve(tmp_path, "lookup.invoke")


@pytest.mark.parametrize(
    "mode,callable_name", [("import", "lookup"), ("construct", "Tool.lookup")]
)
def test_package_source_uses_init_file(tmp_path, mode, callable_name):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text(
        "def lookup(): pass\nclass Tool:\n    def lookup(self): pass\n"
    )
    assert resolve(tmp_path, callable_name, "pkg", mode)[0]["module"] == "pkg.__init__"


@pytest.mark.parametrize(
    "definition",
    [
        "from langchain_core.tools import tool\n@tool\ndef lookup(): pass\n",
        "class Tool:\n    def invoke(self): pass\nlookup = Tool()\n",
        "from other import lookup\n",
    ],
)
def test_replaced_tool_cannot_trace_old_binding(tmp_path, definition):
    (tmp_path / "agent.py").write_text(definition + "lookup = object()\n")
    with pytest.raises(TraceBindingError, match="unobservable"):
        resolve(tmp_path, "lookup.invoke")
