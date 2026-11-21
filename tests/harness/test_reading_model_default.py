"""What the reading is told about a model whose name the environment can override (TH-10401).

The 18 Nov reading of page-turner-langgraph-otel saved no model: the description said to leave
the model out "when the name is only set at run time", and the agent reads
``os.environ.get("PAGE_TURNER_MODEL") or ("claude-sonnet-5-5" if ... else "gpt-4o-mini")``.
The description now asks for the default in effect when the agent starts, as written.
"""

from __future__ import annotations

import pytest

from fi.alk.harness.tools import contract_tools


@pytest.fixture
def schema(tmp_path) -> dict:
    server = contract_tools(tmp_path)
    return next(one for one in server.tools if one.name == "submit_contract").input_schema


def test_the_schema_asks_for_the_default_model_in_effect_at_start(schema) -> None:
    said = schema["properties"]["model"]["description"]

    assert "the environment can" in said and "override the name" in said
    assert "record the default in effect when the agent starts" in said
    # Not a default reassigned later or sitting in code that never runs (Hawk, L1).
    assert "not one reassigned later or in unused code" in said
    assert "only set at run time" not in said


def test_the_schema_never_asks_to_leave_out_a_required_name_with_a_default(schema) -> None:
    # ADR-016: a required name the reading finds stays listed; that the code gives it a
    # default is marked beside it later, never by leaving it out.
    said = schema["properties"]["runtime"]["properties"]["required_environment"]["description"]

    assert said == (
        "Required environment names by selected process name. Native control process is "
        "agent. No values."
    )
