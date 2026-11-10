"""The model an agent talks to the person with: recorded only where its name is on a cited line.

The save is the only gate. A name it can't find whole on the line the model cites is refused with
a message saying what to do, and a contract without a model is saved exactly as before.
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
from fi.alk.harness.tools import (
    _model_problems,
    _named_on,
    accept_contract,
    contract_tools,
)

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
        f"model: the name {name[:100]} is not on line {line} of {path[:200]} as a whole name; "
        "write it exactly as it appears there, or leave model out if the name is only set at "
        "run time"
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
        problems = _model_problems(contract, root)
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


# P1, P8


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


# P2


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


# P3a-P3d


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


# P3e


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


# P3f


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


# P3g


def test_model_line_outside_the_file_is_refused(root):
    (root / "three.py").write_text('A = 1\nB = 2\nMODEL = "gpt-4o"\n')

    problems, _, _ = checked(root, {"name": "gpt-4o", "path": "three.py", "line": 4})

    assert problems == [outside(4, "three.py", 3)]


# P3h


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
    ],
)
def test_model_name_not_on_the_cited_line_is_refused(root, written, name):
    if not name:
        # On a thread that can't hold the run open: a search that never moves on fails here.
        answer: list[bool] = []
        searching = threading.Thread(
            target=lambda: answer.append(_named_on(written, name)), daemon=True
        )
        searching.start()
        searching.join(timeout=2)
        assert answer == [False]
        return
    (root / "named.py").write_text(f"# the model\n{written}\n", encoding="utf-8")

    problems, _, _ = checked(root, {"name": name, "path": "named.py", "line": 2})

    assert problems == [not_named(name, 2, "named.py")]


@pytest.mark.parametrize(
    "written, name",
    [
        pytest.param('model="gpt-4o"', "gpt-4o", id="quoted"),
        pytest.param("model: gpt-4o", "gpt-4o", id="line-end"),
        pytest.param("gpt-4o is the model", "gpt-4o", id="line-start"),
        pytest.param('model = "openai/gpt-4o"', "openai/gpt-4o", id="with-provider-prefix"),
        pytest.param('x = "gpt-4o-mini" or "gpt-4o"', "gpt-4o", id="second-occurrence"),
        pytest.param("«gpt-4o»", "gpt-4o", id="non-ascii-edge"),
    ],
)
def test_model_whole_name_is_found(root, written, name):
    (root / "named.py").write_text(f"# the model\n{written}\n", encoding="utf-8")

    problems, contract, _ = checked(root, {"name": name, "path": "named.py", "line": 2})

    assert problems == []
    assert contract.model is not None and contract.model.name == name


# P4


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


# P5


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


# P6


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


# P7


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
            system_prompt_excerpt="You take orders",
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
        system_prompt_excerpt="short quote",
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
