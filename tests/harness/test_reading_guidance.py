"""What the reading is told about `unreachable`, `module` and a LangGraph app (TH-8800, TH-8810)."""

from __future__ import annotations

import re

from fi.alk.harness.config import SKILLS_ROOT
from fi.alk.harness.tools import contract_tools


def _entrypoint_properties(tmp_path):
    schema = contract_tools(tmp_path).tools[0].input_schema
    return schema["properties"]["tool_entrypoints"]["items"]["properties"]


def _flat(text: str) -> str:
    return " ".join(text.split())


def _skill() -> str:
    return _flat((SKILLS_ROOT / "understand-agent" / "SKILL.md").read_text(encoding="utf-8"))


def test_the_mode_text_says_what_unreachable_now_means(tmp_path):
    said = _flat(_entrypoint_properties(tmp_path)["mode"]["description"])
    unreachable = said[said.index("unreachable:") :]
    assert "can't call it" in unreachable
    assert "traces it when `module` and `callable` say where it is" in unreachable
    assert "never generating a replacement" in unreachable or "rather than generating" in unreachable


def test_the_module_text_roots_the_name_where_the_process_starts(tmp_path):
    said = _flat(_entrypoint_properties(tmp_path)["module"]["description"]).lower()
    assert "the dotted name the agent's own process imports" in said
    assert "from the folder it runs in" in said
    assert "not a filesystem path" in said


def test_skill_item_10_says_a_framework_held_tool_still_names_where_it_is():
    item = _skill()
    start = item.index("10. **Whether its tools have code")
    item = item[start : item.index("11. **How its code says no")]
    assert "dotted name the agent's own process imports, from the folder it runs in" in item
    assert re.search(r"`unreachable`.*`module` and `callable`", item)
    assert "the kit can't call it, but watches it when it knows where it is" in item
    assert "`import`" in item and "`construct`" in item


def test_skill_item_10_keeps_langchain_and_livekit_tools_in_their_modes():
    # Real readings on 17 Nov moved a LangChain `@tool` and LiveKit `@function_tool` methods to
    # `unreachable` under wording that named "`@function_tool`" and "a LangChain `@tool`": the
    # only exception is the OpenAI Agents SDK's FunctionTool.
    item = _skill()
    item = item[item.index("10. **Whether its tools have code") : item.index("11. **How its code says no")]
    assert "a LangChain `@tool`, and a LiveKit `@function_tool` method on an `Agent` class all keep those modes" in item
    assert "the OpenAI Agents SDK's `@function_tool` (imported from `agents`)" in item
    assert "LangChain `@tool` with no plain function" not in item


def test_the_mode_text_keeps_langchain_and_livekit_tools_in_their_modes(tmp_path):
    said = _flat(_entrypoint_properties(tmp_path)["mode"]["description"])
    assert "OpenAI Agents SDK's `@function_tool`" in said
    assert "A LangChain `@tool` or a LiveKit `@function_tool` method stays `import` or `construct`" in said


def test_skill_tells_a_langgraph_app_without_the_cli_to_leave_the_command_empty():
    text = _skill()
    assert "`langgraph.json`" in text
    assert re.search(r"no `langgraph-cli`.*leave `runtime.command` empty", text)
    assert "`execution` to `auto`" in text
    # A Compose file or a Dockerfile may install the CLI where the kit can't see it.
    assert "and no Compose file or Dockerfile of the app installs it" in " ".join(text.split())
    # The save refuses a chat contract with no interface (missing-chat-interface); a reading on
    # 17 Nov told to write none was refused nine times and never saved.
    assert "declare the interface as `callable` with protocol `fi.alk`" in text
    assert "write no interface" not in text
    assert "the kit serves the graph" in text
    # Scoped to that shape: an app that keeps its command can still map the server's ingress.
    assert "In that no-CLI shape, don't map the LangGraph server's `/runs/wait` with a JSON template" in text
    assert (
        "An app that installs `langgraph-cli`, or whose `langgraph.json` has an `http` or `auth` "
        "key or declares more than one graph, keeps its command and its HTTP interface, and may "
        "map `/runs/wait`"
    ) in text
    assert not re.search(r"(?<!In that no-CLI shape, )[Dd]on't map the LangGraph server's `/runs/wait`", text)
