from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from fi.alk.harness.amend import add_rule
from fi.alk.harness.contract import AgentContract, Runtime
from fi.alk.harness.tools import accept_contract, contract_tools
from fi.alk.harness.understand import load, open_stage
from fi.alk.harness.sources import resolve


def payload(**fields):
    return {
        "agent": "pair",
        "modality": "chat",
        "real_use_cases": ["order"],
        "tools": [{"name": "add", "args": ["item_id"]}],
        **fields,
    }


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for name in ("a", "b"):
        (root / "agents" / name).mkdir(parents=True)
        (root / "agents" / name / "agent.py").write_text("x = 1\n")
    (root / "agents" / "b" / "traced.py").write_text("import traceai_openai\n")
    return root


def _saved(destination: Path) -> dict:
    return json.loads((destination / "contract.json").read_text())


def _submit(server, arguments: dict, destination: Path) -> dict:
    """What the model's call does; a first thin submission is sent back once, then accepted."""
    result = {}
    for _ in range(2):
        result = asyncio.run(server.tools[0].handler(arguments))
        if (destination / "contract.json").exists():
            break
    return result


def test_save_defaults_workdir_and_records_folder(tmp_path, repo):
    result = accept_contract(
        payload(), tmp_path / "out", source_root=repo, subfolder="agents/a"
    )

    assert not result.get("is_error")
    runtime = _saved(tmp_path / "out")["runtime"]
    assert runtime["source_subfolder"] == "agents/a"
    assert runtime["workdir"] == "agents/a"


def test_save_refuses_workdir_outside(tmp_path, repo):
    result = accept_contract(
        payload(runtime={"language": "python", "workdir": "agents/b"}),
        tmp_path / "out",
        source_root=repo,
        subfolder="agents/a",
    )

    assert result.get("is_error")
    assert "runtime_workdir_outside_subfolder" in result["content"][0]["text"]
    assert not (tmp_path / "out" / "contract.json").exists()


def test_no_subfolder_contract_dump_unchanged(tmp_path, repo):
    assert "source_subfolder" not in Runtime().model_dump()
    assert "source_subfolder" not in AgentContract(agent="x", runtime=Runtime()).model_dump()["runtime"]
    assert "source_subfolder" not in json.dumps(Runtime.model_json_schema())

    for subfolder in (None, ""):
        destination = tmp_path / f"out-{subfolder!r}"
        accept_contract(
            payload(runtime={"language": "python", "source_subfolder": "agents/z"}),
            destination,
            source_root=repo,
            subfolder=subfolder,
        )
        runtime = _saved(destination)["runtime"]
        assert "source_subfolder" not in runtime
        assert runtime["workdir"] == ""


def test_amend_keeps_recorded_folder(tmp_path, repo):
    accept_contract(payload(), tmp_path, source_root=repo, subfolder="agents/a")
    contract = load(tmp_path)

    added, _ = add_rule(contract, tmp_path, rule="Never share secrets", why="policy")

    assert added
    assert _saved(tmp_path)["runtime"]["source_subfolder"] == "agents/a"


def test_instrumentation_reads_agent_folder(tmp_path, repo):
    own = tmp_path / "own"
    accept_contract(payload(), own, source_root=repo, subfolder="agents/a")
    assert _saved(own)["instrumentation"] == "none"

    whole = tmp_path / "whole"
    accept_contract(payload(), whole, source_root=repo)
    assert _saved(whole)["instrumentation"] == "traceai"


def test_save_on_missing_folder_saves_nothing(tmp_path, repo):
    destination = tmp_path / "out"
    server = contract_tools(destination, source_root=repo, subfolder="agents/zzz")

    result = _submit(server, payload(), destination)

    assert result.get("is_error")
    assert "subfolder_not_found" in result["content"][0]["text"]
    assert not (destination / "contract.json").exists()


def test_save_with_no_source_root_works(tmp_path):
    result = accept_contract(payload(), tmp_path, subfolder="agents/a")

    assert not result.get("is_error")
    assert "source_subfolder" not in (_saved(tmp_path).get("runtime") or {})


def test_no_subfolder_null_runtime_stays_null(tmp_path, repo):
    source = resolve("repo", name="demo", root=str(repo), subfolder="")
    stage, destination = open_stage(source, out=tmp_path / "out")

    _submit(stage.spec.servers["contract"], payload(), destination)

    assert _saved(destination).get("runtime") is None


@pytest.mark.parametrize("subfolder", ["agents/a", ""])
def test_open_stage_saves_the_folder(tmp_path, repo, subfolder):
    source = resolve("repo", name="demo", root=str(repo), subfolder=subfolder)
    stage, destination = open_stage(source, out=tmp_path / "out")

    _submit(
        stage.spec.servers["contract"],
        payload(runtime={"language": "python"}),
        destination,
    )

    runtime = _saved(destination)["runtime"]
    if subfolder:
        assert runtime["source_subfolder"] == "agents/a"
        assert runtime["workdir"] == "agents/a"
    else:
        assert "source_subfolder" not in runtime
        assert runtime["workdir"] == ""


def _compose_stack(root: Path, location: str, builds: str) -> dict:
    (root / location).parent.mkdir(parents=True, exist_ok=True)
    (root / location).write_text(
        f"services:\n  agent:\n    build: {builds}\n    command: [python, agent.py]\n"
    )
    return {
        "language": "python",
        "execution": "compose",
        "compose_file": location,
        "service": "agent",
    }


@pytest.mark.parametrize(
    ("location", "builds"),
    [("compose.yaml", "agents/b"), ("agents/a/compose.yaml", "../b")],
)
def test_save_refuses_a_control_service_building_a_sibling(tmp_path, repo, location, builds):
    runtime = _compose_stack(repo, location, builds)

    result = accept_contract(
        payload(runtime=runtime), tmp_path / "out", source_root=repo, subfolder="agents/a"
    )

    assert result.get("is_error")
    assert "runtime_compose_service_outside_subfolder" in result["content"][0]["text"]
    assert not (tmp_path / "out" / "contract.json").exists()


def test_save_keeps_a_control_service_building_the_folder(tmp_path, repo):
    runtime = _compose_stack(repo, "compose.yaml", "agents/a")

    result = accept_contract(
        payload(runtime=runtime), tmp_path / "out", source_root=repo, subfolder="agents/a"
    )

    assert not result.get("is_error")
    assert _saved(tmp_path / "out")["runtime"]["source_subfolder"] == "agents/a"


def _compose_with_declared_port(root: Path, location: str, builds: str) -> dict:
    (root / location).parent.mkdir(parents=True, exist_ok=True)
    (root / location).write_text(
        f"services:\n  agent:\n    build: {builds}\n    command: [python, agent.py]\n"
        '    ports: ["${AGENT_PORT}:8080"]\n'
    )
    (root / "agents" / "a" / "alk.yaml").write_text(
        'runtime:\n  environment:\n    AGENT_PORT: "9000"\n'
    )
    return {
        "language": "python",
        "execution": "compose",
        "compose_file": location,
        "service": "agent",
    }


def test_save_reads_a_siblings_build_through_a_declared_value(tmp_path, repo):
    (repo / "compose.yaml").write_text(
        "services:\n  agent:\n    build: ${AGENT_DIR}\n    command: [python, agent.py]\n"
    )
    (repo / "agents" / "a" / "alk.yaml").write_text(
        'runtime:\n  environment:\n    AGENT_DIR: "agents/b"\n'
    )
    runtime = {
        "language": "python",
        "execution": "compose",
        "compose_file": "compose.yaml",
        "service": "agent",
    }

    result = accept_contract(
        payload(runtime=runtime), tmp_path / "out", source_root=repo, subfolder="agents/a"
    )

    assert "runtime_compose_service_outside_subfolder" in result["content"][0]["text"]


def test_save_still_refuses_a_sibling_with_declared_values(tmp_path, repo):
    runtime = _compose_with_declared_port(repo, "compose.yaml", "agents/b")

    result = accept_contract(
        payload(runtime=runtime), tmp_path / "out", source_root=repo, subfolder="agents/a"
    )

    assert "runtime_compose_service_outside_subfolder" in result["content"][0]["text"]


def test_save_leaves_a_bad_alk_yaml_to_bundling(tmp_path, repo):
    (repo / "agents" / "a" / "alk.yaml").write_text("- not an object\n")

    result = accept_contract(
        payload(runtime={"language": "python"}),
        tmp_path / "out",
        source_root=repo,
        subfolder="agents/a",
    )

    assert not result.get("is_error"), result
    assert (tmp_path / "out" / "contract.json").exists()


_GOOD_SERVICE = "services:\n  agent:\n    build: .\n"


@pytest.mark.parametrize(
    ("files", "links", "runtime"),
    [
        ({"docker-compose.yml": "x: " + "[" * 1000 + "]" * 1000 + "\n"}, [], {}),
        ({"docker-compose.yml": "x: [\n"}, [], {}),
        (
            {"docker-compose.yml": "include:\n  - loop.yml\n" + _GOOD_SERVICE},
            [("loop.yml", "loop.yml")],
            {},
        ),
        ({"docker-compose.yml": "include:\n  - " + "a" * 5000 + ".yml\n" + _GOOD_SERVICE}, [], {}),
        ({}, [], {"compose_file": "agents/a/" + "a" * 5000 + ".yml"}),
        ({"docker-compose.yml": 'include:\n  - "a\\0b.yml"\n' + _GOOD_SERVICE}, [], {}),
        ({}, [], {"compose_file": "agents/a/missing.yml"}),
    ],
    ids=["deep", "invalid", "include-loop", "include-long", "named-long", "include-nul", "named-missing"],
)
def test_a_compose_file_nobody_can_read_does_not_stop_the_save(
    tmp_path, repo, files, links, runtime
):
    folder = repo / "agents" / "a"
    for name, body in files.items():
        (folder / name).write_text(body)
    for name, target in links:
        (folder / name).symlink_to(target)

    result = accept_contract(
        payload(runtime={"language": "python", **runtime}),
        tmp_path / "out",
        source_root=repo,
        subfolder="agents/a",
    )

    assert not result.get("is_error"), result
    assert (tmp_path / "out" / "contract.json").exists()


def test_save_checks_a_tool_in_the_folder_the_run_imports_from(tmp_path, repo):
    (repo / "agent.py").write_text("def lookup():\n    return 1\n")
    (repo / "agents" / "a" / "agent.py").write_text("# def lookup():\n#     return 1\n")
    entry = {"tool": "lookup", "mode": "import", "module": "agent", "callable": "lookup"}

    result = accept_contract(
        payload(tools=[{"name": "lookup"}], tool_entrypoints=[entry]),
        tmp_path / "out",
        source_root=repo,
        subfolder="agents/a",
    )

    assert result.get("is_error"), result
    assert "lookup" in result["content"][0]["text"]
    assert not (tmp_path / "out" / "contract.json").exists()


def _alias_bomb(levels: int) -> str:
    compose = "x-0: &a0 [v,v,v,v,v,v,v,v,v]\n" + "".join(
        f"x-{level}: &a{level} [{','.join([f'*a{level - 1}'] * 9)}]\n" for level in range(1, levels)
    )
    return compose + f"services:\n  agent:\n    build: {{context: ., args: *a{levels - 1}}}\n"


def _anchors(levels: int, leaf: str = "v") -> str:
    return f"x-0: &a0 [{','.join([leaf] * 9)}]\n" + "".join(
        f"x-{level}: &a{level} [{','.join([f'*a{level - 1}'] * 9)}]\n" for level in range(1, levels)
    )


def _many_services(services: int, levels: int) -> str:
    top = f"*a{levels - 1}"
    return _anchors(levels) + "services:\n" + "".join(
        f"  s{index}: {{build: {{context: ., args: {top}}}, image: {top}, ports: {top}, "
        f"deploy: {top}, volumes: {top}}}\n"
        for index in range(services)
    )


def _long_string(length: int, levels: int) -> str:
    return (
        f'x-s: &s "{"v" * length}"\n'
        + _anchors(levels, leaf="*s")
        + f"services:\n  agent:\n    build: {{context: ., args: *a{levels - 1}}}\n"
    )


def _dict_bomb(levels: int) -> str:
    def keys(ref):
        return "{" + ", ".join(f"k{index}: {ref}" for index in range(9)) + "}"

    return (
        f"x-0: &a0 {keys('v')}\n"
        + "".join(f"x-{level}: &a{level} {keys(f'*a{level - 1}')}\n" for level in range(1, levels))
        + f"services:\n  agent:\n    build: {{context: ., args: *a{levels - 1}}}\n"
    )


def _deep_groups(depth: int, size: int) -> str:
    group = "[" * depth + "]" * depth
    return "x-a: [" + ",".join([group] * (size // (len(group) + 1))) + "]\n"


def _merge_chain(keys: int, levels: int) -> str:
    # Each level is a new mapping holding every key above it.
    return (
        "x-0: &m0 {" + ", ".join(f"k{index}: 1" for index in range(keys)) + "}\n"
        + "".join(f"x-{level}: &m{level} {{<<: *m{level - 1}, l{level}: 1}}\n" for level in range(1, levels))
    )


@pytest.mark.parametrize(
    "name, text",
    [
        pytest.param("docker-compose.yml", _alias_bomb(9), id="nested-aliases"),
        pytest.param("docker-compose.yml", _many_services(200, 5), id="many-services"),
        pytest.param("docker-compose.yml", _long_string(60_000, 4), id="long-string"),
        pytest.param("docker-compose.yml", _dict_bomb(8), id="dict-aliases"),
        pytest.param(
            "docker-compose.yml",
            _merge_chain(20_000, 2_000) + "services:\n  agent:\n    build: .\n",
            id="compose-merge-keys",
        ),
        pytest.param("alk.yaml", _merge_chain(20_000, 2_000), id="alk-merge-keys"),
    ],
)
def test_a_crafted_yaml_file_does_not_hold_up_the_save(tmp_path, repo, name, text):
    import time

    (repo / "agents" / "a" / name).write_text(text)
    started = time.monotonic()

    result = accept_contract(
        payload(runtime={"language": "python"}),
        tmp_path / "out",
        source_root=repo,
        subfolder="agents/a",
    )

    assert time.monotonic() - started < 5
    assert not result.get("is_error"), result


def test_selection_refuses_a_compose_file_of_nested_aliases(repo):
    from fi.alk.harness.runtime_selection import RuntimeSelectionError, select_runtime

    (repo / "agents" / "a" / "docker-compose.yml").write_text(_alias_bomb(9))

    with pytest.raises(RuntimeSelectionError, match="compose_structural_interpolation_limit"):
        select_runtime(
            repo,
            {"language": "python", "source_subfolder": "agents/a", "workdir": "agents/a"},
            validate_environment_dependencies=False,
        )


def _select_in_folder(repo):
    from fi.alk.harness.runtime_selection import select_runtime

    return select_runtime(
        repo,
        {"language": "python", "source_subfolder": "agents/a", "workdir": "agents/a"},
        validate_environment_dependencies=False,
    )


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(_alias_bomb(4), id="nested-aliases"),
        # Each value is under the budget alone; together they're over it.
        pytest.param(_many_services(20, 2), id="many-services"),
        # Few nodes, many characters.
        pytest.param(_long_string(5_000, 2), id="long-string"),
        pytest.param(_dict_bomb(3), id="dict-aliases"),
    ],
)
def test_one_budget_covers_the_whole_selection(repo, monkeypatch, text):
    from fi.alk.harness import runtime_selection

    monkeypatch.setattr(runtime_selection, "_STRUCTURE_NODES", 1_000)
    (repo / "agents" / "a" / "docker-compose.yml").write_text(text)

    with pytest.raises(
        runtime_selection.RuntimeSelectionError, match="compose_structural_interpolation_limit"
    ):
        _select_in_folder(repo)


@pytest.mark.parametrize(
    "text, entries, code",
    [
        pytest.param(_merge_chain(20, 10), 100, "compose_yaml_too_large", id="wide"),
        # Under the entry limit, but deeper than any real file nests its merges.
        pytest.param(_merge_chain(1, 40), None, "compose_yaml_merge_cycle_or_depth", id="deep"),
        pytest.param(_deep_groups(17, 100), None, "compose_yaml_too_deep", id="deep-flow"),
    ],
)
def test_selection_refuses_merge_keys_that_build_too_much(repo, monkeypatch, text, entries, code):
    from fi.alk.harness import runtime_selection

    if entries:
        monkeypatch.setattr(runtime_selection, "_YAML_ENTRIES", entries)
    (repo / "agents" / "a" / "docker-compose.yml").write_text(
        text + "services:\n  agent:\n    build: .\n"
    )

    with pytest.raises(runtime_selection.RuntimeSelectionError, match=f"^{code}$"):
        _select_in_folder(repo)


def test_bounded_loading_reads_an_honest_file_as_yaml_does(repo):
    import yaml

    from fi.alk.harness.runtime_selection import bounded_yaml_load

    text = (
        "x-common: &common {restart: always, environment: {A: '1'}}\n"
        "x-more: &more {<<: *common, init: true}\n"
        "services:\n"
        "  agent: {<<: [*more], build: ., ports: ['8080:8080']}\n"
        "  worker: {<<: *common, build: ., depends_on: [agent]}\n"
    )
    (repo / "agents" / "a" / "docker-compose.yml").write_text(text)

    assert bounded_yaml_load(text) == yaml.safe_load(text)
    assert bounded_yaml_load("") is None
    _select_in_folder(repo)


def test_runtime_manifest_refuses_merge_keys_that_build_too_much(repo, monkeypatch):
    from fi.alk.harness import runtime_selection
    from fi.alk.harness.bundle_author_v2 import BundleAuthorError, _declared_runtime_environment

    monkeypatch.setattr(runtime_selection, "_YAML_ENTRIES", 100)
    (repo / "agents" / "a" / "alk.yaml").write_text(_merge_chain(20, 10))

    with pytest.raises(BundleAuthorError, match="runtime_manifest_invalid: yaml_too_large"):
        _declared_runtime_environment(repo / "agents" / "a")


def test_runtime_manifest_refuses_a_file_past_its_size(repo, monkeypatch):
    from fi.alk.harness import bundle_author_v2

    monkeypatch.setattr(bundle_author_v2, "_LARGEST_RUNTIME_MANIFEST", 10)
    (repo / "agents" / "a" / "alk.yaml").write_text("runtime: {environment: {}}\n")

    with pytest.raises(bundle_author_v2.BundleAuthorError, match="larger than"):
        bundle_author_v2._declared_runtime_environment(repo / "agents" / "a")


def test_loading_refuses_a_base_60_number_too_long_to_build(repo):
    import time

    from fi.alk.harness.bundle_author_v2 import BundleAuthorError, _declared_runtime_environment
    from fi.alk.harness.runtime_selection import YamlTooLarge, bounded_yaml_load

    assert bounded_yaml_load("t: 1:30:00\nu: " + ":".join(["1"] * 17) + "\n")["t"] == 5400
    # Only numbers: text with more colons, such as a PATH, still loads.
    many = ":".join(["1"] * 30)
    assert bounded_yaml_load(f'p: "{many}"\nq: a:{many}\n') == {"p": many, "q": f"a:{many}"}
    started = time.monotonic()
    with pytest.raises(YamlTooLarge, match="yaml_scalar_too_large"):
        bounded_yaml_load("x-a: " + ":".join(["1"] * 200_000) + "\n")
    assert time.monotonic() - started < 2

    (repo / "agents" / "a" / "alk.yaml").write_text("x-a: " + ":".join(["1"] * 18) + "\n")
    with pytest.raises(BundleAuthorError, match="runtime_manifest_invalid: yaml_scalar_too_large"):
        _declared_runtime_environment(repo / "agents" / "a")


def test_loading_refuses_flow_nesting_past_16(repo):
    import time

    from fi.alk.harness.bundle_author_v2 import BundleAuthorError, _declared_runtime_environment
    from fi.alk.harness.runtime_selection import YamlTooLarge, bounded_yaml_load

    assert bounded_yaml_load("x: " + "[" * 16 + "]" * 16 + "\n") is not None
    with pytest.raises(YamlTooLarge, match="yaml_too_deep"):
        bounded_yaml_load("x: " + "{a: " * 16 + "[]" + "}" * 16 + "\n")
    # About 4 s to read under the save's 128 KB before the limit.
    started = time.monotonic()
    with pytest.raises(YamlTooLarge, match="yaml_too_deep"):
        bounded_yaml_load(_deep_groups(400, 120_000))
    assert time.monotonic() - started < 0.5

    (repo / "agents" / "a" / "alk.yaml").write_text(_deep_groups(17, 100))
    with pytest.raises(BundleAuthorError, match="runtime_manifest_invalid: yaml_too_deep"):
        _declared_runtime_environment(repo / "agents" / "a")


@pytest.mark.parametrize("name", ["docker-compose.yml", "alk.yaml"])
def test_deep_flow_nesting_does_not_hold_up_the_save(tmp_path, repo, name):
    import time

    # About 4 s to read before the limit.
    (repo / "agents" / "a" / name).write_text(_deep_groups(400, 120_000))
    started = time.monotonic()

    result = accept_contract(
        payload(runtime={"language": "python"}),
        tmp_path / "out",
        source_root=repo,
        subfolder="agents/a",
    )

    assert time.monotonic() - started < 1
    assert not result.get("is_error"), result


def test_loading_refuses_a_long_tag_prefix():
    from fi.alk.harness.runtime_selection import YamlTooLarge, bounded_yaml_load

    assert bounded_yaml_load("%TAG !e! tag:yaml.org,2002:\n---\na: !e!str 5\n") == {"a": "5"}
    # Each `!a` gets its own copy of the prefix: 131 KB built about 1 GB.
    with pytest.raises(YamlTooLarge, match="yaml_tag_too_long"):
        bounded_yaml_load("%TAG !a! tag:" + "x" * 1025 + "\n--- !a!x\na: 1\n")


def test_the_check_runs_off_the_loop_and_a_refusal_meanwhile_wins(tmp_path, repo, monkeypatch):
    import time

    from fi.alk.harness import tools
    from fi.alk.harness.failure_file import FAILURE_FILE

    destination = tmp_path / "out"
    server = contract_tools(destination, source_root=repo, scan_root=repo, subfolder="agents/a")
    arguments = payload(
        runtime={"language": "python"}, hard_constraints=["be polite"], data_store={"kind": "none"}
    )

    def slow(*args):
        time.sleep(0.5)

    async def save(during):
        worst = [0.0]
        done = asyncio.Event()

        async def beat():
            last = time.perf_counter()
            while not done.is_set():
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                worst[0], last = max(worst[0], now - last), now

        beating = asyncio.create_task(beat())
        monkeypatch.setattr(tools, "_builds_a_sibling", during)
        result = await server.tools[0].handler(arguments)
        done.set()
        await beating
        return result, worst[0]

    result, worst = asyncio.run(save(slow))
    assert (destination / "contract.json").exists(), result
    assert worst < 0.25

    (destination / "contract.json").unlink()

    def refused_meanwhile(*args):
        (destination / FAILURE_FILE).write_text("{}")

    asyncio.run(save(refused_meanwhile))
    assert not (destination / "contract.json").exists()


def test_an_alk_yaml_without_end_is_read_only_to_its_limit(tmp_path, repo, monkeypatch, capsys):
    import io

    from fi.alk.harness.bundle_author_v2 import BundleAuthorError, _declared_runtime_environment

    class Endless(io.RawIOBase):
        def readable(self):
            return True

        def read(self, size=-1):
            assert size >= 0, "read to the end"
            return b"#" * size

    folder = repo / "agents" / "a"
    (folder / "alk.yaml").write_text("")
    (folder / "compose.yaml").write_text("services:\n  agent:\n    build: ../b\n")
    opened = Path.open
    # Like a link to /proc/self/pagemap: its size says 0 and it reads without end.
    monkeypatch.setattr(
        Path,
        "open",
        lambda path, *args, **kwargs: Endless()
        if path.name == "alk.yaml"
        else opened(path, *args, **kwargs),
    )
    with pytest.raises(BundleAuthorError, match="larger than 1 MiB"):
        _declared_runtime_environment(folder)
    result = accept_contract(
        payload(runtime=_SIBLING), tmp_path / "out", source_root=repo, subfolder="agents/a"
    )

    assert not result.get("is_error"), result
    assert capsys.readouterr().err == ""


def test_one_count_covers_every_included_file(repo, monkeypatch):
    from fi.alk.harness import runtime_selection

    included = "x-b: [" + ", ".join(["1"] * 30) + "]\n"
    main = "include:\n  - more.yml\nx-a: [" + ", ".join(["1"] * 30) + "]\n" + _GOOD_SERVICE
    counts = []
    for text in (main, included):
        budget = [10**6]
        runtime_selection.bounded_yaml_load(text, budget)
        counts.append(10**6 - budget[0])
    # Each file is under the count alone; together they're over it.
    monkeypatch.setattr(runtime_selection, "_YAML_ENTRIES", max(counts) + 1)
    (repo / "agents" / "a" / "docker-compose.yml").write_text(main)
    (repo / "agents" / "a" / "more.yml").write_text(included)

    with pytest.raises(runtime_selection.RuntimeSelectionError, match="compose_yaml_too_large"):
        _select_in_folder(repo)


def _plain_list(items: int) -> str:
    return "x-a: [" + ",".join(["1"] * items) + "]\n"


_SIBLING = {
    "language": "python",
    "execution": "compose",
    "compose_file": "agents/a/compose.yaml",
    "service": "agent",
}


@pytest.mark.parametrize(
    "files",
    [
        # Each took the save 5-10 s to read on the event loop.
        pytest.param({"compose.yaml": _plain_list(1_000_000)}, id="compose"),
        pytest.param({"alk.yaml": _plain_list(500_000)}, id="alk"),
        # Under the save's size alone; together over it.
        pytest.param(
            {"compose.yaml": "#" * 70_000 + "\n", "alk.yaml": "#" * 70_000 + "\n"},
            id="together",
        ),
    ],
)
def test_save_leaves_files_past_real_sizes_to_bundling(tmp_path, repo, capsys, files):
    import time

    from fi.alk.harness.runtime_selection import COMPOSE_SERVICE_OUTSIDE_FOLDER, select_runtime

    folder = repo / "agents" / "a"
    sibling = "services:\n  agent:\n    build: ../b\n"
    (folder / "compose.yaml").write_text(files.get("compose.yaml", "") + sibling)
    if "alk.yaml" in files:
        (folder / "alk.yaml").write_text(files["alk.yaml"])
    started = time.monotonic()

    result = accept_contract(
        payload(runtime=_SIBLING), tmp_path / "out", source_root=repo, subfolder="agents/a"
    )

    assert time.monotonic() - started < 2
    assert not result.get("is_error"), result
    assert capsys.readouterr().err == ""
    if len(files) == 2:
        # Bundling reads up to 2 MB and refuses the same service; this row is quick to read.
        with pytest.raises(ValueError, match=COMPOSE_SERVICE_OUTSIDE_FOLDER):
            select_runtime(
                repo,
                {**_SIBLING, "source_subfolder": "agents/a", "workdir": "agents/a"},
                validate_environment_dependencies=False,
            )


def test_save_leaves_compose_overlays_to_bundling(tmp_path, repo, capsys, monkeypatch):
    from fi.alk.harness import compose_merge

    merged = []
    monkeypatch.setattr(compose_merge, "merge_compose", lambda *args, **kwargs: merged.append(args))
    folder = repo / "agents" / "a"
    (folder / "compose.yaml").write_text("services:\n  agent:\n    build: ../b\n")
    (folder / "override.yaml").write_text("services:\n  agent:\n    init: true\n")

    result = accept_contract(
        payload(runtime={**_SIBLING, "compose_overrides": ["agents/a/override.yaml"]}),
        tmp_path / "out",
        source_root=repo,
        subfolder="agents/a",
    )

    assert not result.get("is_error"), result
    assert merged == []
    assert capsys.readouterr().err == ""


def test_a_skipped_check_names_the_error_type_alone(tmp_path, repo, capsys):
    (repo / "agents" / "a" / "alk.yaml").write_text("runtime: [customer-line-42\n")

    result = accept_contract(
        payload(runtime={"language": "python"}),
        tmp_path / "out",
        source_root=repo,
        subfolder="agents/a",
    )

    assert not result.get("is_error"), result
    assert capsys.readouterr().err == "sibling check skipped: BundleAuthorError\n"


@pytest.mark.parametrize("shell", ["sh", "/bin/sh"])
def test_save_refuses_a_shell_command_only_under_a_folder(tmp_path, repo, shell):
    runtime = {"language": "python", "command": [shell, "-c", "cd ../b && python agent.py"]}

    refused = accept_contract(
        payload(runtime=runtime), tmp_path / "in", source_root=repo, subfolder="agents/a"
    )
    kept = accept_contract(
        payload(runtime=runtime), tmp_path / "root", source_root=repo
    )

    assert refused.get("is_error")
    assert "runtime_command_shell_forbidden" in refused["content"][0]["text"]
    assert not (tmp_path / "in" / "contract.json").exists()
    assert not kept.get("is_error")
    assert _saved(tmp_path / "root")["runtime"]["command"] == runtime["command"]


def _deploy(folder: Path, value: str, copies: int = 1, build: str = ".") -> None:
    (folder / "compose.yaml").write_text(
        f"x-s: &s '{value}'\nservices:\n  agent:\n    build: {build}\n    deploy: ["
        + ", ".join(["*s"] * copies)
        + "]\n"
    )


def _select_compose(repo: Path, environment: dict[str, str]):
    from fi.alk.harness.runtime_selection import select_runtime

    return select_runtime(
        repo,
        {
            "language": "python",
            "execution": "compose",
            "compose_file": "agents/a/compose.yaml",
            "service": "agent",
            "source_subfolder": "agents/a",
            "workdir": "agents/a",
        },
        public_environment=environment,
        validate_environment_dependencies=False,
    )


@pytest.mark.parametrize("reference", ["$A", "${A}"])
@pytest.mark.parametrize("times, refused", [(65, False), (66, True)])
def test_selection_refuses_a_value_repeated_past_the_string_limit(
    repo, reference, times, refused
):
    from fi.alk.harness.runtime_selection import RuntimeSelectionError

    _deploy(repo / "agents" / "a", reference * times)
    if refused:
        with pytest.raises(RuntimeSelectionError, match="^compose_structural_interpolation_limit$"):
            _select_compose(repo, {"A": "x" * 1000})
    else:
        _select_compose(repo, {"A": "x" * 1000})


@pytest.mark.parametrize("copies, refused", [(100, False), (200, True)])
def test_what_a_string_builds_is_charged_for_every_alias(repo, copies, refused):
    from fi.alk.harness.runtime_selection import RuntimeSelectionError

    _deploy(repo / "agents" / "a", "$A", copies)
    if refused:
        with pytest.raises(RuntimeSelectionError, match="^compose_structural_interpolation_limit$"):
            _select_compose(repo, {"A": "x" * 60_000})
    else:
        _select_compose(repo, {"A": "x" * 60_000})


@pytest.mark.parametrize("times, raised", [(65, None), (66, "compose_structural_interpolation_limit")])
def test_the_save_caps_a_value_from_alk_yaml_repeated_in_compose(
    tmp_path, repo, capsys, monkeypatch, times, raised
):
    from fi.alk.harness import tools

    folder = repo / "agents" / "a"
    (folder / "alk.yaml").write_text("runtime:\n  environment:\n    A: '" + "x" * 1000 + "'\n")
    _deploy(folder, "$A" * times)
    seen = []
    real = tools.select_runtime

    def recorded(*args, **kwargs):
        try:
            return real(*args, **kwargs)
        except Exception as exc:
            seen.append(str(exc))
            raise

    monkeypatch.setattr(tools, "select_runtime", recorded)

    result = accept_contract(
        payload(runtime={**_SIBLING, "service": "agent"}),
        tmp_path / "out",
        source_root=repo,
        subfolder="agents/a",
    )

    assert not result.get("is_error"), result
    assert seen == ([raised] if raised else [])
    assert capsys.readouterr().err == ""


def test_loading_counts_mapping_keys(repo):
    from fi.alk.harness.runtime_selection import YamlTooLarge, bounded_yaml_load

    text = "{" + ", ".join(f"k{i}: {i}" for i in range(10)) + "}"

    # The mapping, what it merges, ten keys and ten values.
    assert len(bounded_yaml_load(text, [31])) == 10
    with pytest.raises(YamlTooLarge):
        bounded_yaml_load(text, [30])


def _racing_server(tmp_path, repo):
    destination = tmp_path / "out"
    server = contract_tools(destination, source_root=repo, scan_root=repo, subfolder="agents/a")
    return destination, server.tools[0].handler


def _plain(notes: str) -> dict:
    return payload(
        runtime={"language": "python"},
        hard_constraints=["be polite"],
        data_store={"kind": "none"},
        notes=notes,
    )


def test_the_contract_sent_last_is_saved_when_an_earlier_one_finishes_later(
    tmp_path, repo, monkeypatch
):
    import threading

    from fi.alk.harness import tools

    destination, submit = _racing_server(tmp_path, repo)
    second_checked = threading.Event()
    real = tools._checked_contract

    def checked(arguments, **kwargs):
        result = real(arguments, **kwargs)
        if arguments.get("notes") == "sent-first":
            assert second_checked.wait(10)
        else:
            second_checked.set()
        return result

    monkeypatch.setattr(tools, "_checked_contract", checked)

    async def both():
        return await asyncio.gather(submit(_plain("sent-first")), submit(_plain("sent-second")))

    first, second = asyncio.run(both())

    assert _saved(destination)["notes"] == "sent-second"
    assert first["content"][0]["text"].startswith("Not saved: a contract submitted after this one")
    assert not second.get("is_error"), second


def test_a_refusal_during_the_check_wins_though_its_failure_file_is_not_written(
    tmp_path, repo, monkeypatch
):
    import threading

    from fi.alk.harness import tools

    destination, submit = _racing_server(tmp_path, repo)
    checking, refused = threading.Event(), threading.Event()
    real = tools._checked_contract

    def checked(arguments, **kwargs):
        checking.set()
        assert refused.wait(10)
        return real(arguments, **kwargs)

    def unwritable(*args):
        refused.set()
        raise OSError("disk full")

    monkeypatch.setattr(tools, "_checked_contract", checked)
    monkeypatch.setattr(tools, "decide", lambda claimed, *args, **kwargs: "refuse" if claimed == "browser" else "keep")
    monkeypatch.setattr(tools, "write_failure_file", unwritable)

    async def both():
        saving = asyncio.ensure_future(submit(_plain("saving")))
        # The refusal comes once the check is under way, so only the check after it can see it.
        assert await asyncio.to_thread(checking.wait, 10)
        with pytest.raises(OSError):
            await submit({**_plain("refusing"), "modality": "browser"})
        return await saving

    saving = asyncio.run(both())

    assert not (destination / "contract.json").exists()
    assert "Don't submit again" in saving["content"][0]["text"]


def test_a_crafted_python_file_does_not_hold_up_the_conversation(tmp_path, repo):
    import time

    from fi.alk.harness.python_source import LARGEST_PARSED_MODULE

    # Parsing this costs the square of its size: about 7 s on 3.12 and 0.7 s on 3.11 at 1 MiB.
    line = "f'{a}'\n"
    lines = (1024 * 1024) // len(line)
    (repo / "agents" / "a" / "crafted.py").write_text("import traceai_openai\n" + line * lines)
    # Just under the limit it's parsed, and that is quick.
    small = "import traceai_openai\n" + line * ((LARGEST_PARSED_MODULE // 2) // len(line))
    (repo / "agents" / "a" / "small.py").write_text(small)
    destination, submit = _racing_server(tmp_path, repo)

    async def save():
        worst = [0.0]
        done = asyncio.Event()

        async def beat():
            last = time.perf_counter()
            while not done.is_set():
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                worst[0], last = max(worst[0], now - last), now

        beating = asyncio.create_task(beat())
        result = await submit(_plain("crafted"))
        done.set()
        await beating
        return result, worst[0]

    result, worst = asyncio.run(save())

    assert not result.get("is_error"), result
    assert _saved(destination)["instrumentation"] == "traceai"
    assert worst < 0.25


@pytest.mark.parametrize("copies, refused", [(3, False), (4, True)])
def test_each_reference_is_charged_however_little_it_builds(repo, copies, refused):
    from fi.alk.harness.runtime_selection import RuntimeSelectionError

    # An empty value builds nothing, but each `$A` is still worked: 30,000 a copy.
    _deploy(repo / "agents" / "a", "$A" * 30_000, copies)
    if refused:
        with pytest.raises(RuntimeSelectionError, match="^compose_structural_interpolation_limit$"):
            _select_compose(repo, {"A": ""})
    else:
        _select_compose(repo, {"A": ""})


@pytest.mark.parametrize(
    "text, refused",
    [
        ("x" * 536 + "$A", False),
        ("x" * 537 + "$A", True),
        # What it keeps after the last reference, too.
        ("${A}" + "x" * 536, False),
        ("${A}" + "x" * 537, True),
    ],
)
def test_what_a_string_keeps_counts_towards_what_it_builds(repo, text, refused):
    from fi.alk.harness.runtime_selection import RuntimeSelectionError

    _deploy(repo / "agents" / "a", text)
    if refused:
        with pytest.raises(RuntimeSelectionError, match="^compose_structural_interpolation_limit$"):
            _select_compose(repo, {"A": "y" * 65_000})
    else:
        _select_compose(repo, {"A": "y" * 65_000})


@pytest.mark.parametrize("copies, refused", [(3, False), (4, True)])
def test_each_brace_is_charged(repo, copies, refused):
    from fi.alk.harness.runtime_selection import RuntimeSelectionError

    # 16,000 `${A}` a copy: 32,000 with its closing brace charged, 16,000 with the `$` alone, so
    # a fourth copy passes 100,000 only when the braces count.
    _deploy(repo / "agents" / "a", "${A}" * 16_000, copies)
    if refused:
        with pytest.raises(RuntimeSelectionError, match="^compose_structural_interpolation_limit$"):
            _select_compose(repo, {"A": ""})
    else:
        _select_compose(repo, {"A": ""})


def test_an_earlier_contract_is_saved_when_the_later_ones_write_fails(tmp_path, repo, monkeypatch):
    import threading

    from fi.alk.harness import tools

    destination, submit = _racing_server(tmp_path, repo)
    second_written = threading.Event()
    real_check, real_save = tools._checked_contract, tools._save_contract

    def checked(arguments, **kwargs):
        result = real_check(arguments, **kwargs)
        if arguments.get("notes") == "sent-first":
            assert second_written.wait(10)
        return result

    def save(contract, where):
        if contract.notes == "sent-second":
            second_written.set()
            raise OSError("disk full")
        return real_save(contract, where)

    monkeypatch.setattr(tools, "_checked_contract", checked)
    monkeypatch.setattr(tools, "_save_contract", save)

    async def both():
        return await asyncio.gather(
            submit(_plain("sent-first")), submit(_plain("sent-second")), return_exceptions=True
        )

    first, second = asyncio.run(both())

    assert isinstance(second, OSError)
    assert not first.get("is_error"), first
    assert _saved(destination)["notes"] == "sent-first"


@pytest.mark.parametrize("over, refused", [(0, False), (1, True)])
def test_the_prompt_is_copied_only_from_a_file_up_to_1_mib(tmp_path, repo, over, refused):
    folder = repo / "agents" / "a"
    line = "Be brief.\n"
    (folder / "prompt.txt").write_text(line + "#" * (1024 * 1024 + over - len(line)))

    result = accept_contract(
        payload(
            runtime={"language": "python"},
            system_prompt_from=[{"path": "agents/a/prompt.txt", "start_line": 1, "end_line": 1}],
        ),
        tmp_path / "out",
        source_root=repo,
        subfolder="agents/a",
    )

    assert bool(result.get("is_error")) is refused, result
    if refused:
        assert "larger than 1 MiB" in result["content"][0]["text"]


@pytest.mark.parametrize("copies, refused", [(100, False), (200, True)])
def test_a_strings_length_is_charged_though_it_builds_nothing(repo, copies, refused):
    from fi.alk.harness.runtime_selection import RuntimeSelectionError

    # A unset, so `:+` builds nothing; the 60,000 characters are still read for every alias.
    _deploy(repo / "agents" / "a", "${A:+" + "x" * 60_000 + "}", copies)
    if refused:
        with pytest.raises(RuntimeSelectionError, match="^compose_structural_interpolation_limit$"):
            _select_compose(repo, {})
    else:
        _select_compose(repo, {})


def test_a_prompt_file_without_end_is_read_only_to_its_limit(tmp_path, repo, monkeypatch):
    import io

    class Endless(io.RawIOBase):
        def readable(self):
            return True

        def read(self, size=-1):
            assert size >= 0, "read to the end"
            return b"#" * size

    (repo / "agents" / "a" / "prompt.txt").write_text("")
    opened = Path.open
    monkeypatch.setattr(
        Path,
        "open",
        lambda path, *args, **kwargs: Endless()
        if path.name == "prompt.txt"
        else opened(path, *args, **kwargs),
    )

    result = accept_contract(
        payload(
            runtime={"language": "python"},
            system_prompt_from=[{"path": "agents/a/prompt.txt", "start_line": 1, "end_line": 1}],
        ),
        tmp_path / "out",
        source_root=repo,
        subfolder="agents/a",
    )

    assert "larger than 1 MiB" in result["content"][0]["text"], result


@pytest.mark.parametrize(
    "helper, image_only, refused",
    [("../b", False, True), (".", False, False), ("../b", True, False)],
    ids=["builds-a-sibling", "builds-the-folder", "pulled-as-an-image"],
)
def test_every_service_the_stack_runs_is_checked_for_a_sibling(repo, helper, image_only, refused):
    from fi.alk.harness.runtime_selection import COMPOSE_SERVICE_OUTSIDE_FOLDER, RuntimeSelectionError, select_runtime

    folder = repo / "agents" / "a"
    for name in ("a", "b"):
        (repo / "agents" / name / "Dockerfile").write_text("FROM python\n")
    (folder / "compose.yaml").write_text(
        "services:\n  agent:\n    build: .\n    depends_on: [helper]\n"
        f"  helper:\n    build: {helper}\n    image: helper:1\n"
    )
    runtime = {
        "language": "python",
        "execution": "compose",
        "compose_file": "agents/a/compose.yaml",
        "service": "agent",
        "source_subfolder": "agents/a",
        "workdir": "agents/a",
        "compose_image_services": ["helper"] if image_only else [],
    }
    if refused:
        with pytest.raises(RuntimeSelectionError, match=f"^{COMPOSE_SERVICE_OUTSIDE_FOLDER}: helper$"):
            select_runtime(repo, runtime, validate_environment_dependencies=False)
    else:
        select_runtime(repo, runtime, validate_environment_dependencies=False)


def _helpers(count: int, build: str) -> str:
    names = [f"h{n}" for n in range(count)]
    return (
        f"x-b: &b {build}\nservices:\n  agent:\n    build: .\n"
        f"    depends_on: [{', '.join(names)}]\n"
        + "".join(f"  {name}:\n    build: *b\n" for name in names)
    )


def test_services_sharing_a_build_are_checked_once(repo, monkeypatch):
    from fi.alk.harness import runtime_selection

    # An alias gives every helper the same build at no cost in the file.
    (repo / "agents" / "a" / "Dockerfile").write_text("FROM python\n")
    (repo / "agents" / "a" / "compose.yaml").write_text(
        _helpers(20, "{context: ., dockerfile: Dockerfile}")
    )
    resolved: list[str] = []
    real = runtime_selection._resolved
    monkeypatch.setattr(
        runtime_selection,
        "_resolved",
        lambda base, value: resolved.append(value) or real(base, value),
    )

    _select_compose(repo, {})

    assert resolved.count("Dockerfile") == 1


def test_a_build_path_past_the_systems_limit_is_refused(repo):
    from fi.alk.harness.runtime_selection import RuntimeSelectionError

    (repo / "agents" / "a" / "compose.yaml").write_text(
        "services:\n  agent:\n    build: ." + "/x" * 2048 + "\n"
    )

    with pytest.raises(RuntimeSelectionError, match="^compose_source_path_invalid$"):
        _select_compose(repo, {})


def test_each_part_of_a_build_path_is_charged(repo):
    from fi.alk.harness.runtime_selection import RuntimeSelectionError

    # 60 builds of 2,000 parts each: each resolve looks up every part, 120,000 lookups.
    names = [f"h{n}" for n in range(60)]
    (repo / "agents" / "a" / "compose.yaml").write_text(
        "services:\n  agent:\n    build: .\n"
        f"    depends_on: [{', '.join(names)}]\n"
        + "".join(f"  {name}:\n    build: ./{name}" + "/x" * 2000 + "\n" for name in names)
    )

    with pytest.raises(RuntimeSelectionError, match="^compose_structural_interpolation_limit$"):
        _select_compose(repo, {})


@pytest.mark.parametrize("key", ["network_mode", "ipc", "pid"])
def test_a_mode_that_is_not_a_string_is_refused(repo, key):
    from fi.alk.harness.runtime_selection import RuntimeSelectionError

    # Ten levels of such a list expand to gigabytes when written out.
    (repo / "agents" / "a" / "compose.yaml").write_text(
        f"x-l: &l [a, b]\nservices:\n  agent:\n    build: .\n    {key}: *l\n"
    )

    with pytest.raises(RuntimeSelectionError, match=f"^compose_invalid: agent {key} must be a string$"):
        _select_compose(repo, {})


@pytest.mark.parametrize("environment", ["{A: [x, y]}", "[{A: x}]"])
def test_an_environment_value_that_is_not_plain_is_refused(repo, environment):
    from fi.alk.harness.runtime_selection import RuntimeSelectionError, select_runtime

    (repo / "agents" / "a" / "compose.yaml").write_text(
        f"services:\n  agent:\n    build: .\n    environment: {environment}\n"
    )

    with pytest.raises(RuntimeSelectionError, match="^compose_invalid: agent environment values must be plain$"):
        select_runtime(
            repo,
            {
                "language": "python",
                "execution": "compose",
                "compose_file": "agents/a/compose.yaml",
                "service": "agent",
                "source_subfolder": "agents/a",
                "workdir": "agents/a",
            },
            validate_environment_dependencies=True,
        )


@pytest.mark.parametrize("profiles, refused", [("", True), ("    profiles: [debug]\n", False)])
def test_with_no_control_service_a_service_behind_a_profile_is_not_checked(repo, profiles, refused):
    from fi.alk.harness.runtime_selection import COMPOSE_SERVICE_OUTSIDE_FOLDER, RuntimeSelectionError, select_runtime

    (repo / "agents" / "b" / "Dockerfile").write_text("FROM python\n")
    (repo / "agents" / "a" / "compose.yaml").write_text(
        "services:\n  agent:\n    build: .\n  helper:\n    build: ../b\n" + profiles
    )
    # Found by looking in the folder, with no service named: the whole file runs.
    runtime = {
        "language": "python",
        "execution": "auto",
        "source_subfolder": "agents/a",
        "workdir": "agents/a",
    }
    if refused:
        with pytest.raises(RuntimeSelectionError, match=f"^{COMPOSE_SERVICE_OUTSIDE_FOLDER}: helper$"):
            select_runtime(repo, runtime, validate_environment_dependencies=False)
    else:
        select_runtime(repo, runtime, validate_environment_dependencies=False)


@pytest.mark.parametrize("entries, refused", [(1, False), (2, True)])
def test_the_prompt_copies_at_most_1_mib_in_all(tmp_path, repo, entries, refused):
    # Named twice, 600 KB of lines copy twice: a model naming one file a hundred times wrote
    # 100 MB into contract.json.
    lines = 6000
    (repo / "agents" / "a" / "prompt.txt").write_text(("#" * 99 + "\n") * lines)

    result = accept_contract(
        payload(
            runtime={"language": "python"},
            system_prompt_from=[
                {"path": "agents/a/prompt.txt", "start_line": 1, "end_line": lines}
            ]
            * entries,
        ),
        tmp_path / "out",
        source_root=repo,
        subfolder="agents/a",
    )

    assert bool(result.get("is_error")) is refused, result
    if refused:
        assert "more than 1 MiB" in result["content"][0]["text"]


def test_a_write_that_fails_partway_leaves_the_saved_contract(tmp_path, repo, monkeypatch):
    from fi.alk.harness import tools
    from fi.alk.harness.contract import AgentContract

    destination = tmp_path / "out"
    accept_contract(payload(runtime={"language": "python"}), destination, source_root=repo, subfolder="agents/a")
    saved = (destination / "contract.json").read_text()
    contract = AgentContract.model_validate_json(saved)
    write_text = Path.write_text

    def fails_partway(path, text, *args, **kwargs):
        write_text(path, text[: len(text) // 2], *args, **kwargs)
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", fails_partway)

    with pytest.raises(OSError):
        tools._save_contract(contract.model_copy(update={"notes": "later"}), destination)

    assert (destination / "contract.json").read_text() == saved
    assert sorted(path.name for path in destination.iterdir()) == ["contract.json"]


@pytest.mark.parametrize("files, refused", [(4, False), (5, True)])
def test_the_prompt_reads_at_most_4_mib_of_files_in_all(tmp_path, repo, files, refused):
    # Line 1 of each, but each file is read whole to find its lines.
    for n in range(files):
        (repo / "agents" / "a" / f"p{n}.txt").write_text("Be brief.\n" + "#" * 1_000_000)

    result = accept_contract(
        payload(
            runtime={"language": "python"},
            system_prompt_from=[
                {"path": f"agents/a/p{n}.txt", "start_line": 1, "end_line": 1}
                for n in range(files)
            ],
        ),
        tmp_path / "out",
        source_root=repo,
        subfolder="agents/a",
    )

    assert bool(result.get("is_error")) is refused, result
    if refused:
        assert "more than 4 MiB" in result["content"][0]["text"]
