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
    assert took < 0.5


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
    assert took < 0.5


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
    assert took < 0.5


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
        pytest.param("llm = ChatOpenAI(model=settings.llm_model)", "settings.llm_model", id="attribute"),
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
        pytest.param(b'# head\nA = 1\x0cB = 2\nMODEL = "gpt-4o"\n', 4, id="form-feed"),
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
    assert took < 0.5


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
            None,
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

def not_the_quote(named: str) -> str:
    return (
        f"system_prompt_from: the lines named ({named}) don't hold system_prompt_excerpt; name "
        "the lines the quoted prompt is written on"
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


def test_a_bare_value_in_a_configuration_file_must_be_whole(root):
    # Written bare after a key, the value has no quotes to end it: the name's own edges do.
    (root / "llm.yaml").write_text("provider: openai\nmodel: gpt-4o-mini\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": "gpt-4o", "path": "llm.yaml", "line": 2})

    assert problems == [not_named("gpt-4o", 2, "llm.yaml")]


# A configuration file's bare value is found in about one pass over the line


def test_a_crafted_configuration_line_does_not_hold_up_the_check(root):
    (root / "crafted.yaml").write_bytes(b"a " * (MIB // 2))

    problems, _, took = checked(root, {"name": "a", "path": "crafted.yaml", "line": 1})

    assert problems == [not_named("a", 1, "crafted.yaml")]
    assert took < 0.5


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
    ],
)
def test_more_honest_lines_are_found(root, file, written, name):
    (root / file).write_text(f"# settings\n{written}\n", encoding="utf-8")

    problems, contract, _ = checked(root, {"name": name, "path": file, "line": 2})

    assert problems == []
    assert contract.model is not None and contract.model.name == name


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
        # The first 80 letters match and the rest of the quote differs: a quote may trail off.
        pytest.param(_LONG_PROMPT[:120] + " and something else entirely", False, id="same-start"),
        # The quote differs from the lines within its first 80 letters.
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
        + "\n  - system_prompt_from: if the lines are refused again, the quote is kept and the "
        "lines are left out"
    )
    second = submit(wrong)
    assert not second.get("is_error"), second
    assert saved(destination)["system_prompt_excerpt"] == "You are pair, a helpful assistant."
    assert saved(destination)["system_prompt_from"] == []


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
        pytest.param('MODEL = os.getenv("model-name")', "model-name", id="getenv-hyphenated"),
        pytest.param('MODEL = cfg.get("llm.model")', "llm.model", id="get-dotted"),
        pytest.param('MODEL = os.getenv("openai_model_v2")', "openai_model_v2", id="getenv-with-a-digit"),
        pytest.param('MODEL = os.environ["llm-model"]', "llm-model", id="subscript"),
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
