"""Offline tests for the harness. No model calls, no network, no credentials.

Every case here encodes something that must stay true for a generated environment to be
trustworthy: the contract cannot be structurally wrong, an unsupported agent source refuses
rather than half-works, and the submit gate returns its problems instead of writing a bad file.
"""

from __future__ import annotations

import json

import pytest

from fi.alk.harness import (
    AgentContract,
    RepoSource,
    SpecSource,
    ToolSpec,
    artifact_dir,
    load_skill,
    provider_env,
    register_source,
    resolve,
    supported,
    validate_contract,
)
from fi.alk.harness.cli import build_parser
from fi.alk.harness.session import ARTIFACT, DONE, TEXT, TOOL, Event
from fi.alk.harness.tools import accept_contract, qualified
from fi.alk.harness.understand import load, opening


def _contract(**overrides) -> AgentContract:
    payload = {
        "agent": "drive_thru",
        "tools": [ToolSpec(name="order", args=["item_id"])],
        "real_use_cases": ["order an item"],
    }
    payload.update(overrides)
    return AgentContract(**payload)


# --- contract ------------------------------------------------------------------------


def test_valid_contract_has_no_problems():
    assert validate_contract(_contract()) == []


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"agent": " "}, "empty:agent"),
        ({"tools": []}, "no-tools"),
        ({"real_use_cases": []}, "no-use-cases"),
    ],
)
def test_validate_contract_catches_structural_problems(overrides, expected):
    assert expected in validate_contract(_contract(**overrides))


def test_duplicate_tool_names_are_rejected_and_named():
    """Names the offender: tool_names() is a set, so a naive length comparison never fires."""
    contract = _contract(tools=[ToolSpec(name="order"), ToolSpec(name="order")])
    assert "duplicate-tool-names:order" in validate_contract(contract)


def test_types_declared_for_arguments_that_do_not_exist_are_rejected():
    """A type on an argument the tool does not take means the reader misread the signature,
    and a world built from it would be wrong in a way nothing downstream could detect."""
    contract = _contract(
        tools=[ToolSpec(name="order", args=["item_id"], arg_types={"size": "str"})]
    )
    assert "tool[order]:types-for-unknown-args:size" in validate_contract(contract)


def test_brief_carries_argument_types_into_downstream_prompts():
    contract = _contract(
        tools=[
            ToolSpec(
                name="remove_order_item",
                args=["order_id"],
                arg_types={"order_id": "list[str]"},
            )
        ]
    )
    assert "remove_order_item(order_id: list[str])" in contract.brief()


def test_shapes_are_normalised_rather_than_rejected():
    """Benign shape variance is not a grounding error; rejecting it burns turns for nothing."""
    contract = AgentContract.model_validate(
        {
            "agent": "x",
            "one_liner": ["a", "b"],
            "hard_constraints": "only one rule",
            "data_schema": [1, 2],
        }
    )
    assert contract.one_liner == "a\nb"
    assert contract.hard_constraints == ["only one rule"]
    assert contract.data_schema == {"value": [1, 2]}


# --- sources -------------------------------------------------------------------------


def test_repo_and_spec_sources_are_registered():
    assert {"repo", "spec"}.issubset(set(supported()))


def test_unsupported_source_refuses_and_names_what_exists():
    with pytest.raises(NotImplementedError) as raised:
        resolve("browser", name="x")
    assert "repo" in str(raised.value)


def test_repo_source_gets_read_tools_and_a_briefing_that_points_at_the_code(tmp_path):
    source = RepoSource(name="a", root=tmp_path)
    assert source.builtin_tools() == ("Read", "Glob", "Grep")
    assert str(tmp_path) in source.briefing()


def test_spec_source_gets_no_file_tools_because_there_is_nothing_to_read():
    source = SpecSource(
        name="a", system_prompt="you are a bot", tool_schema=[{"name": "t"}]
    )
    assert source.builtin_tools() == ()
    briefing = source.briefing()
    assert "you are a bot" in briefing and "t" in briefing


def test_a_new_kind_of_agent_is_a_registration_not_a_code_change():
    register_source("fake", lambda **kw: RepoSource(name=kw["name"], root="."))
    assert resolve("fake", name="z").name == "z"


# --- session events ------------------------------------------------------------------


@pytest.mark.parametrize(
    "event,expected",
    [
        (Event(TEXT, text="hello"), "hello"),
        (Event(TOOL, tool="Read", detail={"target": "agent.py"}), "  [Read agent.py]"),
        (Event(TOOL, tool="Grep"), "  [Grep]"),
        (
            Event(ARTIFACT, detail={"path": "a/contract.json"}),
            "  [saved a/contract.json]",
        ),
    ],
)
def test_events_render_for_a_terminal(event, expected):
    assert event.line() == expected


def test_done_event_reports_outcome_turns_and_spend():
    line = Event(
        DONE, detail={"outcome": "success", "turns": 9, "cost_usd": 0.36}
    ).line()
    assert "success" in line and "turns=9" in line and "0.36" in line


# --- the submit gate -----------------------------------------------------------------


def test_submit_writes_the_contract_when_it_is_valid(tmp_path):
    result = accept_contract(
        {
            "agent": "drive_thru",
            "tools": [{"name": "order", "args": ["item_id"]}],
            "real_use_cases": ["order an item"],
        },
        tmp_path,
    )
    assert not result.get("is_error")
    written = json.loads((tmp_path / "contract.json").read_text())
    assert written["agent"] == "drive_thru"


def test_submit_returns_problems_and_writes_nothing_when_invalid(tmp_path):
    """The gate reports into the conversation so the next turn can fix it, which is the only
    reason a bad contract does not reach disk."""
    result = accept_contract(
        {"agent": "drive_thru", "tools": [], "real_use_cases": []}, tmp_path
    )
    assert result.get("is_error")
    text = result["content"][0]["text"]
    assert "no-tools" in text and "no-use-cases" in text
    assert not (tmp_path / "contract.json").exists()


def test_load_returns_none_when_the_stage_produced_nothing(tmp_path):
    assert load(tmp_path) is None


# --- the world gate ------------------------------------------------------------------


def _cart_world():
    from fi.alk.harness.world import GeneratedWorld

    class W(GeneratedWorld):
        name = "cart"
        tools = [{"name": "add"}, {"name": "lst"}]
        handlers = {
            "add": (
                "def handle(args, db):\n"
                "    if 'item_id' not in args: raise ToolError('item_id is required')\n"
                "    m = db.one('SELECT * FROM menu WHERE id=?', [args['item_id']])\n"
                "    if not m: raise ToolError('no item %r' % args['item_id'])\n"
                "    db.execute('INSERT INTO cart (item_id) VALUES (?)', [args['item_id']])\n"
                "    return {'ok': 1}\n"
            ),
            "lst": "def handle(args, db):\n    return db.query('SELECT * FROM cart')\n",
        }

    world = W(":memory:")
    world.connection.executescript(
        "CREATE TABLE menu(id TEXT PRIMARY KEY); CREATE TABLE cart(item_id TEXT);"
    )
    world.connection.execute("INSERT INTO menu VALUES ('big_mac')")
    world.connection.commit()
    contract = AgentContract(
        agent="cart",
        real_use_cases=["add an item"],
        tools=[
            ToolSpec(name="add", args=["item_id"], arg_values={"item_id": ["big_mac"]}),
            ToolSpec(name="lst"),
        ],
    )
    return world, contract


_SEQUENCE = [
    {
        "name": "add-then-list",
        "calls": [
            {"tool": "add", "arguments": {"item_id": "big_mac"}},
            {"tool": "lst", "arguments": {}},
        ],
        "expect_state": {"cart.count": 1},
    }
]


def test_a_sound_world_passes_every_probe():
    from fi.alk.harness.world import probe

    world, contract = _cart_world()
    report = probe(world, contract, sequences=_SEQUENCE)
    assert report.score == 1.0, report.summary()


def test_probing_leaves_the_world_exactly_as_it_found_it():
    """Probes mutate. Without reverting between them, each inherits the last one's debris and
    a sequence expecting one row finds several, which reads as a bug in the world."""
    from fi.alk.harness.world import probe

    world, contract = _cart_world()
    probe(world, contract, sequences=_SEQUENCE)
    assert world.state()["cart"] == []


def test_probing_is_repeatable():
    from fi.alk.harness.world import probe

    world, contract = _cart_world()
    first = probe(world, contract, sequences=_SEQUENCE).score
    second = probe(world, contract, sequences=_SEQUENCE).score
    assert first == second == 1.0


def test_a_tool_that_succeeds_on_a_nonexistent_id_fails_the_gate():
    """The defect the whole thing exists to catch: a call that should have been refused."""
    from fi.alk.harness.world import probe

    world, contract = _cart_world()
    world.handlers["add"] = (
        "def handle(args, db):\n"
        "    db.execute('INSERT INTO cart (item_id) VALUES (?)', [args.get('item_id')])\n"
        "    return {'ok': 1}\n"
    )
    report = probe(world, contract, sequences=_SEQUENCE)
    assert any("does not exist" in failure.detail for failure in report.failures), (
        report.summary()
    )
    assert report.score < 0.85


def test_a_crash_is_distinguished_from_a_refusal():
    from fi.alk.harness.world import probe

    world, contract = _cart_world()
    world.handlers["add"] = (
        "def handle(args, db):\n    return {'id': args['item_id']}\n"
    )
    report = probe(world, contract, sequences=_SEQUENCE)
    assert any("crashed instead of refusing" in f.detail for f in report.failures)


def test_a_world_reverts_to_a_checkpoint():
    world, _ = _cart_world()
    mark = world.checkpoint()
    world.call("add", {"item_id": "big_mac"})
    assert len(world.state()["cart"]) == 1
    world.revert(mark)
    assert world.state()["cart"] == []


# --- wiring --------------------------------------------------------------------------


def test_provider_env_pins_the_model_and_never_invents_a_project(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    env = provider_env("claude-sonnet-4-6")
    assert env["CLAUDE_CODE_USE_VERTEX"] == "1"
    assert env["ANTHROPIC_MODEL"] == "claude-sonnet-4-6"
    assert "ANTHROPIC_VERTEX_PROJECT_ID" not in env


def test_qualified_tool_name_matches_the_mcp_convention():
    assert qualified("contract", "submit_contract") == "mcp__contract__submit_contract"


def test_the_skill_exists_and_forbids_guessing():
    text = load_skill("understand-agent")
    assert "submit_contract" in text
    assert "guess" in text.lower()


def test_artifacts_land_under_the_agent_name():
    assert artifact_dir("drive_thru").as_posix().endswith("environments/drive_thru")


def test_cli_defaults_to_staying_open_for_corrections():
    args = build_parser().parse_args(["understand", "--name", "a", "--path", "."])
    assert args.interactive is True
    assert (
        build_parser()
        .parse_args(["understand", "--name", "a", "--path", ".", "--once"])
        .interactive
        is False
    )


def test_opening_names_the_agent_and_asks_for_the_contract(tmp_path):
    text = opening(RepoSource(name="drive_thru", root=tmp_path))
    assert "drive_thru" in text and "submit_contract" in text
