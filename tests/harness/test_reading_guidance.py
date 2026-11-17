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
    assert "plain function" in item and "`import`" in item


def test_skill_tells_a_langgraph_app_without_the_cli_to_leave_the_command_empty():
    text = _skill()
    assert "`langgraph.json`" in text
    assert re.search(r"no `langgraph-cli`.*leave `runtime.command` empty", text)
    assert "`execution` to `auto`" in text
    assert "write no interface" in text
    assert "the kit serves the graph" in text
