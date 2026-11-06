from __future__ import annotations

import io
import json
import logging
import os
import shutil
import stat
import sys
import threading
import tokenize
import tracemalloc
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fi.alk.harness import framework_detection as fd
from fi.alk.harness.framework_detection import (
    Finding,
    FrameworkFolders,
    detect_framework,
    folder_findings,
    framework_folders_for,
)
from fi.alk.harness.job import RepositorySource, SourceKind, SourceVisibility
from fi.alk.harness.source_folder import SubfolderNotFound

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "frameworks"
CREW = "from crewai import Crew\nCrew()\n"
PLAIN = "print('hello')\n"
LONG_LINE = "x = '" + "a" * 70_000 + "'\n"
ROT13 = "# coding: rot13\n"
UNKNOWN = {"name": "unknown", "version": None}
NONE = {"name": "none", "version": None}


def _write(folder: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _named(name: str, label: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"name": name, "version": None}
    if label is not None:
        result["label"] = label
    return result


def _dist(package_dir: Path, dist: str, metadata: bytes | None = None) -> Path:
    folder = (
        package_dir
        / ".venv"
        / "lib"
        / "python3.12"
        / "site-packages"
        / f"{dist}.dist-info"
    )
    folder.mkdir(parents=True)
    if metadata is not None:
        (folder / "METADATA").write_bytes(metadata)
    return folder


def _metadata(version: str) -> bytes:
    return f"Metadata-Version: 2.1\nName: x\nVersion: {version}\n".encode()


@pytest.mark.parametrize(
    ("example", "expected"),
    [
        ("langchain_agent.py", _named("langchain")),
        ("langgraph_agent.py", _named("langgraph")),
        ("crewai_agent.py", _named("crewai")),
        ("google_adk_agent.py", _named("google_adk")),
        ("claude_agent_sdk_agent.py", _named("claude_agent_sdk")),
        ("openai_agents_agent.py", _named("openai_agents")),
        ("pydantic_ai_agent.py", _named("pydantic_ai")),
        ("llamaindex_agent.py", _named("other", "llama_index")),
        ("autogen_agent.py", _named("other", "autogen")),
        ("semantic_kernel_agent.py", _named("other", "semantic_kernel")),
        ("haystack_agent.py", _named("other", "haystack")),
        ("agno_agent.py", _named("other", "agno")),
        ("beeai_agent.py", _named("other", "beeai_framework")),
        ("dspy_agent.py", _named("other", "dspy")),
        ("smolagents_agent.py", _named("other", "smolagents")),
        ("strands_agent.py", _named("other", "strands")),
        ("acme_thirdparty_agent.py", NONE),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_examples_report_their_framework(
    tmp_path: Path, example: str, expected: dict[str, Any]
) -> None:
    shutil.copy(EXAMPLES / example, tmp_path / example)

    assert detect_framework(tmp_path) == expected


@pytest.mark.parametrize(
    ("source", "name"),
    [
        (
            "from livekit.agents import AgentSession, cli\n"
            "cli.run_app(AgentSession())\n",
            "livekit",
        ),
        (
            "from pipecat.pipeline.pipeline import Pipeline\nPipeline([])\n",
            "pipecat",
        ),
        (
            "from deepagents import create_deep_agent\ncreate_deep_agent()\n",
            "deepagents",
        ),
        (
            "from langgraph_supervisor import create_supervisor\ncreate_supervisor()\n",
            "langgraph",
        ),
        ("from claude_code_sdk import query\nquery()\n", "claude_agent_sdk"),
    ],
    ids=["livekit", "pipecat", "deepagents", "langgraph_star", "claude_code_sdk"],
)
def test_every_framework_is_detected(tmp_path: Path, source: str, name: str) -> None:
    _write(tmp_path, {"agent.py": source})

    assert detect_framework(tmp_path) == _named(name)


LIVEKIT = "from livekit.agents import AgentSession\nAgentSession()\n"
PIPECAT = "from pipecat.pipeline.pipeline import Pipeline\nPipeline([])\n"
LANGGRAPH = "from langgraph.graph import StateGraph\nStateGraph(int)\n"
DEEP = "from deepagents import create_deep_agent\ncreate_deep_agent()\n"
LANGCHAIN = "from langchain.agents import create_agent\ncreate_agent()\n"
CHAT_OPENAI = "from langchain_openai import ChatOpenAI\nChatOpenAI()\n"
OPENAI_AGENT = "from agents import Agent\nAgent()\n"
AUTOGEN = "from autogen_agentchat.agents import AssistantAgent\nAssistantAgent()\n"
DSPY = "import dspy\ndspy.Predict('q')\n"
CREW_AGENT = "from crewai import Agent\n"
PYDANTIC_AGENT = "from pydantic_ai import Agent\n"


@pytest.mark.parametrize(
    ("sources", "expected"),
    [
        ((CREW, CHAT_OPENAI), _named("crewai")),
        ((LIVEKIT, LANGGRAPH), _named("livekit")),
        ((LIVEKIT, DEEP), _named("livekit")),
        ((LIVEKIT, LANGCHAIN), _named("livekit")),
        ((PIPECAT, LANGGRAPH), _named("pipecat")),
        ((LANGGRAPH, LANGCHAIN), _named("langgraph")),
        ((DEEP, LANGCHAIN), _named("deepagents")),
        ((LIVEKIT, PIPECAT), UNKNOWN),
        ((CREW, OPENAI_AGENT), UNKNOWN),
        ((DEEP, LANGGRAPH), _named("deepagents")),
        ((LANGGRAPH, CHAT_OPENAI), _named("langgraph")),
        ((AUTOGEN, CHAT_OPENAI), _named("other", "autogen")),
        ((AUTOGEN, DSPY), UNKNOWN),
        (("from langchain_core.messages import HumanMessage\n",), UNKNOWN),
        (
            (
                "from openai import OpenAI\nfrom anthropic import Anthropic\n"
                "OpenAI()\nAnthropic()\n",
            ),
            NONE,
        ),
        (
            (
                "from langchain.schema import ChatMessage\n"
                "from langchain.prompts import ChatPromptTemplate\n"
                "from openai import OpenAI\n"
                "ChatMessage()\nChatPromptTemplate()\nOpenAI()\n",
            ),
            UNKNOWN,
        ),
        (
            ("from langchain_openai import ChatOpenAI\nChatOpenAI().bind_tools([])\n",),
            _named("langchain"),
        ),
        (("from crewai import *\nCrew()\n",), UNKNOWN),
        (
            ("from importlib import import_module\nimport_module('crewai')\n",),
            NONE,
        ),
        ((CREW_AGENT + PYDANTIC_AGENT + "Agent()\n",), UNKNOWN),
        ((PYDANTIC_AGENT + CREW_AGENT + "Agent()\n",), UNKNOWN),
    ],
    ids=[
        "crew_and_model",
        "livekit_and_langgraph",
        "livekit_and_deepagents",
        "livekit_and_langchain",
        "pipecat_and_langgraph",
        "langgraph_and_langchain",
        "deepagents_and_langchain",
        "livekit_and_pipecat",
        "crew_and_openai_agents",
        "deepagents_and_langgraph",
        "langgraph_and_model",
        "other_and_model",
        "two_others",
        "unused_import",
        "direct_model_calls",
        "chat_message_and_prompt",
        "model_alone",
        "star_import",
        "dynamic_import",
        "one_name_two_frameworks",
        "one_name_two_frameworks_reversed",
    ],
)
def test_decision_table(
    tmp_path: Path, sources: tuple[str, ...], expected: dict[str, Any]
) -> None:
    _write(tmp_path, {f"m{index}.py": s for index, s in enumerate(sources)})

    assert detect_framework(tmp_path) == expected


CREW_IMPORT = ("crewai", "import")
CREW_BUILDS = ("crewai", "builds")
TYPE_ONLY_IMPORT = "from typing import TYPE_CHECKING\n{}    from crewai import Crew\n"
LANGCHAIN_IMPORT = ("langchain", "import")
TYPE_CHECKING_LINE = "from typing import TYPE_CHECKING\n"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "from langgraph.graph import StateGraph as SG\nSG(int)\n",
            {("langgraph", "import"), ("langgraph", "builds")},
        ),
        (
            'from pydantic_ai import Agent\nAgent[int]("m")\n',
            {("pydantic_ai", "import"), ("pydantic_ai", "builds")},
        ),
        (
            "def f():\n    from crewai import Crew\n    Crew()\n",
            {CREW_IMPORT, CREW_BUILDS},
        ),
        (
            "try: import crewai\nexcept ImportError: crewai = None\ncrewai.Crew()\n",
            {CREW_IMPORT, CREW_BUILDS},
        ),
        (
            "from crewai import Crew\nclass C(Crew):\n    pass\n",
            {CREW_IMPORT, CREW_BUILDS},
        ),
        (
            "from crewai import CrewBase\n@CrewBase\nclass C:\n    pass\n",
            {CREW_IMPORT, CREW_BUILDS},
        ),
        (
            "from langgraph.func import entrypoint\n"
            "@entrypoint()\ndef f(x):\n    return x\n",
            {("langgraph", "import"), ("langgraph", "builds")},
        ),
        (
            TYPE_CHECKING_LINE + "if TYPE_CHECKING:\n    from crewai import Crew\n",
            set(),
        ),
        (
            "import typing\nif typing.TYPE_CHECKING:\n    from crewai import Crew\n",
            set(),
        ),
        (
            TYPE_CHECKING_LINE
            + "if TYPE_CHECKING:\n    import os\nelse:\n    from crewai import Crew\n"
            + "Crew()\n",
            {CREW_IMPORT, CREW_BUILDS},
        ),
        ("from .crewai import Crew\nCrew()\n", set()),
        ("from . import crewai\ncrewai.Crew()\n", set()),
        ("from ... import crewai\ncrewai.Crew()\n", set()),
        ("from ...crewai import Crew\nCrew()\n", set()),
        (
            "from crewai import (\n    Agent,\n    Crew as C,\n)\nC()\n",
            {CREW_IMPORT, CREW_BUILDS},
        ),
        ("import os, crewai\ncrewai.Crew()\n", {CREW_IMPORT, CREW_BUILDS}),
        ("import os; import crewai\ncrewai.Crew()\n", {CREW_IMPORT, CREW_BUILDS}),
        (
            "import google.adk\ngoogle.adk.agents.Agent()\n",
            {("google_adk", "import"), ("google_adk", "builds")},
        ),
        ("from crewai import Crew\ndef Crew(x):\n    return x\n", {CREW_IMPORT}),
        ("from crewai import Crew\nclass Crew:\n    pass\n", {CREW_IMPORT}),
        ('from crewai import Crew\n# Crew()\nx = "Crew()"\n', {CREW_IMPORT}),
        ("from crewai import Crew\nf(Crew=1)\n", {CREW_IMPORT}),
        ("from crewai import Crew\nx = a @ Crew\n", {CREW_IMPORT}),
        (
            "from crewai import Crew\nclass C(Base, metaclass=Crew):\n    pass\n",
            {CREW_IMPORT},
        ),
        ("import dspy\nclass Q(dspy.Module):\n    pass\n", {("dspy", "import")}),
        (
            "from langchain_openai import AzureChatOpenAI\nAzureChatOpenAI()\n",
            {("langchain", "import"), ("langchain", "model")},
        ),
        (
            'from langchain.chat_models import init_chat_model\ninit_chat_model("m")\n',
            {("langchain", "import"), ("langchain", "model")},
        ),
        ('from crewai import Crew\nx = f"{Crew()}"\n', {CREW_IMPORT}),
        (
            "print(f'{x}')\nfrom crewai import Crew\nCrew()\n",
            {CREW_IMPORT, CREW_BUILDS},
        ),
        ("from crewai import Crew\nself.Crew()\n", {CREW_IMPORT}),
        (
            "import google.adk.agents as g\ng.LlmAgent()\n",
            {("google_adk", "import"), ("google_adk", "builds")},
        ),
        ("from langchain_core.x import ChatFoo\nChatFoo()\n", {LANGCHAIN_IMPORT}),
        (
            "from langchain_core.outputs import ChatGeneration\n"
            "from langchain.x import ChatGeneration as C\nC()\n",
            {LANGCHAIN_IMPORT},
        ),
        ("from langchain.x import ChatResult\nChatResult()\n", {LANGCHAIN_IMPORT}),
        ("def f():\n    yield from crewai\n", set()),
        ("raise E from crewai\n", set()),
        (
            "from crewai import Crew\nclass C[T](Crew):\n    pass\n",
            {CREW_IMPORT, CREW_BUILDS},
        ),
        (
            "from crewai import Crew\nclass C[T: (A, B)](Crew):\n    pass\n",
            {CREW_IMPORT, CREW_BUILDS},
        ),
        ("from crewai import Crew\nclass C: x = (Crew)\n", {CREW_IMPORT}),
        (
            TYPE_ONLY_IMPORT.format("if TYPE_CHECKING:  # pragma: no cover\n"),
            set(),
        ),
        (
            TYPE_ONLY_IMPORT.format(
                "try:\n    import os\nexcept E:\n    pass\nif TYPE_CHECKING:\n"
            ),
            set(),
        ),
        (
            TYPE_ONLY_IMPORT.format(
                "if TYPE_CHECKING:\n    if x:\n        import os\n"
            ),
            set(),
        ),
        (
            "from crewai import Crew\nclass C(\n    Crew,\n):\n    pass\n",
            {CREW_IMPORT, CREW_BUILDS},
        ),
        ("from crewai import Crew\nclass C(A): x = (Crew)\n", {CREW_IMPORT}),
        ("from crewai import Crew\nclass C(make(Crew)):\n    pass\n", {CREW_IMPORT}),
        ("from crewai import Crew\nx = f\"{f'{a}'} {Crew()}\"\n", {CREW_IMPORT}),
    ],
    ids=[
        "alias",
        "subscript_then_call",
        "import_inside_function",
        "import_after_a_block_colon",
        "class_base",
        "decorator",
        "decorator_with_call",
        "type_checking_block",
        "dotted_type_checking_block",
        "type_checking_else",
        "relative_module",
        "relative_from_dot",
        "relative_from_ellipsis",
        "relative_ellipsis_module",
        "parenthesised",
        "comma_import",
        "semicolon_import",
        "dotted_use",
        "def_name",
        "class_name",
        "comment_and_string",
        "keyword_name",
        "matrix_multiply",
        "metaclass_value",
        "other_row_base_alone",
        "azure_model",
        "init_chat_model",
        "inside_fstring",
        "after_fstring",
        "method_of_another_object",
        "dotted_alias",
        "langchain_core_model_excluded",
        "chat_generation_excluded",
        "chat_result_excluded",
        "yield_from",
        "raise_from",
        "generic_class_base",
        "generic_class_constraint_brackets",
        "class_body_on_one_line",
        "type_checking_with_a_comment",
        "type_checking_after_a_block",
        "type_checking_with_a_nested_block",
        "base_on_its_own_line",
        "bracket_in_a_one_line_class_body",
        "call_inside_the_bases",
        "after_nested_fstring",
    ],
)
def test_findings_rules(
    tmp_path: Path, source: str, expected: set[tuple[str, str]]
) -> None:
    _write(tmp_path, {"agent.py": source})

    found = folder_findings(tmp_path)

    assert {(f.framework, f.strength) for f in found.findings} == expected


def test_one_finding_per_file_framework_strength(tmp_path: Path) -> None:
    _write(tmp_path, {"agent.py": "from crewai import Crew\nCrew()\nCrew()\nCrew()\n"})

    assert folder_findings(tmp_path).findings == (
        Finding("crewai", "import", "agent.py", 1),
        Finding("crewai", "builds", "agent.py", 2),
    )


def test_findings_same_when_a_limit_stops_the_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, {"a.py": CREW, "b.py": PIPECAT, "z/c.py": LANGGRAPH})
    monkeypatch.setattr(fd, "_MAX_FILES", 1)
    in_order = folder_findings(tmp_path)
    real_walk = os.walk

    def reversed_walk(*args: Any, **kwargs: Any) -> Any:
        for directory, directories, files in real_walk(*args, **kwargs):
            directories.reverse()
            files.reverse()
            yield directory, directories, files

    monkeypatch.setattr(fd.os, "walk", reversed_walk)

    assert in_order.over_limit
    assert folder_findings(tmp_path) == in_order
    assert [f.file for f in in_order.findings] == ["a.py", "a.py"]


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"main.py": PLAIN, "build/x.py": CREW}, NONE),
        ({"main.py": PLAIN, "dist/x.py": CREW}, NONE),
        ({"main.py": PLAIN, "env/x.py": CREW}, NONE),
        ({"pkg/build/x.py": CREW}, _named("crewai")),
        ({"pkg/dist/x.py": CREW}, _named("crewai")),
        ({"pkg/env/x.py": CREW}, _named("crewai")),
        ({"main.py": PLAIN, "pkg/node_modules/x.py": CREW}, NONE),
        ({"main.py": PLAIN, "pkg/site-packages/x.py": CREW}, NONE),
        (
            {
                "main.py": PLAIN,
                "lib/x.py": CREW,
                "lib/crewai-1.0.dist-info/METADATA": "",
            },
            NONE,
        ),
        (
            {
                "main.py": CREW,
                "crewai/__init__.py": "",
                "crewai-1.0.dist-info/METADATA": "",
            },
            _named("crewai"),
        ),
        (
            {"main.py": PLAIN, "crewai-1.0.dist-info/METADATA": ""},
            UNKNOWN,
        ),
    ],
    ids=[
        "build_at_top",
        "dist_at_top",
        "env_at_top",
        "build_deeper",
        "dist_deeper",
        "env_deeper",
        "node_modules_at_depth",
        "site_packages_at_depth",
        "dist_info_child_stops_descent",
        "pip_install_target_not_shadowed",
        "dist_info_at_top_blocks_none",
    ],
)
def test_walk_rules(
    tmp_path: Path, files: dict[str, str], expected: dict[str, Any]
) -> None:
    _write(tmp_path, files)

    assert detect_framework(tmp_path) == expected


def test_walk_rules_unreadable_directory_blocks_none(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads a mode-000 directory")
    _write(tmp_path, {"main.py": PLAIN, "locked/x.py": CREW})
    locked = tmp_path / "locked"
    locked.chmod(0)
    try:
        found = folder_findings(tmp_path)
        result = detect_framework(tmp_path)
    finally:
        locked.chmod(stat.S_IRWXU)

    assert fd._FILE_SKIPPED in found.blockers
    assert result == UNKNOWN


def test_walk_rules_symlinked_directory_blocks_none(tmp_path: Path) -> None:
    folder = tmp_path / "agent"
    _write(folder, {"main.py": PLAIN})
    _write(tmp_path, {"elsewhere/x.py": CREW})
    (folder / "link").symlink_to(tmp_path / "elsewhere", target_is_directory=True)

    assert fd._FILE_SKIPPED in folder_findings(folder).blockers
    assert detect_framework(folder) == UNKNOWN


def test_byte_order_mark_and_latin1_files_read(tmp_path: Path) -> None:
    (tmp_path / "bom.py").write_bytes(b"\xef\xbb\xbf" + CREW.encode())
    assert detect_framework(tmp_path) == _named("crewai")

    (tmp_path / "bom.py").unlink()
    (tmp_path / "latin.py").write_bytes(
        b"# -*- coding: latin-1 -*-\n# caf\xe9\n" + CREW.encode()
    )
    assert detect_framework(tmp_path) == _named("crewai")


def test_long_line_in_a_side_file_is_not_scanned(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {"main.py": PLAIN, "tests/x.py": "from crewai import Crew\n" + LONG_LINE},
    )

    assert detect_framework(tmp_path) == NONE


def test_long_line_blocks_none_only(tmp_path: Path) -> None:
    _write(tmp_path, {"main.py": PLAIN, "long.py": LONG_LINE})
    assert detect_framework(tmp_path) == UNKNOWN

    _write(tmp_path, {"crew.py": CREW})
    assert detect_framework(tmp_path) == _named("crewai")


@pytest.mark.parametrize(
    ("content", "skipped"),
    [
        (CREW + LONG_LINE, True),
        ("#" * fd._MAX_LINE_BYTES + "\n", False),
        ("#" * (fd._MAX_LINE_BYTES + 1) + "\n", True),
        (PLAIN + "#" * (fd._MAX_LINE_BYTES + 1), True),
    ],
    ids=[
        "after_a_short_line",
        "exactly_at_the_limit",
        "one_over",
        "last_with_no_newline",
    ],
)
def test_long_line_on_any_line(tmp_path: Path, content: str, skipped: bool) -> None:
    _write(tmp_path, {"main.py": content})

    found = folder_findings(tmp_path)

    assert (fd._FILE_SKIPPED in found.blockers) is skipped
    assert detect_framework(tmp_path) == (UNKNOWN if skipped else NONE)


def _in_thread(call: Any) -> Any:
    results: list[Any] = []
    thread = threading.Thread(target=lambda: results.append(call()), daemon=True)
    thread.start()
    thread.join(10)
    assert not thread.is_alive(), "detection blocked on a named pipe"
    return results[0]


def test_named_pipes_never_block(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    _write(plain, {"main.py": PLAIN})
    os.mkfifo(plain / "x.py")
    assert _in_thread(lambda: detect_framework(plain)) == UNKNOWN

    crew = tmp_path / "crew"
    _write(crew, {"main.py": CREW})
    package_dir = tmp_path / "package"
    os.mkfifo(_dist(package_dir, "crewai-1.0") / "METADATA")
    assert _in_thread(
        lambda: detect_framework(crew, package_dir=package_dir)
    ) == _named("crewai")


def test_symlinked_python_is_not_followed(tmp_path: Path) -> None:
    folder = tmp_path / "agent"
    folder.mkdir()
    _write(tmp_path, {"elsewhere.py": CREW})
    (folder / "x.py").symlink_to(tmp_path / "elsewhere.py")

    assert detect_framework(folder) == UNKNOWN


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"crew.py": CREW, "bad.py": ROT13}, _named("crewai")),
        ({"main.py": PLAIN, "bad.py": ROT13}, UNKNOWN),
        ({"agent.py": CREW + "x = '''\n"}, _named("crewai")),
        ({"agent.py": "x = (\n" + CREW}, _named("crewai")),
    ],
    ids=[
        "bad_neighbour_keeps_crew",
        "bad_neighbour_blocks_none",
        "error_after_use",
        "import_inside_an_open_bracket",
    ],
)
def test_failure_scanning_a_file_keeps_what_was_found(
    tmp_path: Path, files: dict[str, str], expected: dict[str, Any]
) -> None:
    _write(tmp_path, files)

    assert fd._FILE_UNREADABLE in folder_findings(tmp_path).blockers
    assert detect_framework(tmp_path) == expected


def test_failure_scanning_a_file_keeps_what_was_found_any_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, {"agent.py": CREW})
    real = fd._visible_tokens

    def fails_after_the_use(data: bytes, work: Any) -> Any:
        yield from real(data, work)
        raise SystemError("from the tokenizer")

    monkeypatch.setattr(fd, "_visible_tokens", fails_after_the_use)

    found = folder_findings(tmp_path)

    assert fd._FILE_UNREADABLE in found.blockers
    assert CREW_BUILDS in {(f.framework, f.strength) for f in found.findings}


def test_too_many_aliases_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fd, "_MAX_ALIASES", 1)
    _write(tmp_path, {"agent.py": "from crewai import Crew, Agent\n"})

    assert fd._FILE_UNREADABLE in folder_findings(tmp_path).blockers


@pytest.mark.parametrize(
    ("constant", "value"),
    [("_MAX_FILES", 1), ("_MAX_TOTAL_BYTES", 10)],
)
def test_total_limits_give_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, constant: str, value: int
) -> None:
    _write(tmp_path, {"a.py": CREW, "b.py": CREW})
    monkeypatch.setattr(fd, constant, value)

    assert folder_findings(tmp_path).over_limit
    assert detect_framework(tmp_path) == UNKNOWN


@pytest.mark.parametrize(
    ("constant", "value", "side_file"),
    [("_MAX_FILES", 1, PLAIN), ("_MAX_TOTAL_BYTES", 20, "#" * 50 + "\n")],
    ids=["files", "bytes"],
)
def test_total_limits_count_side_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
    value: int,
    side_file: str,
) -> None:
    _write(tmp_path, {"main.py": PLAIN, "tests/a.py": side_file})
    monkeypatch.setattr(fd, constant, value)

    assert folder_findings(tmp_path).over_limit


def test_limits_have_their_real_values() -> None:
    assert (
        fd._MAX_LINE_BYTES,
        fd._MAX_TOTAL_BYTES,
        fd._MAX_FILES,
        fd._MAX_WORK,
        fd._WORK_PER_TARGET,
        fd._MAX_ALIASES,
        fd._MAX_DOTTED_PARTS,
        fd._METADATA_BYTES,
        fd._VERSION_MAX,
    ) == (
        65_536,
        67_108_864,
        5_000,
        100_000_000_000,
        2_000,
        10_000,
        64,
        65_536,
        64,
    )


def test_read_stops_at_the_bytes_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, {"a.py": "x=1\n", "b.py": "y=2\n"})
    limits: list[int] = []
    real = fd._read_file

    def spy(path: str, limit: int) -> bytes:
        limits.append(limit)
        return real(path, limit)

    monkeypatch.setattr(fd, "_read_file", spy)

    folder_findings(tmp_path)

    assert limits == [fd._MAX_TOTAL_BYTES + 1, fd._MAX_TOTAL_BYTES - 4 + 1]


def test_tokenize_warnings_are_silenced(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], recwarn: pytest.WarningsRecorder
) -> None:
    _write(tmp_path, {"a.py": "x = f'\\{a}'\n" * 3})

    detect_framework(tmp_path)

    assert not recwarn.list
    assert "Warning" not in capfd.readouterr().err


def _token_work(source: str) -> int:
    tokens = tokenize.tokenize(io.BytesIO(source.encode()).readline)
    return sum(len(token.line) for token in tokens)


# Two reads, and two walks of its one target: the name in the import, then the call.
CREW_WORK = 2 * _token_work(CREW) + 2 * fd._WORK_PER_TARGET
# Comments and what's inside an f-string are never seen by the scan, but cost the same.
UNSEEN = "# note\nx = f'{a}'\n" + CREW
UNSEEN_WORK = 2 * _token_work(UNSEEN) + 2 * fd._WORK_PER_TARGET


@pytest.mark.parametrize(
    ("files", "work"),
    [
        ({"a.py": CREW}, CREW_WORK),
        ({"a.py": CREW, "b.py": CREW}, 2 * CREW_WORK),
        ({"main.py": CREW, "tests/a.py": PLAIN}, CREW_WORK + _token_work(PLAIN)),
        ({"a.py": UNSEEN}, UNSEEN_WORK),
    ],
    ids=[
        "one_target",
        "shared_across_files",
        "side_file_read_once",
        "tokens_the_scan_never_sees",
    ],
)
def test_work_limit_edge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    files: dict[str, str],
    work: int,
) -> None:
    _write(tmp_path, files)

    monkeypatch.setattr(fd, "_MAX_WORK", work)
    assert detect_framework(tmp_path) == _named("crewai")

    monkeypatch.setattr(fd, "_MAX_WORK", work - 1)
    assert folder_findings(tmp_path).over_limit
    assert detect_framework(tmp_path) == UNKNOWN


def test_work_limit_counts_every_target_of_a_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    imports = "".join(f"import crewai.m{index} as C\n" for index in range(10_000))
    source = imports + "C()\n" * 100
    limit = 1_000_000_000
    assert 2 * _token_work(source) < limit
    _write(tmp_path, {"agent.py": source})
    monkeypatch.setattr(fd, "_MAX_WORK", limit)

    found = folder_findings(tmp_path)

    assert found.over_limit
    assert fd._FILE_UNREADABLE not in found.blockers
    assert detect_framework(tmp_path) == UNKNOWN


def test_work_limit_counts_long_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    long_line = "x = [" + "a," * 30_000 + "]\n"
    assert len(long_line) <= fd._MAX_LINE_BYTES
    _write(tmp_path, {"agent.py": CREW + long_line * 2})
    monkeypatch.setattr(fd, "_MAX_WORK", 10_000_000)

    found = folder_findings(tmp_path)

    assert found.over_limit
    assert detect_framework(tmp_path) == UNKNOWN


def test_bytes_exactly_at_the_limit_are_not_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, {"a.py": CREW})
    monkeypatch.setattr(fd, "_MAX_TOTAL_BYTES", len(CREW))

    assert not folder_findings(tmp_path).over_limit


LONG_PART = b"p" * 65_000


def test_names_under_one_long_module_share_its_memory(tmp_path: Path) -> None:
    module = b"from crewai.\\\n" + (LONG_PART + b".\\\n") * 62 + LONG_PART
    names = b"".join(b"a%d,\n" % index for index in range(100))
    (tmp_path / "agent.py").write_bytes(module + b" import (\n" + names + b")\n")

    tracemalloc.start()
    try:
        found = folder_findings(tmp_path)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert peak < 50 * 1024 * 1024
    assert {(f.framework, f.strength) for f in found.findings} == {CREW_IMPORT}


def _aliases(source: str) -> dict[str, set[tuple[str, ...]]]:
    scan = fd._FileScan(frozenset(), frozenset(), fd._Work())
    scan.read_imports(
        fd._outside_type_checking(fd._visible_tokens(source.encode(), scan.work))
    )
    return scan.aliases


def test_a_target_keeps_at_most_the_part_limit() -> None:
    aliases = _aliases("import crewai." + "a." * 200 + "z as C\n")

    assert [len(target) for target in aliases["C"]] == [fd._MAX_DOTTED_PARTS]


def test_plain_dotted_import_maps_the_top_name_to_the_top_name() -> None:
    assert _aliases("import crewai.tools\n") == {"crewai": {("crewai",)}}


def test_use_inside_a_type_checking_block_does_not_count(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {"agent.py": "from crewai import Crew\nif TYPE_CHECKING:\n    Crew()\n"},
    )

    found = folder_findings(tmp_path)

    assert {(f.framework, f.strength) for f in found.findings} == {CREW_IMPORT}


def test_a_use_of_a_long_target_allocates_nothing_of_its_size() -> None:
    data = b"import crewai.\\\n" + (LONG_PART + b".\\\n") * 63 + LONG_PART + b" as C\n"
    scan = fd._FileScan(frozenset(), frozenset(), fd._Work())
    scan.read_imports(fd._outside_type_checking(fd._visible_tokens(data, scan.work)))

    tracemalloc.start()
    try:
        scan._resolve(["C"], "call", 1)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert peak < 64 * 1024


BLOCKER_CASES: list[tuple[str, dict[str, str], str, str]] = [
    (
        fd._FILE_SKIPPED,
        {"agent/main.py": PLAIN, "agent/long.py": LONG_LINE},
        "agent",
        "",
    ),
    (fd._FILE_UNREADABLE, {"agent/main.py": PLAIN, "agent/bad.py": ROT13}, "agent", ""),
    (
        fd._AGENTS_AMBIGUOUS,
        {"agent/main.py": OPENAI_AGENT, "agent/src/agents/x.py": PLAIN},
        "agent",
        "",
    ),
    (
        fd._SIDE_FOLDER_FRAMEWORK,
        {"agent/main.py": PLAIN, "agent/tests/x.py": CREW},
        "agent",
        "",
    ),
    (
        fd._OUTSIDE_IMPORT,
        {"agent/main.py": "import shared\n", "shared.py": PLAIN},
        "agent",
        ".",
    ),
    (
        fd._OUTSIDE_IMPORT,
        {"agent/main.py": "from shared import x\nimport os, tools\n", "tools/y.py": ""},
        "agent",
        ".",
    ),
    (fd._OTHER_SOURCE, {"agent/main.py": PLAIN, "agent/ui.ts": ""}, "agent", ""),
    (fd._OTHER_SOURCE, {"agent/main.py": PLAIN, "agent/ui.tsx": ""}, "agent", ""),
    (fd._OTHER_SOURCE, {"agent/main.py": PLAIN, "agent/x.mts": ""}, "agent", ""),
    (fd._OTHER_SOURCE, {"agent/main.py": PLAIN, "agent/x.cts": ""}, "agent", ""),
    (fd._OTHER_SOURCE, {"agent/main.py": PLAIN, "agent/x.js": ""}, "agent", ""),
    (fd._OTHER_SOURCE, {"agent/main.py": PLAIN, "agent/x.jsx": ""}, "agent", ""),
    (fd._OTHER_SOURCE, {"agent/main.py": PLAIN, "agent/x.mjs": ""}, "agent", ""),
    (fd._OTHER_SOURCE, {"agent/main.py": PLAIN, "agent/x.cjs": ""}, "agent", ""),
    (fd._OTHER_SOURCE, {"agent/main.py": PLAIN, "agent/n.ipynb": ""}, "agent", ""),
    (
        fd._OTHER_SOURCE,
        {"agent/main.py": PLAIN, "agent/root_agent.yaml": ""},
        "agent",
        "",
    ),
    (fd._NO_PYTHON, {"agent/README.md": ""}, "agent", ""),
]


@pytest.mark.parametrize(
    ("blocker", "files", "folder", "repo_root"),
    BLOCKER_CASES,
    ids=[
        "file_skipped",
        "file_unreadable",
        "agents_ambiguous",
        "side_folder_framework",
        "outside_import",
        "outside_import_from_and_comma",
        "other_source_ts",
        "other_source_tsx",
        "other_source_mts",
        "other_source_cts",
        "other_source_js",
        "other_source_jsx",
        "other_source_mjs",
        "other_source_cjs",
        "other_source_ipynb",
        "other_source_root_agent",
        "no_python",
    ],
)
def test_blockers_stop_none(
    tmp_path: Path, blocker: str, files: dict[str, str], folder: str, repo_root: str
) -> None:
    _write(tmp_path, files)
    root = tmp_path if repo_root else None

    assert blocker in folder_findings(tmp_path / folder, repo_root=root).blockers
    assert detect_framework(tmp_path / folder, repo_root=root) == UNKNOWN

    # Anything that blocks may hide what builds the agent, so a chat model names nothing.
    if blocker != fd._NO_PYTHON:
        _write(tmp_path, {f"{folder}/model.py": CHAT_OPENAI})
        found = folder_findings(tmp_path / folder, repo_root=root)
        assert any(f.strength == "model" for f in found.findings)
        assert detect_framework(tmp_path / folder, repo_root=root) == UNKNOWN

    # What the scan saw being built still names the framework.
    _write(tmp_path, {f"{folder}/crew.py": CREW})
    assert detect_framework(tmp_path / folder, repo_root=root) == _named("crewai")


@pytest.mark.parametrize(
    ("folder", "files", "blocked"),
    [
        ("repo/agent", {"repo/agent/main.py": "import agent\n"}, False),
        ("repo/a/b", {"repo/a/b/main.py": "import a\n"}, False),
        ("repo/agent", {"repo/agent/main.py": "from . import shared\n"}, False),
        (
            "repo/agent",
            {"repo/agent/main.py": "import shared\n", "repo/agent/shared.py": ""},
            False,
        ),
        ("copy", {"copy/main.py": "import agent\n", "repo/agent/x.py": ""}, True),
    ],
    ids=[
        "folder_itself",
        "folder_on_the_path",
        "relative",
        "under_the_folder",
        "folder_outside_the_root_excludes_nothing",
    ],
)
def test_blockers_stop_none_outside_import_scope(
    tmp_path: Path, folder: str, files: dict[str, str], blocked: bool
) -> None:
    _write(tmp_path, files)
    (tmp_path / "repo" / "shared.py").write_text(PLAIN, encoding="utf-8")
    (tmp_path / "repo" / "a").mkdir(exist_ok=True)
    (tmp_path / "repo" / "agent").mkdir(exist_ok=True)

    found = folder_findings(tmp_path / folder, repo_root=tmp_path / "repo")

    assert (fd._OUTSIDE_IMPORT in found.blockers) is blocked


def test_blockers_stop_none_no_outside_import_when_folder_is_the_root(
    tmp_path: Path,
) -> None:
    _write(tmp_path, {"main.py": "import shared\n", "shared.py": PLAIN})

    assert detect_framework(tmp_path, repo_root=tmp_path) == NONE


AGENT_JS = (
    "import { Agent, run } from '@openai/agents';\n"
    "const agent = new Agent({ name: 'x' });\n"
    "await run(agent, 'hi');\n"
)
HEALTHCHECK = "import urllib.request\n"


@pytest.mark.parametrize(
    "files",
    [
        {"tests/index.js": AGENT_JS, "healthcheck.py": HEALTHCHECK},
        {"tests/x.jsx": AGENT_JS, "healthcheck.py": HEALTHCHECK},
        {"examples/node/x.mjs": AGENT_JS, "healthcheck.py": HEALTHCHECK},
    ],
    ids=["js_in_tests", "jsx_in_tests", "mjs_in_examples"],
)
def test_javascript_in_test_and_example_folders_does_not_block(
    tmp_path: Path, files: dict[str, str]
) -> None:
    _write(tmp_path, files)

    assert fd._OTHER_SOURCE not in folder_findings(tmp_path).blockers
    assert detect_framework(tmp_path) == NONE


def test_a_label_beside_a_blocker_stays_other(tmp_path: Path) -> None:
    _write(tmp_path, {"main.py": AUTOGEN, "ui.js": ""})

    assert fd._OTHER_SOURCE in folder_findings(tmp_path).blockers
    assert detect_framework(tmp_path) == _named("other", "autogen")


SIDE_WORDS = [
    "tests",
    "test",
    "examples",
    "example",
    "samples",
    "sample",
    "demos",
    "demo",
    "docs",
    "doc",
    "evals",
    "eval",
    "scripts",
    "legacy",
    "notebooks",
    "vendor",
    "vendored",
    "third_party",
    "external",
    "contrib",
    "experiments",
    "benchmarks",
    "archive",
    ".hidden",
]
SIDE_PATHS = [f"{word}/crew.py" for word in SIDE_WORDS] + [
    "test_x.py",
    "x_test.py",
    "conftest.py",
    "pkg/tests/crew.py",
    ".x.py",
]


@pytest.mark.parametrize("path", SIDE_PATHS)
def test_side_folders_do_not_decide(tmp_path: Path, path: str) -> None:
    _write(tmp_path, {"main.py": PLAIN, path: CREW})

    found = folder_findings(tmp_path)

    assert found.findings == ()
    assert found.blockers == {fd._SIDE_FOLDER_FRAMEWORK}
    assert detect_framework(tmp_path) == UNKNOWN


def test_side_folder_as_the_agent_folder_counts(tmp_path: Path) -> None:
    _write(tmp_path, {"examples/x/main.py": CREW})

    assert detect_framework(tmp_path / "examples" / "x") == _named("crewai")


@pytest.mark.parametrize("content", [LONG_LINE, ROT13], ids=["long_line", "rot13"])
def test_problem_in_a_side_file_is_no_blocker(tmp_path: Path, content: str) -> None:
    _write(tmp_path, {"main.py": PLAIN, "tests/bad.py": content})

    assert detect_framework(tmp_path) == NONE


def test_agents_folder_beside_plain_code_is_none(tmp_path: Path) -> None:
    _write(tmp_path, {"main.py": PLAIN, "agents/x.py": PLAIN})

    assert detect_framework(tmp_path) == NONE


@pytest.mark.parametrize(
    ("files", "installed", "repo_root", "expected"),
    [
        ({"main.py": OPENAI_AGENT}, "openai_agents-0.1", None, "openai_agents"),
        ({"main.py": OPENAI_AGENT}, "requests-2.0", None, "none"),
        ({"main.py": OPENAI_AGENT}, None, None, "openai_agents"),
        ({"main.py": OPENAI_AGENT, "src/agents/x.py": PLAIN}, None, None, "unknown"),
        ({"main.py": OPENAI_AGENT, "agents.py": PLAIN}, None, None, "unknown"),
        (
            {"main.py": OPENAI_AGENT, "agents.py": PLAIN},
            "openai_agents-0.1",
            None,
            "openai_agents",
        ),
        ({"main.py": OPENAI_AGENT}, None, "with_agents", "unknown"),
    ],
    ids=[
        "installed",
        "other_packages_installed",
        "no_tree_no_folder",
        "no_tree_own_folder",
        "no_tree_own_module",
        "own_module_with_package_installed",
        "no_tree_folder_under_repo_root",
    ],
)
def test_agents_import_rules(
    tmp_path: Path,
    files: dict[str, str],
    installed: str | None,
    repo_root: str | None,
    expected: str,
) -> None:
    folder = tmp_path / "repo" / "agent"
    _write(folder, files)
    package_dir = None
    if installed is not None:
        package_dir = tmp_path / "package"
        _dist(package_dir, installed)
    root = None
    if repo_root is not None:
        root = tmp_path / "repo"
        (root / "agents").mkdir()

    result = detect_framework(folder, repo_root=root, package_dir=package_dir)

    assert result["name"] == expected


def test_agents_module_under_repo_root_is_ambiguous(tmp_path: Path) -> None:
    _write(tmp_path, {"agent/main.py": OPENAI_AGENT, "agents.py": PLAIN})

    assert detect_framework(tmp_path / "agent", repo_root=tmp_path) == UNKNOWN


def test_folder_with_no_init_does_not_shadow(tmp_path: Path) -> None:
    _write(tmp_path, {"main.py": CREW, "crewai/x.py": PLAIN})

    assert detect_framework(tmp_path) == _named("crewai")


@pytest.mark.parametrize(
    "files",
    [
        {"main.py": CREW, "crewai.py": PLAIN},
        {
            "main.py": "from langchain.agents import create_agent\ncreate_agent()\n",
            "langchain/__init__.py": "",
        },
    ],
    ids=["module", "package"],
)
def test_own_module_shadows_framework(tmp_path: Path, files: dict[str, str]) -> None:
    _write(tmp_path, files)

    assert detect_framework(tmp_path) == NONE


def test_missing_folder_gives_unknown(tmp_path: Path) -> None:
    assert detect_framework(None) == UNKNOWN
    assert detect_framework(tmp_path / "missing") == UNKNOWN


def test_detector_failure_gives_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "/customer/private/path"

    def _raise(*_: Any, **__: Any) -> Any:
        raise RuntimeError(secret)

    monkeypatch.setattr(fd, "folder_findings", _raise)

    with caplog.at_level(logging.INFO, logger=fd.__name__):
        result = detect_framework(tmp_path)

    assert result == UNKNOWN
    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text


@pytest.mark.parametrize(
    "failure", [MemoryError(), RecursionError()], ids=["memory", "recursion"]
)
def test_detector_failure_gives_unknown_on_resource_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    def _raise(*_: Any, **__: Any) -> Any:
        raise failure

    monkeypatch.setattr(fd, "folder_findings", _raise)

    assert detect_framework(tmp_path) == UNKNOWN


def test_never_imports_or_runs_customer_code(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    source = (
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('x')\n"
        "from crewai import Crew\nCrew()\n"
    )
    folder = tmp_path / "agent"
    _write(folder, {"customer_module_never_imported.py": source})

    assert detect_framework(folder) == _named("crewai")

    assert not marker.exists()
    assert "customer_module_never_imported" not in sys.modules
    assert "crewai" not in sys.modules


LAYERED = DEEP + LANGGRAPH
PYDANTIC_BUILDS = "from pydantic_ai import Agent\nAgent()\n"


@pytest.mark.parametrize(
    ("source", "dists", "expected"),
    [
        (
            "from pydantic_ai import Agent\nAgent()\n",
            [("pydantic_ai_slim-1.2.3", "1.2.3")],
            "1.2.3",
        ),
        (
            "from pydantic_ai import Agent\nAgent()\n",
            [("pydantic_ai-2.0", "2.0"), ("pydantic_ai_slim-1.2.3", "1.2.3")],
            "2.0",
        ),
        (LIVEKIT, [("livekit_agents-1.8.8", "1.8.8")], "1.8.8"),
        (LIVEKIT, [("livekit-1.1.20", "1.1.20")], None),
        (CREW, [("crewai-1.0", "1.0"), ("crewai-2.0", "2.0")], None),
        (LAYERED, [("deepagents-0.2", "0.2"), ("langgraph-1.0", "1.0")], "0.2"),
        (
            AUTOGEN,
            [("autogen_agentchat-0.4.1", "0.4.1"), ("pyautogen-0.2", "0.2")],
            "0.4.1",
        ),
        (
            CREW,
            [("crewai-1.0rc1.post2.dev3+local.1", "1.0rc1.post2.dev3+local.1")],
            "1.0rc1.post2.dev3+local.1",
        ),
        (PYDANTIC_BUILDS, [("PyDantic_AI-1.0", "1.0")], "1.0"),
        (PYDANTIC_BUILDS, [("pydantic.ai-1.0", "1.0")], "1.0"),
        (PYDANTIC_BUILDS, [("pydantic_ai-1.0", "1.0RC1")], "1.0RC1"),
    ],
    ids=[
        "second_package_of_the_row",
        "first_package_wins",
        "livekit_agents",
        "livekit_alone_is_not_the_row",
        "two_versions",
        "layered_reads_the_chosen_row",
        "other_row",
        "full_version_shape",
        "name_case_ignored",
        "name_dots_normalised",
        "version_case_ignored",
    ],
)
def test_version_read(
    tmp_path: Path, source: str, dists: list[tuple[str, str]], expected: str | None
) -> None:
    folder = tmp_path / "agent"
    _write(folder, {"main.py": source})
    package_dir = tmp_path / "package"
    for dist, version in dists:
        _dist(package_dir, dist, _metadata(version))

    assert detect_framework(folder, package_dir=package_dir)["version"] == expected


@pytest.mark.parametrize(
    ("python", "metadata"),
    [("python3.13", b"Version: 1.0\n"), ("python3.12", b"Version: 1.0\r\n")],
    ids=["any_python_folder", "crlf_line"],
)
def test_version_read_real_shapes(tmp_path: Path, python: str, metadata: bytes) -> None:
    folder = tmp_path / "agent"
    _write(folder, {"main.py": CREW})
    dist_info = (
        tmp_path / "pkg/.venv/lib" / python / "site-packages/crewai-1.0.dist-info"
    )
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_bytes(metadata)

    found = detect_framework(folder, package_dir=tmp_path / "pkg")

    assert found["version"] == "1.0"


def test_version_read_is_null_for_a_result_with_no_row(tmp_path: Path) -> None:
    folder = tmp_path / "agent"
    _write(folder, {"main.py": "from crewai import Crew\n"})
    package_dir = tmp_path / "package"
    _dist(package_dir, "crewai-1.0", _metadata("1.0"))

    assert detect_framework(folder, package_dir=package_dir) == UNKNOWN


@pytest.mark.parametrize(
    "metadata",
    [
        b"Metadata-Version: 2.1\nVersion: 1.0\x00\n",
        b"Metadata-Version: 2.1\nVersion: sk_live_x\n",
        b"Metadata-Version: 2.1\nVersion: 1" + b".0" * 32 + b"\n",
        b"Metadata-Version: 2.1\nName: crewai\n",
        b"Metadata-Version: 2.1\nX-Version: 1.0\nNotVersion: 2.0\n",
        b"Metadata-Version: 2.1\nVersion: caf\xc3\xa9\n",
        b"Metadata-Version: 2.1\nVersion: bad\nVersion: 1.0\n",
    ],
    ids=[
        "nul",
        "secret_like",
        "too_long",
        "no_version_line",
        "not_at_line_start",
        "non_ascii",
        "later_line_not_read",
    ],
)
def test_bad_metadata_gives_null_version(tmp_path: Path, metadata: bytes) -> None:
    folder = tmp_path / "agent"
    _write(folder, {"main.py": CREW})
    package_dir = tmp_path / "package"
    _dist(package_dir, "crewai-1.0", metadata)

    assert detect_framework(folder, package_dir=package_dir) == _named("crewai")


def test_bad_metadata_gives_null_version_symlink(tmp_path: Path) -> None:
    folder = tmp_path / "agent"
    _write(folder, {"main.py": CREW})
    package_dir = tmp_path / "package"
    elsewhere = tmp_path / "METADATA"
    elsewhere.write_bytes(_metadata("1.0"))
    (_dist(package_dir, "crewai-1.0") / "METADATA").symlink_to(elsewhere)

    assert detect_framework(folder, package_dir=package_dir) == _named("crewai")


def test_metadata_read_stops_at_64_kib(tmp_path: Path) -> None:
    folder = tmp_path / "agent"
    _write(folder, {"main.py": CREW})
    package_dir = tmp_path / "package"
    padding = b"Metadata-Version: 2.1\n" + b"X: y\n" * 14_000
    assert len(padding) > fd._METADATA_BYTES
    _dist(package_dir, "crewai-1.0", padding + b"Version: 1.0\n")

    assert detect_framework(folder, package_dir=package_dir) == _named("crewai")


def _process_manifest(source_origin: str = "bundle") -> SimpleNamespace:
    return SimpleNamespace(
        runtime=SimpleNamespace(kind="process", control_service="agent"),
        processes=[
            SimpleNamespace(name="postgres", working_directory=None),
            SimpleNamespace(
                name="agent",
                working_directory="generated/source/agent",
                source_origin=source_origin,
            ),
        ],
    )


def _compose_manifest() -> SimpleNamespace:
    return SimpleNamespace(
        runtime=SimpleNamespace(kind="compose", control_service=None), processes=[]
    )


@pytest.mark.parametrize(
    ("subfolder", "contract", "manifest", "expected"),
    [
        ("sub", {"runtime": {"workdir": "inner"}}, _process_manifest(), "source/sub"),
        (None, {"runtime": {"workdir": "inner"}}, _process_manifest(), "source/inner"),
        (None, {"runtime": {"workdir": "inner"}}, _compose_manifest(), "source/inner"),
        (
            None,
            {"runtime": {"workdir": "."}},
            _process_manifest(),
            "bundle/generated/source/agent",
        ),
        (
            None,
            {"runtime": {"workdir": ""}},
            _process_manifest(),
            "bundle/generated/source/agent",
        ),
        (
            None,
            {"runtime": {"workdir": 3}},
            _process_manifest(),
            "bundle/generated/source/agent",
        ),
        (
            None,
            {"runtime": {"workdir": "../outside"}},
            _process_manifest(),
            "bundle/generated/source/agent",
        ),
        (
            None,
            {"runtime": {"workdir": "a_file"}},
            _process_manifest(),
            "bundle/generated/source/agent",
        ),
        (None, None, _process_manifest(), "bundle/generated/source/agent"),
        (None, "{not json", _process_manifest(), "bundle/generated/source/agent"),
        (None, ["a", "list"], _process_manifest(), "bundle/generated/source/agent"),
        (
            None,
            None,
            _process_manifest("repository"),
            "source/generated/source/agent",
        ),
        (None, None, _compose_manifest(), "source"),
    ],
    ids=[
        "subfolder_first",
        "workdir_before_process",
        "workdir_on_compose",
        "dot_workdir",
        "empty_workdir",
        "non_string_workdir",
        "workdir_escaping_source",
        "workdir_is_a_file",
        "missing_contract",
        "corrupt_contract",
        "contract_not_an_object",
        "repository_origin_process",
        "compose_falls_to_source",
    ],
)
def test_folder_order(
    tmp_path: Path,
    subfolder: str | None,
    contract: Any,
    manifest: SimpleNamespace,
    expected: str,
) -> None:
    source = tmp_path / "source"
    bundle_dir = tmp_path / "bundle"
    work = tmp_path / "work"
    _write(
        tmp_path,
        {
            "source/a_file": "",
            "source/inner/x.py": "",
            "source/sub/x.py": "",
            "outside/x.py": "",
        },
    )
    bundle_dir.mkdir()
    if isinstance(contract, str):
        (bundle_dir / "contract.json").write_text(contract, encoding="utf-8")
    elif contract is not None:
        (bundle_dir / "contract.json").write_text(
            json.dumps(contract), encoding="utf-8"
        )
    job = SimpleNamespace(source=SimpleNamespace(subfolder=subfolder))

    folders = framework_folders_for(
        job, manifest, source=source, bundle_dir=bundle_dir, work_directory=work
    )

    is_process = manifest.runtime.kind == "process"
    assert folders == FrameworkFolders(
        tmp_path / expected, source, work / "build" / "agent" if is_process else None
    )


def _github_job(subfolder: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        source=RepositorySource(
            kind=SourceKind.GITHUB,
            repository="org/repo",
            visibility=SourceVisibility.PUBLIC,
            commit_sha="a" * 40,
            subfolder=subfolder,
        )
    )


@pytest.mark.parametrize(
    ("subfolder", "expected"),
    [(None, "source"), ("inner", "source/inner")],
    ids=["no_subfolder", "subfolder"],
)
def test_folder_order_on_a_real_job_source(
    tmp_path: Path, subfolder: str | None, expected: str
) -> None:
    source = tmp_path / "source"
    _write(tmp_path, {"source/inner/x.py": ""})

    folders = framework_folders_for(
        _github_job(subfolder),
        _compose_manifest(),
        source=source,
        bundle_dir=tmp_path / "bundle",
        work_directory=tmp_path / "work",
    )

    assert folders == FrameworkFolders(tmp_path / expected, source, None)


def test_folder_order_refuses_a_subfolder_outside_the_source(tmp_path: Path) -> None:
    _write(tmp_path, {"source/x.py": "", "outside/x.py": ""})

    with pytest.raises(SubfolderNotFound):
        framework_folders_for(
            _github_job("../outside"),
            _compose_manifest(),
            source=tmp_path / "source",
            bundle_dir=tmp_path / "bundle",
            work_directory=tmp_path / "work",
        )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import livekit\nlivekit.agents.AgentSession()\n", _named("livekit")),
        ("import google\ngoogle.adk.agents.Agent()\n", _named("google_adk")),
        ("import google\ngoogle.genai.Client()\n", NONE),
        ("import livekit\nlivekit.agents.JobContext()\n", UNKNOWN),
        ("import livekit\nagents = livekit.agents\n", UNKNOWN),
        ("import livekit\nprint(livekit.agents.__version__)\n", UNKNOWN),
        ("import google\ngoogle.adk.tools.FunctionTool(f)\n", UNKNOWN),
        ("import google\nadk = google.adk\n", UNKNOWN),
        ("from livekit import api\napi.AccessToken()\n", NONE),
    ],
    ids=[
        "livekit",
        "google_adk",
        "google_not_adk",
        "livekit_no_builder",
        "livekit_assigned",
        "livekit_read_only",
        "google_adk_no_builder",
        "google_adk_assigned",
        "livekit_not_the_row",
    ],
)
def test_top_name_import_then_dotted_use(
    tmp_path: Path, source: str, expected: dict[str, Any]
) -> None:
    _write(tmp_path, {"main.py": source})

    assert detect_framework(tmp_path) == expected
