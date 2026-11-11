from __future__ import annotations

import ast
import gc
import inspect
import io
import itertools
import json
import os
import tokenize
import tracemalloc
from collections.abc import Iterator
from pathlib import Path

import pytest

from fi.alk.harness import framework_detection as fd
from fi.alk.harness import scan_walk
from fi.alk.harness import source_folder
from fi.alk.harness.scan_walk import (
    Candidate,
    WalkResult,
    build_hazard,
    describe_entry,
    fits_path_rule,
    walk_repo,
)

GRAPH = "from langgraph.graph import StateGraph\nStateGraph(dict)\n"
CLIENT = "from openai import OpenAI\nOpenAI()\n"
FOREVER = float("inf")
ALLOWANCE = 1_000_000_000


def _write(folder: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _walk(source: Path, **kwargs: object) -> WalkResult:
    return walk_repo(source, deadline=FOREVER, **kwargs)  # type: ignore[arg-type]


def _entries(result: WalkResult) -> list[str]:
    return [agent.entry for agent in result.agents]


def _agent(result: WalkResult, entry: str) -> Candidate:
    return next(agent for agent in result.agents if agent.entry == entry)


def _snapshot(folder: Path) -> list[tuple[str, int, int]]:
    seen = []
    for directory, directories, files in os.walk(folder, followlinks=False):
        for name in sorted(directories + files):
            path = os.path.join(directory, name)
            status = os.lstat(path)
            seen.append((path, status.st_size, status.st_mtime_ns))
    return sorted(seen)


def _token_work(source: str) -> int:
    tokens = tokenize.tokenize(io.BytesIO(source.encode()).readline)
    return sum(len(token.line) for token in tokens)


def test_the_walk_skips_what_the_build_skips_and_never_follows_a_link(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    _write(outside, {"agent_graph.py": GRAPH})
    source = tmp_path / "source"
    ran = tmp_path / "ran"
    _write(
        source,
        {
            "app/graph.py": GRAPH + f"open({str(ran)!r}, 'w').close()\n",
            "app/build/graph.py": GRAPH,
            "build/graph.py": GRAPH,
            "env/graph.py": GRAPH,
            "node_modules/pkg/graph.py": GRAPH,
            ".venv/lib/graph.py": GRAPH,
            "lib/site-packages/graph.py": GRAPH,
            "lib/site/pkg-1.0.dist-info/METADATA": "Name: pkg\n",
            "lib/site/graph.py": GRAPH,
        },
    )
    (source / "linked").symlink_to(outside, target_is_directory=True)
    (source / "linked_graph.py").symlink_to(outside / "agent_graph.py")
    before = _snapshot(tmp_path)

    result = _walk(source)

    assert sorted(_entries(result)) == ["app/build/graph.py", "app/graph.py"]
    assert result.unread_files == 1
    assert result.stopped == ""
    assert not ran.exists()
    assert _snapshot(tmp_path) == before


def test_a_heavy_file_uses_only_its_own_allowance(tmp_path: Path) -> None:
    # Every mention of `C`, in its imports and its uses, walks all of its targets.
    targets = 500
    imports = "".join(f"import crewai.m{index} as C\n" for index in range(targets))
    light = imports + "C.Crew()\n" * 100
    heavy = imports + "C.Crew()\n" * 1_500
    per_mention = fd._WORK_PER_TARGET * targets
    light_work = (targets + 100) * per_mention + 3 * _token_work(light)
    heavy_work = (targets + 1_500) * per_mention
    assert scan_walk._FILE_WORK == ALLOWANCE
    assert light_work < ALLOWANCE < 2 * light_work
    assert ALLOWANCE < heavy_work < fd._MAX_WORK
    _write(tmp_path, {"a_light.py": light, "b_light.py": light, "c_heavy.py": heavy})

    result = _walk(tmp_path)

    assert _entries(result) == ["a_light.py", "b_light.py"]
    assert result.unread_files == 1
    assert result.stopped == ""


def test_the_private_detector_names_it_uses_exist() -> None:
    for name in (
        "_ALWAYS_SKIPPED",
        "_TOP_ONLY_SKIPS",
        "_FileScan",
        "_has_long_line",
        "_is_side_file",
        "_is_side_part",
        "_OverBudget",
        "_own_modules",
        "_read_file",
        "_scan_file",
        "_visible_tokens",
    ):
        assert hasattr(fd, name), name
    assert hasattr(source_folder, "_FOLDER_FORM")
    assert source_folder._FOLDER_MAX == 512
    assert list(inspect.signature(fd._FileScan).parameters) == [
        "shadowed",
        "outside_names",
        "work",
    ]
    assert "read_uses" in inspect.signature(fd._scan_file).parameters


@pytest.mark.parametrize(
    "shape",
    [
        b'x = """never closed\n',
        b"if True:\n        a = 1\n    b = 2\n",
        b"if True:\n\ta = 1\n        b = 2\n",
        b"# caf\xe9\nx = 1\n",
        b"x = 1\x00\n",
    ],
    ids=["unclosed_string", "bad_dedent", "tab_and_spaces", "latin1_no_coding", "null"],
)
def test_a_file_the_tokenizer_rejects_is_unread_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: bytes
) -> None:
    (tmp_path / "a_shape.py").write_bytes(GRAPH.encode() + shape)
    _write(tmp_path, {"z_agent.py": GRAPH})

    result = _walk(tmp_path)

    assert result.stopped == ""
    assert "z_agent.py" in _entries(result)
    assert ("a_shape.py" in _entries(result)) != (result.unread_files == 1)

    def rejects(data: bytes, work: object) -> object:
        raise LookupError("a reader fault of any kind")

    monkeypatch.setattr(scan_walk, "_visible_tokens", rejects)
    failing = _walk(tmp_path)

    assert failing.stopped == ""
    assert failing.agents == ()
    assert failing.unread_files == 2


def test_the_token_pass_writes_no_warnings(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], recwarn: pytest.WarningsRecorder
) -> None:
    _write(
        tmp_path,
        {"agent.py": CLIENT + ("import os\nos.getenv('A\\{')\nx = f'\\{a}'\n" * 3)},
    )

    _walk(tmp_path)

    assert not recwarn.list
    assert "Warning" not in capfd.readouterr().err


def test_a_relative_import_joins_the_sub_agent(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/main.py": GRAPH
            + "from . import planner\n"
            + "def build():\n    from .researcher import agent\n    return agent\n",
            "app/planner.py": GRAPH,
            "app/researcher.py": GRAPH,
            "deep/a/b/main.py": GRAPH
            + "from ..helpers import util\nfrom .. import (shared)\n",
            "deep/a/helpers/util.py": GRAPH,
            "deep/a/shared.py": GRAPH,
            "pkg/pyproject.toml": "",
            "pkg/run.py": GRAPH + "import tools.search\n",
            "pkg/src/tools/search.py": GRAPH,
        },
    )

    result = _walk(tmp_path)

    assert sorted(_entries(result)) == ["app/main.py", "deep/a/b/main.py", "pkg/run.py"]


def test_settings_tools_and_clients_are_read(tmp_path: Path) -> None:
    source = tmp_path / "one"
    _write(
        source,
        {
            "bot.py": "import os\n"
            + CLIENT
            + "a = os.getenv('FIRST')\n"
            + 'b = os.environ.get("SECOND")\n'
            + "c = os.environ['THIRD']\n"
            + "d = os.getenv(f'FOURTH')\n"
            + "e = os.getenv('FIF' 'TH')\n"
            + "f = os.getenv('SIX' + 'TH')\n"
            + "g = os.getenv(NAME)\n"
            + "h = os.environ[b'EIGHTH']\n"
            + "def handler():\n    return os.getenv(  'NINTH'  )\n"
            + "@tool\ndef one(): ...\n"
            + "@function_tool\ndef two(): ...\n"
            + "@mcp.tool()\ndef three(): ...\n"
            + "@agents.function_tool(name='x')\ndef four(): ...\n"
            + "@toolkit\ndef not_a_tool(): ...\n"
        },
    )

    [agent] = _walk(source).agents

    assert agent.sign == "client"
    assert agent.env_names == ("FIRST", "NINTH", "SECOND", "THIRD")
    assert agent.tools == 4
    assert [(kind, path) for kind, path, _ in agent.evidence] == [
        ("client openai", "bot.py")
    ]

    forms = {
        "openai.py": "OpenAI()",
        "openai_async.py": "AsyncOpenAI()",
        "openai_azure.py": "openai.AzureOpenAI()",
        "anthropic.py": "Anthropic()",
        "anthropic_async.py": "AsyncAnthropic()",
        "genai.py": "genai.Client()",
        "mistral.py": "Mistral(api_key=k)",
        "cohere.py": "cohere.Client()",
        "cohere_v2.py": "cohere.ClientV2()",
        "groq.py": "Groq()",
        "litellm.py": "litellm.completion(model=m)",
        "litellm_async.py": "litellm.acompletion(model=m)",
    }
    clients = tmp_path / "clients"
    _write(clients, {name: call + "\n" for name, call in forms.items()})
    _write(clients, {"not_a_client.py": "other.Client()\nOpenAI\n"})

    assert sorted(_entries(_walk(clients))) == sorted(forms)


def test_a_getenv_default_is_never_an_env_name(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "bot.py": "import os\n"
            + CLIENT
            + "key = os.getenv('API_KEY', 'sk-fake-123')\n"
            + "url = os.getenv('BASE_URL', 'FALLBACK_NAME')\n"
            + "region = os.environ.get('REGION', 'DEFAULT_REGION')\n"
            + "model = os.getenv('MODEL', default='DEFAULT_MODEL')\n"
        },
    )

    [agent] = _walk(tmp_path).agents

    assert agent.env_names == ("API_KEY", "BASE_URL", "MODEL", "REGION")
    assert "sk-fake-123" not in repr(agent)


def test_env_files_with_values_are_never_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        tmp_path,
        {
            ".env": "REAL_SECRET=sk-fake-123\n",
            ".env.local": "LOCAL_SECRET=sk-fake-456\n",
            "app/.env": "APP_SECRET=sk-fake-789\n",
            ".env.example": "# a note\nEXAMPLE_NAME=sk-fake-000\n"
            "export EXPORTED_NAME=1\n BAD-NAME=2\n",
            "bot.py": CLIENT,
        },
    )
    opened: list[str] = []
    read = scan_walk._read_file

    def spy(path: str, limit: int) -> bytes:
        opened.append(os.path.basename(path))
        return read(path, limit)

    monkeypatch.setattr(scan_walk, "_read_file", spy)

    result = _walk(tmp_path)

    assert ".env.example" in opened
    assert ".env" not in opened
    assert ".env.local" not in opened
    [agent] = result.agents
    assert agent.env_names == ("EXAMPLE_NAME", "EXPORTED_NAME")
    assert "sk-fake" not in repr(result)


@pytest.mark.parametrize(
    "content",
    [b"{not json", b"[" * 100_000 + b"]" * 100_000, b'{"graphs": "\xff"}'],
    ids=["not_json", "deep", "not_utf8"],
)
def test_a_broken_langgraph_json_is_unread_not_a_crash(
    tmp_path: Path, content: bytes
) -> None:
    (tmp_path / "langgraph.json").write_bytes(content)
    _write(tmp_path, {"graph.py": GRAPH})

    result = _walk(tmp_path)

    assert result.stopped == ""
    assert result.unread_files == 1
    assert _entries(result) == ["graph.py"]


def test_the_walk_limit_and_the_clock_stop_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (scan_walk._MAX_JOINED, scan_walk._MAX_BYTES, scan_walk._MAX_HELD) == (
        40_000,
        268_435_456,
        1_610_612_736,
    )
    _write(tmp_path, {"a.py": GRAPH, "b.py": GRAPH, "c.py": GRAPH})
    calls: list[int] = []

    def clock() -> float:
        calls.append(1)
        return 0.0 if len(calls) == 1 else 100.0

    timed = walk_repo(tmp_path, deadline=10.0, clock=clock)

    assert timed.stopped == "clock"
    assert _entries(timed) == ["a.py"]
    # The walk checked before each of its two files; the join after it checks the clock too.
    assert len(calls) >= 2

    monkeypatch.setattr(scan_walk, "_MAX_JOINED", 2)
    by_count = _walk(tmp_path)
    assert by_count.stopped == "limit"
    assert _entries(by_count) == ["a.py", "b.py"]

    monkeypatch.setattr(scan_walk, "_MAX_JOINED", 40_000)
    monkeypatch.setattr(scan_walk, "_MAX_BYTES", len(GRAPH))
    by_bytes = _walk(tmp_path)
    assert by_bytes.stopped == "limit"
    assert _entries(by_bytes) == ["a.py"]


def test_a_memory_error_stops_the_walk_with_its_facts_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, {"a.py": GRAPH, "b.py": GRAPH})
    walks: list[scan_walk._Walk] = []

    class Recording(scan_walk._Walk):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]
            walks.append(self)

    read = scan_walk._read_file

    def runs_out(path: str, limit: int) -> bytes:
        if path.endswith("b.py"):
            raise MemoryError()
        return read(path, limit)

    monkeypatch.setattr(scan_walk, "_Walk", Recording)
    monkeypatch.setattr(scan_walk, "_read_file", runs_out)

    result = _walk(tmp_path)

    assert result.stopped == "memory"
    assert result.agents == ()
    [walk] = walks
    assert walk.facts == {}


def test_a_weak_candidate_does_not_swallow_a_strong_one(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "app/pyproject.toml": "",
            "app/graph.py": GRAPH + "from llm import ask\n",
            "app/llm.py": CLIENT,
            "evaluation/pyproject.toml": "",
            "evaluation/run.py": CLIENT + "from app.graph import graph\n",
        },
    )

    result = _walk(tmp_path)

    assert sorted(_entries(result)) == ["app/graph.py", "evaluation/run.py"]


def test_a_side_folder_script_does_not_swallow_the_agent(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "app/graph.py": GRAPH,
            "evals/run.py": GRAPH + "from app.graph import graph\n",
        },
    )

    result = _walk(tmp_path)

    assert [(agent.entry, agent.side) for agent in result.agents] == [
        ("app/graph.py", False),
        ("evals/run.py", True),
    ]
    assert result.candidate_outside_side


def test_an_import_cycle_is_one_agent(tmp_path: Path) -> None:
    direct = tmp_path / "direct"
    _write(
        direct,
        {"long_name.py": GRAPH + "import b\n", "b.py": GRAPH + "import long_name\n"},
    )
    through = tmp_path / "through"
    _write(
        through,
        {
            "c2.py": GRAPH + "import c1\n",
            "c1.py": GRAPH + "import helper\n",
            "helper.py": "import c2\n",
        },
    )

    assert _entries(_walk(direct)) == ["b.py"]
    assert _entries(_walk(through)) == ["c1.py"]


def test_a_client_beside_a_stronger_agent_is_not_listed(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": "",
            "app/graph.py": GRAPH,
            "tools/backfill.py": CLIENT,
            "other/pyproject.toml": "",
            "other/bot.py": CLIENT,
        },
    )

    result = _walk(tmp_path)

    assert sorted(_entries(result)) == ["app/graph.py", "other/bot.py"]


def test_a_root_test_file_is_side(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {"test_agent.py": GRAPH, "agent_test.py": GRAPH, "conftest.py": GRAPH},
    )

    result = _walk(tmp_path)

    assert {agent.entry: (agent.side, agent.subfolder) for agent in result.agents} == {
        "agent_test.py": (True, ""),
        "conftest.py": (True, ""),
        "test_agent.py": (True, ""),
    }
    assert not result.candidate_outside_side
    assert not result.source_outside_side


def test_the_project_folder_rule(tmp_path: Path) -> None:
    one = tmp_path / "one"
    _write(
        one,
        {"pyproject.toml": "", "app/graph.py": GRAPH, "scripts/run.py": GRAPH},
    )
    two = tmp_path / "two"
    _write(
        two,
        {
            "svc/pyproject.toml": "",
            "svc/a/x.py": GRAPH,
            "svc/b/y.py": GRAPH,
            "lone/requirements.txt": "",
            "lone/deep/z.py": GRAPH,
            "plain/graph.py": GRAPH,
        },
    )

    first = _walk(one)
    second = _walk(two)

    assert {agent.entry: agent.subfolder for agent in first.agents} == {
        "app/graph.py": "",
        "scripts/run.py": "scripts",
    }
    assert ("project pyproject.toml", "pyproject.toml", 0) in _agent(
        first, "app/graph.py"
    ).evidence
    assert {agent.entry: agent.subfolder for agent in second.agents} == {
        "svc/a/x.py": "svc/a",
        "svc/b/y.py": "svc/b",
        "lone/deep/z.py": "lone",
        "plain/graph.py": "plain",
    }


def test_two_graphs_in_one_langgraph_json_mark_the_folder(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "svc/pyproject.toml": "",
            "svc/langgraph.json": json.dumps(
                {"graphs": {"a": "./a.py:graph", "b": "./b.py:graph"}}
            ),
            "svc/a.py": "graph = None\n",
            "svc/b.py": "graph = None\n",
            "one/langgraph.json": json.dumps({"graphs": {"a": "./a.py:graph"}}),
            "one/a.py": "graph = None\n",
        },
    )

    result = _walk(tmp_path)

    assert [
        (agent.entry, agent.sign, agent.subfolder, agent.build_may_stop)
        for agent in result.agents
    ] == [
        ("one/a.py", "graph", "one", ""),
        ("svc/a.py", "graph", "svc", "graphs"),
        ("svc/b.py", "graph", "svc", "graphs"),
    ]
    assert ("graph", "svc/langgraph.json", 0) in _agent(result, "svc/a.py").evidence
    assert build_hazard(tmp_path, "svc") == "graphs"
    assert build_hazard(tmp_path, "one") == ""


def test_two_agent_py_files_mark_the_folder(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "bot/requirements.txt": "",
            "bot/agent.py": CLIENT,
            "bot/tests/agent.py": "x = 1\n",
            "single/agent.py": CLIENT,
            "single/node_modules/pkg/agent.py": "",
            "single/.venv/agent.py": "",
            "single/__pycache__/agent.py": "",
            "kept/agent.py": "",
            "kept/venv/agent.py": "",
        },
    )

    result = _walk(tmp_path)

    assert {
        agent.entry: (agent.sign, agent.subfolder, agent.build_may_stop)
        for agent in result.agents
    } == {
        "bot/agent.py": ("agent_py", "bot", "agent_py"),
        "single/agent.py": ("agent_py", "single", ""),
    }
    assert build_hazard(tmp_path, "kept") == "agent_py"


def test_more_than_fifty_agents_in_one_folder_keeps_fifty(tmp_path: Path) -> None:
    _write(tmp_path, {f"bot_{index:02d}.py": CLIENT for index in range(51)})

    over = _walk(tmp_path)

    assert _entries(over) == [f"bot_{index:02d}.py" for index in range(50)]
    assert over.more_than_fifty

    (tmp_path / "bot_50.py").unlink()
    exact = _walk(tmp_path)

    assert len(exact.agents) == 50
    assert not exact.more_than_fifty


def test_paths_the_path_rule_rejects_are_left_out(tmp_path: Path) -> None:
    long_folder = "/".join(["d" * 100] * 5)
    _write(
        tmp_path,
        {
            "main.py": GRAPH,
            "ok/graph.py": GRAPH,
            "my agent/graph.py": GRAPH,
            "svc\n/graph.py": GRAPH,
            f"{long_folder}/graph.py": GRAPH,
        },
    )

    result = _walk(tmp_path)

    assert {agent.entry: agent.subfolder for agent in result.agents} == {
        "main.py": "",
        "ok/graph.py": "ok",
    }
    assert result.paths_left_out == 3
    assert fits_path_rule("ok/graph.py")
    assert fits_path_rule("x" * 512)
    assert not fits_path_rule("")
    assert not fits_path_rule("svc\n")
    assert not fits_path_rule("a/../b")
    assert not fits_path_rule("x" * 513)


def test_an_upload_keeps_one_agent_at_the_root_counted_for_the_root(
    tmp_path: Path,
) -> None:
    source = tmp_path / "repo"
    _write(
        source,
        {
            "svc/pyproject.toml": "",
            "svc/graph.py": GRAPH + "import os\nos.getenv('SVC_KEY')\n",
            "main.py": "import os\nos.getenv('ROOT_KEY')\n@tool\ndef search(): ...\n",
            "prompts/system.txt": "hello\n",
            ".env.example": "TEMPLATE_KEY=x\n",
            "tests/test_x.py": "import os\nos.getenv('TEST_KEY')\n",
            "other/bot.py": CLIENT,
        },
    )

    uploaded = _walk(source, upload=True)
    listed = _walk(source)

    [agent] = uploaded.agents
    assert (agent.entry, agent.subfolder) == ("svc/graph.py", "")
    assert agent.env_names == ("ROOT_KEY", "SVC_KEY", "TEMPLATE_KEY")
    assert agent.tools == 1
    assert agent.prompt_files == 1
    assert agent.build_may_stop == build_hazard(source, "")
    assert uploaded.upload_others == 1
    in_folder = _agent(listed, "svc/graph.py")
    assert in_folder.subfolder == "svc"
    assert in_folder.env_names == ("SVC_KEY", "TEMPLATE_KEY")
    assert (in_folder.tools, in_folder.prompt_files) == (0, 0)

    side_only = tmp_path / "side"
    _write(
        side_only,
        {
            "scripts/run.py": CLIENT + "import os\nos.getenv('SIDE_KEY')\n",
            "main.py": "import os\nos.getenv('MAIN_KEY')\n",
        },
    )

    [side] = _walk(side_only, upload=True).agents

    assert (side.entry, side.subfolder, side.side) == ("scripts/run.py", "", True)
    assert side.env_names == ("MAIN_KEY", "SIDE_KEY")


def test_env_names_are_checked_and_ordered(tmp_path: Path) -> None:
    names = [
        "Z_CODE",
        "A_CODE",
        "BAD NAME",
        "ENDS\\n",
        "1DIGIT",
        "X" * 129,
        "Y" * 128,
        "A_CODE",
        "naïve",
    ]
    code = "import os\n" + CLIENT + "".join(f"os.getenv('{n}')\n" for n in names)
    _write(
        tmp_path,
        {
            "svc/pyproject.toml": "",
            "svc/bot.py": code,
            "svc/.env.example": "B_TEMPLATE=1\nexport A_TEMPLATE=2\nA_CODE=3\n",
            ".env.sample": "ROOT_TEMPLATE=1\n",
            "elsewhere/.env.template": "ELSEWHERE=1\n",
        },
    )

    [agent] = _walk(tmp_path).agents

    assert agent.subfolder == "svc"
    assert agent.env_names == (
        "A_CODE",
        "Y" * 128,
        "Z_CODE",
        "A_TEMPLATE",
        "B_TEMPLATE",
        "ROOT_TEMPLATE",
    )


def test_describe_entry_reads_process_env(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "web/package.json": "{}\n",
            "web/src/agent.ts": "const a = process.env.OPENAI_API_KEY;\n"
            'const b = process.env["OTHER_KEY"];\n'
            "const c = process.env['THIRD_KEY'];\n"
            "const d = process.env[name];\n"
            "const e = myprocess.env.NOT_THIS;\n",
            "web/src/util.mjs": "export const u = process.env.UTIL_KEY;\n",
            "web/tests/agent.test.ts": "process.env.TEST_ONLY;\n",
            "web/node_modules/pkg/index.js": "process.env.DEPENDENCY_KEY;\n",
            "web/.env.example": "TEMPLATE_KEY=1\n",
            "api/server.js": "process.env.ELSEWHERE;\n",
        },
    )

    candidate = describe_entry(tmp_path, "web/src/agent.ts", "web", deadline=FOREVER)

    assert candidate is not None
    assert (candidate.sign, candidate.subfolder, candidate.side, candidate.partial) == (
        "client",
        "web",
        False,
        False,
    )
    assert candidate.env_names == (
        "OPENAI_API_KEY",
        "OTHER_KEY",
        "THIRD_KEY",
        "UTIL_KEY",
        "TEMPLATE_KEY",
    )


def test_describe_entry_keeps_the_models_folder_or_finds_package_json(
    tmp_path: Path,
) -> None:
    source = tmp_path / "repo"
    _write(
        source,
        {
            "pyproject.toml": "",
            "web/package.json": "{}\n",
            "web/src/agent.ts": "export {};\n",
            "plain/deep/bot.py": CLIENT,
        },
    )
    bare = tmp_path / "bare"
    _write(bare, {"a/b/bot.py": CLIENT})

    def folder_of(root: Path, entry: str, folder: str, upload: bool = False) -> str:
        found = describe_entry(root, entry, folder, deadline=FOREVER, upload=upload)
        assert found is not None
        return found.subfolder

    assert folder_of(source, "web/src/agent.ts", "web/src") == "web/src"
    assert folder_of(source, "web/src/agent.ts", "") == ""
    assert folder_of(source, "web/src/agent.ts", "elsewhere") == "web"
    assert folder_of(source, "web/src/agent.ts", "web/sr") == "web"
    assert folder_of(source, "web/src/agent.ts", "web\n") == "web"
    assert folder_of(source, "plain/deep/bot.py", "nowhere") == ""
    assert folder_of(source, "web/src/agent.ts", "web/src", upload=True) == ""
    assert folder_of(bare, "a/b/bot.py", "x") == "a/b"
    assert describe_entry(source, "web/src/agent.ts\n", "web", deadline=FOREVER) is None
    assert describe_entry(source, "", "", deadline=FOREVER) is None


def test_describe_entry_stops_at_its_deadline_with_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (scan_walk._DESCRIBE_FILES, scan_walk._DESCRIBE_BYTES) == (2_000, 33_554_432)
    _write(
        tmp_path,
        {
            "web/package.json": "{}\n",
            "web/agent.ts": "process.env.FIRST;\n",
            "web/b.ts": "process.env.SECOND;\n",
            "web/c.ts": "process.env.THIRD;\n",
        },
    )

    late = describe_entry(
        tmp_path, "web/agent.ts", "web", deadline=0.0, clock=lambda: 1.0
    )
    on_time = describe_entry(
        tmp_path, "web/agent.ts", "web", deadline=10.0, clock=lambda: 1.0
    )
    monkeypatch.setattr(scan_walk, "_DESCRIBE_FILES", 1)
    capped = describe_entry(tmp_path, "web/agent.ts", "web", deadline=FOREVER)

    assert late is not None and on_time is not None and capped is not None
    assert (late.entry, late.partial, late.env_names) == ("web/agent.ts", True, ())
    assert not on_time.partial
    assert on_time.env_names == ("FIRST", "SECOND", "THIRD")
    assert (capped.partial, capped.env_names) == (True, ("FIRST",))


# Every value line below carries "FAKE", so any name holding it is a piece of a value.
FAKE_KEY_LINE = "FAKEk3yAAAAAbbbbbbbbCCCCCCCCdddddddd0123456789EEEEEEEEffffffffGG"
AROUND = ("A_BEFORE", "Z_AFTER")
# Each shape: a settings template, and the names a reader of it should give, no more.
TEMPLATE_SHAPES: dict[str, tuple[str, tuple[str, ...]]] = {
    "wrapped_bare_short_tail": (
        f"A_BEFORE=1\nSIGNING_KEY={FAKE_KEY_LINE}\n{FAKE_KEY_LINE}\nFAKEt4il=\nZ_AFTER=2\n",
        (*AROUND, "SIGNING_KEY"),
    ),
    "wrapped_once_short_tail": (
        f"A_BEFORE=1\nSIGNING_KEY={FAKE_KEY_LINE}\nFAKEt4il=\nZ_AFTER=2\n",
        (*AROUND, "SIGNING_KEY"),
    ),
    "bare_body_below_an_empty_setting": (
        f"A_BEFORE=1\nSIGNING_KEY=\n{FAKE_KEY_LINE}\nFAKEt4il=\nZ_AFTER=2\n",
        (*AROUND, "SIGNING_KEY"),
    ),
    "crlf_wrapped_short_tail": (
        f"A_BEFORE=1\r\nKEY={FAKE_KEY_LINE}\r\nFAKEcb=\r\nZ_AFTER=2\r\n",
        (*AROUND, "KEY"),
    ),
    "base64url_tail_with_underscore": (
        f"A_BEFORE=1\nJWK_D={FAKE_KEY_LINE}\nFAKE_url_tail_AAAAbbbbCCCCddddEEEEffff0123=\nZ_AFTER=2\n",
        (*AROUND, "JWK_D"),
    ),
    "base64url_line_below_an_empty_setting": (
        "A_BEFORE=1\nJWK_D=\nFAKE_url_tail_AAAAbbbbCCCCddddEEEEffff0123=\nZ_AFTER=2\n",
        (*AROUND, "JWK_D"),
    ),
    "base32_tail": (
        "A_BEFORE=1\nTOTP_SEED=FAKEBASE32AAAABBBBCCCCDDDDEEEE\nFAKEBASE32TAIL234567ABCDEFGH=\nZ_AFTER=2\n",
        (*AROUND, "TOTP_SEED"),
    ),
    "triple_double_quotes": (
        f'A_BEFORE=1\nTRIPLE="""\n{FAKE_KEY_LINE}\nFAKEtq=\n"""\nZ_AFTER=2\n',
        (*AROUND, "TRIPLE"),
    ),
    "triple_single_quotes": (
        f"A_BEFORE=1\nTRIPLE='''\n{FAKE_KEY_LINE}\nFAKEts=\n'''\nZ_AFTER=2\n",
        (*AROUND, "TRIPLE"),
    ),
    "escaped_single_quote": (
        f"A_BEFORE=1\nESC='it\\'s\n{FAKE_KEY_LINE}\nFAKEesc=\n'\nZ_AFTER=2\n",
        (*AROUND, "ESC"),
    ),
    "escaped_double_quote": ('A_BEFORE=1\nESC="say \\"hi\nFAKEesd=\n"\nZ_AFTER=2\n', (*AROUND, "ESC")),
    "quoted_value_left_open": ('A_BEFORE=1\nKEY="\nFAKEdq=\n"\nZ_AFTER=2\n', (*AROUND, "KEY")),
    "key_block_with_one_short_line": (
        "A_BEFORE=1\nPEM=-----BEGIN PRIVATE KEY-----\nFAKEpm=\n-----END PRIVATE KEY-----\nZ_AFTER=2\n",
        (*AROUND, "PEM"),
    ),
    "padded_tail_below_a_short_line": (
        "A_BEFORE=1\nKEY=FAKEshortline\nFAKEpad==\nZ_AFTER=2\n",
        (*AROUND, "KEY"),
    ),
    "key_block_opened_in_a_comment": (
        f"A_BEFORE=1\n#PEM=-----BEGIN PRIVATE KEY-----\n{FAKE_KEY_LINE}\nFAKEpc=\n"
        "-----END PRIVATE KEY-----\nZ_AFTER=2\n",
        AROUND,
    ),
    "line_joined_by_a_backslash": (
        f"A_BEFORE=1\nexport LONG={FAKE_KEY_LINE}\\\nFAKEbs=\nZ_AFTER=2\n",
        (*AROUND, "LONG"),
    ),
    "lines_joined_by_backslashes": (
        "A_BEFORE=1\nexport OPTS=--first \\\n  --second \\\n  FAKEthird=x\nZ_AFTER=2\n",
        (*AROUND, "OPTS"),
    ),
    "key_wrapped_below_a_joined_line": (
        f"A_BEFORE=1\nexport LONG={FAKE_KEY_LINE}\\\n{FAKE_KEY_LINE}\nFAKEt4il=\nZ_AFTER=2\n",
        (*AROUND, "LONG"),
    ),
    "line_separator_in_a_value": ("A_BEFORE=1\nKEY=abc\u2028FAKEls=\nZ_AFTER=2\n", (*AROUND, "KEY")),
    "form_feed_in_a_value": ("A_BEFORE=1\nKEY=abc\x0cFAKEff=\nZ_AFTER=2\n", (*AROUND, "KEY")),
    "byte_order_mark": ("\ufeffOPENAI_API_KEY=\nZ_AFTER=2\n", ("OPENAI_API_KEY", "Z_AFTER")),
    "escaped_backslash_before_the_closing_quote": (
        'A_BEFORE=1\nWIN="C:\\\\tmp\\\\"\nMID_NAME=3\nZ_AFTER=2\n',
        (*AROUND, "WIN", "MID_NAME"),
    ),
    "key_marker_in_an_inline_comment": (
        "A_BEFORE=1\nKEY=x # paste -----BEGIN RSA PRIVATE KEY----- here\nMID_NAME=3\nZ_AFTER=2\n",
        (*AROUND, "KEY", "MID_NAME"),
    ),
    "key_marker_in_a_comment_line": (
        "A_BEFORE=1\n# paste the key from -----BEGIN on\nMID_NAME=3\nZ_AFTER=2\n",
        (*AROUND, "MID_NAME"),
    ),
    "one_line_key_block": (
        "A_BEFORE=1\nKEY=-----BEGIN FAKE KEY-----FAKEbody-----END FAKE KEY-----\nMID_NAME=3\nZ_AFTER=2\n",
        (*AROUND, "KEY", "MID_NAME"),
    ),
    "word_names": (
        "apiKey=\njwtSecret=\nOPENAIAPIKEY=\nlowercasename=\nLANGCHAIN_TRACING_V2=\n"
        "NEXT_PUBLIC_SUPABASE_ANON_KEY=\n",
        (
            "apiKey",
            "jwtSecret",
            "OPENAIAPIKEY",
            "lowercasename",
            "LANGCHAIN_TRACING_V2",
            "NEXT_PUBLIC_SUPABASE_ANON_KEY",
        ),
    ),
    "export_quoted": (
        f'A_BEFORE=1\nexport KEY="{FAKE_KEY_LINE}\nFAKEex=\n"\nZ_AFTER=2\n',
        (*AROUND, "KEY"),
    ),
    "inline_comment_with_a_quote": (
        "A_BEFORE=1\nKEY=plain # don't\nMID_NAME=3\nKEY2=\"v\" # it's\nZ_AFTER=2\n",
        (*AROUND, "KEY", "MID_NAME", "KEY2"),
    ),
    "key_block_marked": (
        f"A_BEFORE=1\nPEM=-----BEGIN PRIVATE KEY-----\n{FAKE_KEY_LINE}\nFAKEpm=\n"
        "-----END PRIVATE KEY-----\nZ_AFTER=2\n",
        (*AROUND, "PEM"),
    ),
    "json_blob_over_lines": (
        'A_BEFORE=1\nSA={\n  "type": "service_account",\n  "private_key_id": "FAKEid",\n'
        '  "private_key": "-----BEGIN PRIVATE KEY-----\\nFAKEjs=\\n-----END PRIVATE KEY-----\\n"\n'
        "}\nZ_AFTER=2\n",
        (*AROUND, "SA"),
    ),
    "json_blob_on_one_line": (
        'A_BEFORE=1\nSA={"k":"FAKEjc=","p":"FAKEjc2="}\nZ_AFTER=2\n',
        (*AROUND, "SA"),
    ),
    "crlf_quoted": (
        f'A_BEFORE=1\r\nKEY="{FAKE_KEY_LINE}\r\nFAKEcr=\r\n"\r\nZ_AFTER=2\r\n',
        (*AROUND, "KEY"),
    ),
    "backtick_over_two_lines": (
        f"A_BEFORE=1\nKEY=`{FAKE_KEY_LINE}\nFAKEbt=`\nZ_AFTER=2\n",
        (*AROUND, "KEY"),
    ),
    "backtick_over_three_lines": (
        f"A_BEFORE=1\nKEY=`{FAKE_KEY_LINE}\n{FAKE_KEY_LINE}\nFAKEbt=`\nMID_NAME=3\nZ_AFTER=2\n",
        (*AROUND, "KEY", "MID_NAME"),
    ),
    "escaped_backtick": ("A_BEFORE=1\nESC=`say \\`hi\nFAKEebt=\n`\nZ_AFTER=2\n", (*AROUND, "ESC")),
    "encoded_line_ending_in_a_blank": (
        f"A_BEFORE=1\nKEY={FAKE_KEY_LINE} \nFAKEtb=\nZ_AFTER=2\n",
        (*AROUND, "KEY"),
    ),
    "encoded_line_ending_in_a_tab": (
        f"A_BEFORE=1\nKEY={FAKE_KEY_LINE}\t\nFAKEtt=\nZ_AFTER=2\n",
        (*AROUND, "KEY"),
    ),
    "encoded_line_ending_in_a_comment": (
        f"A_BEFORE=1\nKEY={FAKE_KEY_LINE} # paste the key here\nFAKEtc=\nZ_AFTER=2\n",
        (*AROUND, "KEY"),
    ),
}


def _name_problems(names: object, expected: tuple[str, ...]) -> tuple[list[int], list[str]]:
    """The lengths of the names that shouldn't be there, and the names missing. Lengths only,
    so a failure never prints a piece of a value."""
    found = set(names)  # type: ignore[call-overload]
    return sorted(len(name) for name in found - set(expected)), sorted(set(expected) - found)


@pytest.mark.parametrize("shape", sorted(TEMPLATE_SHAPES))
def test_a_template_shape_gives_its_names_and_no_value(shape: str) -> None:
    text, expected = TEMPLATE_SHAPES[shape]

    problems = _name_problems(scan_walk._template_names(text.encode("utf-8")), expected)

    assert problems == ([], [])


@pytest.mark.parametrize(
    "encoded",
    [
        lambda text: b"\xff\xfe" + text.encode("utf-16-le"),
        lambda text: b"\xfe\xff" + text.encode("utf-16-be"),
        lambda text: text.encode("latin-1"),
    ],
    ids=["utf16_little_endian", "utf16_big_endian", "latin1"],
)
def test_a_template_in_utf16_or_latin1_gives_its_names_and_no_value(
    tmp_path: Path, encoded: object
) -> None:
    # Not UTF-8 either way: a byte-order mark of UTF-16, or a Latin-1 `ü`.
    text = (
        f"A_BEFORE=1\nCITY=Zürich\nSIGNING_KEY={FAKE_KEY_LINE}\n{FAKE_KEY_LINE}\nFAKEt4il=\n"
        "Z_AFTER=2\n"
    )
    expected = ("A_BEFORE", "CITY", "SIGNING_KEY", "Z_AFTER")
    data = encoded(text)  # type: ignore[operator]
    (tmp_path / ".env.example").write_bytes(data)
    (tmp_path / "bot.py").write_text(CLIENT, encoding="utf-8")

    problems = _name_problems(scan_walk._template_names(data), expected)
    walked = _walk(tmp_path)
    walked_problems = _name_problems(walked.agents[0].env_names, expected)

    assert problems == ([], [])
    assert walked.unread_files == 0
    assert walked_problems == ([], [])


def test_a_multi_line_value_in_a_template_is_never_a_name(tmp_path: Path) -> None:
    # Every line of a pasted key below carries "Fake"; the last base64 line of a key ends in
    # `=` padding, so it reads like a setting with an empty value.
    template = (
        "BEFORE_NAME=1\n"
        'QUOTED_KEY="-----BEGIN PRIVATE KEY-----\n'
        "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCFake01\n"
        "QuotedTailFake02=\n"
        "-----END PRIVATE KEY-----\"\n"
        "MIDDLE_NAME=2\n"
        "RAW_KEY=-----BEGIN PRIVATE KEY-----\n"
        "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCFake03\n"
        "RawTailFake04=\n"
        "-----END PRIVATE KEY-----\n"
        "SINGLE_QUOTED='first line\n"
        "SingleTailFake05=\n"
        "'\n"
        "BarePaddedFake06==\n"
        "WRAPPED_BLOB=MIIBIjANBgkqhkiG9w0BAQEFAAOCFake07\n"
        "hkiG9w0BAQEFAAOCAQ8AFake08=\n"
        "AFTER_NAME=3\n"
        "OPENAIAPIKEY=\n"
        "lowercasename=\n"
    )
    expected = (
        "AFTER_NAME",
        "BEFORE_NAME",
        "MIDDLE_NAME",
        "OPENAIAPIKEY",
        "QUOTED_KEY",
        "RAW_KEY",
        "SINGLE_QUOTED",
        "WRAPPED_BLOB",
        "lowercasename",
    )
    # Each shape in a repository of its own, so one shape's value can't hide another's.
    repos = {"mixed": (template, expected), **TEMPLATE_SHAPES}
    for shape, (text, _) in repos.items():
        (tmp_path / shape).mkdir()
        (tmp_path / shape / ".env.example").write_bytes(text.encode("utf-8"))
        (tmp_path / shape / "bot.py").write_text(CLIENT, encoding="utf-8")

    for shape, (_, names) in repos.items():
        [agent] = _walk(tmp_path / shape).agents
        described = describe_entry(tmp_path / shape, "bot.py", "", deadline=FOREVER)

        assert described is not None
        for found in (agent.env_names, described.env_names):
            problems = _name_problems(found, names)
            assert (shape, problems) == (shape, ([], []))
            assert found == tuple(sorted(names))
            assert not any("FAKE" in name.upper() or "MIIE" in name for name in found), shape


def test_the_walks_hazard_look_stops_at_the_walks_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The build counts `agent.py` in `venv/`, which the walk itself skips.
    _write(tmp_path, {"bot.py": CLIENT, "venv/a/agent.py": "", "venv/b/agent.py": ""})
    [on_time] = _walk(tmp_path).agents
    assert on_time.build_may_stop == "agent_py"
    listed: list[str] = []
    real_walk = os.walk

    def counting(top, *args, **kwargs):
        for directory, directories, files in real_walk(top, *args, **kwargs):
            listed.extend(os.path.join(directory, name) for name in directories + files)
            yield directory, directories, files

    monkeypatch.setattr(scan_walk.os, "walk", counting)
    # On time while the walk reads its one file, past the deadline from then on.
    ticks = iter([0.0])

    [late] = walk_repo(tmp_path, deadline=10.0, clock=lambda: next(ticks, 100.0)).agents

    assert (late.entry, late.build_may_stop) == ("bot.py", "")
    assert not any(path.endswith("agent.py") for path in listed)


def test_build_hazard_is_bounded_and_asked_once_per_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert scan_walk._HAZARD_ENTRIES == 200_000
    _write(tmp_path, {f"bot_{index}.py": CLIENT for index in range(5)})
    for index in range(30):
        (tmp_path / "venv" / f"pkg_{index:02d}").mkdir(parents=True)
    entries: list[str] = []
    real_walk = os.walk

    def counting(top, *args, **kwargs):
        for directory, directories, files in real_walk(top, *args, **kwargs):
            entries.extend(os.path.join(directory, name) for name in directories + files)
            yield directory, directories, files

    monkeypatch.setattr(scan_walk.os, "walk", counting)
    venv = os.path.join(tmp_path, "venv") + os.sep

    hazards: dict[str, str] = {}
    result = walk_repo(tmp_path, deadline=FOREVER, hazards=hazards)
    for _ in range(2):
        describe_entry(tmp_path, "bot_0.py", "", deadline=FOREVER, hazards=hazards)

    # Five root agents and two descriptions share the root: its skipped `venv/` is looked
    # through once.
    assert len(result.agents) == 5
    assert hazards == {"": ""}
    assert sum(path.startswith(venv) for path in entries) == 30

    # Two `agent.py` files, each 15 folders down: finding both visits about 30 entries.
    for branch in ("a", "b"):
        _write(tmp_path, {"/".join(["deep", *[branch] * 15, "agent.py"]): ""})
    entries.clear()
    monkeypatch.setattr(scan_walk, "_HAZARD_ENTRIES", 10)
    assert build_hazard(tmp_path, "deep") == ""
    assert len(entries) <= 11

    monkeypatch.setattr(scan_walk, "_HAZARD_ENTRIES", 200_000)
    assert build_hazard(tmp_path, "deep") == "agent_py"

    entries.clear()
    assert build_hazard(tmp_path, "deep", deadline=0.0, clock=lambda: 1.0) == ""
    # The folder's own listing and the one the clock is checked after, never the rest.
    assert len(entries) <= 3


def _ordered_walk(real_walk, *, descending: bool, listed: list[str]):
    """``os.walk`` with each folder's subfolders handed over in one forced order, recording every
    file name it lists."""

    def walk(*args, **kwargs):
        for here, directories, files in real_walk(*args, **kwargs):
            directories.sort(reverse=descending)
            listed.extend(files)
            yield here, directories, files

    return walk


@pytest.mark.parametrize("below", ["", "x/"], ids=["top", "nested"])
@pytest.mark.parametrize("case", ["found", "stopped"])
def test_a_hazard_look_stopped_by_its_cap_gives_one_answer_in_any_listing_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, below: str
) -> None:
    """The look goes in name order whatever order the file system lists folders in, so where its
    cap stops it is the same everywhere. Both orders are forced, at the folder the look starts
    from and a level below it."""
    if case == "found":
        # Name order reaches both agent.py files before the big folder.
        _files(tmp_path, {f"{below}a/agent.py": "", f"{below}b/agent.py": ""})
        _files(tmp_path, {f"{below}z/f{index:02}": "" for index in range(50)})
    else:
        # Name order meets the big folder first and stops there, before either agent.py.
        _files(tmp_path, {f"{below}a/f{index:02}": "" for index in range(50)})
        _files(tmp_path, {f"{below}b/agent.py": "", f"{below}c/agent.py": ""})
    monkeypatch.setattr(scan_walk, "_HAZARD_ENTRIES", 20)
    real_walk = os.walk
    answers, seen = [], []

    for descending in (False, True):
        listed: list[str] = []
        monkeypatch.setattr(scan_walk.os, "walk", _ordered_walk(real_walk, descending=descending, listed=listed))
        answers.append(scan_walk.build_hazard(tmp_path, ""))
        seen.append(listed.count("agent.py"))

    if case == "found":
        assert answers == ["agent_py", "agent_py"]
    else:
        assert answers == ["", ""]
        # The "" is the cap's, not a look that missed them: neither agent.py was ever listed.
        assert seen == [0, 0]


def test_the_prompt_files_listed_are_capped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert scan_walk._MAX_PROMPT_PATHS == 20_000
    _write(tmp_path, {"bot.py": CLIENT, **{f"prompts/p{index}.txt": "x\n" for index in range(5)}})
    monkeypatch.setattr(scan_walk, "_MAX_PROMPT_PATHS", 3)

    [agent] = _walk(tmp_path).agents

    assert agent.prompt_files == 3


def _decision_table_sources(row: str) -> tuple[str, ...]:
    """One row's files from the framework check's own decision table, read from its test."""
    tree = ast.parse((Path(__file__).parent / "test_framework_detection.py").read_text())
    test = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "test_decision_table"
    )
    table = test.decorator_list[0]
    assert isinstance(table, ast.Call)
    ids = next(keyword.value for keyword in table.keywords if keyword.arg == "ids")
    index = ast.literal_eval(ids).index(row)
    rows = table.args[1]
    assert isinstance(rows, ast.List)
    sources, _ = rows.elts[index].elts  # type: ignore[attr-defined]
    return ast.literal_eval(sources)


def test_a_chat_model_alone_is_a_candidate(tmp_path: Path) -> None:
    [source] = _decision_table_sources("model_alone")
    _write(tmp_path, {"bot.py": source})

    result = _walk(tmp_path)

    [agent] = result.agents
    assert (agent.entry, agent.sign, agent.builds) == ("bot.py", "model", ())
    assert result.candidate_outside_side
    assert ("model langchain", "bot.py", 2) in agent.evidence


def test_a_chat_model_beside_a_stronger_agent_is_not_listed(tmp_path: Path) -> None:
    chat_model = "from langchain_openai import ChatOpenAI\nChatOpenAI()\n"
    _write(
        tmp_path,
        {
            "svc/pyproject.toml": "",
            "svc/graph.py": GRAPH,
            "svc/helper.py": chat_model,
            "other/pyproject.toml": "",
            "other/chain.py": chat_model,
        },
    )

    result = _walk(tmp_path)

    assert {agent.entry: agent.sign for agent in result.agents} == {
        "svc/graph.py": "framework",
        "other/chain.py": "model",
    }


def test_a_deeper_project_does_not_move_an_agents_folder(tmp_path: Path) -> None:
    """The platform keys an agent on repo and subfolder, so adding an unrelated project with
    its own agent must not move the first agent's folder."""
    project = "[project]\nname = 'x'\n"
    _write(tmp_path, {"pyproject.toml": project, "src/app/agent.py": GRAPH})
    alone = _walk(tmp_path)
    assert {agent.entry: agent.subfolder for agent in alone.agents} == {"src/app/agent.py": ""}

    _write(tmp_path, {"plugins/x/pyproject.toml": project, "plugins/x/agent.py": GRAPH})
    beside = _walk(tmp_path)
    assert {agent.entry: agent.subfolder for agent in beside.agents} == {
        "src/app/agent.py": "",
        "plugins/x/agent.py": "plugins/x",
    }


def test_side_folders_cannot_use_up_the_file_cap_before_the_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The customer's own code is read first; a big tests folder sorted ahead of the agent
    no longer hides it behind the file cap. Each test file imports something, so it counts."""
    monkeypatch.setattr(scan_walk, "_MAX_JOINED", 10)
    _write(tmp_path, {f"tests/test_{index:02}.py": "import os\n" for index in range(25)})
    _write(tmp_path, {"zz_agent/agent.py": GRAPH})

    result = _walk(tmp_path)

    assert "zz_agent/agent.py" in _entries(result)
    assert result.stopped == "limit"


@pytest.mark.parametrize("shape", ["linked_template", "unterminated_py", "long_line_py", "over_size_py"])
def test_one_unreadable_file_does_not_stop_an_ai_rows_description(tmp_path: Path, shape: str) -> None:
    """The row was paid for: a bad file marks it partial, and the reading goes on."""
    _write(
        tmp_path,
        {
            "app/bot.py": GRAPH + "import os\nos.getenv('OPENAI_API_KEY')\n",
            "app/z_tool.py": "import os\nos.getenv('BOT_MODEL')\n",
        },
    )
    if shape == "linked_template":
        (tmp_path / "outside.env").write_text("SEARCH_KEY=x\n", encoding="utf-8")
        (tmp_path / "app/.env.example").symlink_to(tmp_path / "outside.env")
    elif shape == "unterminated_py":
        (tmp_path / "app/a_gen.py").write_text("x = '''never closed\n", encoding="utf-8")
    elif shape == "long_line_py":
        (tmp_path / "app/a_gen.py").write_text("x = 1  # " + "y" * 70_000 + "\n", encoding="utf-8")
    else:
        (tmp_path / "app/a_gen.py").write_text("x = 1\n" * 300_000, encoding="utf-8")

    candidate = describe_entry(tmp_path, "app/bot.py", "app", deadline=FOREVER)

    assert candidate is not None
    assert candidate.partial is True
    assert {"OPENAI_API_KEY", "BOT_MODEL"} <= set(candidate.env_names)
    assert "langgraph" in candidate.builds


def test_every_file_is_read_in_exactly_one_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The two passes split the files: together they give each file once."""
    _write(
        tmp_path,
        {
            "agent.py": GRAPH,
            "app/.env.example": "A=1\n",
            "app/test_helpers.py": "x = 1\n",
            "app/conftest.py": "x = 1\n",
            "tests/test_a.py": "x = 1\n",
            "tests/.env.example": "B=1\n",
            "examples/demo/agent.py": GRAPH,
            "pkg/sub/mod.py": "x = 1\n",
            "pkg/sub/tests/test_b.py": "x = 1\n",
            "zz_app/.env.example": "C=1\n",
        },
    )
    seen: list[str] = []
    take = scan_walk._Walk._take

    def counting(self, directory, folder, name):
        seen.append(scan_walk._join(folder, name))
        return take(self, directory, folder, name)

    monkeypatch.setattr(scan_walk._Walk, "_take", counting)
    _walk(tmp_path)

    every = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert sorted(seen) == every
    assert len(seen) == len(set(seen))
    # A settings template is read with its folder's own code, before every side file: here
    # one in a folder that sorts after tests/, which a side-pass template would follow.
    assert seen.index("zz_app/.env.example") < seen.index("tests/test_a.py")
    assert seen.index("app/.env.example") < seen.index("app/test_helpers.py")


def test_a_template_beside_the_agent_is_read_before_a_big_tests_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scan_walk, "_MAX_JOINED", 10)
    _write(tmp_path, {f"tests/test_{index:02}.py": "import os\n" for index in range(25)})
    _write(tmp_path, {"zz_agent/agent.py": GRAPH, "zz_agent/.env.example": "OPENAI_API_KEY=\n"})

    result = _walk(tmp_path)

    assert "OPENAI_API_KEY" in _agent(result, "zz_agent/agent.py").env_names


def test_a_reader_fault_on_every_file_logs_one_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _write(tmp_path, {f"m{index}.py": "x = 1\n" for index in range(5)})

    def broken(data: bytes, shadowed: object) -> object:
        raise LookupError("a fault in the kit's reader")

    monkeypatch.setattr(scan_walk, "_python_facts", broken)
    with caplog.at_level("WARNING", logger=scan_walk.logger.name):
        result = _walk(tmp_path)

    assert result.unread_files == 5
    assert caplog.text.count("a file's reader failed") == 1
    assert "LookupError" in caplog.text


def test_a_reader_fault_on_every_described_file_logs_one_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _write(tmp_path, {"app/bot.py": GRAPH, **{f"app/m{index}.py": "x = 1\n" for index in range(5)}})

    def broken(data: bytes, shadowed: object) -> object:
        raise LookupError("a fault in the kit's reader")

    monkeypatch.setattr(scan_walk, "_python_facts", broken)
    with caplog.at_level("WARNING", logger=scan_walk.logger.name):
        candidate = describe_entry(tmp_path, "app/bot.py", "app", deadline=FOREVER)

    assert candidate is not None and candidate.partial is True
    assert caplog.text.count("a file's reader failed") == 1


def test_the_step_after_the_walk_watches_the_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reviewed shape, small: many files that build a client, each importing a web of modules.
    Joining them into agents must stop at its deadline and end as a clock stop, never run on
    to the helper's hard stop."""
    files = {
        f"pkg/m{index}.py": "".join(f"from .m{(index * 7 + step) % 200} import x\n" for step in range(1, 6))
        for index in range(200)
    }
    files.update(
        {
            f"pkg/c{index}.py": CLIENT + "".join(f"from .m{(index * 3 + step) % 200} import x\n" for step in range(5))
            for index in range(50)
        }
    )
    _write(tmp_path, files)

    complete = _walk(tmp_path)
    assert complete.stopped == "" and complete.agents

    monkeypatch.setattr(scan_walk, "_RESULT_GRACE", 0.0)
    calls: list[int] = []

    def clock() -> float:
        calls.append(1)
        return float(len(calls))

    # Each file read checks the clock once, so this deadline lets the walk finish and then
    # gives the join only a few checks.
    timed = walk_repo(tmp_path, deadline=float(len(files) + 5), clock=clock)

    assert timed.stopped == "clock"
    assert timed.agents == ()
    assert len(calls) < len(files) + 50


def test_the_step_after_the_walk_holds_a_bounded_number_of_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewed shape, small: every candidate imports one package that re-exports modules, so
    each candidate reaches every other and the pairs grow with the square of the candidates."""
    files = {"pkg/__init__.py": "".join(f"from .m{index} import x\n" for index in range(20))}
    files.update({f"pkg/m{index}.py": "x = 1\n" for index in range(20)})
    files.update({f"c{index}.py": CLIENT + "import pkg\nfrom pkg import x\n" for index in range(30)})
    files.update({f"pkg/m{index}.py": CLIENT for index in range(20, 40)})
    files["pkg/__init__.py"] += "".join(f"from .m{index} import x\n" for index in range(20, 40))
    _write(tmp_path, files)

    assert _walk(tmp_path).stopped == ""

    monkeypatch.setattr(scan_walk, "_MAX_EDGES", 10)
    bounded = _walk(tmp_path)

    assert bounded.stopped == "limit"
    assert bounded.agents == ()


# A UTF-16 byte-order mark followed by an odd number of bytes: the decoder can't read it. The
# same template with its last byte pair whole can.
ODD_UTF16_TEMPLATE = b"\xff\xfeA\x00=\x001\x00\n"
EVEN_UTF16_TEMPLATE = ODD_UTF16_TEMPLATE + b"\x00"


def test_a_template_the_decoder_rejects_marks_the_row_short(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with pytest.raises(UnicodeDecodeError):
        scan_walk._template_names(ODD_UTF16_TEMPLATE)
    # The entry, and a file after the template that is still read: its setting is kept.
    _write(tmp_path, {"app/bot.py": CLIENT, "app/tool.py": "import os\nos.getenv('TOOL_KEY')\n"})
    (tmp_path / "app" / ".env.example").write_bytes(EVEN_UTF16_TEMPLATE)
    readable = describe_entry(tmp_path, "app/bot.py", "app", deadline=FOREVER)
    (tmp_path / "app" / ".env.example").write_bytes(ODD_UTF16_TEMPLATE)

    with caplog.at_level("WARNING", logger=scan_walk.logger.name):
        rejected = describe_entry(tmp_path, "app/bot.py", "app", deadline=FOREVER)

    assert readable is not None and readable.partial is False
    assert set(readable.env_names) == {"A", "TOOL_KEY"}
    assert rejected is not None and rejected.partial is True
    assert set(rejected.env_names) == {"TOOL_KEY"}
    # Short because the decoder rejected it, not because the kit's reader failed.
    assert "agent description:" not in caplog.text


def test_the_walks_summary_line_counts_reader_faults_apart_from_unread_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _write(tmp_path, {f"m{index}.py": "x = 1\n" for index in range(3)})
    # An ordinary unreadable file: the tokenizer rejects it, which is no fault of the kit's.
    _write(tmp_path, {"unterminated.py": "x = '''never closed\n"})
    real = scan_walk._python_facts

    def broken(data: bytes, shadowed: frozenset[str]) -> object:
        if data == b"x = 1\n":
            raise LookupError("a fault in the kit's reader")
        return real(data, shadowed)

    monkeypatch.setattr(scan_walk, "_python_facts", broken)
    with caplog.at_level("INFO", logger=scan_walk.logger.name):
        _walk(tmp_path)

    assert "unread=4 reader_faults=3 " in caplog.text


def test_a_fault_in_the_description_keeps_what_was_read_and_logs_the_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The description runs after the model was paid for, so a fault in the kit that escapes
    it keeps what was read, as a partial row, instead of ending the scan in a paid retry. The
    fault raised is outside the per-file catch, so only `describe_entry`'s own catch holds it."""
    _write(
        tmp_path,
        {
            "app/bot.py": CLIENT,
            "app/a_first.py": "import os\nos.getenv('FIRST_KEY')\n",
            "app/z_last.py": "x = 1\n",
        },
    )
    take = scan_walk._Description._take
    calls: list[str] = []

    def failing_after_the_first(self: object, directory: str, folder: str, name: str) -> None:
        calls.append(name)
        if len(calls) > 1:
            raise RuntimeError("a fault in the kit")
        take(self, directory, folder, name)  # type: ignore[arg-type]

    monkeypatch.setattr(scan_walk._Description, "_take", failing_after_the_first)
    with caplog.at_level("WARNING", logger=scan_walk.logger.name):
        candidate = describe_entry(tmp_path, "app/bot.py", "app", deadline=FOREVER)

    assert calls[0] == "a_first.py"
    assert candidate is not None
    assert (candidate.entry, candidate.subfolder, candidate.partial) == ("app/bot.py", "app", True)
    assert "FIRST_KEY" in candidate.env_names
    assert "agent description stopped" in caplog.text
    assert "RuntimeError: a fault in the kit" in caplog.text


# -- what the walk and its join keep (TH-9449), and its one capped reader (TH-9450) ----------


def _unique_parts() -> Iterator[str]:
    first = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"
    rest = first + "0123456789"
    return ("".join(name) for name in itertools.product(first, rest, rest))


def _crafted(parts: Iterator[str]) -> str:
    """200 imports of 64 unique three-letter parts: the most kept for the bytes read."""
    return "".join("import " + ".".join(next(parts) for _ in range(64)) + "\n" for _ in range(200))


def _files(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _shape(root: Path, shape: str) -> None:
    """Sixteen repository shapes, small, each growing one thing the walk or its join keeps."""
    if shape == "empty":
        _files(root, {f"p{index % 3}/m{index:03}.py": "" for index in range(300)})
    elif shape == "plain_imports":
        # Nine-character names: the allocator gives each 64 bytes, more than its size asked for.
        _files(root, {f"m{i:02}.py": "".join(f"import mod{i:02}_{n:03}\n" for n in range(200)) for i in range(60)})
    elif shape == "dotted_imports":
        _files(
            root,
            {f"m{i:02}.py": "".join(f"import pkg{i}_{n}.sub{n}.mod{n}\n" for n in range(200)) for i in range(60)},
        )
    elif shape == "from_import":
        _files(
            root,
            {
                f"m{i:02}.py": "from pkg.mod import (" + ", ".join(f"name_{i}_{n}" for n in range(300)) + ")\n"
                for i in range(60)
            },
        )
    elif shape == "long_names":
        _files(
            root,
            {
                f"m{i:02}.py": "".join(
                    "import " + ".".join([f"n{i}_{n}" + "q" * 250] * 16) + "\n" for n in range(200)
                )
                for i in range(20)
            },
        )
    elif shape == "settings":
        _files(
            root,
            {
                f"m{i:02}.py": "import os\n" + "".join(f"os.getenv('E{i}_{n}_{'X' * 120}')\n" for n in range(200))
                for i in range(60)
            },
        )
    elif shape == "wide_names":
        _files(
            root,
            {
                f"m{i:02}.py": "".join(f"import 変数変数変数変数{i}_{n}.модульмодуль{n}\n" for n in range(200))
                for i in range(60)
            },
        )
    elif shape == "crafted":
        parts = _unique_parts()
        _files(root, {f"m{i:02}.py": _crafted(parts) for i in range(12)})
    elif shape == "templates":
        _files(
            root,
            {f"f{i:03}/.env.example": "".join(f"S{i}_{n}_NAME=\n" for n in range(200)) for i in range(200)},
        )
    elif shape == "graphs":
        _files(
            root,
            {
                f"g{i:02}/langgraph.json": json.dumps(
                    {"graphs": {f"x{n}": f"./deep/m{n}_{'y' * 40}.py:graph" for n in range(200)}}
                )
                for i in range(20)
            },
        )
    elif shape == "comb_and_trunk":
        _files(root, {"/".join(f"c{c}d{d}" for d in range(100)) + "/m.py": "" for c in range(40)})
        trunk = "/".join(f"t{d}" for d in range(100))
        for depth in range(1, 101):
            _files(root, {"/".join(trunk.split("/")[:depth]) + "/pyproject.toml": ""})
        _files(root, {f"{trunk}/leaf{n}/bot.py": "import os\nOpenAI()\n" for n in range(20)})
    elif shape == "importers_and_candidates":
        deep = "/".join(f"level{d:02}_{'z' * 20}" for d in range(30))
        _files(root, {f"{deep}/mod{n:03}.py": "" for n in range(200)})
        _files(
            root,
            {f"{deep}/imp{n:02}.py": "".join(f"from . import mod{k:03}\n" for k in range(200)) for n in range(50)},
        )
        # Candidates that reach the importers, and many that reach nothing: the join's shares
        # and import lists outweigh its fixed part.
        _files(root, {f"{deep}/c{n:02}.py": f"from . import imp{n:02}\nOpenAI()\n" for n in range(50)})
        _files(root, {f"tiny/c{n:04}.py": "OpenAI()\n" for n in range(5_000)})
    elif shape == "project_per_file":
        for index in range(300):
            _files(root, {f"proj{index:03}/pyproject.toml": "", f"proj{index:03}/m.py": "x = 1\n"})
    elif shape == "rows_and_broom":
        _files(root, {f"a{n:02}.py": "OpenAI()\n" for n in range(50)})
        _files(
            root,
            {
                f"lib/m{i:03}.py": "import os\n" + "".join(f"os.getenv('L{i}_{n}')\n" for n in range(100))
                for i in range(100)
            },
        )
        chain = root / "broom"
        for depth in range(50):
            chain = chain / f"d{depth}"
            for sibling in range(20):
                (chain.parent / f"s{depth}_{sibling}").mkdir(parents=True, exist_ok=True)
        chain.mkdir(parents=True, exist_ok=True)
    elif shape == "one_name_files":
        _files(root, {"a.py": "OpenAI()\n"})
        _files(root, {f"n/f{n:04}.py": f"os.getenv('N{n:05}')\n" for n in range(2_000)})
    elif shape == "levels_over_256":
        # Each import's level is an int of its own, and each client's line number too.
        dots = "." * 300
        _files(
            root,
            {
                f"m{i:02}.py": "".join(f"from {dots}m{n} import x\n" for n in range(200)) + "OpenAI()\n" * 100
                for i in range(60)
            },
        )
    else:
        raise AssertionError(shape)


SHAPES = [
    "empty",
    "plain_imports",
    "dotted_imports",
    "from_import",
    "long_names",
    "settings",
    "wide_names",
    "crafted",
    "templates",
    "graphs",
    "comb_and_trunk",
    "importers_and_candidates",
    "project_per_file",
    "rows_and_broom",
    "one_name_files",
    "levels_over_256",
]
ORDINARY = SHAPES[1:7]


def _allocated(size: int) -> int:
    """The block a request of ``size`` gets: pymalloc's 16-byte classes up to 512, then glibc's
    malloc (an 8-byte header, 16-byte alignment, 32 at least), as on the Linux sandbox. Kept
    apart from the code's own `_block`, so a fault in that can't hide here."""
    if size <= 512:
        return max(16, (size + 15) // 16 * 16)
    return max(32, (size + 8 + 15) // 16 * 16)


def _rounded_live() -> int:
    return sum(_allocated(trace.size) for trace in tracemalloc.take_snapshot().traces)


def _largest_parse_peak(root: Path) -> int:
    files = sorted(root.rglob("*.py"), key=lambda path: path.stat().st_size)
    if not files:
        return 0
    tracemalloc.start()
    try:
        base = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        scan_walk._python_facts(files[-1].read_bytes(), frozenset())
        return tracemalloc.get_traced_memory()[1] - base
    finally:
        tracemalloc.stop()


def _hazard_peak(root: Path) -> int:
    """A build hazard's own look at the root, the biggest any row's can be: bounded on its own
    (`_HAZARD_PATH_BYTES`), not charged."""
    tracemalloc.start()
    try:
        base = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        scan_walk.build_hazard(root, "")
        return tracemalloc.get_traced_memory()[1] - base
    finally:
        tracemalloc.stop()


def _measure(root: Path) -> dict[str, int]:
    """Walk and join ``root`` under tracemalloc: what was charged, what is really kept."""
    gc.collect()
    tracemalloc.start()
    try:
        walk = scan_walk._Walk(root, deadline=FOREVER, clock=lambda: 0.0)
        walk.run()
        walk_peak = tracemalloc.get_traced_memory()[1]
        gc.collect()
        after_walk = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        result = walk.result(upload=False)
        join_peak = tracemalloc.get_traced_memory()[1]
        gc.collect()
        live = _rounded_live()
    finally:
        tracemalloc.stop()
    join_charged = scan_walk._JOIN_BASE
    join_charged += sum(scan_walk._held_for_join(path, facts) for path, facts in walk.facts.items())
    join_charged += sum(scan_walk._join_share(target) for target in walk.graphs)
    assert result.stopped == ""
    return {
        "charged": walk.reader.held,
        "live": live,
        "walk_peak": walk_peak,
        "join_rise": join_peak - after_walk,
        "join_charged": join_charged,
    }


@pytest.mark.parametrize("shape", SHAPES)
def test_what_is_charged_is_never_below_what_the_walk_and_its_join_keep(
    tmp_path: Path, shape: str
) -> None:
    """Checked against tracemalloc on CPython 3.12, the kit's Python, with each live block
    rounded as the allocator rounds it (`_block`). The sizes in the estimate are 3.12's."""
    _shape(tmp_path, shape)
    parse_peak = _largest_parse_peak(tmp_path)
    hazard_peak = _hazard_peak(tmp_path)

    seen = _measure(tmp_path)

    assert seen["live"] <= seen["charged"], seen
    assert seen["join_rise"] <= seen["join_charged"] + hazard_peak, (seen, hazard_peak)
    assert seen["walk_peak"] <= seen["charged"] + parse_peak + 1_048_576, seen
    if shape in ORDINARY:
        # High, but not so high that an ordinary repository is cut early.
        assert seen["charged"] <= 2.5 * seen["live"], seen


def test_the_walk_stops_at_what_it_keeps_not_at_how_many_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parts = _unique_parts()
    crafted = _crafted(parts)
    _files(tmp_path / "crafted", {f"m{index:02}.py": _crafted(parts) for index in range(12)})
    _files(tmp_path / "small", {f"m{index:02}.py": "" for index in range(12)})
    one = scan_walk._held_facts("m00.py", scan_walk._python_facts(crafted.encode(), frozenset()))
    monkeypatch.setattr(scan_walk, "_MAX_HELD", scan_walk._JOIN_BASE + 6 * one)

    stopped = _walk(tmp_path / "crafted")
    whole = _walk(tmp_path / "small")

    assert stopped.stopped == "limit"
    # The file whose charge crossed the budget is kept nowhere and isn't counted unread.
    assert stopped.unread_files == 0
    assert whole.stopped == ""


def test_many_small_files_are_read_whole_and_only_joined_files_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A 40,000-file cap used to stop here, with the agent the last file read."""
    for folder in range(201):
        package = tmp_path / "small" / f"p{folder:03}"
        package.mkdir(parents=True)
        for index in range(200):
            (package / f"m{index:03}.py").touch()
    _files(tmp_path / "small", {"zz_agent/agent.py": GRAPH})
    _files(tmp_path / "importing", {f"m{index:02}.py": "import os\n" for index in range(12)})

    with caplog.at_level("INFO", logger=scan_walk.logger.name):
        small = _walk(tmp_path / "small")
    monkeypatch.setattr(scan_walk, "_MAX_JOINED", 10)
    importing = _walk(tmp_path / "importing")

    assert small.stopped == ""
    assert _entries(small) == ["zz_agent/agent.py"]
    assert "files_read=40201 " in caplog.text
    assert importing.stopped == "limit"


# For each thing kept: two repositories that differ only in it, and which measure shows it:
# what is live after the join, the walk's peak (the folders `os.walk` holds until it enters
# them), or the join's own rise (what the join builds for the files it reaches).
KEPT = {
    "templates": "live",
    "graphs": "live",
    "prompt_paths": "live",
    "project_sets": "live",
    "pending_folders": "walk_peak",
    "edges": "join",
    "join_share": "join",
    "import_levels": "live",
    "line_numbers": "live",
}


def _kept_pair(with_it: Path, without: Path, structure: str) -> None:
    if structure == "templates":
        _files(with_it, {f"f{i:02}/.env.example": "".join(f"N{i}_{n}=\n" for n in range(100)) for i in range(40)})
        _files(without, {f"f{i:02}/readme.txt": "x\n" for i in range(40)})
    elif structure == "graphs":
        graphs = {"graphs": {f"g{n}": f"./m{n}_{'y' * 30}.py:graph" for n in range(200)}}
        _files(with_it, {f"a{i}/langgraph.json": json.dumps(graphs) for i in range(10)})
        _files(without, {f"a{i}/langgraph.json": "{}" for i in range(10)})
    elif structure == "prompt_paths":
        _files(with_it, {f"prompts/prompt_{n:04}_{'p' * 60}.txt": "" for n in range(500)})
        _files(without, {f"others/other_{n:04}_{'p' * 60}.txt": "" for n in range(500)})
    elif structure == "project_sets":
        for index in range(200):
            _files(with_it, {f"p{index:03}/pyproject.toml": "", f"p{index:03}/m.py": "", f"p{index:03}/n.py": ""})
            _files(without, {f"p{index:03}/m.py": "", f"p{index:03}/n.py": ""})
    elif structure == "pending_folders":
        for root, siblings in ((with_it, 20), (without, 0)):
            chain = root
            for depth in range(40):
                chain = chain / f"d{depth}"
                for sibling in range(siblings):
                    (chain.parent / f"s{depth}_{sibling}_{'w' * 30}").mkdir(parents=True)
            chain.mkdir(parents=True)
    elif structure == "edges":
        # One candidate reaches 200 files that each import the other 199: the join keeps an
        # import list for each. Without, the same files import nothing.
        web = {f"m{n:03}.py": "".join(f"import m{k:03}\n" for k in range(200) if k != n) for n in range(200)}
        _files(with_it, {"bot.py": "import m000\nOpenAI()\n", **web})
        _files(without, {"bot.py": "OpenAI()\n", **{name: "" for name in web}})
    elif structure == "import_levels":
        # The same imports; only their levels pass 256, so each is an int of its own rather than
        # a shared small one.
        _files(with_it, {f"m{i:02}.py": "".join(f"from {'.' * 300}m{n} import x\n" for n in range(200)) for i in range(30)})
        _files(without, {f"m{i:02}.py": "".join(f"from .m{n} import x\n" for n in range(200)) for i in range(30)})
    elif structure == "line_numbers":
        # One client a file, on a line past 256 or on line 1: an int of its own, or a shared one.
        _files(with_it, {f"c{n:04}.py": "\n" * 300 + "OpenAI()\n" for n in range(2_000)})
        _files(without, {f"c{n:04}.py": "OpenAI()\n" + "\n" * 300 for n in range(2_000)})
    else:
        _files(with_it, {f"c{n:04}.py": "OpenAI()\n" for n in range(5_000)})
        _files(without, {f"c{n:04}.py": "x = 1\n" for n in range(5_000)})


@pytest.mark.parametrize("structure", list(KEPT))
def test_each_thing_kept_is_charged_at_least_what_it_holds(tmp_path: Path, structure: str) -> None:
    _kept_pair(tmp_path / "with", tmp_path / "without", structure)

    a, b = _measure(tmp_path / "with"), _measure(tmp_path / "without")

    if KEPT[structure] == "join":
        # Charged at the walk for every file, whether the join reaches it or not, so the
        # repository with the thing is checked on its own.
        assert a["join_rise"] - b["join_rise"] > 0, (a, b)
        assert a["join_rise"] <= a["join_charged"], a
    else:
        measure = KEPT[structure]
        assert a["charged"] - b["charged"] >= a[measure] - b[measure] > 0, (a, b)


def _all_names(walk: scan_walk._Walk, subfolder: str) -> list[str]:
    """What today's code built: every distinct name under the row's folder, sorted."""
    return sorted({name for path, facts in walk.facts.items() if scan_walk._under(path, subfolder) for name in facts.env})


@pytest.mark.parametrize("names", [30, 300])
def test_a_rows_setting_names_are_its_first_fifty(tmp_path: Path, names: int) -> None:
    _files(tmp_path, {"bot.py": "from openai import OpenAI\nOpenAI()\n"})
    _files(
        tmp_path,
        {f"lib/m{i:02}.py": "import os\n" + "".join(f"os.getenv('K{(i * 37 + n) % names:04}')\n" for n in range(40)) for i in range(20)},
    )
    _files(tmp_path, {".env.example": "".join(f"T{n:03}=\n" for n in range(40))})
    walk = scan_walk._Walk(tmp_path, deadline=FOREVER, clock=lambda: 0.0)
    walk.run()
    full = _all_names(walk, "")
    expected = (*full, *sorted(walk.templates[""] - set(full)))[: scan_walk.ENV_NAMES_KEPT]

    row = _agent(walk.result(upload=False), "bot.py")
    described = describe_entry(tmp_path, "bot.py", "", deadline=FOREVER)

    assert row.env_names == expected
    assert described is not None and described.env_names == expected
    assert scan_walk.ENV_NAMES_KEPT == 50
    from fi.alk.harness import scan_document

    assert scan_document.ENV_NAMES_KEPT is scan_walk.ENV_NAMES_KEPT


def test_a_hazard_look_gives_up_past_its_listed_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _files(tmp_path, {"a/agent.py": "", "b/agent.py": ""})
    # Beside a and b, so the root's own listing passes the bound before either agent.py is
    # seen, whatever order the file system lists folders in.
    for index in range(40):
        (tmp_path / f"folder_{index:02}_{'v' * 40}").mkdir()
    assert scan_walk.build_hazard(tmp_path, "") == "agent_py"

    monkeypatch.setattr(scan_walk, "_HAZARD_PATH_BYTES", 500)

    assert scan_walk.build_hazard(tmp_path, "") == ""


@pytest.mark.parametrize(
    ("weak", "stronger", "listed"),
    [
        ("tests/test_client.py", "tests/test_graph.py", False),
        ("app/client.py", "tests/test_graph.py", True),
        ("app/client.py", "app/graph.py", False),
    ],
    ids=["side_beside_side", "own_beside_side_only", "own_beside_own"],
)
def test_a_side_candidate_never_decides_for_one_outside_side_folders(
    tmp_path: Path, weak: str, stronger: str, listed: bool
) -> None:
    _files(tmp_path, {"pyproject.toml": "", weak: "from openai import OpenAI\nOpenAI()\n", stronger: GRAPH})

    assert (weak in _entries(_walk(tmp_path))) is listed


def test_the_join_is_linear_in_its_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _files(tmp_path, {"pyproject.toml": "", **{f"c{n:04}.py": "OpenAI()\n" for n in range(2_000)}})
    _files(tmp_path, {f"lib/m{n:04}.py": "" for n in range(2_000)})
    walk = scan_walk._Walk(tmp_path, deadline=FOREVER, clock=lambda: 0.0)
    walk.run()
    project_of = scan_walk._Walk._project_of
    side_checks = scan_walk._is_side_path
    counted = {"project_of": 0, "side": 0}

    def counting_project_of(self: object, path: str) -> str:
        counted["project_of"] += 1
        return project_of(self, path)  # type: ignore[arg-type]

    def counting_side(path: str) -> bool:
        counted["side"] += 1
        return side_checks(path)

    monkeypatch.setattr(scan_walk._Walk, "_project_of", counting_project_of)
    monkeypatch.setattr(scan_walk, "_is_side_path", counting_side)
    result = walk.result(upload=False)

    assert len(result.agents) == 50
    # Today's loops asked for each candidate's project once per other candidate, and walked
    # every kept file once per row: millions here.
    assert counted["project_of"] <= 3 * 2_000
    assert counted["side"] <= 10 * 4_000


def test_a_folder_and_its_ancestors_are_looked_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 150 levels: macOS paths stop at 1,024 characters.
    deep = "/".join(f"d{depth}" for depth in range(150))
    _files(tmp_path, {f"{deep}/m{n:03}.py": f"import m{(n + 1) % 300:03}\n" for n in range(300)})
    file_in = scan_walk._Projects.file_in
    looks: list[str] = []

    def counting(self: object, folder: str) -> str | None:
        looks.append(folder)
        return file_in(self, folder)  # type: ignore[arg-type]

    monkeypatch.setattr(scan_walk._Projects, "file_in", counting)
    _walk(tmp_path)

    # 151 folders, the root included; a look a file at a time would be 300 times as many.
    assert len(looks) <= 2 * 151
    assert len(set(looks)) == 151


def test_the_capped_reader_counts_in_the_order_the_stop_points_need(tmp_path: Path) -> None:
    for name, size in (("a", 6), ("b", 6), ("big", scan_walk._FILE_BYTES + 1)):
        (tmp_path / name).write_bytes(b"x" * size)
    missing = str(tmp_path / "missing")

    by_files = scan_walk._CappedReader(files=2, total_bytes=10**9)
    assert by_files.read(str(tmp_path / "a")) == b"x" * 6
    with pytest.raises(FileNotFoundError):
        by_files.read(missing)
    assert by_files.files_read == 1  # a file that can't be read isn't counted
    by_files.read(str(tmp_path / "b"))
    with pytest.raises(scan_walk._CapReached):
        by_files.read(missing)  # the count is checked before reading

    by_bytes = scan_walk._CappedReader(files=None, total_bytes=10)
    by_bytes.read(str(tmp_path / "a"))
    with pytest.raises(scan_walk._CapReached):
        by_bytes.read(str(tmp_path / "b"))  # read and counted, then past the bytes
    assert (by_bytes.files_read, by_bytes.bytes_read) == (2, 12)

    by_size = scan_walk._CappedReader(files=None, total_bytes=10**9)
    with pytest.raises(scan_walk._Unread):
        by_size.read(str(tmp_path / "big"))
    assert by_size.files_read == 1

    by_held = scan_walk._CappedReader(files=None, total_bytes=10**9, held=10)
    by_held.charge(10)
    with pytest.raises(scan_walk._CapReached):
        by_held.charge(1)
    scan_walk._CappedReader(files=None, total_bytes=10**9).charge(10**12)  # no budget: never
    assert not issubclass(scan_walk._CapReached, scan_walk._FILE_ERRORS)


def test_the_walk_and_the_description_read_through_the_one_capped_reader(tmp_path: Path) -> None:
    walk = scan_walk._Walk(tmp_path, deadline=FOREVER, clock=lambda: 0.0)
    description = scan_walk._Description(os.fspath(tmp_path), "bot.py", "", deadline=FOREVER, clock=lambda: 0.0)

    assert isinstance(walk.reader, scan_walk._CappedReader)
    assert isinstance(description.reader, scan_walk._CappedReader)
    for reading in (scan_walk._Walk, scan_walk._Description):
        assert "_read_file(" not in inspect.getsource(reading), reading.__name__


@pytest.mark.parametrize("where", ["template", "source"])
def test_the_descriptions_cap_stops_it_partial_at_either_read_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, where: str
) -> None:
    _files(tmp_path, {"app/bot.py": CLIENT, "app/tool.py": "x = 1\n"})
    if where == "template":
        # The cap is checked before each read: here before the first template is looked at.
        _files(tmp_path, {"app/.env.example": "A=1\n"})
        monkeypatch.setattr(scan_walk, "_DESCRIBE_FILES", 0)
    else:
        # No template is there, so the first read counted is bot.py; tool.py is past the cap.
        monkeypatch.setattr(scan_walk, "_DESCRIBE_FILES", 1)
    # Past the cap the description reads nothing more: these come after it.
    _files(tmp_path, {f"app/prompts/prompt_{n}.md": "Be brief.\n" for n in range(7)})

    with caplog.at_level("WARNING", logger=scan_walk.logger.name):
        candidate = describe_entry(tmp_path, "app/bot.py", "app", deadline=FOREVER)

    assert candidate is not None and candidate.partial is True
    assert not candidate.prompt_files
    assert "agent description stopped" not in caplog.text


def test_a_rows_pass_over_many_files_watches_the_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One row's pass over the kept files checks the clock as it goes, so a huge tree of files
    that import nothing ends as a clock stop, never past the helper's hard stop."""
    _files(tmp_path, {"bot.py": "from openai import OpenAI\nOpenAI()\n"})
    for folder in range(50):
        package = tmp_path / f"p{folder:02}"
        package.mkdir()
        for index in range(200):
            (package / f"m{index:03}.py").touch()
    now = [0.0]
    side_checks = scan_walk._is_side_path
    seen = [0]

    def counting(path: str) -> bool:
        seen[0] += 1
        if seen[0] == 5_000:
            now[0] = 10_000.0  # past the result's deadline, mid-pass
        return side_checks(path)

    walk = scan_walk._Walk(tmp_path, deadline=100.0, clock=lambda: now[0])
    walk.run()
    assert walk.stopped == ""
    monkeypatch.setattr(scan_walk, "_is_side_path", counting)

    with pytest.raises(scan_walk._ResultOutOfTime):
        walk.result(upload=False)


def test_a_project_lookup_is_charged_at_least_what_it_keeps(tmp_path: Path) -> None:
    """Each folder the lookup keeps, with its link to its nearest project, measured on the
    lookup alone: the walk also charges these folders as ones to enter, which would hide it."""
    folders = ["/".join(f"c{c}d{d}" for d in range(60)) for c in range(30)]
    for folder in folders:
        (tmp_path / folder).mkdir(parents=True)
    (tmp_path / "pyproject.toml").touch()
    charged = [0]

    def charge(size: int) -> None:
        charged[0] += size

    projects = scan_walk._Projects(os.fspath(tmp_path), scan_walk._PROJECT_FILES, charge)
    gc.collect()
    tracemalloc.start()
    try:
        for folder in folders:
            projects.nearest(folder)
        gc.collect()
        kept = _rounded_live()
    finally:
        tracemalloc.stop()

    assert projects.nearest(folders[0]) == ""
    assert kept <= charged[0]


def test_the_first_names_hold_no_more_than_fifty_and_stop_early() -> None:
    names = [f"N{n:04}" for n in range(300)]
    taken: list[str] = []

    def group(start: int) -> Iterator[str]:
        for name in names[start:]:
            taken.append(name)
            yield name

    first = scan_walk._first_names([group(100), group(0), group(250)])

    assert first == names[:50]
    # Each group stops at its first name past the last of 50 kept: the first fills to 50 and
    # stops at its 51st, the second replaces them all and stops at its 51st, and the third's
    # first name is already past.
    assert len(taken) == 51 + 51 + 1


def test_a_rows_names_leave_out_a_deeper_listed_folder_and_a_sibling_with_the_same_start(
    tmp_path: Path,
) -> None:
    _files(
        tmp_path,
        {
            "bot.py": GRAPH + "import os\nos.getenv('ROOT_KEY')\n",
            "app/pyproject.toml": "",
            "app/agent.py": GRAPH + "import os\nos.getenv('APP_KEY')\n",
            # Not under app/, though its name starts the same way.
            "app2/helper.py": "import os\nos.getenv('APP2_KEY')\n",
            "plugins/x/pyproject.toml": "",
            "plugins/x/agent.py": GRAPH + "import os\nos.getenv('PLUGIN_KEY')\n",
        },
    )

    result = _walk(tmp_path)
    by_folder = {agent.subfolder: set(agent.env_names) for agent in result.agents}

    assert by_folder == {
        "": {"ROOT_KEY", "APP2_KEY"},
        "app": {"APP_KEY"},
        "plugins/x": {"PLUGIN_KEY"},
    }


def test_the_estimate_rounds_each_object_up_to_its_block() -> None:
    """The estimate's sizes are 3.12's, each rounded as the allocator rounds it. The whole-walk
    comparison has margins beside the rounding, so the rounding is held here on its own."""
    import sys

    for size in range(1, 4_097):
        assert scan_walk._block(size) >= _allocated(size), size
    for name in ("", "a", "abcdefgh", "x" * 255, "変数", "модуль", "\U0001d400" * 3):
        assert scan_walk._text(name) >= _allocated(sys.getsizeof(name)), name
    for items in range(0, 210):
        assert scan_walk._tuple(items) >= _allocated(sys.getsizeof(tuple(range(items)))), items


@pytest.mark.parametrize("structure", ["pending_folders", "prompt_paths"])
def test_a_charge_made_outside_one_files_read_stops_the_walk_at_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, structure: str
) -> None:
    """A folder's listing and a prompt path are charged outside any one file's read, so only the
    walk's own catch turns their cap into `limit`: the walk keeps the row it found and never
    raises out of `walk_repo`."""
    _files(tmp_path, {"bot.py": CLIENT})
    (tmp_path / "z").mkdir()
    before = scan_walk._Walk(tmp_path, deadline=FOREVER, clock=lambda: 0.0)
    before.run()
    for index in range(300):
        if structure == "pending_folders":
            (tmp_path / "z" / f"s{index:03}_{'w' * 30}").mkdir()
        else:
            (tmp_path / "z" / f"prompt_{index:03}.txt").touch()
    monkeypatch.setattr(scan_walk, "_MAX_HELD", before.reader.held + 1_024)

    result = _walk(tmp_path)

    assert result.stopped == "limit"
    assert _entries(result) == ["bot.py"]


def test_a_descriptions_byte_cap_marks_it_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _files(tmp_path, {"app/bot.py": CLIENT, "app/tool.py": "x = 1\n" * 50})
    whole = describe_entry(tmp_path, "app/bot.py", "app", deadline=FOREVER)
    monkeypatch.setattr(scan_walk, "_DESCRIBE_BYTES", len(CLIENT) + 10)

    capped = describe_entry(tmp_path, "app/bot.py", "app", deadline=FOREVER)

    assert whole is not None and whole.partial is False
    assert capped is not None and capped.partial is True


def test_a_project_lookup_charges_each_folder_and_each_link_it_keeps(tmp_path: Path) -> None:
    """Every folder looked at is charged once for its entry, and every link to a nearest project
    once more; the first look at a deep folder keeps a link for each ancestor, so a file below
    one of them is answered from the links."""
    deep = "/".join(f"d{depth}" for depth in range(40))
    (tmp_path / deep).mkdir(parents=True)
    (tmp_path / "d0" / "d1" / "pyproject.toml").touch()
    charges: list[int] = []
    projects = scan_walk._Projects(os.fspath(tmp_path), scan_walk._PROJECT_FILES, charges.append)

    assert projects.nearest(deep) == "d0/d1"

    ancestors = [deep.rsplit("/", cut)[0] for cut in range(1, 40)] + [""]
    assert set(projects._nearest) == {deep, *ancestors}
    assert sum(charges) == sum(scan_walk._ENTRY + scan_walk._text(folder) for folder in projects._file) + (
        scan_walk._ENTRY * len(projects._nearest)
    )
    looked = len(charges)
    assert projects.nearest(ancestors[10]) == "d0/d1"
    assert len(charges) == looked


def test_a_hazard_look_counts_each_listed_folder_by_its_full_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _files(tmp_path, {"a/agent.py": "", "b/agent.py": ""})
    names = [f"folder_{index:02}" for index in range(40)]
    for name in names:
        (tmp_path / name).mkdir()
    # Above the names alone, below the names with the folder they sit in: the root's listing alone
    # passes it, before either agent.py is found.
    monkeypatch.setattr(scan_walk, "_HAZARD_PATH_BYTES", sum(len(name) + 1 for name in [*names, "a", "b"]) + 100)

    assert scan_walk.build_hazard(tmp_path, "") == ""


def test_the_file_whose_charge_crosses_the_budget_is_kept_nowhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _files(tmp_path, {"a.py": GRAPH, "b.py": GRAPH, "c.py": GRAPH})
    whole = scan_walk._Walk(tmp_path, deadline=FOREVER, clock=lambda: 0.0)
    whole.run()
    # One byte short of the whole walk's charge: the last file's charge crosses it.
    monkeypatch.setattr(scan_walk, "_MAX_HELD", whole.reader.held - 1)

    walk = scan_walk._Walk(tmp_path, deadline=FOREVER, clock=lambda: 0.0)
    walk.run()
    result = _walk(tmp_path)

    assert walk.stopped == "limit"
    assert len(walk.facts) == len(walk._keys) == 2
    assert result.stopped == "limit" and len(_entries(result)) == 2


def test_an_import_inside_an_inner_project_resolves_at_an_outer_projects_root(tmp_path: Path) -> None:
    """The places an import is looked for are every project at or above the file, nearest
    first, then the repository's root: here the helper is found at the outer project's root, so
    it joins the graph's agent rather than standing as one of its own."""
    _files(
        tmp_path,
        {
            "svc/pyproject.toml": "",
            "svc/a/pyproject.toml": "",
            "svc/a/graph.py": GRAPH + "from helpers.llm import ask\n",
            "svc/helpers/llm.py": CLIENT + "def ask():\n    pass\n",
        },
    )

    assert _entries(_walk(tmp_path)) == ["svc/a/graph.py"]


def test_a_cap_reached_while_joining_ends_as_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _files(tmp_path, {"bot.py": CLIENT})

    def charging(self: object, **kwargs: object) -> object:
        raise scan_walk._CapReached()

    monkeypatch.setattr(scan_walk._Walk, "result", charging)

    assert _walk(tmp_path).stopped == "limit"


def test_each_folder_is_charged_once_across_the_two_passes(tmp_path: Path) -> None:
    """The side pass walks the customer's own folders again; only the folders the first pass
    never entered (side folders, and folders below one) are charged there."""
    for index in range(40):
        (tmp_path / f"app{index:02}" / "inner").mkdir(parents=True)
        (tmp_path / "tests" / f"t{index:02}" / "deep").mkdir(parents=True)
        (tmp_path / f"app{index:02}" / "examples" / "e").mkdir(parents=True)
        (tmp_path / f"app{index:02}" / ".hidden" / "x").mkdir(parents=True)
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    charged: dict[str, int] = {"own": 0, "side": 0, "once": 0}

    def into(key: str):
        def charge(size: int) -> None:
            charged[key] += size

        return charge

    top = str(tmp_path)
    for _ in scan_walk._source_files(top, top, side=False, charge=into("own")):
        pass
    for _ in scan_walk._source_files(top, top, side=True, charge=into("side")):
        pass
    for _ in scan_walk._source_files(top, top, charge=into("once")):
        pass

    assert charged["own"] > 0 and charged["side"] > 0
    assert charged["own"] + charged["side"] == charged["once"], charged


def test_the_estimates_sizes_were_measured_on_the_hosted_kits_python() -> None:
    """The sizes are CPython 3.12's. On 3.14 a tuple's header is larger, so the estimate would be
    low there. This fails when the hosted image's kit venv moves to another
    Python, so the sizes are measured again before the bound is trusted."""
    dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile.hosted").read_text()
    major, minor = scan_walk._SIZES_MEASURED_ON
    made = [
        line
        for line in dockerfile.splitlines()
        if "/opt/alk-venv" in line and ("-m venv" in line or "uv venv" in line or "virtualenv" in line)
    ]
    assert len(made) == 1, made
    assert f"python{major}.{minor} -m venv /opt/alk-venv" in made[0], made
    assert made[0].count("-m venv") == 1, made


def test_a_memory_budget_stop_while_joining_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A held-budget stop during the join ends at `limit`, with its own cause in the log, not the
    edge budget's."""
    _files(tmp_path, {"bot.py": CLIENT})

    def reached(self, *, upload: bool):
        raise scan_walk._CapReached()

    monkeypatch.setattr(scan_walk._Walk, "result", reached)
    kept: list[int] = []
    real_empty = scan_walk._empty_result

    def empty(walk, stopped):
        kept.append(len(walk.facts))
        return real_empty(walk, stopped)

    monkeypatch.setattr(scan_walk, "_empty_result", empty)
    with caplog.at_level("WARNING", logger=scan_walk.logger.name):
        result = scan_walk.walk_repo(tmp_path, deadline=FOREVER, clock=lambda: 0.0)

    assert result.stopped == "limit"
    assert "passed its memory budget" in caplog.text
    assert "edge budget" not in caplog.text
    assert kept == [0]  # the facts are let go before the empty result is built


@pytest.mark.parametrize("shape", ["one_entry", "two_entries"])
def test_a_fan_out_join_is_charged_without_its_base(tmp_path: Path, shape: str) -> None:
    """110 hubs of leaves that each import one shared module, about 22,000 files: the join walks
    every leaf. One entry puts the import table just past its growth at 21,846 entries. Two
    entries are dearer still: the first fills that table, then the second's own search grows its
    seen set while the table stays full (about 59.5 bytes a file). What the join really holds is
    covered by the per-file charge alone, without `_JOIN_BASE`, and only with `_JOIN_PER_FILE`
    at 64: at 56 the two-entry shape fails. Leaves that import nothing share
    CPython's empty tuple, which would hide the per-file cost."""
    per_hub = 199 if shape == "one_entry" else 198
    hubs = {
        f"h{h:03}.py": "".join(f"import l{h:03}_{n:03}\n" for n in range(per_hub)) for h in range(110)
    }
    leaves = {f"l{h:03}_{n:03}.py": "import common\n" for h in range(110) for n in range(per_hub)}
    entries = {"a.py": "".join(f"import h{h:03}\n" for h in range(110)) + CLIENT}
    if shape == "two_entries":
        entries["b.py"] = "".join(f"import h{h:03}\n" for h in range(100)) + CLIENT
    _files(tmp_path, {**entries, "common.py": "x = 1\n", **hubs, **leaves})

    seen = _measure(tmp_path)

    assert seen["join_rise"] > 1_000_000, seen
    assert seen["join_rise"] <= seen["join_charged"] - scan_walk._JOIN_BASE, seen


@pytest.mark.parametrize("entries", ["folders", "files", "links"])
def test_a_wide_folder_is_charged_its_listing(tmp_path: Path, entries: str) -> None:
    """One folder of many entries: what `os.walk` holds for the folder it is in, its names, is
    charged once when the walk reaches it, before any name is filtered out (links to a folder are
    listed as folders and then left out), so the charge is never below the walk's peak."""
    target = tmp_path / "target"
    target.mkdir()
    wide = tmp_path / "wide"
    wide.mkdir()
    for index in range(80_000 if entries == "files" else 30_000):
        if entries == "folders":
            (wide / f"f{index:05}").mkdir()
        elif entries == "files":
            (wide / f"f{index:05}.txt").touch()
        else:
            (wide / f"f{index:05}").symlink_to(target)

    seen = _measure(tmp_path)

    assert seen["walk_peak"] <= seen["charged"], seen
