"""A prompt named by its lines is saved as its text when the lines hold one string made only of
plain literals, and as written otherwise. Nothing in the customer's file is run."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from fi.alk.harness.contract import AgentContract, PromptSource
from fi.alk.harness.tools import _copy_prompt, contract_tools

REPO = Path(__file__).resolve().parents[2]
PYDANTIC_AI = REPO / "examples" / "harness" / "pydantic_ai_support" / "agent.py"
SENTENCE = (
    "You are a customer support agent for an online store. Use the tools to look up orders and "
    "start returns, and never guess an order's status. Answer briefly."
)


def copied(root: Path, source: str, start: int, end: int, name: str = "agent.py"):
    """What the copy saves for lines ``start``-``end``, and the text it offers (empty when the
    lines stay as written)."""
    (root / name).write_text(source, encoding="utf-8", newline="")
    contract = AgentContract(
        agent="a",
        system_prompt_excerpt="quoted",
        system_prompt_from=[PromptSource(path=name, start_line=start, end_line=end)],
    )
    as_text: list[str] = []
    assert _copy_prompt(contract, root, as_text=as_text) == []
    return contract.system_prompt_excerpt, as_text


def as_written(source: str, start: int, end: int) -> str:
    return "\n".join(source.split("\n")[start - 1 : end])


# The reference agent's prompt


def _reference(tmp_path: Path) -> tuple[Path, int, int]:
    """The Pydantic AI reference agent, and the lines its ``instructions=(`` call spans."""
    shutil.copy(PYDANTIC_AI, tmp_path / "agent.py")
    lines = PYDANTIC_AI.read_text(encoding="utf-8").split("\n")
    opens = next(index for index, line in enumerate(lines, 1) if "instructions=(" in line)
    return tmp_path, opens, opens + 3


def test_the_reference_agents_prompt_is_saved_as_its_sentence(tmp_path) -> None:
    root, opens, closes = _reference(tmp_path)
    source = (root / "agent.py").read_text(encoding="utf-8")

    excerpt, as_text = copied(root, source, opens, closes)

    assert excerpt == as_written(source, opens, closes)
    assert as_text == [SENTENCE]


def test_named_tightly_the_reference_agents_prompt_is_its_sentence(tmp_path) -> None:
    root, opens, closes = _reference(tmp_path)
    source = (root / "agent.py").read_text(encoding="utf-8")

    assert copied(root, source, opens + 1, closes - 1)[1] == [SENTENCE]


def test_named_loosely_the_whole_call_stays_as_written(tmp_path) -> None:
    # The agent's other arguments are on these lines too; text would drop them.
    root, opens, closes = _reference(tmp_path)
    source = (root / "agent.py").read_text(encoding="utf-8")

    excerpt, as_text = copied(root, source, opens - 1, closes + 2)

    assert as_text == []
    assert excerpt == as_written(source, opens - 1, closes + 2)


def test_through_the_save_the_reference_agents_prompt_is_its_sentence(tmp_path) -> None:
    root, opens, closes = _reference(tmp_path)
    destination = tmp_path / "out"
    server = contract_tools(destination, source_root=root)
    submit = next(tool for tool in server.tools if tool.name == "submit_contract")

    result = asyncio.run(
        submit.handler(
            {
                "agent": "support",
                "modality": "chat",
                "real_use_cases": ["a return"],
                "tools": [{"name": "look_up_order", "args": ["order_id"]}],
                # The quote is checked against the lines as written, then the text is kept.
                "system_prompt_excerpt": "You are a customer support agent ... Answer briefly.",
                "system_prompt_from": [
                    {"path": "agent.py", "start_line": opens, "end_line": closes}
                ],
            }
        )
    )

    assert not result.get("is_error"), result
    saved = json.loads((destination / "contract.json").read_text(encoding="utf-8"))
    assert saved["system_prompt_excerpt"] == SENTENCE


# Each row of the rule


@pytest.mark.parametrize(
    ("source", "start", "end", "text"),
    [
        pytest.param('PROMPT = "Be brief."\n', 1, 1, "Be brief.", id="one-literal"),
        pytest.param('PROMPT: str = "Be brief."\n', 1, 1, "Be brief.", id="annotated-name"),
        pytest.param('PROMPT = "Line one.\\nLine two."\n', 1, 1, "Line one.\nLine two.",
                     id="escapes-resolved"),
        pytest.param('PROMPT = (\n    "Be "\n    "brief."\n)\n', 1, 4, "Be brief.",
                     id="side-by-side"),
        pytest.param('PROMPT = "Be " + "brief."\n', 1, 1, "Be brief.", id="joined-with-plus"),
        pytest.param('PROMPT = f"Be {{brief}}."\n', 1, 1, "Be {brief}.", id="f-string-no-gaps"),
        pytest.param('agent = Agent(\n    instructions="Be brief.",  # the persona\n)\n', 2, 2,
                     "Be brief.", id="an-argument"),
        pytest.param('PROMPT = """\nBe brief.\nBe kind.\n"""\n', 1, 4, "\nBe brief.\nBe kind.\n",
                     id="triple-quoted-whole"),
        pytest.param('PROMPT = """\nBe brief.\nBe kind.\n"""\n', 2, 3, "Be brief.\nBe kind.",
                     id="inside-a-triple-quoted-string"),
    ],
)
def test_one_plain_string_is_saved_as_its_text(tmp_path, source, start, end, text) -> None:
    assert copied(tmp_path, source, start, end)[1] == [text]


@pytest.mark.parametrize(
    ("source", "start", "end"),
    [
        pytest.param('PROMPT = f"Hi {name}."\n', 1, 1, id="f-string-with-a-gap"),
        pytest.param('PROMPT = "Hi {}.".format(name)\n', 1, 1, id="format"),
        pytest.param('PROMPT = "Hi %s." % name\n', 1, 1, id="percent"),
        pytest.param('PROMPT = "Hi " + name\n', 1, 1, id="plus-a-name"),
        pytest.param('PROMPT = "Hi " + who()\n', 1, 1, id="plus-a-call"),
        pytest.param('PROMPT = dedent("Be brief.")\n', 1, 1, id="a-call-around-it"),
        pytest.param('PROMPT = "Be brief.".strip()\n', 1, 1, id="a-method-on-it"),
        pytest.param('PROMPT = BASE\n', 1, 1, id="a-name-defined-elsewhere"),
        pytest.param('A = "Be brief."\nB = "Be kind."\n', 1, 2, id="two-strings"),
        pytest.param('PROMPT = b"Be brief."\n', 1, 1, id="bytes"),
        pytest.param('PROMPT = 42\n', 1, 1, id="not-a-string"),
        pytest.param('PROMPT = """\nBe brief.\n"""\n', 2, 3, id="a-quote-mark-on-the-lines"),
        pytest.param('agent = Agent(instructions="Be brief.")\n', 1, 1, id="the-call-on-the-line"),
        pytest.param('PROMPT = "Be brief."\nprint(PROMPT)\n', 1, 2, id="more-code-on-the-lines"),
        pytest.param('import os\nDB = "x"\n', 1, 2, id="a-statement-before-it"),
        pytest.param('PROMPT = "Be brief."\ndef broken(:\n', 1, 1, id="a-file-that-doesnt-parse"),
        pytest.param('X = 1\rPROMPT = "Be brief."\n', 1, 1, id="a-lone-carriage-return"),
    ],
)
def test_anything_else_stays_as_written(tmp_path, source, start, end) -> None:
    excerpt, as_text = copied(tmp_path, source, start, end)

    assert as_text == []
    assert excerpt == as_written(source, start, end)


def test_a_prompt_file_that_is_not_python_is_text_already(tmp_path) -> None:
    source = 'You are "Ava".\nAnswer briefly.\n'

    as_text = copied(tmp_path, source, 1, 2, name="prompt.md")[1]

    assert as_text == ['You are "Ava".\nAnswer briefly.']


def test_all_parts_or_none(tmp_path) -> None:
    (tmp_path / "persona.py").write_text('PERSONA = "Be brief."\n', encoding="utf-8")
    (tmp_path / "rules.py").write_text('RULES = f"Serve {shop}."\n', encoding="utf-8")
    (tmp_path / "extra.md").write_text("Be kind.\n", encoding="utf-8")

    def parts(*names: str) -> tuple[str, list[str]]:
        contract = AgentContract(
            agent="a",
            system_prompt_excerpt="quoted",
            system_prompt_from=[
                PromptSource(path=name, start_line=1, end_line=1) for name in names
            ],
        )
        as_text: list[str] = []
        assert _copy_prompt(contract, tmp_path, as_text=as_text) == []
        return contract.system_prompt_excerpt, as_text

    assert parts("persona.py", "extra.md")[1] == ["Be brief.\n\nBe kind."]
    # One part only known when the agent runs: every part stays as written.
    assert parts("persona.py", "rules.py") == (
        'PERSONA = "Be brief."\n\nRULES = f"Serve {shop}."',
        [],
    )


def test_the_customers_code_is_never_run(tmp_path) -> None:
    marker = tmp_path / "ran"
    source = (
        f'import pathlib\npathlib.Path({str(marker)!r}).write_text("x")\n'
        'PROMPT = "Be brief."\n'
    )

    assert copied(tmp_path, source, 3, 3)[1] == ["Be brief."]
    assert not marker.exists()


# What the text can't be read from, or shouldn't be


def _submit(
    root: Path, destination: Path, start: int, end: int, quote: str, **options
) -> dict:
    server = contract_tools(destination, source_root=root, **options)
    submit = next(tool for tool in server.tools if tool.name == "submit_contract")
    return asyncio.run(
        submit.handler(
            {
                "agent": "support",
                "modality": "chat",
                "real_use_cases": ["a return"],
                "tools": [{"name": "look_up_order", "args": ["order_id"]}],
                "system_prompt_excerpt": quote,
                "system_prompt_from": [{"path": "agent.py", "start_line": start, "end_line": end}],
            }
        )
    )


def _saved_prompt(destination: Path) -> str:
    saved = json.loads((destination / "contract.json").read_text(encoding="utf-8"))
    return saved["system_prompt_excerpt"]


@pytest.mark.parametrize("term", ["1", '"a"'], ids=["numbers", "strings"])
def test_a_long_chain_elsewhere_in_the_file_is_read_past(tmp_path, term) -> None:
    prompt = 'PROMPT = "Answer briefly and never guess an order\'s status."'
    source = f"TOTAL = {' + '.join([term] * 1500)}\n{prompt}\n"
    (tmp_path / "agent.py").write_text(source, encoding="utf-8")
    destination = tmp_path / "out"

    result = _submit(tmp_path, destination, 2, 2, "Answer briefly and never guess")

    assert not result.get("is_error"), result
    assert _saved_prompt(destination) == "Answer briefly and never guess an order's status."


@pytest.mark.parametrize("failing", ["python_tree", "prompt_text"])
def test_a_file_the_text_cant_be_read_from_keeps_the_lines_as_written(
    tmp_path, monkeypatch, failing
) -> None:
    from fi.alk.harness import prompt_text

    def fails(*args, **kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(prompt_text, failing, fails)
    written = 'PROMPT = "Answer briefly and never guess an order\'s status."'
    (tmp_path / "agent.py").write_text(written + "\n", encoding="utf-8")
    destination = tmp_path / "out"

    # Checked in this process, where the stand-in that fails is.
    result = _submit(
        tmp_path, destination, 1, 1, "Answer briefly and never guess", save_checks="thread"
    )

    assert not result.get("is_error"), result
    assert _saved_prompt(destination) == written


@pytest.mark.parametrize(
    "escape",
    ["\\x00", "\\x1b", "\\x7f", "\\x85", "\\u202e", "\\u2066", "\\u200f", "\\ud800", "\\r"],
    ids=["nul", "escape", "delete", "c1-next-line", "right-to-left-override", "isolate",
         "right-to-left-mark", "lone-surrogate", "carriage-return"],
)
def test_a_control_character_keeps_the_lines_as_written(tmp_path, escape) -> None:
    source = f'PROMPT = "Be brief.{escape}Be kind."\n'

    excerpt, as_text = copied(tmp_path, source, 1, 1)

    assert as_text == []
    assert excerpt == as_written(source, 1, 1)


def test_a_tab_and_a_line_break_are_text(tmp_path) -> None:
    source = 'PROMPT = "Be brief.\\tBe kind.\\nBe quick."\n'

    assert copied(tmp_path, source, 1, 1)[1] == ["Be brief.\tBe kind.\nBe quick."]
