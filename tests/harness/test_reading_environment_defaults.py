"""What a reading writes about the agent's environment and model when the agent's own code gives
a default (TH-10401, found by the 18 Nov real readings).

A variable the code reads with a default is not one the person must supply: the Claude Agent
SDK reference agent reads ``os.getenv("ANTHROPIC_MODEL", "vertex_ai/gemini-3.7-flash")``, and its
reading asked for it anyway. A model whose name the environment can override still has the
default written in the code: page-turner-langgraph-otel's reading saved no model at all."""

from __future__ import annotations

from pathlib import Path

import pytest

from fi.alk.harness.contract import AgentContract
from fi.alk.harness.tools import _checked_contract, contract_tools

_AGENT = """\
import os

MODEL = os.getenv("ANTHROPIC_MODEL", "vertex_ai/gemini-3.7-flash")
BASE_URL = os.environ["ANTHROPIC_BASE_URL"]
TOKEN = os.getenv("ANTHROPIC_AUTH_TOKEN")
NAME = os.environ.get("PAGE_TURNER_MODEL") or "gpt-4o-mini"
PORT = int(os.environ.get("PORT", 8080))
REGION = os.getenv("REGION", "us")
KEY = os.getenv("OPENAI_API_KEY", "")
ORG = os.environ.get("OPENAI_ORG") or None
LANGUAGE = os.getenv("LANGUAGE_CODE", "") or "en"


def add(item_id):
    return item_id
"""


def _source(root: Path, **files: str) -> Path:
    source = root / "source"
    source.mkdir()
    (source / "agent.py").write_text(_AGENT, encoding="utf-8")
    for name, text in files.items():
        (source / f"{name}.py").write_text(text, encoding="utf-8")
    return source


def _saved(source: Path, *names: str) -> list[str]:
    checked = _checked_contract(
        {
            "agent": "support",
            "modality": "chat",
            "real_use_cases": ["order"],
            "tools": [{"name": "add", "args": ["item_id"]}],
            "runtime": {"language": "python", "required_environment": {"agent": list(names)}},
        },
        source_root=source,
        require_setup_contract=False,
        subfolder=None,
    )
    assert isinstance(checked, AgentContract), checked
    return checked.runtime.required_environment.get("agent", [])


def test_a_variable_the_code_reads_with_a_default_is_not_required(tmp_path) -> None:
    # "" and None are how code says unset, so a key read with one is still needed.
    source = _source(tmp_path)

    assert _saved(
        source,
        "ANTHROPIC_MODEL",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "PAGE_TURNER_MODEL",
        "PORT",
        "OPENAI_API_KEY",
        "OPENAI_ORG",
        "LANGUAGE_CODE",
    ) == ["ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "OPENAI_ORG"]


def test_a_variable_also_read_without_a_default_stays_required(tmp_path) -> None:
    source = _source(tmp_path, settings='import os\nREGION = os.environ["REGION"]\n')

    assert _saved(source, "REGION") == ["REGION"]


def test_a_variable_that_falls_back_to_another_variable_stays_required(tmp_path) -> None:
    # One of two names is needed: that is not a default, and the save doesn't decide which
    # (the "one of these" case is a design question of its own).
    source = _source(
        tmp_path,
        tracing='import os\nURL = os.environ.get("TRACES_URL") or os.environ.get("OTLP_URL")\n',
    )

    assert _saved(source, "TRACES_URL") == ["TRACES_URL"]


def test_a_variable_the_code_never_reads_is_left_as_the_reading_said(tmp_path) -> None:
    # Named by a compose file, a README or a library: nothing here says it has a default.
    source = _source(tmp_path)

    assert _saved(source, "LIVEKIT_URL") == ["LIVEKIT_URL"]


def test_a_variable_named_in_a_file_that_does_not_parse_stays_required(tmp_path) -> None:
    # What that file does with it can't be read, so its default here proves nothing (ADR-011).
    source = _source(tmp_path, broken='import os\nREGION = os.environ["REGION"\n')

    assert _saved(source, "REGION") == ["REGION"]


def test_a_process_left_with_no_required_variable_keeps_an_empty_list(tmp_path) -> None:
    source = _source(tmp_path)

    assert _saved(source, "ANTHROPIC_MODEL") == []


# --- what the schema tells the reading -------------------------------------------------------


@pytest.fixture
def schema(tmp_path) -> dict:
    server = contract_tools(tmp_path)
    return next(one for one in server.tools if one.name == "submit_contract").input_schema


def test_the_schema_says_a_variable_with_a_default_in_code_is_not_required(schema) -> None:
    said = schema["properties"]["runtime"]["properties"]["required_environment"]["description"]

    assert "a default in the code is not required" in said


def test_the_schema_says_to_record_the_default_model_the_code_names(schema) -> None:
    said = schema["properties"]["model"]["description"]

    assert "the environment can override" in said
    assert "record the default the source writes" in said
    assert "only set at run time" not in said
