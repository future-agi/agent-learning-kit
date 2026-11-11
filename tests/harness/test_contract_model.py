"""The model an agent talks to the person with: recorded only where its name is the value written
on the line the reader cites.

The save is the gate. A name that isn't that value (not quoted, a setting's name, a key, inside a
comment) is refused with a message saying what to do, once per reading; a contract without a
model is saved exactly as before.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import pytest

from fi.alk.harness.contract import AgentContract
from fi.alk.harness.runtime_selection import PathLookupsSpent, path_lookup_budget
from fi.alk.harness.model_citation import model_problems, named_on
from fi.alk.harness.tools import accept_contract, contract_tools

MIB = 1024 * 1024
# How long a check this file holds to "quick" may take: it takes well under a tenth of a second
# alone, and the bound leaves room for a shared CI runner under load. A check under tracemalloc,
# which slows Python down several times over, gets its own.
QUICK_SECONDS = 1.0
QUICK_SECONDS_TRACED = 5.0
FIXTURES = Path(__file__).resolve().parents[1] / "providers" / "fixtures" / "authored"

EMPTY_NAME = (
    "model: name is empty; give the model's name as written in the source, or leave model out"
)
NAME_CHARACTERS = (
    "model: a model name is letters, digits and . _ : / @ -; write it as it appears in the "
    "source, or leave model out"
)
PROVIDER_CHARACTERS = (
    "model: a provider is letters, digits and . _ : / @ -; write it as the source names it, or "
    "leave it out"
)
TOO_LONG = "model: name and provider are at most 255 characters"
NO_LINE = "model: give path and line, the file and the line the model's name is written on"
LONG_PATH = "model: path is too long"
REFUSED = "Not accepted. Fix all of these and call submit_contract again:\n  - "
PROMPT_LINES = (
    "system_prompt_excerpt is a quote, and system_prompt_from names no lines. If the prompt is "
    "written in the source, name its lines in system_prompt_from so it is copied whole."
)
ASKED_FOR_LINES = (
    REFUSED
    + PROMPT_LINES
    + "\n  - If any of these genuinely does not apply, submit again as is."
)


def not_a_file(path: str) -> str:
    return f"model: {path[:200]} is not a file in the source"


def unreadable(path: str) -> str:
    return (
        f"model: {path[:200]} can't be read as a file of at most 1 MiB; check the path, and cite "
        "the line in the file the name is written in"
    )


def outside(line: int, path: str, count: int) -> str:
    return f"model: line {line} is outside {path[:200]}, which has {count} lines"


def not_named(name: str, line: int, path: str) -> str:
    return (
        f"model: the name {name[:100]} is not the value written on line {line} of {path[:200]}; "
        "cite the line where it is written in quotes, or after : or = in a configuration file, "
        "or leave model out if the name is only set at run time"
    )


def not_a_model_line(path: str, line: int) -> str:
    return (
        f"model: in a .env file, cite the line whose setting names the model (such as "
        f"OPENAI_MODEL), with the name as its value; {path} line {line} sets another setting "
        "or another value"
    )


def kept_out(path: str) -> str:
    return (
        f"model: {path} is kept out of this session because it holds credentials; cite the line "
        "in the agent's code or configuration the model is set on, or leave model out"
    )


def payload(**fields):
    return {
        "agent": "pair",
        "modality": "chat",
        "real_use_cases": ["order"],
        "tools": [{"name": "add", "args": ["item_id"]}],
        **fields,
    }


@pytest.fixture
def root(tmp_path: Path) -> Path:
    folder = tmp_path / "src"
    folder.mkdir()
    (folder / "agent.py").write_text('AGENT = "pair"\nMODEL = "gpt-4o"\n', encoding="utf-8")
    return folder


def checked(root: Path, model) -> tuple[list[str], AgentContract, float]:
    """The model check alone, under the save's budget, and how long it took."""
    contract = AgentContract.model_validate(payload(model=model))
    with path_lookup_budget():
        started = time.perf_counter()
        problems = model_problems(contract, root)
        took = time.perf_counter() - started
    return problems, contract, took


def saved(destination: Path) -> dict:
    return json.loads((destination / "contract.json").read_text(encoding="utf-8"))


def link(target: str, at: Path) -> None:
    try:
        os.symlink(target, at)
    except (OSError, NotImplementedError, AttributeError) as unable:
        pytest.skip(f"links can't be made here: {unable}")


def chain(root: Path, prefix: str, count: int) -> None:
    """Links each costing about a thousand lookups to follow, the last leading to agent.py."""
    for index in range(count):
        following = f"{prefix}{index + 1}" if index < count - 1 else "agent.py"
        link("." + "/" * 999 + following, root / f"{prefix}{index}")


def text_of(result: dict) -> str:
    return result["content"][0]["text"]


def submitter(destination: Path, source_root: Path | None = None):
    server = contract_tools(destination, source_root=source_root)
    tool = next(one for one in server.tools if one.name == "submit_contract")
    return lambda arguments: asyncio.run(tool.handler(dict(arguments)))


# A contract without a model saves and builds as before


def test_a_contract_without_a_model_saves_and_builds_as_before(tmp_path):
    from fi.alk.providers.agent import build_authored_agent

    contract = json.loads((FIXTURES / "chat_source.json").read_text(encoding="utf-8"))["contract"]

    result = accept_contract(dict(contract), tmp_path / "out")

    assert not result.get("is_error"), result
    raw = (tmp_path / "out" / "contract.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "7a61d8e863f75541c6d32c287fea02fb7a605d3b440a91f4c1b4212a3e1832b6"
    )
    assert "model" not in json.loads(raw)
    document = build_authored_agent(
        AgentContract.model_validate(json.loads(raw)).model_dump(mode="json")
    )
    assert document["setup_hash"] == (
        "sh1:3854d6ecb87a1ee2890c5c16f25e854e20be84c109dc80311ad292e6f656d622"
    )
    assert document["model"] is None


# No source: nothing to check the model against


def test_model_without_a_source_is_dropped(tmp_path):
    for index, model in enumerate(
        (
            {"provider": "openai", "name": "gpt-4o", "path": "agent.py", "line": 2},
            # Unchecked shapes too: with no source nothing about the model is known.
            {"name": "gpt 4o"},
        )
    ):
        destination = tmp_path / f"out{index}"

        result = accept_contract(payload(model=model), destination)

        assert not result.get("is_error"), result
        assert "model" not in saved(destination)


# The citation's shape, checked before the disk


@pytest.mark.parametrize(
    "model, expected",
    [
        pytest.param({"name": "", "path": "agent.py", "line": 2}, [EMPTY_NAME], id="empty-name"),
        pytest.param({"name": "   ", "path": "agent.py", "line": 2}, [EMPTY_NAME], id="blank-name"),
        pytest.param(
            {"name": "gpt 4o", "path": "agent.py", "line": 2},
            [NAME_CHARACTERS],
            id="name-with-a-space",
        ),
        pytest.param(
            {"name": 'gpt-4o"', "path": "agent.py", "line": 2},
            [NAME_CHARACTERS],
            id="name-with-a-quote",
        ),
        pytest.param(
            {"provider": "open\x00ai", "name": "gpt-4o", "path": "agent.py", "line": 2},
            [PROVIDER_CHARACTERS],
            id="provider-with-a-nul",
        ),
        pytest.param(
            {"provider": "http://[x", "name": "gpt-4o", "path": "agent.py", "line": 2},
            [PROVIDER_CHARACTERS],
            id="provider-url-bracket",
        ),
        pytest.param("gpt-4o", [NO_LINE], id="bare-string"),
        pytest.param(
            {"name": "a" * 256, "path": "agent.py", "line": 2}, [TOO_LONG], id="long-name"
        ),
        pytest.param(
            {"provider": "a" * 256, "name": "gpt-4o", "path": "agent.py", "line": 2},
            [TOO_LONG],
            id="long-provider",
        ),
        pytest.param({"name": "gpt-4o", "line": 2}, [NO_LINE], id="no-path"),
        pytest.param({"name": "gpt-4o", "path": "   ", "line": 2}, [NO_LINE], id="blank-path"),
        pytest.param(
            {"name": "gpt-4o", "path": "agent.py", "line": 0}, [NO_LINE], id="line-zero"
        ),
        pytest.param(
            {"name": "gpt-4o", "path": "a" * 4097, "line": 2}, [LONG_PATH], id="long-path"
        ),
    ],
)
def test_model_shape_problems(root, model, expected):
    problems, _, _ = checked(root, model)

    assert problems == expected


# The cited path must lead to a file in the source


def _parent(root: Path) -> str:
    return "../outside.py"


def _absolute_outside(root: Path) -> str:
    return str(root.parent / "outside.py")


def _link_outside(root: Path) -> str:
    link(str(root.parent / "outside.py"), root / "out.py")
    return "out.py"


def _link_to_dev_zero(root: Path) -> str:
    if not Path("/dev/zero").exists():
        pytest.skip("no /dev/zero here")
    # Only made, never read: it must be refused before anything opens it.
    link("/dev/zero", root / "zero.py")
    return "zero.py"


def _link_loop(root: Path) -> str:
    link("b.py", root / "a.py")
    link("a.py", root / "b.py")
    return "a.py"


def _nul(root: Path) -> str:
    return "agent\x00.py"


def _unwinding_chain(root: Path) -> str:
    # Each link leaves a long part behind as it unwinds, so the path grows past any limit.
    for index in range(900):
        target = f"L{index + 1}/" + "A" * 990 if index < 899 else "."
        link(target, root / f"L{index}")
    return "L0/agent.py"


@pytest.mark.parametrize(
    "cited",
    [
        pytest.param(_parent, id="parent"),
        pytest.param(_absolute_outside, id="absolute-outside"),
        pytest.param(_link_outside, id="link-outside"),
        pytest.param(_link_to_dev_zero, id="link-to-dev-zero"),
        pytest.param(_link_loop, id="link-loop"),
        pytest.param(_nul, id="nul"),
        pytest.param(_unwinding_chain, id="unwinding-chain"),
    ],
)
def test_model_path_outside_the_source_is_refused(root, cited):
    # Holds the name on the cited line, so only where the file is can refuse it.
    (root.parent / "outside.py").write_text('AGENT = "pair"\nMODEL = "gpt-4o"\n')
    path = cited(root)

    problems, _, took = checked(root, {"name": "gpt-4o", "path": path, "line": 2})

    assert problems == [not_a_file(path)]
    assert took < QUICK_SECONDS


# The save's one budget


def test_model_path_spending_the_save_budget_is_refused(tmp_path, root):
    chain(root, "E", 25)
    destination = tmp_path / "out"

    started = time.perf_counter()
    result = accept_contract(
        payload(model={"name": "gpt-4o", "path": "E0", "line": 2}),
        destination,
        source_root=root,
    )
    took = time.perf_counter() - started

    assert result.get("is_error")
    assert text_of(result) == REFUSED + str(PathLookupsSpent())
    assert not (destination / "contract.json").exists()
    assert took < QUICK_SECONDS


@pytest.mark.parametrize("links", [pytest.param(11, id="lookup-budget-real")])
def test_model_path_charged_to_the_same_budget_as_the_prompt(tmp_path, root, links):
    chain(root, "P", links)
    chain(root, "M", links)
    model = {"name": "gpt-4o", "path": "M0", "line": 2}
    # Either half alone fits the budget, so only charging both to one budget refuses them.
    alone, _, _ = checked(root, model)
    assert alone == []
    destination = tmp_path / "out"

    result = accept_contract(
        payload(
            system_prompt_from=[{"path": "P0", "start_line": 1, "end_line": 2}],
            model=model,
        ),
        destination,
        source_root=root,
    )

    assert result.get("is_error")
    assert text_of(result) == REFUSED + str(PathLookupsSpent())
    assert not (destination / "contract.json").exists()


def test_model_path_through_an_honest_link_is_kept(tmp_path, root):
    (root / "pkg").mkdir()
    (root / "pkg" / "settings.py").write_text('MODEL = "gpt-4o"\n')
    link("pkg/settings.py", root / "alias.py")
    destination = tmp_path / "out"

    result = accept_contract(
        payload(model={"provider": "openai", "name": "gpt-4o", "path": "alias.py", "line": 1}),
        destination,
        source_root=root,
    )

    assert not result.get("is_error"), result
    assert saved(destination)["model"] == {
        "provider": "openai",
        "name": "gpt-4o",
        "path": "pkg/settings.py",
        "line": 1,
    }


# The cited file must be readable, and at most 1 MiB


def _over_a_mib(root: Path) -> str:
    head = b'MODEL = "gpt-4o"\n'
    (root / "big.py").write_bytes(head + b"#" * (MIB + 1 - len(head)))
    return "big.py"


def _fifo_inside(root: Path) -> str:
    if not hasattr(os, "mkfifo"):
        pytest.skip("no FIFOs here")
    try:
        os.mkfifo(root / "pipe.py")
    except OSError as unable:
        pytest.skip(f"a FIFO can't be made here: {unable}")
    return "pipe.py"


def _directory(root: Path) -> str:
    (root / "pkg").mkdir()
    return "pkg"


def _long_component(root: Path) -> str:
    return "a" * 300


@pytest.mark.parametrize(
    "cited",
    [
        pytest.param(_over_a_mib, id="over-1-mib"),
        pytest.param(_fifo_inside, id="fifo-inside"),
        pytest.param(_directory, id="directory"),
        pytest.param(_long_component, id="long-component"),
    ],
)
def test_model_file_that_cannot_be_read_is_refused(root, cited):
    path = cited(root)
    # On a thread that can't hold the run open: a check that opens a FIFO waits on it forever.
    answer: list = []
    checking = threading.Thread(
        target=lambda: answer.append(checked(root, {"name": "gpt-4o", "path": path, "line": 1})),
        daemon=True,
    )
    checking.start()
    checking.join(timeout=5)
    assert answer, "the check did not return"
    problems, _, took = answer[0]

    assert problems == [unreadable(path)]
    assert took < QUICK_SECONDS


# The cited line must be in the file


def test_model_line_outside_the_file_is_refused(root):
    (root / "three.py").write_text('A = 1\nB = 2\nMODEL = "gpt-4o"\n')

    problems, _, _ = checked(root, {"name": "gpt-4o", "path": "three.py", "line": 4})

    assert problems == [outside(4, "three.py", 3)]


# The name must be the value written on the line


@pytest.mark.parametrize(
    "written, name",
    [
        pytest.param("abc", "", id="empty-name-direct"),
        pytest.param('MODEL = "claude"', "gpt-4o", id="absent"),
        pytest.param('model="gpt-4o-mini"', "gpt-4o", id="prefix-of-longer"),
        pytest.param('model="gpt-4o"', "o", id="inside-longer"),
        pytest.param(
            'MODEL = "llama3:8b-instruct-q4_K_M"', "llama3:8b-instruct-q4", id="underscore-suffix"
        ),
        pytest.param('model="GPT-4o"', "gpt-4o", id="case"),
        pytest.param("gpt-4o is the model", "gpt-4o", id="not-quoted"),
        # Dotted with a version, so not refused as a setting's name: refused as not written there.
        pytest.param("llm = ChatOpenAI(model=cfg.gpt4o)", "cfg.gpt4o", id="attribute"),
        pytest.param('model = "gpt-4o"  # was gpt-4o-mini', "gpt-4o-mini", id="in-a-comment"),
        pytest.param("«gpt-4o»", "gpt-4o", id="non-ascii-marks"),
        pytest.param("model = gpt_4o", "gpt_4o", id="bare-in-python"),
    ],
)
def test_model_name_not_on_the_cited_line_is_refused(root, written, name):
    if not name:
        # On a thread that can't hold the run open: a search that never moves on fails here.
        answer: list[bool] = []
        searching = threading.Thread(
            target=lambda: answer.append(named_on(written, name, config=False)), daemon=True
        )
        searching.start()
        searching.join(timeout=2)
        assert answer == [False]
        return
    (root / "named.py").write_text(f"# the model\n{written}\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": name, "path": "named.py", "line": 2})

    assert problems == [not_named(name, 2, "named.py")]


@pytest.mark.parametrize(
    "file, written, name",
    [
        pytest.param("named.py", 'model="gpt-4o"', "gpt-4o", id="quoted"),
        pytest.param("named.py", "model='gpt-4o'", "gpt-4o", id="single-quoted"),
        pytest.param("named.ts", "const model = `gpt-4o`", "gpt-4o", id="backticks"),
        pytest.param("named.yaml", "model: gpt-4o", "gpt-4o", id="line-end-in-yaml"),
        pytest.param(".env", "OPENAI_MODEL=gpt-4o", "gpt-4o", id="env-file"),
        pytest.param("named.toml", 'model = "gpt-4o"', "gpt-4o", id="toml-quoted"),
        pytest.param(
            "named.py", 'model = "openai/gpt-4o"', "openai/gpt-4o", id="with-provider-prefix"
        ),
        pytest.param(
            "named.py", 'x = "gpt-4o-mini" or "gpt-4o"', "gpt-4o", id="second-occurrence"
        ),
        pytest.param(
            "named.py", 'os.getenv("OPENAI_MODEL", "gpt-4o")', "gpt-4o", id="getenv-default"
        ),
    ],
)
def test_model_whole_name_is_found(root, file, written, name):
    (root / file).write_text(f"# the model\n{written}\n", encoding="utf-8")

    problems, contract, _ = checked(root, {"name": name, "path": file, "line": 2})

    assert problems == []
    assert contract.model is not None and contract.model.name == name


# What is saved


def test_model_on_the_cited_line_is_saved_stripped(tmp_path, root):
    destination = tmp_path / "out"

    result = accept_contract(
        payload(
            model={
                "provider": "  openai  ",
                "name": "  gpt-4o  ",
                "path": f"  {root / 'agent.py'}  ",
                "line": 2,
            }
        ),
        destination,
        source_root=root,
    )

    assert not result.get("is_error"), result
    assert saved(destination)["model"] == {
        "provider": "openai",
        "name": "gpt-4o",
        "path": "agent.py",
        "line": 2,
    }


# One numbering of a file's lines, for the prompt and the model


@pytest.mark.parametrize(
    "written, line",
    [
        # A form feed is no line break to the reading's Read, nor to the save.
        pytest.param(b'# head\nA = 1\x0cB = 2\nMODEL = "gpt-4o"\n', 3, id="form-feed"),
        # Read as latin-1, \x85 is a line break and the model's line moves down one.
        pytest.param(b'# coding: latin-1\nA = "\x85"\nMODEL = "gpt-4o"\n', 3, id="latin-1-c1"),
    ],
)
def test_model_line_numbering_matches_the_prompt_copy(tmp_path, root, written, line):
    (root / "numbered.py").write_bytes(written)
    destination = tmp_path / "out"

    result = accept_contract(
        payload(
            system_prompt_from=[{"path": "numbered.py", "start_line": line, "end_line": line}],
            model={"name": "gpt-4o", "path": "numbered.py", "line": line},
        ),
        destination,
        source_root=root,
    )

    assert not result.get("is_error"), result
    contract = saved(destination)
    assert contract["system_prompt_excerpt"] == 'MODEL = "gpt-4o"'
    assert contract["model"]["line"] == line


# Crafted files


def _ace_label() -> bytes:
    written = "".join(chr(0x4E00 + index * 37) for index in range(40))
    while written:
        try:
            label = written.encode("idna")
        except UnicodeError:
            label = b""
        if label and len(label) <= 63:
            return label
        written = written[:-1]
    raise AssertionError("no ACE label")


def _filled(head: bytes, unit: bytes) -> bytes:
    return head + unit * ((MIB - len(head)) // len(unit))


def _one_line() -> bytes:
    head = b'MODEL = "gpt-4o" #'
    return head + b"x" * (MIB - len(head))


def _idna_ace() -> bytes:
    return _filled(b"# coding: idna\nMODEL = 'gpt-4o'\n", _ace_label() + b".")


def _punycode() -> bytes:
    return _filled(b"# coding: punycode\nMODEL = 'gpt-4o'\n", b"a" * 60 + b"-" + b"kva" + b"\n")


def _line_of_m() -> bytes:
    return b"m" * MIB


def _name_at_the_end() -> bytes:
    tail = b'="' + b"m" * 255 + b'"'
    return b"m" * (MIB - len(tail)) + tail


@pytest.mark.parametrize(
    "written, line, name, refused",
    [
        pytest.param(_one_line, 1, "gpt-4o", False, id="one-line-1-mib"),
        pytest.param(_idna_ace, 2, "gpt-4o", False, id="idna-ace-1-mib"),
        pytest.param(_punycode, 2, "gpt-4o", False, id="punycode-1-mib"),
        pytest.param(_line_of_m, 1, "m" * 255, True, id="long-name-on-a-line-of-it"),
        pytest.param(_name_at_the_end, 1, "m" * 255, False, id="name-at-the-end-of-a-line-of-it"),
    ],
)
def test_a_crafted_model_file_does_not_hold_up_the_save(root, written, line, name, refused):
    content = written()
    assert len(content) <= MIB
    (root / "crafted.py").write_bytes(content)

    problems, _, took = checked(root, {"name": name, "path": "crafted.py", "line": line})

    assert problems == ([not_named(name, line, "crafted.py")] if refused else [])
    assert took < QUICK_SECONDS


# Asking once for the prompt's lines


def test_a_quote_without_lines_is_asked_for_lines_once(tmp_path, root):
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    quoted = payload(
        hard_constraints=["Never share a card number."],
        system_prompt_excerpt="You take orders for the cafe.",
    )

    first = submit(quoted)

    assert first.get("is_error")
    assert text_of(first) == ASKED_FOR_LINES
    assert not (destination / "contract.json").exists()

    second = submit(quoted)

    assert not second.get("is_error"), second
    assert PROMPT_LINES not in text_of(second)
    assert saved(destination)["system_prompt_excerpt"] == "You take orders for the cafe."


@pytest.mark.parametrize("alias", ["system_prompt", "prompt", "instructions"])
def test_quote_under_an_alias_is_asked_too(tmp_path, root, alias):
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    quoted = payload(
        hard_constraints=["Never share a card number."],
        **{alias: "You take orders for the cafe."},
    )

    first = submit(quoted)

    assert text_of(first) == ASKED_FOR_LINES
    assert not (destination / "contract.json").exists()
    second = submit(quoted)
    assert not second.get("is_error"), second
    assert saved(destination)["system_prompt_excerpt"] == "You take orders for the cafe."


def test_lines_given_are_not_asked(tmp_path, root):
    destination = tmp_path / "out"
    submit = submitter(destination, root)

    first = submit(
        payload(
            hard_constraints=["Never share a card number."],
            system_prompt_excerpt='AGENT = "pair"',
            system_prompt_from=[{"path": "agent.py", "start_line": 1, "end_line": 2}],
        )
    )

    assert not first.get("is_error"), first
    assert PROMPT_LINES not in text_of(first)
    assert saved(destination)["system_prompt_excerpt"] == 'AGENT = "pair"\nMODEL = "gpt-4o"'


def test_no_source_is_not_asked(tmp_path):
    destination = tmp_path / "out"
    submit = submitter(destination)

    first = submit(
        payload(
            hard_constraints=["Never share a card number."],
            system_prompt_excerpt="You take orders for the cafe.",
        )
    )

    assert not first.get("is_error"), first
    assert PROMPT_LINES not in text_of(first)
    assert saved(destination)["system_prompt_excerpt"] == "You take orders for the cafe."


# The schema the model writes against


def test_submit_contract_schema_describes_model(tmp_path):
    server = contract_tools(tmp_path)
    schema = next(one for one in server.tools if one.name == "submit_contract").input_schema

    model = schema["properties"]["model"]

    assert set(model["properties"]) == {"provider", "name", "path", "line"}
    assert "required" not in model
    assert "model" not in schema["required"]


def test_saved_model_and_whole_prompt_reach_the_document(tmp_path):
    # The whole path the platform sees: the save copies the prompt's lines over the quote and
    # records the model, the build reads contract.json back, and the document carries both.
    from fi.alk.providers.agent import build_authored_agent

    source = tmp_path / "source"
    source.mkdir()
    (source / "agent.py").write_text(
        "import openai\n"
        "PROMPT = '''\n"
        "You book rides for riders.\n"
        "Never share a rider's PIN.\n"
        "Confirm the pickup before booking.\n"
        "'''\n"
        "\n"
        "client = openai.OpenAI()\n"
        'model="gpt-4o"\n'
    )
    payload = json.loads((FIXTURES / "chat_source.json").read_text())["contract"]
    payload.update(
        # A quote of the prompt's start; the named lines then send the whole of it.
        system_prompt_excerpt="You book rides for riders.",
        system_prompt_from=[{"path": "agent.py", "start_line": 3, "end_line": 5}],
        model={"provider": "openai", "name": "gpt-4o", "path": "agent.py", "line": 9},
    )
    out = tmp_path / "out"

    result = accept_contract(payload, out, source_root=source)

    assert not result.get("is_error"), result
    contract = AgentContract.model_validate(
        json.loads((out / "contract.json").read_text(encoding="utf-8"))
    ).model_dump(mode="json")
    document = build_authored_agent(contract)
    assert document["behaviour"][0]["instructions"] == (
        "You book rides for riders.\n"
        "Never share a rider's PIN.\n"
        "Confirm the pickup before booking."
    )
    assert document["model"] == {
        "provider": "openai",
        "name": "gpt-4o",
        "temperature": None,
        "max_tokens": None,
    }
    assert document["extension"]["contract"]["model"]["path"] == "agent.py"


@pytest.mark.parametrize(
    "model, said",
    [
        pytest.param(
            {"name": "gpt-5", "path": "agent.py", "line": 2},
            lambda: not_named("gpt-5", 2, "agent.py"),
            id="not-on-line",
        ),
        pytest.param(
            {"name": "gpt-4o", "path": "../outside.py", "line": 2},
            lambda: not_a_file("../outside.py"),
            id="parent",
        ),
    ],
)
def test_a_refused_model_is_not_saved(tmp_path, root, model, said):
    # The check's answer has to reach the save: a model it refuses is never written.
    (tmp_path / "outside.py").write_text('AGENT = "pair"\nMODEL = "gpt-4o"\n', encoding="utf-8")
    destination = tmp_path / "out"

    result = accept_contract(payload(model=model), destination, source_root=root)

    assert result.get("is_error")
    assert text_of(result) == REFUSED + said()
    assert not (destination / "contract.json").exists()


def test_a_quote_under_an_alias_is_not_called_missing(tmp_path, root):
    # A quote under another name is a quote: the save asks for its lines, and never says in the
    # same answer that there is no prompt at all.
    destination = tmp_path / "out"
    submit = submitter(destination, root)

    first = submit(payload(prompt="You take orders for the cafe."))

    assert text_of(first) == ASKED_FOR_LINES


# The name must be the value the model is set from, through the whole save


@pytest.mark.parametrize(
    "written, name, said",
    [
        pytest.param(
            'MODEL = os.getenv("OPENAI_MODEL")',
            "OPENAI_MODEL",
            "model: OPENAI_MODEL reads as the name of a setting, not a model; cite the line its "
            "value is written on, or leave model out",
            id="getenv-setting-name",
        ),
        pytest.param(
            "llm = ChatOpenAI(model=settings.llm_model)",
            "settings.llm_model",
            "model: settings.llm_model reads as the name of a setting, not a model; cite the line "
            "its value is written on, or leave model out",
            id="attribute",
        ),
        pytest.param('model = "gpt-4o"  # was gpt-4o-mini', "gpt-4o-mini", None, id="in-a-comment"),
    ],
)
def test_a_name_that_is_not_the_model_is_never_sent(tmp_path, root, written, name, said):
    from fi.alk.providers.agent import build_authored_agent

    (root / "llm.py").write_text(f"import os\n{written}\n", encoding="utf-8")
    destination = tmp_path / "out"

    result = accept_contract(
        payload(model={"provider": "openai", "name": name, "path": "llm.py", "line": 2}),
        destination,
        source_root=root,
    )

    assert text_of(result) == REFUSED + (said or not_named(name, 2, "llm.py"))
    assert not (destination / "contract.json").exists()
    # Left out, the model is unknown in the document rather than wrong.
    saved_without = accept_contract(payload(), destination, source_root=root)
    assert not saved_without.get("is_error"), saved_without
    contract = AgentContract.model_validate(saved(destination)).model_dump(mode="json")
    assert build_authored_agent(contract)["model"] is None


# The lines named for the prompt must hold the quote they replace

def prose(path: str) -> str:
    return (
        f"model: {path} is text (for people, or a prompt), not code or configuration; cite the line "
        "in the agent's code or configuration the model is set on, or leave model out"
    )


def lines_alone(named: str, quote: str, *, short: bool = False) -> str:
    outcome = (
        "the lines are copied as the prompt and recorded as unconfirmed, since that quote is too "
        "short to stand for it"
        if short
        else "the quote is kept and the lines are recorded as unconfirmed"
    )
    return (
        f"system_prompt_from: the lines named ({named}) don't hold the quote you gave before "
        f"({' '.join(quote.split())[:80]}); if these lines are the prompt, quote them word for "
        f"word in system_prompt_excerpt, with ... where you leave text out. If they are refused "
        f"again against it, {outcome}"
    )


def not_the_quote(named: str) -> str:
    return (
        f"system_prompt_from: the lines named ({named}) don't hold system_prompt_excerpt; quote "
        "the prompt word for word, with ... where you leave text out, and name the lines it is "
        "written on"
    )


@pytest.mark.parametrize(
    "quote, lines, refused, kept",
    [
        pytest.param(
            "You are pair, a helpful assistant.", (1, 2), True, None, id="wrong-lines"
        ),
        pytest.param(
            "You are pair,\n   a helpful   assistant who books",
            (3, 3),
            False,
            'PROMPT = """You are pair, a helpful assistant who books tables."""',
            id="same-text-other-spacing",
        ),
        pytest.param(
            "",
            (1, 2),
            False,
            'import os\nDB = "x"',
            id="no-quote",
        ),
    ],
)
def test_named_lines_must_contain_the_quote(tmp_path, root, quote, lines, refused, kept):
    (root / "prompt.py").write_text(
        'import os\nDB = "x"\nPROMPT = """You are pair, a helpful assistant who books tables."""\n',
        encoding="utf-8",
    )
    destination = tmp_path / "out"
    fields = {
        "hard_constraints": ["Be polite."],
        "system_prompt_from": [{"path": "prompt.py", "start_line": lines[0], "end_line": lines[1]}],
    }
    if quote:
        fields["system_prompt_excerpt"] = quote

    result = accept_contract(payload(**fields), destination, source_root=root)

    if refused:
        assert text_of(result) == REFUSED + not_the_quote(f"prompt.py {lines[0]}-{lines[1]}")
        assert not (destination / "contract.json").exists()
    else:
        assert not result.get("is_error"), result
        assert saved(destination)["system_prompt_excerpt"] == kept


def test_an_unreadable_prompt_file_is_refused_not_raised(tmp_path, root):
    locked = root / "locked.py"
    locked.write_text('PROMPT = "Be polite."\n', encoding="utf-8")
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("this user can read a file with no permissions")
        result = accept_contract(
            payload(
                hard_constraints=["Be polite."],
                system_prompt_from=[{"path": "locked.py", "start_line": 1, "end_line": 1}],
            ),
            tmp_path / "out",
            source_root=root,
        )
    finally:
        locked.chmod(0o644)

    assert text_of(result) == (
        REFUSED + "system_prompt_from[0]: locked.py is not a file in the source"
    )


# Nudges come one at a time, and a wrong citation is refused once


def test_one_prompt_nudge_at_a_time(tmp_path, root):
    destination = tmp_path / "out"
    submit = submitter(destination, root)

    first = submit(payload())
    assert first.get("is_error")
    assert "no hard_constraints and no system_prompt_excerpt" in text_of(first)
    assert "name its lines in system_prompt_from" in text_of(first)

    second = submit(payload(system_prompt_excerpt="You take orders for the cafe."))

    assert not second.get("is_error"), second
    assert PROMPT_LINES not in text_of(second)
    assert saved(destination)["system_prompt_excerpt"] == "You take orders for the cafe."


_LEFT_OUT_NEXT_TIME = "model: if the model is refused again, it is left out"


def test_a_wrong_citation_is_refused_once_then_dropped(tmp_path, root):
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    wrong = payload(
        hard_constraints=["Be polite."],
        model={"provider": "openai", "name": "gpt-5", "path": "agent.py", "line": 2},
    )

    first = submit(wrong)

    assert text_of(first) == (
        REFUSED + not_named("gpt-5", 2, "agent.py") + "\n  - " + _LEFT_OUT_NEXT_TIME
    )
    assert not (destination / "contract.json").exists()

    second = submit(wrong)

    assert not second.get("is_error"), second
    assert "model" not in saved(destination)


def test_accept_contract_refuses_a_wrong_citation_every_time(tmp_path, root):
    wrong = payload(model={"provider": "openai", "name": "gpt-5", "path": "agent.py", "line": 2})

    for _ in range(2):
        result = accept_contract(wrong, tmp_path / "out", source_root=root)
        assert text_of(result) == REFUSED + not_named("gpt-5", 2, "agent.py")
    assert not (tmp_path / "out" / "contract.json").exists()


@pytest.mark.parametrize(
    "written, name",
    [
        pytest.param("model: gpt-4o-mini", "gpt-4o", id="longer-on-the-right"),
        pytest.param("model: ollama:llama3", "llama3", id="longer-on-the-left"),
        pytest.param("model: azure-gpt-4o", "gpt-4o", id="longer-on-the-left-after-a-dash"),
    ],
)
def test_a_bare_value_in_a_configuration_file_must_be_whole(root, written, name):
    # Written bare after a key, the value has no quotes to end it: the name's own edges do.
    (root / "llm.yaml").write_text(f"provider: openai\n{written}\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": name, "path": "llm.yaml", "line": 2})

    assert problems == [not_named(name, 2, "llm.yaml")]


# A configuration file's bare value is found in about one pass over the line


def test_a_crafted_configuration_line_does_not_hold_up_the_check(root):
    (root / "crafted.yaml").write_bytes(b"a " * (MIB // 2))

    problems, _, took = checked(root, {"name": "a", "path": "crafted.yaml", "line": 1})

    assert problems == [not_named("a", 1, "crafted.yaml")]
    assert took < QUICK_SECONDS


# Names a setting goes by are not models, written in any case


@pytest.mark.parametrize(
    "file, written, name",
    [
        pytest.param("llm.py", 'MODEL = os.getenv("openai_model")', "openai_model", id="lowercase-env"),
        pytest.param("llm.py", 'MODEL = config["model_name"]', "model_name", id="subscript"),
        pytest.param("llm.py", 'MODEL = cfg.get("model")', "model", id="generic-word"),
        pytest.param("llm.json", '{"llm_model": "gpt-4o"}', "llm_model", id="json-key"),
        pytest.param(
            "llm.py", 'MODEL = os.getenv("OPENAI_MODEL_V2")', "OPENAI_MODEL_V2", id="capitals-with-a-digit"
        ),
    ],
)
def test_a_setting_name_is_not_a_model(root, file, written, name):
    (root / file).write_text(f"# settings\n{written}\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": name, "path": file, "line": 2})

    assert problems == [
        f"model: {name} reads as the name of a setting, not a model; cite the line its value is "
        "written on, or leave model out"
    ]


def test_a_quoted_key_is_not_a_value(root):
    (root / "llm.json").write_text('{"gpt-4o": true}\n', encoding="utf-8")

    problems, _, _ = checked(root, {"name": "gpt-4o", "path": "llm.json", "line": 1})

    assert problems == [not_named("gpt-4o", 1, "llm.json")]


# A value in a comment is not the model


@pytest.mark.parametrize(
    "file, written, name",
    [
        pytest.param("llm.py", '# model = "gpt-3.5-turbo"', "gpt-3.5-turbo", id="commented-out"),
        pytest.param("llm.yaml", "# model: gpt-3.5-turbo", "gpt-3.5-turbo", id="yaml-comment"),
        pytest.param("llm.ts", '// const model = "gpt-3.5-turbo"', "gpt-3.5-turbo", id="slashes"),
        pytest.param(
            "llm.py", 'model = "gpt-4o"  # was "gpt-4o-mini"', "gpt-4o-mini", id="trailing-comment"
        ),
    ],
)
def test_a_value_in_a_comment_is_not_the_model(root, file, written, name):
    (root / file).write_text(f"# settings\n{written}\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": name, "path": file, "line": 2})

    assert problems == [not_named(name, 2, file)]


@pytest.mark.parametrize(
    "file, written, name",
    [
        pytest.param("Dockerfile", "ENV MODEL=gpt-4o", "gpt-4o", id="dockerfile-env"),
        pytest.param("run.sh", "export MODEL=gpt-4o", "gpt-4o", id="shell-export"),
        pytest.param("env.example", "MODEL=gpt-4o", "gpt-4o", id="env-example"),
        pytest.param("models.yaml", "  - gpt-4o", "gpt-4o", id="yaml-list-item"),
        pytest.param("llm.py", 'model = "2024"', "2024", id="digits-only"),
        pytest.param("llm.py", 'model = "gpt-4o"  # the default', "gpt-4o", id="before-a-comment"),
        pytest.param("llm.ts", 'const model = prod ? "gpt-4o" : "gpt-4o-mini"', "gpt-4o", id="ternary"),
        pytest.param("run.sh", "  --model=gpt-4o \\", "gpt-4o", id="shell-flag"),
        pytest.param("llm.py", 'client(**{"model": "gpt-4o"})', "gpt-4o", id="splatted-dict"),
        pytest.param("llm.py", '    *rest, model="gpt-4o")', "gpt-4o", id="star-args"),
    ],
)
def test_more_honest_lines_are_found(root, file, written, name):
    (root / file).write_text(f"# settings\n{written}\n", encoding="utf-8")

    problems, contract, _ = checked(root, {"name": name, "path": file, "line": 2})

    assert problems == []
    assert contract.model is not None and contract.model.name == name


# Model and deployment ids are not settings; settings in any spelling still are


@pytest.mark.parametrize(
    "written, name",
    [
        pytest.param('DEPLOYMENT = "GPT4"', "GPT4", id="azure-gpt4"),
        pytest.param('DEPLOYMENT = "GPT4O"', "GPT4O", id="azure-gpt4o"),
        pytest.param('DEPLOYMENT = "GPT35TURBO"', "GPT35TURBO", id="azure-gpt35turbo"),
        pytest.param('MODEL = "O1"', "O1", id="capital-o1"),
        pytest.param('MODEL = "mistral_large"', "mistral_large", id="mistral-large"),
        pytest.param('MODEL = "command_r_plus"', "command_r_plus", id="command-r-plus"),
        pytest.param('MODEL = "nova_pro"', "nova_pro", id="nova-pro"),
        pytest.param('MODEL = "text_bison"', "text_bison", id="text-bison"),
        pytest.param('DEPLOYMENT = "gpt4o-deployment"', "gpt4o-deployment", id="named-deployment"),
        # The model is a first argument here, as for any call that isn't a lookup.
        pytest.param('llm = genai.GenerativeModel("gemini-pro")', "gemini-pro", id="first-argument"),
        pytest.param("llm = GenerativeModel('gemini-1.5-flash')", "gemini-1.5-flash", id="first-argument-single-quotes"),
    ],
)
def test_real_model_ids_are_not_settings(root, written, name):
    (root / "llm.py").write_text(f"# the model\n{written}\n", encoding="utf-8")

    problems, contract, _ = checked(root, {"name": name, "path": "llm.py", "line": 2})

    assert problems == []
    assert contract.model is not None and contract.model.name == name


@pytest.mark.parametrize(
    "written, name",
    [
        pytest.param('MODEL = os.getenv("OPENAI_MODEL_V2")', "OPENAI_MODEL_V2", id="capitals"),
        pytest.param('m = Field(default="gpt-4o", env="AZURE_OPENAI_DEPLOYMENT")', "AZURE_OPENAI_DEPLOYMENT", id="quoted-env-var-value"),
        pytest.param('m = Field(env="AGENT_BRAIN")', "AGENT_BRAIN", id="capitals-without-a-setting-word"),
        pytest.param('VAR = "llm_model"', "llm_model", id="snake-case"),
        pytest.param('VAR = "openai_model_v2"', "openai_model_v2", id="snake-case-with-a-digit"),
        pytest.param('VAR = "api_key"', "api_key", id="a-key"),
        pytest.param('VAR = "agent_name"', "agent_name", id="a-name"),
        pytest.param('VAR = "use_default"', "use_default", id="a-default"),
        pytest.param('VAR = "search_engine"', "search_engine", id="an-engine"),
        pytest.param('VAR = "cloud_provider"', "cloud_provider", id="a-provider"),
        pytest.param('VAR = "prod_env"', "prod_env", id="an-env"),
        pytest.param('VAR = "deployment_id"', "deployment_id", id="an-id"),
        pytest.param('VAR = "openai.chat"', "openai.chat", id="dotted-without-a-setting-word"),
        pytest.param('VAR = "model-name"', "model-name", id="kebab-case"),
        pytest.param('model = this.config.get<string>("openai.model");', "openai.model", id="dotted-get-generic"),
        pytest.param('model = configService.getOrThrow("llm.model");', "llm.model", id="dotted-get-or-throw"),
        pytest.param("$model = config('services.openai.model');", "services.openai.model", id="dotted-config"),
        pytest.param('MODEL = env.str("llm.model")', "llm.model", id="dotted-env-str"),
        pytest.param('model = props.getProperty("llm.model");', "llm.model", id="dotted-get-property"),
        pytest.param('MODEL = os.environ.get ("llm.model")', "llm.model", id="dotted-space-before-the-bracket"),
    ],
)
def test_settings_in_any_spelling_are_not_models(root, written, name):
    (root / "llm.py").write_text(f"# the model\n{written}\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": name, "path": "llm.py", "line": 2})

    assert problems == [
        f"model: {name} reads as the name of a setting, not a model; cite the line its value is "
        "written on, or leave model out"
    ]


# Comments are found outside strings only, with or without spaces


@pytest.mark.parametrize(
    "file, written, name",
    [
        pytest.param("llm.py", '{"a": "Agent #1", "model": "gpt-4o"}', "gpt-4o", id="hash-in-a-string"),
        pytest.param("llm.js", 'url = "http://x //y"; m = "gpt-4o"', "gpt-4o", id="slashes-in-a-string"),
        pytest.param("llm.js", 'note = "a /* b"; m = "gpt-4o"', "gpt-4o", id="block-start-in-a-string"),
        pytest.param("llm.py", 'm = "it\\"s #1"; model = "gpt-4o"', "gpt-4o", id="escaped-quote-in-a-string"),
        pytest.param("run.sh", 'python run.py --model "gpt-4o"', "gpt-4o", id="a-flag-is-not-sql"),
        pytest.param("run.sh", 'echo $#; MODEL="gpt-4o"', "gpt-4o", id="shell-count"),
        pytest.param("index.html", '<b>Tom &#38; Jerry</b> <i model="gpt-4o">', "gpt-4o", id="html-character"),
    ],
)
def test_comment_markers_inside_strings_do_not_hide_the_model(root, file, written, name):
    (root / file).write_text(f"# settings\n{written}\n", encoding="utf-8")

    problems, contract, _ = checked(root, {"name": name, "path": file, "line": 2})

    assert problems == []
    assert contract.model is not None and contract.model.name == name


@pytest.mark.parametrize(
    "file, written, name",
    [
        pytest.param("llm.js", 'x = 1;// "gpt-4o"', "gpt-4o", id="slashes-without-a-space"),
        pytest.param("llm.py", 'x=1#"gpt-4o"', "gpt-4o", id="hash-without-a-space"),
        pytest.param("llm.sql", "select 1 -- 'gpt-4o'", "gpt-4o", id="sql"),
        pytest.param("llm.sql", "-- model 'gpt-4o'", "gpt-4o", id="sql-line"),
        pytest.param("llm.sql", "select 1--'gpt-4o'", "gpt-4o", id="sql-without-spaces"),
        pytest.param("init.lua", "x = a--'gpt-4o'", "gpt-4o", id="lua"),
        pytest.param("llm.ts", '    * model: "gpt-4o"', "gpt-4o", id="block-comment-middle"),
        pytest.param("llm.ts", 'x = 1; /* "gpt-4o" */', "gpt-4o", id="block-comment-after-code"),
        pytest.param("index.html", '<!-- "gpt-4o" -->', "gpt-4o", id="markup"),
    ],
)
def test_comments_without_spaces_are_comments(root, file, written, name):
    (root / file).write_text(f"# settings\n{written}\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": name, "path": file, "line": 2})

    assert problems == [not_named(name, 2, file)]


# Prose names models, it doesn't set them


@pytest.mark.parametrize(
    "file, written",
    [
        pytest.param("README.md", "This agent uses `gpt-4o` (we tried `claude-3-opus`)", id="readme"),
        pytest.param("README.md", "| fallback | `claude-3-opus` |", id="table-cell"),
        pytest.param("README.md", 'This agent uses "gpt-4o" (we tried "claude-3-opus")', id="straight-quotes"),
        pytest.param("README.md", '* default model: "claude-3-opus"', id="list-item"),
        pytest.param("notes.txt", "tried `claude-3-opus`", id="text"),
        pytest.param("index.rst", "uses `claude-3-opus` here", id="rst"),
        pytest.param("guide.markdown", 'model: "claude-3-opus"', id="markdown"),
        pytest.param("guide.adoc", 'model: "claude-3-opus"', id="asciidoc"),
        pytest.param("page.mdx", 'model: "claude-3-opus"', id="mdx"),
        pytest.param("README", 'model: "claude-3-opus"', id="readme-without-a-suffix"),
    ],
)
def test_prose_files_hold_no_model(root, file, written):
    (root / file).write_text(f"# notes\n{written}\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": "claude-3-opus", "path": file, "line": 2})

    assert problems == [prose(file)]


def test_backticks_in_code_are_values(root):
    (root / "llm.ts").write_text("// the model\nconst model = `claude-3-opus`;\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": "claude-3-opus", "path": "llm.ts", "line": 2})

    assert problems == []


@pytest.mark.parametrize(
    "file, written",
    [
        pytest.param("Makefile", "MODEL ?= gpt-4o", id="makefile"),
        pytest.param("rules.mk", "MODEL := gpt-4o", id="make-include"),
        pytest.param(".flaskenv", "MODEL=gpt-4o", id="flaskenv"),
        pytest.param("run.sh", 'python run.py --model "${MODEL:-gpt-4o}"', id="shell-default"),
        pytest.param(".env", "MODEL=${MODEL:-gpt-4o}", id="env-default"),
    ],
)
def test_more_configuration_forms(root, file, written):
    (root / file).write_text(f"# settings\n{written}\n", encoding="utf-8")

    problems, contract, _ = checked(root, {"name": "gpt-4o", "path": file, "line": 2})

    assert problems == []
    assert contract.model is not None and contract.model.name == "gpt-4o"


def test_a_shell_default_is_only_found_in_configuration(root):
    (root / "llm.py").write_text('# the model\nx = ${MODEL:-gpt-4o}\n', encoding="utf-8")

    problems, _, _ = checked(root, {"name": "gpt-4o", "path": "llm.py", "line": 2})

    assert problems == [not_named("gpt-4o", 2, "llm.py")]


# The reader never waits on a pipe, and leaves a trace when it can't read


def test_a_pipe_is_not_waited_on(tmp_path):
    from fi.alk.harness.model_citation import read_source

    if not hasattr(os, "mkfifo"):
        pytest.skip("no named pipes here")
    pipe = tmp_path / "pipe"
    os.mkfifo(pipe)
    answer: list[bytes | None] = []
    reading = threading.Thread(target=lambda: answer.append(read_source(pipe, 10)), daemon=True)
    reading.start()
    reading.join(timeout=2)

    assert answer == [None]


def test_a_file_read_in_short_pieces_is_read_whole(tmp_path, monkeypatch):
    from fi.alk.harness import model_citation

    (tmp_path / "agent.py").write_bytes(b"x" * 100)
    real_read = os.read
    monkeypatch.setattr(model_citation.os, "read", lambda fd, n: real_read(fd, min(n, 7)))

    assert model_citation.read_source(tmp_path / "agent.py", 50) == b"x" * 51
    assert model_citation.read_source(tmp_path / "agent.py", 200) == b"x" * 100


def test_an_unreadable_file_is_logged_without_its_content(tmp_path, caplog):
    from fi.alk.harness.model_citation import read_source

    locked = tmp_path / "locked.py"
    locked.write_text('SECRET = "do-not-log"\n', encoding="utf-8")
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("this user can read a file with no permissions")
        with caplog.at_level("INFO", logger="fi.alk.harness.model_citation"):
            assert read_source(locked, 100) is None
    finally:
        locked.chmod(0o600)

    assert [record.getMessage() for record in caplog.records] == [
        "source file not read (PermissionError)"
    ]
    assert str(locked) not in caplog.text
    assert "do-not-log" not in caplog.text


def test_a_link_put_in_place_of_the_file_is_not_followed(tmp_path):
    from fi.alk.harness.model_citation import read_source

    (tmp_path / "outside.py").write_text("SECRET\n", encoding="utf-8")
    link("outside.py", tmp_path / "agent.py")

    assert read_source(tmp_path / "agent.py", 100) is None


def test_mismatched_quotes_are_not_a_value(root):
    (root / "llm.py").write_text("# settings\nmodel = \"gpt-4o'\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": "gpt-4o", "path": "llm.py", "line": 2})

    assert problems == [not_named("gpt-4o", 2, "llm.py")]


# The quote is compared by its letters, so honest ways of writing a prompt fit


@pytest.mark.parametrize(
    "file, written",
    [
        pytest.param(
            "prompt.py",
            'PROMPT = ("You are pair, "\n    "a helpful assistant who books tables.")',
            id="string-pieces",
        ),
        pytest.param(
            "prompt.py",
            'PROMPT = "You are pair,\\na helpful assistant who books tables."',
            id="escaped-newline",
        ),
        pytest.param(
            "prompt.json",
            '{"prompt": "You are pair,\\na helpful assistant who books tables."}',
            id="json",
        ),
    ],
)
def test_honest_prompt_forms_hold_the_quote(tmp_path, root, file, written):
    (root / file).write_text(written + "\n", encoding="utf-8")
    count = len(written.splitlines())
    destination = tmp_path / "out"

    result = accept_contract(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt="You are pair, a helpful assistant who books tables.",
            system_prompt_from=[{"path": file, "start_line": 1, "end_line": count}],
        ),
        destination,
        source_root=root,
    )

    assert not result.get("is_error"), result
    assert saved(destination)["system_prompt_excerpt"] == written


_LONG_PROMPT = "You are pair, a helpful assistant who books tables for guests. " * 4


@pytest.mark.parametrize(
    "quote, refused",
    [
        # A quote that says where it trails off fits.
        pytest.param(_LONG_PROMPT[:120] + " ...", False, id="trails-off-with-dots"),
        # The start matches and the rest differs: every part of the quote must be in the lines.
        pytest.param(_LONG_PROMPT[:120] + " and something else entirely", True, id="differs-late"),
        # The quote differs from the lines near its start.
        pytest.param("You are pair, a helpful assistant who books rooms for guests.", True, id="differs-early"),
        # The lines hold the quote's start but are shorter than the quote.
        pytest.param(_LONG_PROMPT * 3, True, id="lines-shorter-than-the-quote"),
    ],
)
def test_the_quote_window(tmp_path, root, quote, refused):
    (root / "prompt.py").write_text(f'PROMPT = """{_LONG_PROMPT}"""\n', encoding="utf-8")
    destination = tmp_path / "out"

    result = accept_contract(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt=quote,
            system_prompt_from=[{"path": "prompt.py", "start_line": 1, "end_line": 1}],
        ),
        destination,
        source_root=root,
    )

    if refused:
        assert text_of(result) == REFUSED + not_the_quote("prompt.py 1-1")
    else:
        assert not result.get("is_error"), result


def test_lines_refused_twice_leave_the_quote(tmp_path, root):
    (root / "prompt.py").write_text('import os\nDB = "x"\n', encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    wrong = payload(
        hard_constraints=["Be polite."],
        system_prompt_excerpt="You are pair, a helpful assistant.",
        system_prompt_from=[{"path": "prompt.py", "start_line": 1, "end_line": 2}],
    )

    first = submit(wrong)

    assert text_of(first) == (
        REFUSED
        + not_the_quote("prompt.py 1-2")
        + "\n  - system_prompt_from: lines named later are checked against this quote until you "
        "give another; a later mismatch records the lines as unconfirmed and keeps the quote, or the "
        "lines if the quote has fewer than 16 letters"
    )
    second = submit(wrong)
    assert not second.get("is_error"), second
    assert saved(destination)["system_prompt_excerpt"] == "You are pair, a helpful assistant."
    assert saved(destination)["system_prompt_from"] == []
    assert saved(destination)["unconfirmed_prompt_from"] == wrong["system_prompt_from"]


# Every piece of the quote must be in the lines named, in order

_BOOKING = (
    "import os\n"
    "import json\n"
    "from acme.tools import book\n"
    "from acme.tools import cancel\n"
    "\n"
    "PROMPT = (\n"
    '    "You are Acme\'s booking agent. "\n'
    '    "Help guests book tables. "\n'
    '    "Never share other guests\' bookings. "\n'
    '    "Confirm every booking before saving it. "\n'
    '    "Be warm and brief."\n'
    ")\n"
)
_BOOKING_QUOTE = (
    "You are Acme's booking agent. Help guests book tables. Never share other guests' bookings. "
    "Confirm every booking before saving it. Be warm and brief."
)


@pytest.mark.parametrize(
    "quote, lines, refused",
    [
        # Starting early pads the range, but its last line is still missing.
        pytest.param(_BOOKING_QUOTE, (1, 10), True, id="starts-early-stops-short"),
        pytest.param(_BOOKING_QUOTE, (6, 12), False, id="the-prompt"),
        pytest.param("You are Acme's booking agent. ... Be warm and brief.", (6, 12), False, id="elided"),
        pytest.param("You are Acme's booking agent. ... Be warm and brief.", (1, 10), True, id="elided-stops-short"),
        pytest.param("Be warm and brief. ... You are Acme's booking agent.", (6, 12), True, id="out-of-order"),
        pytest.param("You are Acme's booking agent. [...] Be warm and brief.", (6, 12), False, id="bracketed-dots"),
        pytest.param("You are Acme's booking agent. … Be warm and brief.", (6, 12), False, id="ellipsis-character"),
        pytest.param("YOU ARE ACME'S BOOKING AGENT. ... BE WARM AND BRIEF.", (6, 12), False, id="case"),
        pytest.param("... Confirm every booking before saving it. Be warm and brief.", (6, 12), False, id="starts-with-dots"),
        pytest.param("...", (1, 4), False, id="only-dots"),
        # One line can't hold a piece the quote has twice.
        pytest.param("Be warm and brief. ... Be warm and brief.", (6, 12), True, id="a-piece-twice"),
    ],
)
def test_a_range_that_stops_short_is_refused(tmp_path, root, quote, lines, refused):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"

    result = accept_contract(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt=quote,
            system_prompt_from=[{"path": "agent.py", "start_line": lines[0], "end_line": lines[1]}],
        ),
        destination,
        source_root=root,
    )

    if refused:
        assert text_of(result) == REFUSED + not_the_quote(f"agent.py {lines[0]}-{lines[1]}")
    else:
        assert not result.get("is_error"), result
        copied = "\n".join(_BOOKING.splitlines()[lines[0] - 1 : lines[1]])
        assert saved(destination)["system_prompt_excerpt"] == copied


@pytest.mark.parametrize(
    "written, quote",
    [
        pytest.param('PROMPT = "Book at Tom &amp; Jerry\'s diner tonight"', "Book at Tom & Jerry's diner tonight", id="entity-in-the-source"),
        pytest.param('PROMPT = "Book at Tom & Jerry\'s diner tonight"', "Book at Tom &amp; Jerry's diner tonight", id="entity-in-the-quote"),
        pytest.param('PROMPT = "Order a caf\u00e9 and wait for the bill"', "Order a cafe\u0301 and wait for the bill", id="decomposed-accent"),
    ],
)
def test_an_honest_quote_in_another_form_fits(tmp_path, root, written, quote):
    (root / "prompt.py").write_text(written + "\n", encoding="utf-8")
    destination = tmp_path / "out"

    result = accept_contract(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt=quote,
            system_prompt_from=[{"path": "prompt.py", "start_line": 1, "end_line": 1}],
        ),
        destination,
        source_root=root,
    )

    assert not result.get("is_error"), result
    assert saved(destination)["system_prompt_excerpt"] == written


def test_lines_sent_without_the_refused_quote_are_still_checked(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    imports = [{"path": "agent.py", "start_line": 1, "end_line": 4}]

    first = submit(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt=_BOOKING_QUOTE,
            system_prompt_from=imports,
        )
    )
    assert text_of(first).startswith(REFUSED + not_the_quote("agent.py 1-4"))

    # The quote left out: the imports are still checked against it, asked about once more...
    second = submit(payload(hard_constraints=["Be polite."], system_prompt_from=imports))
    assert text_of(second) == REFUSED + lines_alone("agent.py 1-4", _BOOKING_QUOTE)
    assert not (destination / "contract.json").exists()

    # ... and then the quote is what's sent, with the lines on record as unconfirmed.
    third = submit(payload(hard_constraints=["Be polite."], system_prompt_from=imports))
    assert not third.get("is_error"), third
    assert saved(destination)["system_prompt_excerpt"] == _BOOKING_QUOTE
    assert saved(destination)["system_prompt_from"] == []
    assert saved(destination)["unconfirmed_prompt_from"] == imports


def test_the_right_lines_after_a_refusal_are_copied(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)

    first = submit(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt=_BOOKING_QUOTE,
            system_prompt_from=[{"path": "agent.py", "start_line": 1, "end_line": 4}],
        )
    )
    assert first.get("is_error"), first

    second = submit(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_from=[{"path": "agent.py", "start_line": 6, "end_line": 12}],
        )
    )
    assert not second.get("is_error"), second
    assert saved(destination)["system_prompt_excerpt"] == "\n".join(_BOOKING.splitlines()[5:12])


def test_a_new_quote_replaces_the_refused_one(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)

    first = submit(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt="You are pair, a helpful assistant.",
            system_prompt_from=[{"path": "agent.py", "start_line": 1, "end_line": 4}],
        )
    )
    assert first.get("is_error"), first

    second = submit(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt=_BOOKING_QUOTE,
            system_prompt_from=[{"path": "agent.py", "start_line": 6, "end_line": 12}],
        )
    )
    assert not second.get("is_error"), second
    assert saved(destination)["system_prompt_excerpt"] == "\n".join(_BOOKING.splitlines()[5:12])


def test_a_quote_of_many_pieces_does_not_hold_up_the_check(root):
    from fi.alk.harness.model_citation import quote_problem

    copied = "a" * MIB
    started = time.perf_counter()
    answer = quote_problem("a ... " * 50_000 + "b", copied, "agent.py 1-1")
    took = time.perf_counter() - started

    assert answer is not None
    assert took < QUICK_SECONDS


# A prompt written with Python string prefixes holds its quote


@pytest.mark.parametrize(
    "written",
    [
        pytest.param('PROMPT = ("You are pair, " f"a helpful assistant who books tables.")', id="f-string-piece"),
        pytest.param('PROMPT = ("You are pair, " r"a helpful assistant who books tables.")', id="raw-piece"),
        pytest.param("PROMPT = ('You are pair, ' rb'a helpful assistant who books tables.')", id="two-letter-prefix"),
        pytest.param('PROMPT = ("You are pair, " F"a helpful assistant who books tables.")', id="capital-prefix"),
        pytest.param('PROMPT = f"You are pair, " "a helpful assistant who books tables."', id="prefix-on-the-first-piece"),
        pytest.param('PROMPT = (u"You are pair, " fr"a helpful assistant who books tables.")', id="u-and-fr"),
    ],
)
def test_a_prompt_with_string_prefixes_holds_the_quote(tmp_path, root, written):
    (root / "prompt.py").write_text(written + "\n", encoding="utf-8")
    destination = tmp_path / "out"

    result = accept_contract(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt="You are pair, a helpful assistant who books tables.",
            system_prompt_from=[{"path": "prompt.py", "start_line": 1, "end_line": 1}],
        ),
        destination,
        source_root=root,
    )

    assert not result.get("is_error"), result
    assert saved(destination)["system_prompt_excerpt"] == written


@pytest.mark.parametrize(
    "written, quote, refused",
    [
        # A word ending in a prefix letter keeps that letter, on both sides.
        pytest.param('PROMPT = "He said" " hello and goodbye."', 'He said hello and goodbye.', False, id="word-before-a-quote"),
        pytest.param('PROMPT = "Pick one of" "f the options."', "Pick one off the options.", False, id="prefix-letter-inside-a-string"),
    ],
)
def test_a_word_before_a_quote_is_not_a_prefix(tmp_path, root, written, quote, refused):
    (root / "prompt.py").write_text(written + "\n", encoding="utf-8")
    destination = tmp_path / "out"

    result = accept_contract(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt=quote,
            system_prompt_from=[{"path": "prompt.py", "start_line": 1, "end_line": 1}],
        ),
        destination,
        source_root=root,
    )

    if refused:
        assert text_of(result) == REFUSED + not_the_quote("prompt.py 1-1")
    else:
        assert not result.get("is_error"), result


# A value inside a docstring or a multi-line string is not the model

_DOCSTRING = (
    "def build():\n"
    '    """Build the agent.\n'
    "\n"
    '    Example: model = "gpt-4o"\n'
    '    """\n'
    '    return Agent(model="claude-3-opus")\n'
)


@pytest.mark.parametrize(
    "file, written, line, name",
    [
        pytest.param("llm.py", _DOCSTRING, 4, "gpt-4o", id="docstring-line"),
        pytest.param("llm.py", "X = '''\nmodel = \"gpt-4o\"\n'''\n", 2, "gpt-4o", id="single-quote-block"),
        pytest.param("llm.py", 'PROMPT = """You are pair.\nUse "gpt-4o" for hard cases.\n"""\n', 2, "gpt-4o", id="prompt-line"),
        pytest.param("llm.py", 'PROMPT = """Use "gpt-4o" for hard cases."""\n', 1, "gpt-4o", id="opened-on-the-line"),
        pytest.param("llm.toml", 'notes = """\nmodel = "gpt-4o"\n"""\n', 2, "gpt-4o", id="toml-multiline"),
        pytest.param("llm.py", '"""Docs with \'\'\' inside.\nmodel = "gpt-4o"\n"""\n', 2, "gpt-4o", id="other-kind-does-not-close"),
        pytest.param("llm.py", 'X = """docs \'\'\' and "gpt-4o"\n"""\n', 1, "gpt-4o", id="other-kind-on-the-line-does-not-close"),
        pytest.param("llm.py", 'X = """gpt-4o" is the default"""\n', 1, "gpt-4o", id="a-block-that-opens-at-the-name"),
    ],
)
def test_a_value_inside_a_triple_quoted_block_is_not_the_model(root, file, written, line, name):
    (root / file).write_text(written, encoding="utf-8")

    problems, _, _ = checked(root, {"name": name, "path": file, "line": line})

    assert problems == [not_named(name, line, file)]


@pytest.mark.parametrize(
    "file, written, line, name",
    [
        pytest.param("llm.py", _DOCSTRING, 6, "claude-3-opus", id="closed-before"),
        pytest.param("llm.py", 'DOC = """notes"""; MODEL = "gpt-4o"\n', 1, "gpt-4o", id="closed-on-the-line"),
        pytest.param("llm.py", 'MODEL = """gpt-4o"""\n', 1, "gpt-4o", id="triple-quoted-value"),
        pytest.param("llm.py", "MODEL = '''gpt-4o'''\n", 1, "gpt-4o", id="triple-single-quoted-value"),
        pytest.param("llm.py", 'X = """a\nb"""\nMODEL = "gpt-4o"\n', 3, "gpt-4o", id="block-closed-on-an-earlier-line"),
    ],
)
def test_a_value_after_a_closed_block_is_the_model(root, file, written, line, name):
    (root / file).write_text(written, encoding="utf-8")

    problems, contract, _ = checked(root, {"name": name, "path": file, "line": line})

    assert problems == []
    assert contract.model is not None and contract.model.name == name


@pytest.mark.parametrize(
    "written",
    [
        pytest.param('"""' * (MIB // 3 - 20) + '\nMODEL = "gpt-4o"\n', id="a-file-of-triple-quotes"),
        pytest.param('"""' + '""" "gpt-4o" """' * (MIB // 20), id="a-line-of-blocks"),
    ],
)
def test_a_crafted_file_of_triple_quotes_does_not_hold_up_the_check(root, written):
    (root / "crafted.py").write_text(written, encoding="utf-8")
    line = written.count("\n") + (0 if written.endswith("\n") else 1)

    _, _, took = checked(root, {"name": "gpt-4o", "path": "crafted.py", "line": line})

    assert took < QUICK_SECONDS


def test_leaving_the_quote_out_is_not_asked_about(tmp_path, root):
    # Following the advice to leave the quote out must not set off the "no prompt" question.
    (root / "prompt.py").write_text('PROMPT = "You are pair."\n', encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)

    result = submit(
        payload(system_prompt_from=[{"path": "prompt.py", "start_line": 1, "end_line": 1}])
    )

    assert not result.get("is_error"), result
    assert saved(destination)["system_prompt_excerpt"] == 'PROMPT = "You are pair."'


def test_a_prompt_path_through_a_link_outside_is_refused(tmp_path, root):
    (tmp_path / "outside.py").write_text('PROMPT = "elsewhere"\n', encoding="utf-8")
    link("../outside.py", root / "inside.py")

    result = accept_contract(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_from=[{"path": "inside.py", "start_line": 1, "end_line": 1}],
        ),
        tmp_path / "out",
        source_root=root,
    )

    assert text_of(result) == (
        REFUSED + "system_prompt_from[0]: inside.py is not a file in the source"
    )


# What is looked up is not the model, however it is spelled


@pytest.mark.parametrize(
    "written, name",
    [
        # Keys shaped like model names, so the setting-shape rule passes them and the lookup is
        # what refuses them.
        pytest.param('MODEL = os.getenv("fast-4")', "fast-4", id="getenv-hyphenated"),
        pytest.param('MODEL = cfg.get("chat.v2")', "chat.v2", id="get-dotted"),
        pytest.param('MODEL = os.getenv("gpt_4o")', "gpt_4o", id="getenv-with-a-digit"),
        pytest.param('MODEL = os.environ["gpt-4o"]', "gpt-4o", id="subscript"),
        pytest.param('model = this.config.get<string>("gpt-4o");', "gpt-4o", id="get-generic"),
        pytest.param('model = configService.getOrThrow("gpt-4o");', "gpt-4o", id="get-or-throw"),
        pytest.param('model = props.getProperty("gpt-4o");', "gpt-4o", id="get-property"),
        pytest.param('MODEL = os.environ.get ("gpt-4o")', "gpt-4o", id="space-before-the-bracket"),
        pytest.param('MODEL = env.str("gpt-4o")', "gpt-4o", id="env-str"),
        pytest.param("$model = config('gpt-4o');", "gpt-4o", id="config-call"),
        pytest.param('model = env.getOrDefault("gpt-4o", x);', "gpt-4o", id="get-or-default"),
        pytest.param('model = bundle.getString("gpt-4o");', "gpt-4o", id="get-string"),
        pytest.param('model = config.getValue("gpt-4o");', "gpt-4o", id="get-value"),
        pytest.param("$model = env('gpt-4o');", "gpt-4o", id="env-call"),
    ],
)
def test_a_lookup_key_is_not_the_model(root, written, name):
    (root / "llm.py").write_text(f"import os\n{written}\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": name, "path": "llm.py", "line": 2})

    assert problems == [not_named(name, 2, "llm.py")]


@pytest.mark.parametrize(
    "written, name",
    [
        pytest.param('MODELS = ["gpt-4o", "gpt-4o-mini"]', "gpt-4o", id="list-literal"),
        pytest.param('model = genai.GenerativeModel("gemini-1.5-pro")', "gemini-1.5-pro", id="constructor"),
        pytest.param('MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")', "gpt-4o", id="getenv-default"),
        pytest.param('llm = target("gpt-4o")', "gpt-4o", id="a-call-ending-in-get"),
        pytest.param('llm = my_get("gpt-4o")', "gpt-4o", id="a-call-ending-in-_get"),
        pytest.param('["gpt-4o", "gpt-4o-mini"]', "gpt-4o", id="a-list-at-the-line-start"),
    ],
)
def test_a_value_passed_to_a_call_is_the_model(root, written, name):
    (root / "llm.py").write_text(f"import os\n{written}\n", encoding="utf-8")

    problems, contract, _ = checked(root, {"name": name, "path": "llm.py", "line": 2})

    assert problems == []
    assert contract.model is not None and contract.model.name == name


@pytest.mark.parametrize(
    "file, written, name",
    [
        pytest.param("llm.ts", 'const model = "gpt-4o"; /* was "gpt-4o-mini" */', "gpt-4o-mini", id="block-comment"),
        pytest.param("llm.html", '<!-- model: "gpt-4o-mini" -->', "gpt-4o-mini", id="markup-comment"),
        pytest.param("llm.html", 'x <!-- "gpt-4o-mini" -->', "gpt-4o-mini", id="trailing-markup-comment"),
    ],
)
def test_a_value_in_a_block_comment_is_not_the_model(root, file, written, name):
    (root / file).write_text(f"# settings\n{written}\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": name, "path": file, "line": 2})

    assert problems == [not_named(name, 2, file)]


def test_a_prompt_with_escaped_characters_holds_the_quote(tmp_path, root):
    # json.dump writes every non-ASCII character as an escape; the quote has the character.
    written = '{"prompt": "You are pair \\u2014 a helpful assistant who books tables."}'
    (root / "prompt.json").write_text(written + "\n", encoding="utf-8")
    destination = tmp_path / "out"

    result = accept_contract(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt="You are pair — a helpful assistant who books tables.",
            system_prompt_from=[{"path": "prompt.json", "start_line": 1, "end_line": 1}],
        ),
        destination,
        source_root=root,
    )

    assert not result.get("is_error"), result
    assert saved(destination)["system_prompt_excerpt"] == written


# Code between the string pieces of a prompt is passed over, a little at a time

_LONG_OPENING = (
    "You are Acme's booking agent. Help guests book tables, change bookings and cancel them. "
)


@pytest.mark.parametrize(
    "written, quote",
    [
        pytest.param(
            f'PROMPT = (\n    "{_LONG_OPENING}"\n    f"Today is {{today}}. "\n    "Be warm and brief."\n)',
            _LONG_OPENING + "Today is {today}. Be warm and brief.",
            id="f-string-piece-late",
        ),
        pytest.param(
            f'PROMPT = (\n    "{_LONG_OPENING}"  # the rules\n    "Be warm and brief."\n)',
            _LONG_OPENING + "Be warm and brief.",
            id="comment-between-pieces",
        ),
        pytest.param(
            f'PROMPT = (\n    "{_LONG_OPENING}"\n    r"Never share \\d+ ids. "\n    "Be warm and brief."\n)',
            _LONG_OPENING + "Never share \\d+ ids. Be warm and brief.",
            id="raw-piece-late",
        ),
        pytest.param(
            f'PROMPT = "{_LONG_OPENING}" + NAME + " Be warm and brief."',
            _LONG_OPENING + "Be warm and brief.",
            id="a-variable-left-out",
        ),
        pytest.param(
            f'PROMPT = "{_LONG_OPENING}<rules>Be warm and brief.</rules>"',
            _LONG_OPENING + "Be warm and brief.",
            id="markup-left-out",
        ),
        pytest.param(
            f'PROMPT = "{_LONG_OPENING}{{{{ guest_name }}}}, be warm and brief."',
            _LONG_OPENING + "Be warm and brief.",
            id="a-placeholder-left-out",
        ),
    ],
)
def test_code_between_string_pieces_is_passed_over(tmp_path, root, written, quote):
    (root / "prompt.py").write_text(written + "\n", encoding="utf-8")
    count = len(written.splitlines())
    destination = tmp_path / "out"

    result = accept_contract(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt=quote,
            system_prompt_from=[{"path": "prompt.py", "start_line": 1, "end_line": count}],
        ),
        destination,
        source_root=root,
    )

    assert not result.get("is_error"), result
    assert saved(destination)["system_prompt_excerpt"] == written


def test_a_long_gap_between_words_is_not_passed_over(tmp_path, root):
    written = (
        f'PROMPT = (\n    "{_LONG_OPENING}"  # twenty-five letters of a comment here\n'
        '    "Be warm and brief."\n)'
    )
    (root / "prompt.py").write_text(written + "\n", encoding="utf-8")

    result = accept_contract(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt=_LONG_OPENING + "Be warm and brief.",
            system_prompt_from=[{"path": "prompt.py", "start_line": 1, "end_line": 4}],
        ),
        tmp_path / "out",
        source_root=root,
    )

    assert text_of(result) == REFUSED + not_the_quote("prompt.py 1-4")


def test_a_quote_that_passed_replaces_the_held_one(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    prompt = [{"path": "agent.py", "start_line": 6, "end_line": 12}]

    first = submit(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt="You are pair, a helpful assistant.",
            system_prompt_from=[{"path": "agent.py", "start_line": 1, "end_line": 4}],
        )
    )
    assert first.get("is_error"), first
    second = submit(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt=_BOOKING_QUOTE,
            system_prompt_from=prompt,
        )
    )
    assert not second.get("is_error"), second

    # The lines again, without a quote: checked against the quote that passed, not the old one.
    third = submit(payload(hard_constraints=["Be polite."], system_prompt_from=prompt))

    assert not third.get("is_error"), third
    assert saved(destination)["system_prompt_excerpt"] == "\n".join(_BOOKING.splitlines()[5:12])
    assert saved(destination)["system_prompt_from"] == prompt


@pytest.mark.parametrize("placeholder", ["...", "…", "[...]", "-", "—", "?"])
def test_a_quote_without_words_does_not_skip_the_held_one(tmp_path, root, placeholder):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    imports = [{"path": "agent.py", "start_line": 1, "end_line": 4}]

    first = submit(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt=_BOOKING_QUOTE,
            system_prompt_from=imports,
        )
    )
    assert first.get("is_error"), first

    again = payload(
        hard_constraints=["Be polite."], system_prompt_excerpt=placeholder, system_prompt_from=imports
    )
    second = submit(again)
    assert text_of(second) == REFUSED + lines_alone("agent.py 1-4", _BOOKING_QUOTE)

    third = submit(again)
    assert not third.get("is_error"), third
    assert saved(destination)["system_prompt_excerpt"] == _BOOKING_QUOTE
    assert saved(destination)["system_prompt_from"] == []
    assert saved(destination)["unconfirmed_prompt_from"] == imports


@pytest.mark.parametrize(
    "written",
    [
        pytest.param('PROMPT = "Order a caf\\u00e9 here, then wait for the bill."', id="short-unicode-escape"),
        pytest.param('PROMPT = "Order a caf\\xe9 here, then wait for the bill."', id="hex-escape"),
        pytest.param('PROMPT = "Order a caf\\U000000e9 here, then wait for the bill."', id="long-unicode-escape"),
        pytest.param('PROMPT = "Order a caf\\N{LATIN SMALL LETTER E WITH ACUTE} here, then wait for the bill."', id="named-escape"),
        pytest.param('PROMPT = "Order a caf\\351 here, then wait for the bill."', id="octal-escape"),
    ],
)
def test_more_escapes_hold_the_quote(tmp_path, root, written):
    (root / "prompt.py").write_text(written + "\n", encoding="utf-8")
    destination = tmp_path / "out"

    result = accept_contract(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt="Order a café here, then wait for the bill.",
            system_prompt_from=[{"path": "prompt.py", "start_line": 1, "end_line": 1}],
        ),
        destination,
        source_root=root,
    )

    assert not result.get("is_error"), result


def test_a_quote_that_grows_when_normalised_costs_little(root):
    from fi.alk.harness.model_citation import quote_problem

    import tracemalloc

    # U+FDFA grows eighteen times under NFKC; NFC leaves it as it is.
    copied = "\ufdfa" * (MIB // 3)
    tracemalloc.start()
    try:
        started = time.perf_counter()
        quote_problem("\ufdfa" * 1000 + " b", copied, "agent.py 1-1")
        took = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert took < QUICK_SECONDS_TRACED
    assert peak < 32 * MIB


# An apostrophe in bare text doesn't hide the comment after it


@pytest.mark.parametrize(
    "file, written, name",
    [
        pytest.param("cfg.yaml", "description: Pair's booking agent  # model: gpt-4o-mini", "gpt-4o-mini", id="yaml"),
        pytest.param(".env", "AGENT_NAME=Bob's agent # MODEL=gpt-4o-mini", "gpt-4o-mini", id="env"),
        pytest.param("llm.sh", "echo it\\'s # 'gpt-4o-mini'", "gpt-4o-mini", id="shell-escaped"),
        pytest.param("lib.rs", "fn f<'a>(x: &'a str) -> &'a str { x } // \"gpt-4o-mini\"", "gpt-4o-mini", id="rust-lifetimes"),
        pytest.param("cfg.yaml", "note: Pair's agent # it's \"gpt-4o-mini\"", "gpt-4o-mini", id="apostrophes-around-the-marker"),
        pytest.param("llm.ini", "; model = gpt-4o-mini", "gpt-4o-mini", id="ini-comment"),
    ],
)
def test_an_apostrophe_does_not_hide_a_comment(root, file, written, name):
    (root / file).write_text(f"# settings\n{written}\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": name, "path": file, "line": 2})

    # In a .env file a line whose setting isn't a model's is refused before it is read (TH-9042).
    assert problems == [not_a_model_line(file, 2) if file == ".env" else not_named(name, 2, file)]


@pytest.mark.parametrize(
    "file, written, name",
    [
        pytest.param("run.sh", "curl -s http://localhost:8000/v1/chat/completions -d '{\"model\": \"gpt-4o\"}'", "gpt-4o", id="curl"),
        pytest.param("run.sh", "python app.py --base-url=http://localhost:4000 --model=gpt-4o", "gpt-4o", id="url-flag"),
        pytest.param("Dockerfile", "CMD python app.py --base-url=http://ollama:11434 --model=llama3", "llama3", id="dockerfile-cmd"),
        pytest.param("cfg.yaml", "llm: {base_url: http://localhost:11434, model: llama3}", "llama3", id="yaml-flow-map"),
        pytest.param("run.sh", 'exec uv run -- python -m app --model "gpt-4o"', "gpt-4o", id="uv-run"),
        pytest.param("Dockerfile", "CMD tini -- python app.py --model=gpt-4o", "gpt-4o", id="tini"),
        pytest.param("run.sh", "for f in data/*; do MODEL=gpt-4o; done", "gpt-4o", id="glob"),
        pytest.param("run.sh", "[ ${#MODELS[@]} -eq 0 ] && MODEL=gpt-4o", "gpt-4o", id="shell-length"),
        pytest.param("llm.py", "n = len(x)//2; model = 'gpt-4o'", "gpt-4o", id="floor-division"),
        pytest.param("llm.ts", ' */ const model = "gpt-4o";', "gpt-4o", id="after-a-block-comment-closes"),
    ],
)
def test_urls_globs_and_dashes_are_not_comments(root, file, written, name):
    (root / file).write_text(f"# settings\n{written}\n", encoding="utf-8")

    problems, contract, _ = checked(root, {"name": name, "path": file, "line": 2})

    assert problems == []
    assert contract.model is not None and contract.model.name == name


@pytest.mark.parametrize(
    "written, name",
    [
        pytest.param('DEPLOYMENT = "gpt-4o_deployment"', "gpt-4o_deployment", id="underscore-deployment"),
        pytest.param('DEPLOYMENT = "chat-deployment"', "chat-deployment", id="deployment-without-a-digit"),
        pytest.param('MODEL = "azure/gpt4o_deployment"', "azure/gpt4o_deployment", id="provider-prefixed-deployment"),
        # A setting's word with a version and no underscore: a deployment's own name.
        pytest.param('DEPLOYMENT = "gpt4o-default"', "gpt4o-default", id="versioned-with-a-setting-word"),
    ],
)
def test_deployment_ids_are_models(root, written, name):
    (root / "llm.py").write_text(f"# the model\n{written}\n", encoding="utf-8")

    problems, contract, _ = checked(root, {"name": name, "path": "llm.py", "line": 2})

    assert problems == []
    assert contract.model is not None and contract.model.name == name


def test_a_crafted_line_of_generic_calls_does_not_hold_up_the_check(root):
    # Each occurrence looks back for a generic's < only a few characters, not to the first get<.
    (root / "crafted.ts").write_text('get<x>("a") ' + '>("a") ' * 100_000, encoding="utf-8")

    _, _, took = checked(root, {"name": "a", "path": "crafted.ts", "line": 1})

    assert took < QUICK_SECONDS


def test_a_crafted_line_of_apostrophes_does_not_hold_up_the_check(root):
    # Once one apostrophe finds no partner, none after it is paired again.
    # Small enough that pairing each apostrophe again would show as seconds, not hang.
    (root / "crafted.py").write_text("'" + "\\'" * 40_000 + "\n", encoding="utf-8")

    problems, _, took = checked(root, {"name": "a", "path": "crafted.py", "line": 1})

    assert problems == [not_named("a", 1, "crafted.py")]
    assert took < QUICK_SECONDS


# Lines sent alone after a refusal are asked about once more before either side is given up


def test_right_lines_sent_alone_after_a_refused_paraphrase_are_kept(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    prompt = [{"path": "agent.py", "start_line": 6, "end_line": 12}]
    paraphrase = "Acme booking agent: helps guests reserve tables and never shares bookings."

    first = submit(
        payload(hard_constraints=["Be polite."], system_prompt_excerpt=paraphrase, system_prompt_from=prompt)
    )
    assert text_of(first).startswith(REFUSED + not_the_quote("agent.py 6-12"))
    second = submit(payload(hard_constraints=["Be polite."], system_prompt_from=prompt))
    assert text_of(second) == REFUSED + lines_alone("agent.py 6-12", paraphrase)

    # Quoted word for word from the lines, as asked: the lines are copied, nothing unconfirmed.
    third = submit(
        payload(hard_constraints=["Be polite."], system_prompt_excerpt=_BOOKING_QUOTE, system_prompt_from=prompt)
    )

    assert not third.get("is_error"), third
    contract = saved(destination)
    assert contract["system_prompt_excerpt"] == "\n".join(_BOOKING.splitlines()[5:12])
    assert contract["system_prompt_from"] == prompt
    assert "unconfirmed_prompt_from" not in contract


def test_unconfirmed_lines_reach_the_document(tmp_path, root):
    from fi.alk.providers.agent import build_authored_agent

    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    imports = [{"path": "agent.py", "start_line": 1, "end_line": 4}]
    quoted = payload(hard_constraints=["Be polite."], system_prompt_excerpt=_BOOKING_QUOTE, system_prompt_from=imports)

    assert submit(quoted).get("is_error")
    assert submit(payload(hard_constraints=["Be polite."], system_prompt_from=imports)).get("is_error")
    assert not submit(payload(hard_constraints=["Be polite."], system_prompt_from=imports)).get("is_error")

    contract = AgentContract.model_validate(saved(destination)).model_dump(mode="json")
    document = build_authored_agent(contract)
    assert document["behaviour"][0]["instructions"] == _BOOKING_QUOTE
    assert document["extension"]["contract"]["unconfirmed_prompt_from"] == imports


def test_only_the_save_records_unconfirmed_lines(tmp_path, root):
    destination = tmp_path / "out"
    claimed = [{"path": "agent.py", "start_line": 1, "end_line": 1}]

    result = accept_contract(
        payload(hard_constraints=["Be polite."], unconfirmed_prompt_from=claimed),
        destination,
        source_root=root,
    )

    assert not result.get("is_error"), result
    assert "unconfirmed_prompt_from" not in saved(destination)


def test_a_quote_whose_start_matches_everywhere_does_not_hold_up_the_check(root):
    from fi.alk.harness.model_citation import quote_problem

    # Every start matches a thousand words before failing: the attempts are counted and stop.
    started = time.perf_counter()
    answer = quote_problem("a " * 1000 + "b", "a" * MIB, "agent.py 1-1")
    took = time.perf_counter() - started

    assert answer is not None
    assert took < QUICK_SECONDS


def test_a_first_word_found_early_is_tried_again(tmp_path, root):
    # "you" first appears in the comment, too far from "are"; the prompt's own "You are" follows.
    written = "# you should keep this comment long enough to break the match\n" + _BOOKING
    (root / "agent.py").write_text(written, encoding="utf-8")
    destination = tmp_path / "out"

    result = accept_contract(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt=_BOOKING_QUOTE,
            system_prompt_from=[{"path": "agent.py", "start_line": 1, "end_line": 13}],
        ),
        destination,
        source_root=root,
    )

    assert not result.get("is_error"), result
    assert saved(destination)["system_prompt_excerpt"] == "\n".join(written.splitlines())


def test_wrong_lines_alone_after_a_passed_quote_are_asked_about_then_left_out(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    imports = [{"path": "agent.py", "start_line": 1, "end_line": 4}]

    first = submit(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt=_BOOKING_QUOTE,
            system_prompt_from=[{"path": "agent.py", "start_line": 6, "end_line": 12}],
        )
    )
    assert not first.get("is_error"), first
    second = submit(payload(hard_constraints=["Be polite."], system_prompt_from=imports))
    assert text_of(second) == REFUSED + lines_alone("agent.py 1-4", _BOOKING_QUOTE)

    third = submit(payload(hard_constraints=["Be polite."], system_prompt_from=imports))

    assert not third.get("is_error"), third
    assert saved(destination)["system_prompt_excerpt"] == _BOOKING_QUOTE
    assert saved(destination)["unconfirmed_prompt_from"] == imports


# A quote is held from the moment it is sent, whatever sends the call back


@pytest.mark.parametrize(
    "first",
    [
        pytest.param({}, id="asked-for-lines"),
        pytest.param({"modality": "telepathy"}, id="refused-before-the-checks"),
    ],
)
def test_a_quote_sent_without_lines_is_held(tmp_path, root, first):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    imports = [{"path": "agent.py", "start_line": 1, "end_line": 4}]

    sent_back = submit(
        payload(hard_constraints=["Be polite."], system_prompt_excerpt=_BOOKING_QUOTE, **first)
    )
    assert sent_back.get("is_error"), sent_back

    second = submit(payload(hard_constraints=["Be polite."], system_prompt_from=imports))
    assert text_of(second) == REFUSED + lines_alone("agent.py 1-4", _BOOKING_QUOTE)
    third = submit(payload(hard_constraints=["Be polite."], system_prompt_from=imports))

    assert not third.get("is_error"), third
    assert saved(destination)["system_prompt_excerpt"] == _BOOKING_QUOTE
    assert saved(destination)["unconfirmed_prompt_from"] == imports


def test_the_quote_shown_with_lines_sent_alone_is_on_one_line(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    submit = submitter(tmp_path / "out", root)
    imports = [{"path": "agent.py", "start_line": 1, "end_line": 4}]
    quote = "You are Acme's booking agent.\n\nHelp guests book tables."

    submit(payload(hard_constraints=["Be polite."], system_prompt_excerpt=quote, system_prompt_from=imports))
    second = submit(payload(hard_constraints=["Be polite."], system_prompt_from=imports))

    assert "(You are Acme's booking agent. Help guests book tables.)" in text_of(second)


@pytest.mark.parametrize("placeholder", ["n/a", "N/A", "TBD", "none", "see lines", "as above"])
def test_a_quote_of_a_few_letters_says_nothing(tmp_path, root, placeholder):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    imports = [{"path": "agent.py", "start_line": 1, "end_line": 4}]

    assert submit(
        payload(hard_constraints=["Be polite."], system_prompt_excerpt=_BOOKING_QUOTE, system_prompt_from=imports)
    ).get("is_error")
    again = payload(hard_constraints=["Be polite."], system_prompt_excerpt=placeholder, system_prompt_from=imports)

    assert text_of(submit(again)) == REFUSED + lines_alone("agent.py 1-4", _BOOKING_QUOTE)
    assert not submit(again).get("is_error")
    assert saved(destination)["system_prompt_excerpt"] == _BOOKING_QUOTE
    assert saved(destination)["unconfirmed_prompt_from"] == imports


# The gap between two words of a quote is 24 letters, no more


@pytest.mark.parametrize("letters, refused", [(24, False), (25, True)])
def test_the_gap_between_words_is_24_letters(letters, refused):
    from fi.alk.harness.model_citation import quote_problem

    copied = "Confirm every booking " + "x" * letters + " before saving it."

    answer = quote_problem("Confirm every booking before saving it.", copied, "agent.py 1-1")

    assert (answer is not None) is refused


# Each search is charged what it reads


def test_an_elided_quote_of_a_long_prompt_holds(tmp_path, root):
    from fi.alk.harness.model_citation import quote_problem

    filler = "A guest asks a question and a host answers it at a table. " * 3_000
    copied = f'PROMPT = """You are Acme\'s booking agent. {filler}A short answer is best."""'

    answer = quote_problem(
        "You are Acme's booking agent. ... A short answer is best.", copied, "agent.py 1-1"
    )

    assert answer is None


def test_a_crafted_long_word_costs_little(root):
    from fi.alk.harness.model_citation import quote_problem

    started = time.perf_counter()
    answer = quote_problem("a " + "a" * MIB + "b", "a" * MIB, "agent.py 1-1")
    took = time.perf_counter() - started

    assert answer is not None
    assert took < 0.25


# Scripts written without spaces: each character is a word


@pytest.mark.parametrize(
    "written, quote",
    [
        pytest.param('PROMPT = ("你是一个乐于助人的订餐" f"助手，今天是{today}。")', "你是一个乐于助人的订餐助手，今天是{today}。", id="chinese-prefix-mid-clause"),
        pytest.param('PROMPT = ("你是一个乐于助人的订餐"  # 角色\n    "助手，请礼貌地回答。")', "你是一个乐于助人的订餐助手，请礼貌地回答。", id="chinese-comment-mid-clause"),
        pytest.param('PROMPT = ("あなたは親切な予約" f"アシスタントです。{name}")', "あなたは親切な予約アシスタントです。", id="japanese-prefix-mid-clause"),
        pytest.param('PROMPT = "あなたは予約・案内のアシスタントです。"', "あなたは予約・案内のアシスタントです。", id="japanese-middle-dot"),
        pytest.param('PROMPT = ("คุณเป็นผู้ช่วย" f"จองโต๊ะที่สุภาพ")', "คุณเป็นผู้ช่วยจองโต๊ะที่สุภาพ", id="thai"),
        pytest.param('PROMPT = "သင်သည် စားပွဲ ကြိုတင်မှာယူမှု အကူအညီပေးသူ ဖြစ်သည်။"', "သင်သည် စားပွဲ ကြိုတင်မှာယူမှု အကူအညီပေးသူ ဖြစ်သည်။", id="burmese"),
        pytest.param('PROMPT = "អ្នកគឺជាជំនួយការកក់តុដែលគួរសម។"', "អ្នកគឺជាជំនួយការកក់តុដែលគួរសម។", id="khmer"),
    ],
)
def test_an_unspaced_prompt_holds_its_quote(tmp_path, root, written, quote):
    (root / "prompt.py").write_text(written + "\n", encoding="utf-8")
    count = len(written.splitlines())
    destination = tmp_path / "out"

    result = accept_contract(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt=quote,
            system_prompt_from=[{"path": "prompt.py", "start_line": 1, "end_line": count}],
        ),
        destination,
        source_root=root,
    )

    assert not result.get("is_error"), result


def test_an_escape_that_names_no_character_is_kept_as_written(tmp_path, root):
    from fi.alk.harness.model_citation import quote_problem

    quote = "Order a table \\UFFFFFFFF and \\N{NOT A REAL NAME} for two."

    assert quote_problem(quote, 'PROMPT = "' + quote + '"', "prompt.py 1-1") is None


# The reader: what counts is limit + 1, and every descriptor is closed


def test_a_read_that_lands_on_the_limit_reads_one_more(tmp_path, monkeypatch):
    from fi.alk.harness import model_citation

    (tmp_path / "agent.py").write_bytes(b"x" * 100)
    real_read = os.read
    monkeypatch.setattr(model_citation.os, "read", lambda fd, n: real_read(fd, min(n, 7)))

    assert model_citation.read_source(tmp_path / "agent.py", 49) == b"x" * 50


def test_the_reader_closes_what_it_opens(tmp_path, monkeypatch):
    from fi.alk.harness import model_citation

    (tmp_path / "agent.py").write_bytes(b"x" * 10)
    (tmp_path / "folder").mkdir()
    opened: list[int] = []
    closed: list[int] = []
    real_open, real_close = os.open, os.close
    monkeypatch.setattr(model_citation.os, "open", lambda *a: opened.append(real_open(*a)) or opened[-1])
    monkeypatch.setattr(model_citation.os, "close", lambda fd: closed.append(fd) or real_close(fd))

    model_citation.read_source(tmp_path / "agent.py", 100)
    model_citation.read_source(tmp_path / "folder", 100)

    assert sorted(opened) == sorted(closed) and len(opened) == 2


# Triple quotes count only in languages that have them, and never inside a string or comment


@pytest.mark.parametrize(
    "file, written, line, name",
    [
        pytest.param("llm.py", "parts = r.split('\"\"\"')\nMODEL = \"gpt-4o\"\n", 2, "gpt-4o", id="in-a-one-line-string"),
        pytest.param("llm.py", "# docstrings use \"\"\"\nMODEL = \"gpt-4o\"\n", 2, "gpt-4o", id="in-a-comment"),
        pytest.param("llm.py", "SEP = \"'''\"; MODEL = \"gpt-4o\"\n", 1, "gpt-4o", id="in-a-string-on-the-line"),
        pytest.param("llm.ts", "const prompt = 'Use \"\"\" for blocks';\nconst model = \"gpt-4o\";\n", 2, "gpt-4o", id="typescript"),
        pytest.param("main.go", "var sep = `\"\"\"`\nvar model = \"gpt-4o\"\n", 2, "gpt-4o", id="go"),
        pytest.param("cfg.yaml", "a: '''quoted'''\nmodel: gpt-4o\n", 2, "gpt-4o", id="yaml"),
        pytest.param("llm.py", 'X = """a\nb"""; MODEL = "gpt-4o"\n', 2, "gpt-4o", id="a-block-closing-before-the-value"),
        pytest.param("llm.py", "msg = f'Agent #{n}'; MODEL = \"gpt-4o\"\n", 1, "gpt-4o", id="prefixed-string-with-a-hash"),
        pytest.param("llm.py", "x = a[1]//2; m = \"gpt-4o\"\n", 1, "gpt-4o", id="floor-division-after-a-subscript"),
        pytest.param("llm.py", "n = a//2; model = 'gpt-4o'\n", 1, "gpt-4o", id="floor-division-after-a-name"),
    ],
)
def test_stray_triple_quotes_do_not_hide_the_model(root, file, written, line, name):
    (root / file).write_text(written, encoding="utf-8")

    problems, contract, _ = checked(root, {"name": name, "path": file, "line": line})

    assert problems == []
    assert contract.model is not None and contract.model.name == name


@pytest.mark.parametrize(
    "file, written, line, name",
    [
        pytest.param("llm.py", '"""Uses "gpt-4o".\n"""\n', 1, "gpt-4o", id="a-docstring-opening-on-the-line"),
        pytest.param("llm.py", "X = '''It's a''' # it's \"gpt-4o\"\n", 1, "gpt-4o", id="a-comment-after-a-block"),
        pytest.param("llm.py", 'X = """a\nb"""  # was "gpt-4o"\n', 2, "gpt-4o", id="a-comment-after-a-block-closes"),
        pytest.param("lib.hs", "x' = 1 -- \"gpt-4o\"\n", 1, "gpt-4o", id="haskell-prime"),
        pytest.param("llm.ts", '\t*\tmodel: "gpt-4o"\n', 1, "gpt-4o", id="a-tabbed-block-comment-line"),
        pytest.param("llm.cfg", "; model = gpt-4o\n", 1, "gpt-4o", id="cfg-comment"),
        pytest.param("llm.conf", "; model = gpt-4o\n", 1, "gpt-4o", id="conf-comment"),
    ],
)
def test_more_comments_and_docstrings_are_not_the_model(root, file, written, line, name):
    (root / file).write_text(written, encoding="utf-8")

    problems, _, _ = checked(root, {"name": name, "path": file, "line": line})

    assert problems == [not_named(name, line, file)]


def test_a_crafted_file_of_one_byte_lines_and_triple_quotes_costs_little(root):
    # Lines with no triple quote, and lines inside a block without its closing quote, are passed
    # over without a scan.
    (root / "crafted.py").write_text(
        '"""\n' + "x\n" * (MIB // 4) + '"""\n' + "y\n" * (MIB // 4) + 'MODEL = "gpt-4o"\n',
        encoding="utf-8",
    )
    line = 2 * (MIB // 4) + 3

    _, _, took = checked(root, {"name": "gpt-4o", "path": "crafted.py", "line": line})

    assert took < 0.25


@pytest.mark.parametrize(
    "file",
    ["notes.org", "rules.mdc", "CHANGELOG", "CHANGELOG.md", "LICENSE", "CONTRIBUTING", "README"],
)
def test_more_prose_files_hold_no_model(root, file):
    (root / file).write_text('# notes\nmodel: "claude-3-opus"\n', encoding="utf-8")

    problems, _, _ = checked(root, {"name": "claude-3-opus", "path": file, "line": 2})

    assert problems == [prose(file)]


def test_a_file_named_after_readme_is_code(root):
    (root / "readme_writer.py").write_text('# writes READMEs\nMODEL = "gpt-4o"\n', encoding="utf-8")

    problems, _, _ = checked(root, {"name": "gpt-4o", "path": "readme_writer.py", "line": 2})

    assert problems == []


@pytest.mark.parametrize(
    "quote, lines, refused",
    [
        pytest.param("You are pair.", (1, 4), True, id="short-and-not-in-the-lines"),
        pytest.param("Be warm and brief.", (6, 12), False, id="short-and-in-the-lines"),
        # Too few letters to say anything: not looked for, like no quote at all.
        pytest.param("TBD", (1, 4), False, id="a-placeholder-with-nothing-held"),
    ],
)
def test_a_short_quote_is_looked_for_whole(tmp_path, root, quote, lines, refused):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")

    result = accept_contract(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt=quote,
            system_prompt_from=[{"path": "agent.py", "start_line": lines[0], "end_line": lines[1]}],
        ),
        tmp_path / "out",
        source_root=root,
    )

    if refused:
        assert text_of(result) == REFUSED + not_the_quote(f"agent.py {lines[0]}-{lines[1]}")
    else:
        assert not result.get("is_error"), result


def test_full_width_digits_hold_their_quote(tmp_path, root):
    (root / "prompt.py").write_text('PROMPT = "Seat at most ８ guests per table tonight."\n', encoding="utf-8")

    result = accept_contract(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt="Seat at most 8 guests per table tonight.",
            system_prompt_from=[{"path": "prompt.py", "start_line": 1, "end_line": 1}],
        ),
        tmp_path / "out",
        source_root=root,
    )

    assert not result.get("is_error"), result


@pytest.mark.parametrize(
    "file, written, name",
    [
        pytest.param("agent.ts", 'class A { #model = "gpt-4o"; }', "gpt-4o", id="js-private-field"),
        pytest.param("agent.c", '#define MODEL "gpt-4o"', "gpt-4o", id="c-define"),
        pytest.param("main.rs", '#[default = "gpt-4o"] model: String,', "gpt-4o", id="rust-attribute"),
        pytest.param("cfg.yaml", "llm: {url: http://x/#top, model: gpt-4o}", "gpt-4o", id="yaml-url-fragment"),
        pytest.param("cfg.yaml", "llm: {a: 1,#b, model: gpt-4o}", "gpt-4o", id="yaml-hash-without-a-space"),
        pytest.param("agent.ts", '/* the default */ const model = "gpt-4o";', "gpt-4o", id="a-closed-block-comment-before"),
        pytest.param("index.html", '<!-- the default --> <i model="gpt-4o">', "gpt-4o", id="a-closed-markup-comment-before"),
        pytest.param("llm.py", 'm = str("gpt-4o")', "gpt-4o", id="str-as-a-function"),
        pytest.param("GNUmakefile", "MODEL ?= gpt-4o", "gpt-4o", id="gnu-makefile"),
        pytest.param("chat.prompt", "model: googleai/gemini-1.5-pro", "googleai/gemini-1.5-pro", id="dotprompt-frontmatter"),
    ],
)
def test_more_honest_forms_by_language(root, file, written, name):
    (root / file).write_text(f"\n{written}\n", encoding="utf-8")

    problems, contract, _ = checked(root, {"name": name, "path": file, "line": 2})

    assert problems == []
    assert contract.model is not None and contract.model.name == name


@pytest.mark.parametrize(
    "file, written, name",
    [
        pytest.param("agent.ts", 'x = 1; /* "gpt-4o" */ y = 2;', "gpt-4o", id="inside-a-closed-block-comment"),
        pytest.param("agent.py", "x = 1  # it's \"gpt-4o\"", "gpt-4o", id="python-hash"),
        pytest.param("cfg.yaml", 'model: gpt-4o-mini # was "gpt-4o"', "gpt-4o", id="yaml-spaced-hash"),
        pytest.param("agent.ts", 'const m = config?.["gpt-4o"];', "gpt-4o", id="optional-chained-lookup"),
        pytest.param("agent.py", 'm = env.str("gpt-4o")', "gpt-4o", id="str-as-a-method"),
    ],
)
def test_more_comments_and_lookups_by_language(root, file, written, name):
    (root / file).write_text(f"\n{written}\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": name, "path": file, "line": 2})

    assert problems == [not_named(name, 2, file)]


@pytest.mark.parametrize("file", ["prompt.j2", "prompt.jinja", "prompt.jinja2", "prompt.hbs", "prompt.mustache"])
def test_a_prompt_template_holds_no_model(root, file):
    (root / file).write_text('You are an assistant.\nYou run on "gpt-4o".\n', encoding="utf-8")

    problems, _, _ = checked(root, {"name": "gpt-4o", "path": file, "line": 2})

    assert problems == [prose(file)]


def test_a_short_quote_with_nothing_held_is_looked_for_whole(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    submit = submitter(tmp_path / "out", root)

    result = submit(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt="You are pair.",
            system_prompt_from=[{"path": "agent.py", "start_line": 1, "end_line": 4}],
        )
    )

    assert text_of(result).startswith(REFUSED + not_the_quote("agent.py 1-4"))


def test_a_long_first_word_is_charged_on_every_try(root):
    from fi.alk.harness.model_citation import quote_problem

    started = time.perf_counter()
    # Without the charge, each of 200,000 tries would compare 200,000 letters.
    answer = quote_problem("a" * 200_000 + " b", "a" * 400_000, "agent.py 1-1")
    took = time.perf_counter() - started

    assert answer is not None
    assert took < 0.25


def test_a_short_unspaced_quote_is_still_searched_with_gaps(tmp_path, root):
    # Six characters, each counted as three letters: long enough to be searched with gaps.
    (root / "prompt.py").write_text('PROMPT = ("你是订餐" f"助手")\n', encoding="utf-8")

    result = accept_contract(
        payload(
            hard_constraints=["Be polite."],
            system_prompt_excerpt="你是订餐助手",
            system_prompt_from=[{"path": "prompt.py", "start_line": 1, "end_line": 1}],
        ),
        tmp_path / "out",
        source_root=root,
    )

    assert not result.get("is_error"), result


@pytest.mark.parametrize(
    "file, written, line, name",
    [
        pytest.param("cfg.yaml", 'note: x """ y\nmodel: gpt-4o\n', 2, "gpt-4o", id="yaml-bare-triple"),
        pytest.param("main.go", 'var s = x + """\nvar model = "gpt-4o"\n', 2, "gpt-4o", id="go-bare-triple"),
    ],
)
def test_triple_quotes_are_tracked_only_where_they_are_strings(root, file, written, line, name):
    (root / file).write_text(written, encoding="utf-8")

    problems, _, _ = checked(root, {"name": name, "path": file, "line": line})

    assert problems == []


def test_a_value_before_a_block_closes_is_text(root):
    (root / "llm.py").write_text('X = """a\nuse "gpt-4o" here"""\n', encoding="utf-8")

    problems, _, _ = checked(root, {"name": "gpt-4o", "path": "llm.py", "line": 2})

    assert problems == [not_named("gpt-4o", 2, "llm.py")]


def test_lines_without_a_triple_quote_are_not_scanned(root, monkeypatch):
    from fi.alk.harness import model_citation

    written = '"""\n' + "x\n" * 499 + '"""\n' + "y\n" * 499 + 'MODEL = "gpt-4o"\n'
    (root / "llm.py").write_text(written, encoding="utf-8")
    scanned: list[str] = []
    real_scan = model_citation._line_scan
    monkeypatch.setattr(
        model_citation, "_line_scan", lambda line, *a, **k: scanned.append(line) or real_scan(line, *a, **k)
    )

    problems, _, _ = checked(root, {"name": "gpt-4o", "path": "llm.py", "line": 1001})

    assert problems == []
    # The block's opening and closing lines, and the cited line: nothing between.
    assert scanned == ['"""', '"""', 'MODEL = "gpt-4o"']


# A short or list quote is held like any other


def test_a_short_quote_is_held_and_its_lines_asked_about(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    imports = [{"path": "agent.py", "start_line": 1, "end_line": 4}]

    first = submit(
        payload(hard_constraints=["Be polite."], system_prompt_excerpt="Be warm and brief.", system_prompt_from=imports)
    )
    assert text_of(first).startswith(REFUSED + not_the_quote("agent.py 1-4"))
    second = submit(payload(hard_constraints=["Be polite."], system_prompt_from=imports))
    assert text_of(second) == REFUSED + lines_alone("agent.py 1-4", "Be warm and brief.", short=True)
    third = submit(payload(hard_constraints=["Be polite."], system_prompt_from=imports))

    # A quote this short can't stand for the prompt: the lines go out, on record as unconfirmed,
    # and the reply says so.
    assert not third.get("is_error"), third
    assert text_of(third).endswith(
        "\nsystem_prompt_from: the lines named (agent.py 1-4) are recorded as unconfirmed, since "
        "they don't hold the quote; the lines are the prompt."
    )
    assert saved(destination)["system_prompt_from"] == imports
    assert saved(destination)["unconfirmed_prompt_from"] == imports


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"system_prompt_excerpt": _BOOKING_QUOTE.split(". ")}, id="a-list"),
        pytest.param({"prompt": _BOOKING_QUOTE.split(". ")}, id="a-list-under-an-alias"),
        pytest.param({"system_prompt": _BOOKING_QUOTE}, id="an-alias"),
    ],
)
def test_a_quote_in_another_shape_is_held(tmp_path, root, fields):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    submit = submitter(tmp_path / "out", root)
    imports = [{"path": "agent.py", "start_line": 1, "end_line": 4}]

    first = submit(payload(hard_constraints=["Be polite."], system_prompt_from=imports, **fields))
    assert text_of(first).startswith(REFUSED + not_the_quote("agent.py 1-4"))
    second = submit(payload(hard_constraints=["Be polite."], system_prompt_from=imports))

    assert text_of(second).startswith(REFUSED + "system_prompt_from: the lines named (agent.py 1-4) don't hold the quote you gave before")


def test_a_list_quote_that_passes_replaces_the_held_one(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    prompt = [{"path": "agent.py", "start_line": 6, "end_line": 12}]
    paraphrase = "Acme booking agent: helps guests reserve tables and never shares bookings."

    assert submit(payload(hard_constraints=["Be polite."], system_prompt_excerpt=paraphrase, system_prompt_from=prompt)).get("is_error")
    assert not submit(
        payload(hard_constraints=["Be polite."], prompt=_BOOKING_QUOTE.split(". "), system_prompt_from=prompt)
    ).get("is_error")
    third = submit(payload(hard_constraints=["Be polite."], system_prompt_from=prompt))

    assert not third.get("is_error"), third
    assert saved(destination)["system_prompt_from"] == prompt
    assert "unconfirmed_prompt_from" not in saved(destination)


def test_a_placeholder_with_the_right_lines_keeps_the_lines(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    prompt = [{"path": "agent.py", "start_line": 6, "end_line": 12}]
    again = payload(hard_constraints=["Be polite."], system_prompt_excerpt="see lines", system_prompt_from=prompt)

    assert submit(again).get("is_error")
    second = submit(again)

    assert not second.get("is_error"), second
    assert saved(destination)["system_prompt_excerpt"] == "\n".join(_BOOKING.splitlines()[5:12])
    assert saved(destination)["unconfirmed_prompt_from"] == prompt


def test_two_calls_in_one_message_are_each_asked(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    server = contract_tools(destination, source_root=root)
    tool = next(one for one in server.tools if one.name == "submit_contract")
    paraphrase = "Acme booking agent: helps guests reserve tables and never shares bookings."
    both = payload(
        hard_constraints=["Be polite."],
        system_prompt_excerpt=paraphrase,
        system_prompt_from=[{"path": "agent.py", "start_line": 6, "end_line": 12}],
    )

    async def send_together():
        return await asyncio.gather(tool.handler(dict(both)), tool.handler(dict(both)))

    first, second = asyncio.run(send_together())

    # Neither had seen a refusal when it was sent, so both are refused. This models a dispatcher
    # that runs one message's calls side by side (ADK, the Vertex backend). The Claude backend runs
    # them one after another, and there the second sees the first's refusal: a known limit.
    assert first.get("is_error") and second.get("is_error")
    assert not (destination / "contract.json").exists()


def test_a_quote_sent_alone_after_its_lines_were_refused_is_not_asked_for_lines(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)

    assert submit(
        payload(hard_constraints=["Be polite."], system_prompt_excerpt=_BOOKING_QUOTE,
                system_prompt_from=[{"path": "agent.py", "start_line": 1, "end_line": 4}])
    ).get("is_error")
    second = submit(payload(hard_constraints=["Be polite."], system_prompt_excerpt=_BOOKING_QUOTE))

    assert not second.get("is_error"), second
    assert saved(destination)["system_prompt_excerpt"] == _BOOKING_QUOTE


def test_a_new_quote_with_wrong_lines_after_lines_alone_were_refused_is_kept(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    imports = [{"path": "agent.py", "start_line": 1, "end_line": 4}]
    quote = "You are Acme's booking agent. Help guests book tables."

    assert not submit(
        payload(hard_constraints=["Be polite."], system_prompt_excerpt=_BOOKING_QUOTE,
                system_prompt_from=[{"path": "agent.py", "start_line": 6, "end_line": 12}])
    ).get("is_error")
    assert submit(payload(hard_constraints=["Be polite."], system_prompt_from=imports)).get("is_error")
    third = submit(payload(hard_constraints=["Be polite."], system_prompt_excerpt=quote, system_prompt_from=imports))

    assert not third.get("is_error"), third
    assert saved(destination)["system_prompt_excerpt"] == quote
    assert saved(destination)["unconfirmed_prompt_from"] == imports


def test_lines_set_aside_in_a_call_refused_for_something_else_are_said(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    submit = submitter(tmp_path / "out", root)
    wrong = payload(
        hard_constraints=["Be polite."],
        system_prompt_excerpt=_BOOKING_QUOTE,
        system_prompt_from=[{"path": "agent.py", "start_line": 1, "end_line": 4}],
    )
    assert submit(wrong).get("is_error")

    second = submit({**wrong, "model": {"name": "gpt-4o", "path": "agent.py", "line": 1}})

    assert "model: the name gpt-4o is not the value written on line 1" in text_of(second)
    assert (
        "system_prompt_from: the lines named (agent.py 1-4) are left out and the quote is kept, "
        "since they don't hold it"
    ) in text_of(second)


# Pins for the line scan


@pytest.mark.parametrize("quote", ['x = "' + '\\"' * 40_000, "x = `" + "\\`" * 40_000])
def test_an_unclosed_string_of_escapes_costs_one_pass(root, quote):
    (root / "crafted.ts").write_text(quote + "\n", encoding="utf-8")

    _, _, took = checked(root, {"name": "gpt-4o", "path": "crafted.ts", "line": 1})

    assert took < 0.25


@pytest.mark.parametrize(
    "file, written, name",
    [
        pytest.param("agent.ts", 'const model = "gpt-4o"; /* fast */', "gpt-4o", id="ts-comment-after"),
        pytest.param("Agent.java", 'String model = "gpt-4o"; /* the default */', "gpt-4o", id="java-comment-after"),
        pytest.param("llm.py", 'MODEL = "gpt-4o"; PROMPT = """', "gpt-4o", id="a-block-opening-after"),
        pytest.param("llm.py", '    * 2, model="gpt-4o")', "gpt-4o", id="python-continuation-star"),
        pytest.param("run.sh", "echo a#b; MODEL=gpt-4o", "gpt-4o", id="shell-hash-in-a-word"),
        pytest.param("run.sh", "ls /*; MODEL=gpt-4o", "gpt-4o", id="shell-slash-star"),
        pytest.param("build.groovy", 'a #b; def model = "gpt-4o"', "gpt-4o", id="groovy-hash"),
        pytest.param("license.py", 'MODEL = "gpt-4o"', "gpt-4o", id="license-py-is-code"),
        pytest.param("readme.py", 'MODEL = "gpt-4o"', "gpt-4o", id="readme-py-is-code"),
        pytest.param("llm.py", "x = rb'Agent #1'; MODEL = \"gpt-4o\"", "gpt-4o", id="two-letter-prefix"),
        pytest.param("llm.py", "x = 'a\\'b # c'; MODEL = \"gpt-4o\"", "gpt-4o", id="escaped-apostrophe"),
        pytest.param("agent.ts", 'u = `${b}//v1`; m = "gpt-4o"', "gpt-4o", id="backtick-string"),
    ],
)
def test_more_honest_lines_after_round_3(root, file, written, name):
    (root / file).write_text(f"\n{written}\n", encoding="utf-8")

    problems, contract, _ = checked(root, {"name": name, "path": file, "line": 2})

    assert problems == []
    assert contract.model is not None and contract.model.name == name


@pytest.mark.parametrize(
    "suffix",
    [".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
     ".java", ".go", ".rs", ".swift", ".kt", ".kts", ".cs", ".scala", ".dart", ".m", ".mm",
     ".groovy", ".gradle"],
)
def test_hash_is_no_comment_in_these_languages(root, suffix):
    (root / f"agent{suffix}").write_text('\na = b #c; m = "gpt-4o"\n', encoding="utf-8")

    problems, _, _ = checked(root, {"name": "gpt-4o", "path": f"agent{suffix}", "line": 2})

    assert problems == []


def test_a_tab_before_hash_starts_a_yaml_comment(root):
    (root / "cfg.yaml").write_text('\nnote: a\t# "gpt-4o"\n', encoding="utf-8")

    problems, _, _ = checked(root, {"name": "gpt-4o", "path": "cfg.yaml", "line": 2})

    assert problems == [not_named("gpt-4o", 2, "cfg.yaml")]


@pytest.mark.parametrize(
    "file, written, name",
    [
        pytest.param("llm.py", 'n = a // 2; model = "gpt-4o"', "gpt-4o", id="python-floor-division-spaced"),
        pytest.param("init.lua", 'local n = #t; model = "gpt-4o"', "gpt-4o", id="lua-length"),
        pytest.param("llm.py", "x = t'Agent #{n}'; MODEL = \"gpt-4o\"", "gpt-4o", id="python-t-string"),
        pytest.param("config.yaml.j2", "model: gpt-4o", "gpt-4o", id="a-config-template"),
    ],
)
def test_more_honest_lines_after_round_3_probes(root, file, written, name):
    (root / file).write_text(f"\n{written}\n", encoding="utf-8")

    problems, contract, _ = checked(root, {"name": name, "path": file, "line": 2})

    assert problems == []
    assert contract.model is not None and contract.model.name == name


@pytest.mark.parametrize(
    "file, written, name",
    [
        pytest.param("env.py", "model = gpt-4o", "gpt-4o", id="env-py-is-code"),
        pytest.param("dockerfile_gen.py", "MODEL = gpt-4o", "gpt-4o", id="dockerfile-py-is-code"),
        pytest.param("agent.js", 'call()// "gpt-4o"', "gpt-4o", id="js-comment-after-a-call"),
        pytest.param("agent.js", 'x = 1// "gpt-4o"', "gpt-4o", id="js-comment-after-a-number"),
        pytest.param("agent.ts", 'x = a[1]// "gpt-4o"', "gpt-4o", id="js-comment-after-a-subscript"),
    ],
)
def test_more_wrong_values_after_round_3_probes(root, file, written, name):
    (root / file).write_text(f"\n{written}\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": name, "path": file, "line": 2})

    assert problems == [not_named(name, 2, file)]


# Pins for rules the plants found unpinned


@pytest.mark.parametrize(
    "written",
    [
        pytest.param('curl http://x/v1; MODEL="gpt-4o"', id="a-url"),
        pytest.param('cp data/* out; MODEL="gpt-4o"', id="a-glob"),
        pytest.param('n=${#MODELS}; MODEL="gpt-4o"', id="a-length"),
        pytest.param('echo $#; MODEL="gpt-4o"', id="a-count"),
    ],
)
def test_a_script_without_a_suffix_keeps_its_model(root, written):
    (root / "start").write_text(f"#!/bin/sh\n{written}\n", encoding="utf-8")

    problems, contract, _ = checked(root, {"name": "gpt-4o", "path": "start", "line": 2})

    assert problems == []
    assert contract.model is not None and contract.model.name == "gpt-4o"


@pytest.mark.parametrize("prefix", ["rt", "tr", "RT"])
def test_a_raw_template_string_is_a_string(root, prefix):
    (root / "llm.py").write_text(f"\nx = {prefix}'Agent #{{n}}'; MODEL = \"gpt-4o\"\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": "gpt-4o", "path": "llm.py", "line": 2})

    assert problems == []


def test_a_triple_quote_after_floor_division_opens_a_block(root):
    (root / "llm.py").write_text(
        'half = n // 2; DOC = """\nmodel = "gpt-4o"\n"""\n', encoding="utf-8"
    )

    problems, _, _ = checked(root, {"name": "gpt-4o", "path": "llm.py", "line": 2})

    assert problems == [not_named("gpt-4o", 2, "llm.py")]


def test_a_quote_alone_after_lines_alone_were_refused_is_not_asked_for_lines(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)

    assert not submit(
        payload(hard_constraints=["Be polite."], system_prompt_excerpt=_BOOKING_QUOTE,
                system_prompt_from=[{"path": "agent.py", "start_line": 6, "end_line": 12}])
    ).get("is_error")
    assert submit(
        payload(hard_constraints=["Be polite."],
                system_prompt_from=[{"path": "agent.py", "start_line": 1, "end_line": 4}])
    ).get("is_error")
    third = submit(payload(hard_constraints=["Be polite."], system_prompt_excerpt=_BOOKING_QUOTE))

    assert not third.get("is_error"), third
    assert saved(destination)["system_prompt_excerpt"] == _BOOKING_QUOTE


# Lines numbered as the reading numbers them


@pytest.mark.parametrize(
    "written, prompt_line, model_line",
    [
        pytest.param(
            b'# head\nA = 1\x0cB = 2\rC = 3\nNOTE = "a\xe2\x80\xa8b"\n'
            b'PROMPT = "You are Acme\'s booking agent."\nMODEL = "gpt-4o"\n',
            4, 5, id="form-feed-lone-cr-and-u2028",
        ),
        pytest.param(
            b'# head\r\nPROMPT = "You are Acme\'s booking agent."\r\nMODEL = "gpt-4o"\r\n',
            2, 3, id="crlf",
        ),
        pytest.param(
            b'# head\nPROMPT = "You are Acme\'s booking agent."\nMODEL = "gpt-4o"',
            2, 3, id="no-newline-at-the-end",
        ),
    ],
)
def test_lines_are_numbered_at_each_newline(tmp_path, root, written, prompt_line, model_line):
    (root / "agent.py").write_bytes(written)
    destination = tmp_path / "out"

    result = accept_contract(
        payload(
            system_prompt_from=[{"path": "agent.py", "start_line": prompt_line, "end_line": prompt_line}],
            model={"name": "gpt-4o", "path": "agent.py", "line": model_line},
        ),
        destination,
        source_root=root,
    )

    assert not result.get("is_error"), result
    assert saved(destination)["system_prompt_excerpt"] == 'PROMPT = "You are Acme\'s booking agent."'
    assert saved(destination)["model"]["line"] == model_line


def test_a_file_ending_in_a_newline_has_no_line_after_it(root):
    (root / "agent.py").write_text('\nMODEL = "gpt-4o"\n', encoding="utf-8")

    problems, _, _ = checked(root, {"name": "gpt-4o", "path": "agent.py", "line": 3})

    assert problems == ["model: line 3 is outside agent.py, which has 2 lines"]


@pytest.mark.parametrize(
    "letter, escaped",
    [
        pytest.param("\U00020000", "\\ud840\\udc00", id="cjk-extension-b"),
        pytest.param("\U0001d400", "\\ud835\\udc00", id="math-bold"),
    ],
)
def test_a_json_escaped_letter_past_ffff_holds_its_quote(letter, escaped):
    from fi.alk.harness.model_citation import quote_problem

    quote = f"Greet each guest as {letter} and seat them at once."
    written = '{"prompt": "' + quote.replace(letter, escaped) + '"}'

    assert quote_problem(quote, written, "prompt.json 1-1") is None


def test_an_escape_past_the_last_character_is_kept_as_written():
    from fi.alk.harness.model_citation import quote_problem

    quote = "Order a table \\U00110000 for two guests tonight."

    assert quote_problem(quote, 'PROMPT = "' + quote + '"', "prompt.py 1-1") is None


@pytest.mark.parametrize(
    "quote, says",
    [
        # 6 letters is the floor of a quote that says something, looked for whole.
        pytest.param("Be kind", True, id="six-letters"),
        pytest.param("Be wry", False, id="five-letters"),
    ],
)
def test_six_letters_say_something(quote, says):
    from fi.alk.harness.model_citation import quote_problem

    assert (quote_problem(quote, 'x = "nothing here"', "a.py 1-1") is not None) == says


def test_sixteen_letters_are_searched_with_gaps():
    from fi.alk.harness.model_citation import quote_pieces, quote_problem

    # 16 letters: words in order, with code between them. 15 would be looked for whole.
    quote = "Be warm and kind now"
    written = 'P = "Be warm " + tone + " and kind now"'

    assert quote_pieces(quote)
    assert quote_problem(quote, written, "a.py 1-1") is None
    assert not quote_pieces("Be warm and kind no")


def test_a_pipe_is_logged_as_not_a_regular_file(tmp_path, caplog):
    from fi.alk.harness.model_citation import read_source

    if not hasattr(os, "mkfifo"):
        pytest.skip("no named pipes here")
    pipe = tmp_path / "pipe"
    os.mkfifo(pipe)
    answer: list[bytes | None] = []
    with caplog.at_level("INFO", logger="fi.alk.harness.model_citation"):
        reading = threading.Thread(target=lambda: answer.append(read_source(pipe, 10)), daemon=True)
        reading.start()
        reading.join(timeout=2)

    assert answer == [None]
    assert [record.getMessage() for record in caplog.records] == [
        "source file not read (not a regular file)"
    ]


@pytest.mark.parametrize(
    "file, written, name",
    [
        pytest.param("init.lua", 'local n = a // 2; model = "gpt-4o"', "gpt-4o", id="lua-floor-division"),
        pytest.param("Makefile", "\techo a#b; MODEL=gpt-4o", "gpt-4o", id="makefile-hash-in-a-word"),
        pytest.param(".flaskenv", "URL=http://x/#a MODEL=gpt-4o", "gpt-4o", id="flaskenv-hash-in-a-word"),
        pytest.param("Dockerfile.dev", "ENV A=a#b MODEL=gpt-4o", "gpt-4o", id="dockerfile-name-hash-in-a-word"),
        pytest.param("run.bash", "echo a#b; MODEL=gpt-4o", "gpt-4o", id="bash-hash-in-a-word"),
        pytest.param("run.zsh", "echo a#b; MODEL=gpt-4o", "gpt-4o", id="zsh-hash-in-a-word"),
        pytest.param("rules.mk", "\techo a#b; MODEL=gpt-4o", "gpt-4o", id="mk-hash-in-a-word"),
        pytest.param("app.dockerfile", "ENV A=a#b MODEL=gpt-4o", "gpt-4o", id="dockerfile-suffix-hash-in-a-word"),
        # prod.env is a .env file, so the name must be its line's own model setting's value (since
        # TH-9042's second round).
        pytest.param("prod.env", "MODEL=gpt-4o # see a#b", "gpt-4o", id="env-suffix-hash-in-a-word"),
        pytest.param("agent.mts", 'class A { #model = "gpt-4o"; }', "gpt-4o", id="mts-private-field"),
        pytest.param("agent.cts", 'this.#model = "gpt-4o";', "gpt-4o", id="cts-private-field"),
        pytest.param("agent.sc", 'val t: A#B = x; val model = "gpt-4o"', "gpt-4o", id="scala-script-type-projection"),
        pytest.param(".env.j2", "MODEL=gpt-4o", "gpt-4o", id="env-template"),
        pytest.param("Dockerfile.j2", "ENV MODEL=gpt-4o", "gpt-4o", id="dockerfile-template"),
        pytest.param(".env.local.j2", "MODEL=gpt-4o", "gpt-4o", id="env-local-template"),
        pytest.param("Dockerfile.dev.j2", "ENV MODEL=gpt-4o", "gpt-4o", id="dockerfile-dev-template"),
        pytest.param("config.yaml.j2", "  - gpt-4o", "gpt-4o", id="yaml-template-list-item"),
        pytest.param("appsettings.json", '"ModelId": "gpt-4o", // the default', "gpt-4o", id="json-comment-after"),
        pytest.param("agent.json", '"url": "http://x//y", "model": "gpt-4o"', "gpt-4o", id="json-slashes-in-a-string"),
    ],
)
def test_more_honest_lines_after_the_settled_round(root, file, written, name):
    (root / file).write_text(f"\n{written}\n", encoding="utf-8")

    problems, contract, _ = checked(root, {"name": name, "path": file, "line": 2})

    assert problems == []
    assert contract.model is not None and contract.model.name == name


@pytest.mark.parametrize(
    "file, written, name",
    [
        pytest.param("appsettings.json", '// "ModelId": "gpt-35-turbo",', "gpt-35-turbo", id="json-line-comment"),
        pytest.param("appsettings.json", '"ModelId": "gpt-4o" /* was "gpt-35-turbo" */', "gpt-35-turbo", id="json-block-comment"),
        pytest.param("agent.json", '"model": "gpt-4o", // "gpt-4-turbo" is slower', "gpt-4-turbo", id="json-comment-after-a-value"),
        pytest.param("agent.json", '* "model": "gpt-4",', "gpt-4", id="json-block-comment-middle-line"),
        pytest.param("dockerfile_gen.py", 'MODEL = DEFAULT#"gpt-4"', "gpt-4", id="python-named-like-a-dockerfile"),
        pytest.param("cfg.yaml", "note: the cart's total # model: 'gpt-4'", "gpt-4", id="a-word-ending-like-a-prefix"),
        pytest.param("cfg.yaml", "note: it_t's ok # model: 'gpt-4'", "gpt-4", id="an-underscore-before-a-prefix"),
        pytest.param("env.rb", "MODEL = gpt-4o", "gpt-4o", id="env-rb-is-code"),
    ],
)
def test_more_wrong_values_after_the_settled_round(root, file, written, name):
    (root / file).write_text(f"\n{written}\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": name, "path": file, "line": 2})

    assert problems == [not_named(name, 2, file)]


def test_a_python_template_is_text(root):
    (root / "agent.py.j2").write_text('MODEL = "gpt-4o"\n', encoding="utf-8")

    problems, _, _ = checked(root, {"name": "gpt-4o", "path": "agent.py.j2", "line": 1})

    assert problems == [prose("agent.py.j2")]


def test_a_type_projection_does_not_hide_a_triple_quote(root):
    (root / "agent.scala").write_text(
        'val t: A#B = x; val p = """\nmodel = "gpt-4o"\n"""\n', encoding="utf-8"
    )

    problems, _, _ = checked(root, {"name": "gpt-4o", "path": "agent.scala", "line": 2})

    assert problems == [not_named("gpt-4o", 2, "agent.scala")]


# The flows


_SHORT_PROMPT = 'import os\n\nINSTRUCTIONS = "Be warm and brief."\n'
_PARAPHRASE = "The agent books tables for Acme guests, keeps other bookings private, and confirms first."


@pytest.mark.parametrize(
    "source, lines, quote, paraphrase",
    [
        pytest.param(
            _SHORT_PROMPT, (3, 3), "Be warm and brief.",
            "The agent is told to be warm and to keep its answers brief.", id="a-whole-short-prompt",
        ),
        pytest.param(_BOOKING, (6, 12), "Be warm and brief.", _PARAPHRASE, id="one-sentence-of-a-long-prompt"),
        pytest.param(_BOOKING, (6, 12), "be warm and brief", _PARAPHRASE, id="another-case-no-full-stop"),
        pytest.param(_BOOKING, (6, 12), "Be warm ... brief.", _PARAPHRASE, id="a-short-elided-quote"),
    ],
)
@pytest.mark.parametrize("then", ["the-same-again", "the-lines-alone"])
def test_a_short_verbatim_quote_after_a_refused_paraphrase_keeps_the_lines(
    tmp_path, root, source, lines, quote, paraphrase, then
):
    (root / "agent.py").write_text(source, encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    named = [{"path": "agent.py", "start_line": lines[0], "end_line": lines[1]}]

    assert submit(
        payload(hard_constraints=["Be polite."], system_prompt_excerpt=paraphrase, system_prompt_from=named)
    ).get("is_error")
    second = submit(
        payload(hard_constraints=["Be polite."], system_prompt_excerpt=quote, system_prompt_from=named)
    )
    assert not second.get("is_error"), second
    again = (
        payload(hard_constraints=["Be polite."], system_prompt_excerpt=quote, system_prompt_from=named)
        if then == "the-same-again"
        else payload(hard_constraints=["Be polite."], system_prompt_from=named)
    )
    third = submit(again)

    assert not third.get("is_error"), third
    assert saved(destination)["system_prompt_from"] == named
    assert "unconfirmed_prompt_from" not in saved(destination)


def test_a_short_quote_not_in_its_lines_is_still_checked_against_the_held_one(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    imports = [{"path": "agent.py", "start_line": 1, "end_line": 4}]

    assert submit(
        payload(hard_constraints=["Be polite."], system_prompt_excerpt=_PARAPHRASE,
                system_prompt_from=[{"path": "agent.py", "start_line": 6, "end_line": 12}])
    ).get("is_error")
    second = submit(
        payload(hard_constraints=["Be polite."], system_prompt_excerpt="Be warm and brief.", system_prompt_from=imports)
    )

    assert text_of(second) == REFUSED + lines_alone("agent.py 1-4", _PARAPHRASE)


@pytest.mark.parametrize("lines", [(6, 12), (1, 4)], ids=["the-right-lines", "the-wrong-lines"])
def test_a_null_in_a_list_quote_is_dropped_before_it_is_held(tmp_path, root, lines):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    named = [{"path": "agent.py", "start_line": lines[0], "end_line": lines[1]}]

    result = submit(
        payload(hard_constraints=["Be polite."], system_prompt_excerpt=["Be warm", None, "and brief."],
                system_prompt_from=named)
    )

    if lines == (6, 12):
        assert not result.get("is_error"), result
        assert saved(destination)["system_prompt_from"] == named
    else:
        # Refused as this call's own quote, not as one given before with "None" in it.
        assert text_of(result).startswith(REFUSED + not_the_quote("agent.py 1-4"))


def test_a_blank_quote_is_no_quote(tmp_path, root):
    submit = submitter(tmp_path / "out", root)

    result = submit(payload(system_prompt_excerpt="   "))

    assert result.get("is_error")
    assert "no hard_constraints and no system_prompt_excerpt" in text_of(result)
    assert PROMPT_LINES not in text_of(result)


def test_two_quotes_alone_in_one_message_are_each_asked_for_lines(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    server = contract_tools(destination, source_root=root)
    tool = next(one for one in server.tools if one.name == "submit_contract")
    both = payload(hard_constraints=["Be polite."], system_prompt_excerpt=_BOOKING_QUOTE)

    async def send_together():
        return await asyncio.gather(tool.handler(dict(both)), tool.handler(dict(both)))

    first, second = asyncio.run(send_together())

    assert PROMPT_LINES in text_of(first) and PROMPT_LINES in text_of(second)
    assert not (destination / "contract.json").exists()


def test_short_lines_set_aside_in_a_call_refused_for_something_else_are_said(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    submit = submitter(tmp_path / "out", root)
    wrong = payload(
        hard_constraints=["Be polite."],
        system_prompt_excerpt="Be warm and brief.",
        system_prompt_from=[{"path": "agent.py", "start_line": 1, "end_line": 4}],
    )
    assert submit(wrong).get("is_error")

    second = submit({**wrong, "model": {"name": "gpt-4o", "path": "agent.py", "line": 1}})

    assert "model: the name gpt-4o is not the value written on line 1" in text_of(second)
    assert (
        "system_prompt_from: the lines named (agent.py 1-4) are copied unconfirmed, since they "
        "don't hold the short quote"
    ) in text_of(second)


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"system_prompt_excerpt": ["Be warm", "and brief."]}, id="a-list"),
        pytest.param({"prompt": ["Be warm", None, "and brief."]}, id="a-list-with-a-null-under-an-alias"),
        pytest.param({"system_prompt_excerpt": 42}, id="a-number"),
    ],
)
def test_the_held_quote_is_the_quote_the_contract_reads(fields):
    from fi.alk.harness.contract import prompt_quote
    from fi.alk.harness.tools import _without_nulls

    contract = AgentContract.model_validate(_without_nulls(payload(**fields)))

    assert prompt_quote(_without_nulls(payload(**fields))) == contract.system_prompt_excerpt


def test_a_saved_call_that_kept_the_quote_says_so(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    imports = [{"path": "agent.py", "start_line": 1, "end_line": 4}]
    wrong = payload(hard_constraints=["Be polite."], system_prompt_excerpt=_BOOKING_QUOTE, system_prompt_from=imports)

    assert submit(wrong).get("is_error")
    second = submit(wrong)

    assert not second.get("is_error"), second
    assert text_of(second).endswith(
        "\nsystem_prompt_from: the lines named (agent.py 1-4) are recorded as unconfirmed, since "
        "they don't hold the quote; the quote is the prompt."
    )


# A short quote on its own lines, and its limits


@pytest.mark.parametrize(
    "first_lines, short, line",
    [
        # The quote held from a call with no lines; then a name on the prompt's first line.
        pytest.param(None, "PROMPT", 6, id="a-name-after-a-quote-alone"),
        pytest.param(None, "SYSTEM_PROMPT", 6, id="a-setting-name-after-a-quote-alone"),
        # The quote held from lines that held it; then a sentence repeated in a comment.
        pytest.param((6, 12), "Be warm and brief.", 14, id="a-comment-after-a-confirmed-quote"),
    ],
)
def test_a_short_quote_on_its_own_lines_does_not_replace_a_stronger_one(
    tmp_path, root, first_lines, short, line
):
    (root / "agent.py").write_text(_BOOKING + "\n# Style: Be warm and brief.\n", encoding="utf-8")
    destination = tmp_path / "out"
    submit = submitter(destination, root)
    named = [{"path": "agent.py", "start_line": line, "end_line": line}]
    first = payload(hard_constraints=["Be polite."], system_prompt_excerpt=_BOOKING_QUOTE)
    if first_lines:
        first["system_prompt_from"] = [{"path": "agent.py", "start_line": first_lines[0], "end_line": first_lines[1]}]
    submit(first)

    second = submit(payload(hard_constraints=["Be polite."], system_prompt_excerpt=short, system_prompt_from=named))
    assert text_of(second).startswith(REFUSED + lines_alone(f"agent.py {line}-{line}", _BOOKING_QUOTE))
    third = submit(payload(hard_constraints=["Be polite."], system_prompt_excerpt=short, system_prompt_from=named))

    assert not third.get("is_error"), third
    assert saved(destination)["system_prompt_excerpt"] == _BOOKING_QUOTE
    assert saved(destination)["unconfirmed_prompt_from"] == named


def test_a_padded_short_quote_is_this_calls_own(tmp_path, root):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    submit = submitter(tmp_path / "out", root)

    result = submit(
        payload(hard_constraints=["Be polite."], system_prompt_excerpt="  Be warm and brief.  ",
                system_prompt_from=[{"path": "agent.py", "start_line": 1, "end_line": 4}])
    )

    assert text_of(result).startswith(REFUSED + not_the_quote("agent.py 1-4"))


@pytest.mark.parametrize(
    "quote, held",
    [
        pytest.param("Be warm ... brief.", True, id="pieces-in-order"),
        pytest.param("brief ... Be warm", False, id="pieces-out-of-order"),
    ],
)
def test_a_short_elided_quote_is_looked_for_piece_by_piece(quote, held):
    from fi.alk.harness.model_citation import quote_problem

    assert (quote_problem(quote, 'P = "Be warm and brief."', "a.py 1-1") is None) == held


def test_the_quote_under_its_own_name_comes_before_an_alias():
    from fi.alk.harness.contract import prompt_quote

    both = payload(system_prompt_excerpt="You are Acme's booking agent.", prompt="Something else entirely.")

    assert prompt_quote(both) == "You are Acme's booking agent."
    assert AgentContract.model_validate(both).system_prompt_excerpt == "You are Acme's booking agent."


@pytest.mark.parametrize(
    "file",
    ["prompts/dockerfile_writer.j2", "prompts/env.system.j2", "prompts/system.prompt.j2", "dockerfile.md"],
)
def test_a_prompt_named_like_configuration_is_text(root, file):
    (root / file).parent.mkdir(parents=True, exist_ok=True)
    (root / file).write_text("You write the deployment.\nmodel: gpt-4o\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": "gpt-4o", "path": file, "line": 2})

    assert problems == [prose(file)]


def test_a_conf_file_has_no_block_comments(root):
    (root / "site.conf").write_text('\nrewrite ^/(.*)//$ /$1 permanent; set $m "gpt-4o";\n', encoding="utf-8")

    problems, contract, _ = checked(root, {"name": "gpt-4o", "path": "site.conf", "line": 2})

    assert problems == []
    assert contract.model is not None and contract.model.name == "gpt-4o"


def test_a_json_template_keeps_its_comments(root):
    (root / "config.json.j2").write_text('\n// "model": "gpt-4-turbo",\n', encoding="utf-8")

    problems, _, _ = checked(root, {"name": "gpt-4-turbo", "path": "config.json.j2", "line": 2})

    assert problems == [not_named("gpt-4-turbo", 2, "config.json.j2")]


@pytest.mark.parametrize("nothing", ["n / a", "- -", "a b"])
def test_words_that_say_nothing_do_not_replace_the_held_quote(tmp_path, root, nothing):
    (root / "agent.py").write_text(_BOOKING, encoding="utf-8")
    submit = submitter(tmp_path / "out", root)
    submit(payload(hard_constraints=["Be polite."], system_prompt_excerpt=_BOOKING_QUOTE))

    result = submit(
        payload(hard_constraints=["Be polite."], system_prompt_excerpt=nothing,
                system_prompt_from=[{"path": "agent.py", "start_line": 1, "end_line": 4}])
    )

    assert text_of(result) == REFUSED + lines_alone("agent.py 1-4", _BOOKING_QUOTE)


# TH-9042: a model citation never reads a file on the secret list, apart from a .env file's
# line whose setting names a model; and it never tells such a file's length or whether it exists.


@pytest.mark.parametrize(
    "file, written",
    [
        pytest.param(".pgpass", "db:5432:app:admin:changeme", id="pgpass"),
        pytest.param("infra/prod.tfvars", 'db_password = "changeme"', id="tfvars"),
        pytest.param("kubeconfig.yaml", "token: changeme", id="kubeconfig"),
        pytest.param("gcp-key.json", '{"private_key": "changeme"}', id="key-json"),
    ],
)
def test_a_model_citation_never_reads_a_listed_file(root, file, written):
    target = root / file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{written}\n", encoding="utf-8")

    present, contract, _ = checked(root, {"name": "changeme", "path": file, "line": 1})
    past_the_end, _, _ = checked(root, {"name": "changeme", "path": file, "line": 9})
    target.unlink()
    missing, _, _ = checked(root, {"name": "changeme", "path": file, "line": 1})

    assert present == past_the_end == missing == [kept_out(file)]
    assert contract.model is not None and contract.model.name == "changeme"


def test_a_model_citation_through_a_link_to_a_listed_file_is_refused(root):
    (root / ".pgpass").write_text("db:5432:app:admin:changeme\n", encoding="utf-8")
    (root / "settings.txt").symlink_to(".pgpass")
    (root / "gone.yaml").symlink_to("kubeconfig.yaml")

    linked, _, _ = checked(root, {"name": "changeme", "path": "settings.txt", "line": 1})
    dangling, _, _ = checked(root, {"name": "changeme", "path": "gone.yaml", "line": 1})

    assert linked == [kept_out("settings.txt")]
    assert dangling == [kept_out("gone.yaml")]


def test_a_env_file_cites_a_model_only_on_a_line_that_sets_one(root):
    (root / ".env").write_text(
        "DB_PASSWORD=changeme\nOPENAI_MODEL=gpt-4o\nexport LLM_MODEL=gpt-4o-mini\n", encoding="utf-8"
    )

    password, _, _ = checked(root, {"name": "changeme", "path": ".env", "line": 1})
    model, contract, _ = checked(root, {"name": "gpt-4o", "path": ".env", "line": 2})
    exported, _, _ = checked(root, {"name": "gpt-4o-mini", "path": ".env", "line": 3})
    past_the_end, _, _ = checked(root, {"name": "gpt-4o", "path": ".env", "line": 40})

    assert password == [not_a_model_line(".env", 1)]
    assert model == [] and contract.model is not None and contract.model.path == ".env"
    assert exported == []
    assert past_the_end == ["model: line 40 is outside .env"]


def test_a_env_line_whose_setting_is_not_a_model_is_refused_even_with_a_model_on_it(root):
    (root / ".env.local").write_text("\nURL=http://x/#a MODEL=gpt-4o\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": "gpt-4o", "path": ".env.local", "line": 2})

    assert problems == [not_a_model_line(".env.local", 2)]


def test_a_source_under_a_listed_folder_still_cites_its_model(tmp_path, monkeypatch):
    source = tmp_path / ".claude" / "worktrees" / "repo"
    source.mkdir(parents=True)
    (source / "agent.py").write_text('MODEL = "gpt-4o"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path / ".claude")

    relative, _, _ = checked(source, {"name": "gpt-4o", "path": "agent.py", "line": 1})
    absolute, _, _ = checked(source, {"name": "gpt-4o", "path": str(source / "agent.py"), "line": 1})

    assert relative == absolute == []


@pytest.mark.parametrize(
    "written, name",
    [
        pytest.param("AZURE_OPENAI_DEPLOYMENT=gpt4o-prod", "gpt4o-prod", id="azure-deployment"),
        pytest.param("AZURE_OPENAI_DEPLOYMENT_NAME=gpt4o-prod", "gpt4o-prod", id="azure-deployment-name"),
        pytest.param("OPENAI_ENGINE=gpt-4o", "gpt-4o", id="engine"),
        pytest.param("AZURE_OPENAI_ENGINE=gpt4o-prod", "gpt4o-prod", id="azure-engine"),
        pytest.param("LLM=gpt-4o", "gpt-4o", id="llm"),
        pytest.param("DEFAULT_LLM=gpt-4o", "gpt-4o", id="default-llm"),
        pytest.param("export LLM_MODEL=gpt-4o", "gpt-4o", id="exported"),
        pytest.param('OPENAI_MODEL="gpt-4o"', "gpt-4o", id="double-quoted"),
        pytest.param("export OPENAI_MODEL='gpt-4o'  # pinned", "gpt-4o", id="single-quoted-exported"),
        pytest.param("OPENAI_MODEL=gpt-4o # the default", "gpt-4o", id="comment-after"),
        pytest.param('OPENAI_MODEL="${OPENAI_MODEL:-gpt-4o}"', "gpt-4o", id="quoted-compose-default"),
    ],
)
def test_a_env_setting_named_for_a_model_by_any_of_its_words_is_accepted(root, written, name):
    (root / ".env").write_text(f"{written}\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": name, "path": ".env", "line": 1})

    assert problems == []


@pytest.mark.parametrize("file", ["prod.env", "deploy/app.env", ".env.production"])
def test_every_kind_of_env_file_takes_the_same_rule(root, file):
    (root / file).parent.mkdir(parents=True, exist_ok=True)
    (root / file).write_text("DB_PASSWORD=fake-guess\nOPENAI_MODEL=gpt-4o\n", encoding="utf-8")

    accepted, _, _ = checked(root, {"name": "gpt-4o", "path": file, "line": 2})
    refused, _, _ = checked(root, {"name": "fake-guess", "path": file, "line": 1})

    assert accepted == []
    assert refused == [not_a_model_line(file, 1)]


@pytest.mark.parametrize(
    "file, written, name",
    [
        pytest.param(".env", "MODEL_API_KEY=sk-fake-guess", "sk-fake-guess", id="model-key"),
        pytest.param(".env", "LLM_TOKEN=fake-guess", "fake-guess", id="llm-token"),
        pytest.param(".env", "OPENAI_MODEL_PASSWORD=fake-guess", "fake-guess", id="model-password"),
        pytest.param(".env", "LLM_APIKEY=sk-fake-guess", "sk-fake-guess", id="joined-apikey"),
        pytest.param(".env", "MODEL_ACCESSKEY=fake-guess", "fake-guess", id="joined-accesskey"),
        pytest.param(".env", "OPENAI_MODEL_APIKEY=sk-fake-guess", "sk-fake-guess", id="model-joined-apikey"),
        pytest.param(".env", "LLM_PASSWD=fake-guess", "fake-guess", id="joined-passwd"),
        pytest.param(".env", "LLM_KEY=fake-guess", "fake-guess", id="part-key"),
        pytest.param(".env", "MODEL_TOKEN=fake-guess", "fake-guess", id="part-token"),
        pytest.param(".env", "MODEL_SECRET=fake-guess", "fake-guess", id="part-secret"),
        pytest.param(".env", "LLM_PASS=fake-guess", "fake-guess", id="part-pass"),
        pytest.param(".env", "LLM_PWD=fake-guess", "fake-guess", id="part-pwd"),
        pytest.param(".env", "LLM_AUTH=fake-guess", "fake-guess", id="part-auth"),
        pytest.param(".env", "MODEL_CREDENTIAL=fake-guess", "fake-guess", id="part-credential"),
        pytest.param(".env", "LLM_URL=fake-guess", "fake-guess", id="part-url"),
        pytest.param(".env", "MODEL_URI=fake-guess", "fake-guess", id="part-uri"),
        pytest.param(".env", "LLM_DSN=fake-guess", "fake-guess", id="part-dsn"),
        pytest.param(".env", "MODEL_URL=https://admin:fake@llm.local/v1", "https://admin:fake@llm.local/v1", id="model-url"),
        pytest.param(
            ".env",
            "DB_ENGINE_URL=postgresql://admin:fake@db/app",
            "postgresql://admin:fake@db/app",
            id="database-engine-url",
        ),
        pytest.param(".env", "DB_ENGINE=postgresql", "postgresql", id="database-engine"),
        pytest.param("prod.env", "URL=http://x/#a MODEL=gpt-4o", "gpt-4o", id="env-suffix-url-first"),
        pytest.param(".env", "OPENAI_MODEL=gpt-4o OPENAI_API_KEY=sk-fakeguess1", "sk-fakeguess1", id="second-setting"),
        pytest.param(".env", "OPENAI_MODEL=gpt-4o;OPENAI_API_KEY=sk-fakeguess1", "sk-fakeguess1", id="second-setting-semicolon"),
        pytest.param(".env", 'OPENAI_MODEL="gpt-4o" DB_PASSWORD="fakeguess1"', "fakeguess1", id="second-setting-quoted"),
        pytest.param(".envrc", "export LLM_MODEL=gpt-4o; export DB_PASSWORD=fakeguess1", "fakeguess1", id="envrc-second-export"),
        pytest.param("prod.env", "MODEL_NOTE=a#b OPENAI_API_KEY=sk-fakeguess1", "sk-fakeguess1", id="hash-then-key"),
        pytest.param(".env", 'LLM_CONFIG={"model":"gpt-4o","api_key":"sk-fakeguess1"}', "sk-fakeguess1", id="json-value"),
        pytest.param(".env.yaml", "model: {name: gpt-4o, api_key: sk-fakeguess1}", "sk-fakeguess1", id="flow-mapping"),
        pytest.param(".env", "MODEL_NOTE=a#b MODEL=gpt-4o", "gpt-4o", id="model-as-a-second-setting"),
        # Named like a .env file, so it takes the .env rule (since TH-9042's second round).
        pytest.param(".env_loader.py", 'MODEL = DEFAULT#"gpt-4"', "gpt-4", id="python-named-like-an-env-file"),
        pytest.param(".env.yaml", "DB_PASSWORD: MODELfake123", "MODELfake123", id="colon-setting"),
        pytest.param(".env", "MODELfake123", "MODELfake123", id="no-setting"),
    ],
)
def test_a_env_line_that_holds_a_secret_or_sets_nothing_is_refused(root, file, written, name):
    (root / file).write_text(f"{written}\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": name, "path": file, "line": 1})

    assert problems == [not_a_model_line(file, 1)]


def test_a_backup_copy_of_a_credential_file_is_refused(root):
    (root / ".pgpass.bak").write_text("db:5432:app:admin:changeme\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": "changeme", "path": ".pgpass.bak", "line": 1})

    assert problems == [kept_out(".pgpass.bak")]
