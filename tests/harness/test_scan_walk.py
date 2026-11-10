from __future__ import annotations

import ast
import inspect
import io
import json
import os
import tokenize
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
    assert (scan_walk._MAX_FILES, scan_walk._MAX_BYTES) == (20_000, 268_435_456)
    _write(tmp_path, {"a.py": GRAPH, "b.py": GRAPH, "c.py": GRAPH})
    calls: list[int] = []

    def clock() -> float:
        calls.append(1)
        return 0.0 if len(calls) == 1 else 100.0

    timed = walk_repo(tmp_path, deadline=10.0, clock=clock)

    assert timed.stopped == "clock"
    assert _entries(timed) == ["a.py"]
    assert len(calls) == 2

    monkeypatch.setattr(scan_walk, "_MAX_FILES", 2)
    by_count = _walk(tmp_path)
    assert by_count.stopped == "limit"
    assert _entries(by_count) == ["a.py", "b.py"]

    monkeypatch.setattr(scan_walk, "_MAX_FILES", 20_000)
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


def test_paths_his_rule_rejects_are_left_out(tmp_path: Path) -> None:
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
    no longer hides it behind the file cap."""
    monkeypatch.setattr(scan_walk, "_MAX_FILES", 10)
    _write(tmp_path, {f"tests/test_{index:02}.py": "x = 1\n" for index in range(25)})
    _write(tmp_path, {"zz_agent/agent.py": GRAPH})

    result = _walk(tmp_path)

    assert "zz_agent/agent.py" in _entries(result)
    assert result.stopped == "limit"
