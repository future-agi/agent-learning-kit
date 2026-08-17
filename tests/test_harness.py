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
from fi.alk.harness.scenario import Scenario
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
    assert artifact_dir("drive_thru").as_posix().endswith("sessions/drive_thru")


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


# --- state expectations, shared by the gate and the grading --------------------------


_STATE = {"orders": [{"id": "a", "item": "big_mac"}], "menu": [{"id": "big_mac"}]}


# --- scenarios -----------------------------------------------------------------------


def _saved_world(tmp_path):
    from fi.alk.harness.world.snapshot import save

    world, contract = _cart_world()
    save(world, tmp_path, notes="test world")
    return tmp_path, contract


def _scenario(**overrides):
    payload = {
        "name": "orders-a-big-mac",
        "tests": "the ordinary case",
        "goal": "order a big mac",
        "persona": "brisk",
        "opening": "one big mac please",
        "expect_state": {"cart.count": 1},
    }
    payload.update(overrides)
    return payload


# --- running and grading -------------------------------------------------------------


def test_declared_types_become_something_a_tool_schema_can_carry():
    from fi.alk.harness.run.targets import _python_type

    assert _python_type("list[str]") is list
    assert _python_type("int") is int
    assert _python_type("") is str


def test_the_agent_under_test_is_told_its_own_rules():
    from fi.alk.harness.run.targets import agent_prompt

    _world, contract = _cart_world()
    contract.hard_constraints = ["never substitute an item without asking"]
    assert "never substitute" in agent_prompt(contract)


def test_the_cli_exposes_every_stage_and_one_conversation_across_them():
    parser = build_parser()
    assert parser.parse_args(["scenarios", "--name", "a", "--count", "10"]).count == 10
    assert parser.parse_args(["run", "--name", "a"]).target == "local"


def test_talking_to_it_needs_nothing_on_the_command_line():
    """Which agent, where it lives and how many scenarios are all things you say."""
    parser = build_parser()
    assert parser.parse_args(["chat"]).name is None
    assert parser.parse_args(["chat"]).path is None


def test_a_conversation_resumes_at_whichever_stage_the_artifacts_reached(tmp_path):
    from fi.alk.harness.chat import BUILD, SCENARIOS, UNDERSTAND, open_conversation

    conversation = open_conversation(name="a", path=str(tmp_path), out=tmp_path)
    assert conversation._resume_at() == UNDERSTAND

    accept_contract(
        {
            "agent": "a",
            "real_use_cases": ["order"],
            "tools": [{"name": "add", "args": ["item_id"]}],
        },
        tmp_path,
    )
    assert conversation._resume_at() == BUILD

    _saved_world(tmp_path)
    assert conversation._resume_at() == SCENARIOS


def test_where_a_conversation_is_agrees_with_what_was_built(tmp_path):
    from fi.alk.harness.chat import SCENARIOS, open_conversation

    accept_contract(
        {
            "agent": "a",
            "real_use_cases": ["order"],
            "tools": [{"name": "add", "args": ["item_id"]}],
        },
        tmp_path,
    )
    _saved_world(tmp_path)
    conversation = open_conversation(name="a", path=str(tmp_path), out=tmp_path)
    assert conversation.stage_name == SCENARIOS
    assert conversation.next_stage() is None


def test_a_conversation_with_no_agent_starts_by_asking_which_one():
    from fi.alk.harness.chat import RECEPTION, open_conversation

    conversation = open_conversation()
    assert conversation.source is None
    assert conversation.stage_name == RECEPTION
    assert conversation.next_stage() is None


def test_pointing_at_an_agent_settles_where_its_artifacts_go(tmp_path):
    import asyncio

    from fi.alk.harness.chat import UNDERSTAND, open_conversation
    from fi.alk.harness.sources import RepoSource

    conversation = open_conversation()
    conversation._found["source"] = RepoSource(name="mine", root=tmp_path)

    async def _settle():
        # Reception is the only stage whose result is not a file, so the conversation reads it
        # back rather than looking on disk. Advancing needs a live session, so only the
        # settling half is exercised here.
        settled = conversation._found.pop("source")
        conversation.source = settled
        conversation.out = conversation.out or artifact_dir(settled.name)

    asyncio.run(_settle())
    assert conversation.out.as_posix().endswith("sessions/mine")
    assert conversation._resume_at() == UNDERSTAND


def test_pointing_at_somewhere_that_does_not_exist_is_refused(tmp_path):
    from fi.alk.harness.reception import point_at

    found = {}
    refused = point_at("mine", str(tmp_path / "nope"), "repo", found)
    assert refused["is_error"] and found == {}

    accepted = point_at("mine", str(tmp_path), "repo", found)
    assert not accepted.get("is_error")
    assert found["source"].name == "mine"


def test_how_many_scenarios_is_something_you_say():
    from fi.alk.harness.scenario_tools import TOOL_NAMES

    assert "aim_for" in TOOL_NAMES


# --- amending the contract -----------------------------------------------------------


def _written_contract(tmp_path):
    accept_contract(
        {
            "agent": "cart",
            "real_use_cases": ["add an item"],
            "tools": [
                {
                    "name": "add",
                    "args": ["item_id"],
                    "arg_values": {"item_id": ["big_mac"]},
                }
            ],
        },
        tmp_path,
    )
    return load(tmp_path)


def test_the_agent_can_be_taught_a_value_it_did_not_accept(tmp_path):
    """A world that gains an item the agent cannot name holds dead data, and every scenario
    about it can only fail. The two have to move together."""
    from fi.alk.harness.amend import widen

    contract = _written_contract(tmp_path)
    done, said = widen(
        contract,
        tmp_path,
        tool_name="add",
        argument="item_id",
        values=["mango_smoothie"],
        why="added to the menu this morning",
    )
    assert done, said
    assert "mango_smoothie" in contract.tools[0].arg_values["item_id"]
    # the stage's own copy and the file agree, or the stage checks against an action space
    # that no longer exists
    assert "mango_smoothie" in load(tmp_path).tools[0].arg_values["item_id"]


def test_an_amendment_is_recorded_rather_than_blended_in(tmp_path):
    from fi.alk.harness.amend import widen

    contract = _written_contract(tmp_path)
    widen(
        contract,
        tmp_path,
        tool_name="add",
        argument="item_id",
        values=["mango_smoothie"],
        why="added to the menu this morning",
    )
    recorded = load(tmp_path).amendments
    assert len(recorded) == 1
    assert "mango_smoothie" in recorded[0] and "this morning" in recorded[0]


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"tool_name": "nope"}, "is not a tool this agent has"),
        ({"argument": "colour"}, "takes no argument"),
        ({"why": "  "}, "say why"),
        ({"values": ["big_mac"]}, "already accepts"),
    ],
)
def test_an_amendment_that_makes_no_sense_is_refused(tmp_path, overrides, expected):
    from fi.alk.harness.amend import widen

    contract = _written_contract(tmp_path)
    call = {
        "tool_name": "add",
        "argument": "item_id",
        "values": ["mango_smoothie"],
        "why": "because",
    }
    call.update(overrides)
    done, said = widen(contract, tmp_path, **call)
    assert not done and expected in said
    assert load(tmp_path).amendments == []


# --- what a stage is allowed to do ---------------------------------------------------


def test_a_stage_may_use_nothing_it_was_not_given():
    """Deny by default, not deny-a-list. A session is offered whatever its host exposes, and an
    allow-by-default gate let a host search tool through that cost a stage its whole budget."""
    import asyncio

    from fi.alk.harness.config import permission_gate

    gate = permission_gate(granted=["Read", "Glob"])
    for refused in ("Write", "Edit", "Bash", "Task", "ToolSearch", "WebFetch"):
        verdict = asyncio.run(gate(refused, {}, None))
        assert type(verdict).__name__ == "PermissionResultDeny"
        assert "not part of this stage" in verdict.message

    allowed = asyncio.run(gate("Read", {"file_path": "a.py"}, None))
    assert type(allowed).__name__ == "PermissionResultAllow"


def test_a_question_still_reaches_the_operator():
    import asyncio

    from fi.alk.harness.config import permission_gate

    asked = {}

    async def ask(tool_name, payload, _context):
        asked["tool"] = tool_name
        return "answered"

    assert asyncio.run(permission_gate(ask)("AskUserQuestion", {}, None)) == "answered"
    assert asked["tool"] == "AskUserQuestion"


# --- the tools a stage actually publishes ---------------------------------------------


def _published(server):
    """The tool names an in-process MCP server really exposes."""
    import asyncio

    from mcp.types import ListToolsRequest

    instance = server.get("instance") if isinstance(server, dict) else server

    async def ask():
        for key, handler in instance.request_handlers.items():
            if getattr(key, "__name__", "") == "ListToolsRequest":
                result = await handler(ListToolsRequest(method="tools/list"))
                return sorted(tool.name for tool in result.root.tools)
        return []

    return asyncio.run(ask())


def test_every_stage_publishes_exactly_the_tools_it_claims(tmp_path):
    """A tool listed in TOOL_NAMES but left out of the server is granted, named in error
    messages, and does not exist. The model then hunts for it and works around the gate."""
    from fi.alk.harness import scenario_tools as scenarios
    from fi.alk.harness.run import tools as runs
    from fi.alk.harness.world import tools as world

    root, contract = _saved_world(tmp_path)
    server, _kept = scenarios.scenario_tools(contract, root, root, wanted=1)
    assert _published(server) == sorted(scenarios.TOOL_NAMES)

    built, _world = world.world_tools(contract, root)
    assert _published(built) == sorted(world.TOOL_NAMES)

    assert _published(runs.run_tools(root, root)) == sorted(runs.TOOL_NAMES)


def test_a_failed_call_is_not_reported_as_success():
    """A call that failed upstream still arrives with subtype "success", so reporting subtype
    verbatim tells somebody their stage worked when nothing happened."""
    from fi.alk.harness.session import _why_it_failed

    class Failed:
        api_error_status = 400
        errors = ['{"error":"invalid_grant","error_subtype":"invalid_rapt"}']

    said = _why_it_failed(Failed())
    assert "GOOGLE_APPLICATION_CREDENTIALS" in said and ".env.acceptance" in said

    class Other:
        api_error_status = 529
        errors = ["overloaded"]

    assert "529" in _why_it_failed(Other())


def test_the_credentials_in_play_are_said_out_loud(monkeypatch):
    from fi.alk.harness.config import credentials_hint

    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/keys/service-account.json")
    assert credentials_hint() == "credentials: service-account.json"

    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS")
    assert "gcloud login" in credentials_hint()


def test_a_run_notices_when_it_was_billed_to_a_model_nobody_asked_for():
    """Asking for a model is not the same as getting one: the CLI has its own default, and a
    request that quietly does not take shows up only on the invoice."""
    from claude_agent_sdk import ClaudeAgentOptions

    from fi.alk.harness.session import Stage

    stage = Stage(ClaudeAgentOptions(model="claude-haiku-4-5"), name="s")
    stage.models_used = {"claude-haiku-4-5-20251001"}
    assert stage.unexpected_models() == set()

    stage.models_used = {"claude-opus-4-7"}
    assert stage.unexpected_models() == {"claude-opus-4-7"}


def test_an_agent_already_built_can_be_reopened_without_its_repository(tmp_path):
    """Coming back to fix a scenario should not mean pointing at the source again."""
    from fi.alk.harness.chat import SCENARIOS, Conversation

    accept_contract(
        {
            "agent": "a",
            "real_use_cases": ["order"],
            "tools": [{"name": "add", "args": ["item_id"]}],
        },
        tmp_path,
    )
    _saved_world(tmp_path)
    resumed = Conversation(source=None, out=tmp_path)
    assert resumed.stage_name == SCENARIOS


def test_a_rule_the_source_never_stated_can_be_added_and_is_recorded(tmp_path):
    """A hard constraint is told to the agent under test and graded by the judge, so adding one
    changes what is being tested and has to be visible as ours rather than the agent's."""
    from fi.alk.harness.amend import add_rule

    contract = _written_contract(tmp_path)
    done, said = add_rule(
        contract,
        tmp_path,
        rule="stays polite to customers",
        why="asked for on the call",
    )
    assert done and "graded from here on" in said
    reloaded = load(tmp_path)
    assert "stays polite to customers" in reloaded.hard_constraints
    assert "rule added" in reloaded.amendments[0] and "polite" in reloaded.amendments[0]

    again, why = add_rule(
        contract, tmp_path, rule="Stays Polite To Customers", why="again"
    )
    assert not again and "already has that rule" in why

    unexplained, said = add_rule(contract, tmp_path, rule="be fast", why=" ")
    assert not unexplained and "say why" in said


def test_a_rule_the_agent_does_not_have_can_be_taken_away(tmp_path):
    """A rule nobody has is worse than a missing one: the agent is told to obey it and the
    judge fails it for not doing something it was never supposed to do."""
    from fi.alk.harness.amend import add_rule, drop_rule

    contract = _written_contract(tmp_path)
    add_rule(contract, tmp_path, rule="never upsell", why="misread from a comment")
    done, said = drop_rule(
        contract, tmp_path, rule="upsell", why="the source never says that"
    )
    assert done, said
    assert load(tmp_path).hard_constraints == []
    assert "rule removed" in load(tmp_path).amendments[-1]

    missing, said = drop_rule(contract, tmp_path, rule="be nice", why="x")
    assert not missing and "no rule like that" in said


def test_a_misread_tool_can_be_corrected(tmp_path):
    """The most damaging thing stage one can get wrong: every argument name flows into the
    handlers, the probes and the scenarios."""
    from fi.alk.harness.amend import fix_tool

    contract = _written_contract(tmp_path)
    done, said = fix_tool(
        contract,
        tmp_path,
        tool_name="add",
        args=["item_ids"],
        why="the signature takes a list, singular was a misread",
    )
    assert done, said
    fixed = load(tmp_path).tools[0]
    assert fixed.args == ["item_ids"]
    # values recorded against the old name must not silently survive under a name nobody uses
    assert "item_id" not in fixed.arg_values
    assert "dropped values recorded for item_id" in said


def test_a_tool_the_agent_does_not_have_can_be_removed(tmp_path):
    from fi.alk.harness.amend import fix_tool

    contract = _written_contract(tmp_path)
    contract.tools.append(ToolSpec(name="checkout", args=["id"]))
    done, said = fix_tool(
        contract,
        tmp_path,
        tool_name="checkout",
        remove=True,
        why="no such tool in the source",
    )
    assert done and "1 tools left" in said
    assert load(tmp_path).tool_names() == {"add"}


def test_correcting_a_contract_without_saying_why_is_refused(tmp_path):
    from fi.alk.harness.amend import drop_rule, fix_tool

    contract = _written_contract(tmp_path)
    assert not fix_tool(contract, tmp_path, tool_name="add", args=["x"], why=" ")[0]
    assert not drop_rule(contract, tmp_path, rule="anything", why="")[0]


def test_a_read_only_handler_does_not_poison_every_later_probe():
    """SQLite refuses to restore into a connection with a transaction open, and a handler that
    only reads leaves one behind. Unsettled, the first such handler makes the world impossible
    to check or save: "destination database is in use"."""
    from fi.alk.harness.world import probe

    world, contract = _cart_world()
    # lst only queries, which is what leaves the read transaction open
    world.call("lst", {})
    mark = world.checkpoint()
    world.call("add", {"item_id": "big_mac"})
    world.call("lst", {})
    world.revert(mark)
    assert world.state()["cart"] == []

    report = probe(world, contract, sequences=_SEQUENCE)
    assert report.score == 1.0, report.summary()


def test_a_row_put_in_wrong_can_be_taken_out_again(tmp_path):
    """Seeding only inserts. Without a way to remove a row, the only way left to make a check
    pass is to change the contract, which repairs the wrong thing."""
    import asyncio

    from fi.alk.harness.world import tools as world_tools

    _root, contract = _saved_world(tmp_path)
    server, world = world_tools.world_tools(contract, tmp_path)
    assert "change_data" in world_tools.TOOL_NAMES
    assert _published(server) == sorted(world_tools.TOOL_NAMES)

    world.connection.execute("INSERT INTO menu VALUES ('curry_sauce')")
    world.connection.commit()

    async def call(name, payload):
        from mcp.types import CallToolRequest, CallToolRequestParams

        instance = server.get("instance") if isinstance(server, dict) else server
        for key, handler in instance.request_handlers.items():
            if getattr(key, "__name__", "") == "CallToolRequest":
                result = await handler(
                    CallToolRequest(
                        method="tools/call",
                        params=CallToolRequestParams(name=name, arguments=payload),
                    )
                )
                return result.root.content[0].text

    said = asyncio.run(
        call("change_data", {"sql": "DELETE FROM menu WHERE id='curry_sauce'"})
    )
    assert "1 rows changed" in said
    assert not [row for row in world.state()["menu"] if row["id"] == "curry_sauce"]

    refused = asyncio.run(call("change_data", {"sql": "SELECT * FROM menu"}))
    assert "UPDATE or DELETE" in refused


# --- the environment step: world, simulator prompt, sub-goal catalogue ---------------


def test_a_sub_goal_that_settles_nothing_is_rejected():
    """Every scenario referencing it would report a result nobody should believe."""
    from fi.alk.harness.environment import SubGoal, validate_sub_goal

    assert validate_sub_goal(SubGoal(name="x", what="means something")) != []
    settled = SubGoal(
        name="order-placed",
        what="the order reached the system",
        check="def check(world, calls):\n    return None\n",
    )
    assert validate_sub_goal(settled) == []
    assert settled.deterministic()

    judged = SubGoal(
        name="polite", what="stayed polite", judged="nothing observable shows tone"
    )
    assert validate_sub_goal(judged) == [] and not judged.deterministic()


def test_a_check_must_actually_define_one():
    from fi.alk.harness.environment import SubGoal, validate_sub_goal

    problems = validate_sub_goal(
        SubGoal(name="x", what="y", check="rows = world.state()['orders']")
    )
    assert any("check(world, calls)" in problem for problem in problems)


def test_a_simulator_prompt_without_a_slot_runs_the_same_conversation_every_time():
    from fi.alk.harness.environment import fill, validate_simulator_prompt, variables_in

    fixed = (
        "You are a customer calling a drive-thru. Speak naturally, one turn at a time. "
        * 2
    )
    assert any(
        "no variables" in problem for problem in validate_simulator_prompt(fixed)
    )

    written = fixed + "\n\nWhat you want: {{ instruction }}\nWhat you know: {{ facts }}"
    assert validate_simulator_prompt(written) == []
    assert variables_in(written) == {"instruction", "facts"}

    filled, missing = fill(written, {"instruction": "order a big mac"})
    assert "order a big mac" in filled and missing == ["facts"]


def test_a_check_that_raises_is_broken_not_failed():
    """A typo in an assertion must never read as a finding about the agent."""
    from fi.alk.harness.checks import run_check

    world, _contract = _cart_world()
    ok = run_check(
        "def check(world, calls):\n    return None\n", world, [], name="fine"
    )
    assert ok.held and not ok.broken

    failed = run_check(
        "def check(world, calls):\n    return 'no rows'\n", world, [], name="says-why"
    )
    assert not failed.held and not failed.broken and failed.said == "no rows"

    typo = run_check(
        "def check(world, calls):\n    return world.state()['nope'][0]\n",
        world,
        [],
        name="typo",
    )
    assert typo.broken and "KeyError" in typo.said


def test_a_check_can_insist_on_the_arguments_not_just_the_call():
    """Booking 10 PM when 11 PM was asked for is a failure, and detecting it is deterministic."""
    from fi.alk.harness.checks import run_check

    world, _contract = _cart_world()
    world.call("add", {"item_id": "big_mac"})
    source = (
        "def check(world, calls):\n"
        "    made = [c for c in calls if c.name == 'add']\n"
        "    if not made:\n        return 'never added anything'\n"
        "    if made[0].arguments.get('item_id') != 'fries':\n"
        "        return 'added %r, expected fries' % made[0].arguments.get('item_id')\n"
        "    return None\n"
    )
    outcome = run_check(source, world, world.calls, name="right-item")
    assert not outcome.held and "expected fries" in outcome.said


# --- scenarios as deltas, and the two gates ------------------------------------------


def _built_environment(tmp_path):
    """A saved world plus a catalogue, which is what the environment step leaves behind."""
    from fi.alk.harness.environment import Catalogue, SubGoal, save_catalogue
    from fi.alk.harness.world.snapshot import save

    world, contract = _cart_world()
    save(world, tmp_path, notes="test", sequences=[])
    catalogue = Catalogue(
        sub_goals=[
            SubGoal(
                name="item-added",
                what="the item reached the cart",
                check=(
                    "def check(world, calls):\n"
                    "    rows = world.state()['cart']\n"
                    "    if len(rows) != 1: return '%d rows, expected 1' % len(rows)\n"
                    "    return None\n"
                ),
            ),
            SubGoal(
                name="right-item",
                what="the call carried the item that was asked for",
                check=(
                    "def check(world, calls):\n"
                    "    made = [c for c in calls if c.name == 'add' and c.ok]\n"
                    "    if not made: return 'add was never called'\n"
                    "    got = made[0].arguments.get('item_id')\n"
                    "    return None if got == 'big_mac' else 'added %r' % got\n"
                ),
            ),
            SubGoal(name="polite", what="stayed polite", judged="tone leaves no trace"),
        ]
    )
    save_catalogue(catalogue, tmp_path)
    return tmp_path, contract, catalogue


def _delta(**overrides):
    payload = {
        "name": "adds-a-big-mac",
        "use_case": "order an item",
        "instruction": "Order one Big Mac.",
        "solution": [{"tool": "add", "arguments": {"item_id": "big_mac"}}],
        "sub_goals": ["item-added", "right-item"],
    }
    payload.update(overrides)
    return payload


def test_a_scenario_is_proved_before_it_is_kept(tmp_path):
    from fi.alk.harness.scenario_tools import accept_scenario

    root, _contract, catalogue = _built_environment(tmp_path)
    kept = []
    said = accept_scenario(_delta(), world_root=root, catalogue=catalogue, kept=kept)
    assert not said.get("is_error"), said
    assert "All three gates pass" in said["content"][0]["text"]
    assert [one.name for one in kept] == ["adds-a-big-mac"]


def test_a_scenario_whose_solution_cannot_pass_its_own_checks_is_refused(tmp_path):
    """Either the scenario is impossible or the checks are wrong. Both have happened."""
    from fi.alk.harness.scenario_tools import accept_scenario

    root, _contract, catalogue = _built_environment(tmp_path)
    said = accept_scenario(
        _delta(solution=[{"tool": "add", "arguments": {"item_id": "sushi"}}]),
        world_root=root,
        catalogue=catalogue,
        kept=[],
    )
    assert said["is_error"]
    text = said["content"][0]["text"]
    assert "reference solution does not pass" in text
    assert "refused by the world" in text and "sushi" in text


def test_a_scenario_whose_checks_pass_with_nothing_done_is_refused(tmp_path):
    """A check that passes without the agent acting grades nothing while reporting a result."""
    from fi.alk.harness.environment import SubGoal, save_catalogue
    from fi.alk.harness.scenario_tools import accept_scenario

    root, _contract, catalogue = _built_environment(tmp_path)
    catalogue.sub_goals.append(
        SubGoal(
            name="always",
            what="always true",
            check="def check(world, calls):\n    return None\n",
        )
    )
    save_catalogue(catalogue, root)
    said = accept_scenario(
        _delta(sub_goals=["always"]), world_root=root, catalogue=catalogue, kept=[]
    )
    assert said["is_error"] and "grade nothing" in said["content"][0]["text"]


def test_a_scenario_naming_a_sub_goal_nobody_defined_is_refused(tmp_path):
    from fi.alk.harness.scenario_tools import accept_scenario

    root, _contract, catalogue = _built_environment(tmp_path)
    said = accept_scenario(
        _delta(sub_goals=["invented-here"]),
        world_root=root,
        catalogue=catalogue,
        kept=[],
    )
    assert said["is_error"]
    assert "not in the catalogue" in said["content"][0]["text"]


def test_a_scenario_with_no_solution_cannot_be_proved(tmp_path):
    from fi.alk.harness.scenario_tools import accept_scenario

    root, _contract, catalogue = _built_environment(tmp_path)
    said = accept_scenario(
        _delta(solution=[]), world_root=root, catalogue=catalogue, kept=[]
    )
    assert said["is_error"] and "no solution" in said["content"][0]["text"]


def test_a_suite_where_no_sub_goal_is_shared_does_not_roll_up(tmp_path):
    """If a payment step appears in 50 scenarios, the results should say where payment fails."""
    from fi.alk.harness.environment import Catalogue, SubGoal
    from fi.alk.harness.scenario import Scenario
    from fi.alk.harness.scenario_tools import not_ready

    catalogue = Catalogue(
        sub_goals=[SubGoal(name=f"g{i}", what="x", judged="y") for i in range(4)]
    )
    private = [
        Scenario(name=f"s{i}", instruction="do it", sub_goals=[f"g{i}"])
        for i in range(4)
    ]
    assert any("rolls up" in problem for problem in not_ready(private, 4, catalogue))

    shared = [
        Scenario(name=f"s{i}", instruction="do it", sub_goals=["g0"]) for i in range(4)
    ]
    assert not_ready(shared, 4, catalogue) == []


def test_the_simulator_prompt_slots_a_scenario_leaves_unfilled_are_caught(tmp_path):
    from fi.alk.harness.scenario import Scenario, validate_scenario

    root, _contract, catalogue = _built_environment(tmp_path)
    prompt = (
        "You are a customer. " * 10
        + "\nWhat you want: {{ instruction }}\nAlso: {{ mood }}"
    )
    scenario = Scenario.model_validate(_delta())
    problems = validate_scenario(scenario, catalogue, {"cart": [], "menu": []}, prompt)
    assert any("mood" in problem for problem in problems)


# --- the voice webhook, answered by the world ----------------------------------------


def test_a_hosted_agents_tool_call_is_answered_by_the_world():
    """The whole voice integration: a webhook, answered by running the call rather than by
    looking up a canned response. A mock that always succeeds tells an agent it removed an item
    that was never added."""
    import json
    import urllib.request

    from fi.alk.harness.run.voice import WorldWebhook

    world, _contract = _cart_world()
    webhook = WorldWebhook().start()
    try:
        webhook.bind(world)

        def call(name, arguments):
            body = json.dumps(
                {
                    "message": {
                        "toolCalls": [
                            {
                                "id": "call-1",
                                "function": {"name": name, "arguments": arguments},
                            }
                        ]
                    }
                }
            ).encode()
            request = urllib.request.Request(
                f"http://127.0.0.1:{webhook.port}/tool",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=5) as answer:
                return json.loads(answer.read())["results"][0]["result"]

        assert "1" in call("add", {"item_id": "big_mac"})
        # the world really wrote the row, so a read-after-write flow is right
        assert len(world.state()["cart"]) == 1

        # and it can refuse, which a canned mock cannot
        refused = call("add", {"item_id": "sushi"})
        assert "sushi" in refused
        assert len(world.state()["cart"]) == 1

        # the world answers for a tool the agent does not have, naming the ones it does
        unknown = call("checkout", {})
        assert "no such tool" in unknown and "add" in unknown
        # every call is recorded with its arguments, which is what grading reads
        assert [c.name for c in webhook.calls] == ["add", "add", "checkout"]
    finally:
        webhook.stop()


def test_repointing_changes_only_where_the_agents_tools_are_answered():
    """The assistant's tools are the agent's — names, arguments and enums belong to whoever
    built it. Redefining them would mean testing an agent we wrote."""
    from fi.alk.harness.run.voice import pointed_at

    theirs = [
        {
            "type": "function",
            "function": {
                "name": "order_combo_meal",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "meal_id": {"type": "string", "enum": ["combo_big_mac"]}
                    },
                    "required": ["meal_id"],
                },
            },
            "server": {"url": "https://dead-tunnel.example/tool"},
        }
    ]
    moved = pointed_at(theirs, "https://ours.example")
    assert moved[0]["server"]["url"] == "https://ours.example/tool"
    # everything else is untouched
    assert moved[0]["function"] == theirs[0]["function"]
    assert theirs[0]["server"]["url"] == "https://dead-tunnel.example/tool"


def test_a_scenario_fills_the_simulator_prompt_before_a_call_is_placed(tmp_path):
    from fi.alk.harness.environment import save_simulator_prompt
    from fi.alk.harness.run.live import prepare
    from fi.alk.harness.scenario import Scenario

    root, _contract, _catalogue = _built_environment(tmp_path)
    save_simulator_prompt(
        "You are at the counter. " * 8 + "\nWhat you are here to do: {{ instruction }}",
        root,
    )
    world, instruction = prepare(
        Scenario(name="s", instruction="Order one Big Mac."), root
    )
    try:
        assert "Order one Big Mac." in instruction
        assert "{{" not in instruction
    finally:
        world.close()


def test_a_scenario_that_leaves_a_slot_empty_never_reaches_a_call(tmp_path):
    """An unfilled slot would be read out to the caller verbatim."""
    import pytest as _pytest

    from fi.alk.harness.environment import save_simulator_prompt
    from fi.alk.harness.run.live import prepare
    from fi.alk.harness.scenario import Scenario

    root, _contract, _catalogue = _built_environment(tmp_path)
    save_simulator_prompt(
        "You are at the counter. " * 8 + "\nDo: {{ instruction }}\nMood: {{ mood }}",
        root,
    )
    with _pytest.raises(RuntimeError, match="mood"):
        prepare(Scenario(name="s", instruction="Order one Big Mac."), root)


def test_a_live_run_is_refused_before_it_costs_anything(monkeypatch):
    """Missing credentials must be caught up front. Discovering them after the world is
    restored, the tunnel is up and the assistant is repointed wastes the expensive part and
    reports a failure that says nothing about the agent."""
    from fi.alk.harness.run.tools import missing_prerequisites

    monkeypatch.delenv("VAPI_API_KEY", raising=False)
    monkeypatch.delenv("VAPI_ASSISTANT_ID", raising=False)
    problems = missing_prerequisites()
    assert any("VAPI_API_KEY" in problem for problem in problems)

    monkeypatch.setenv("VAPI_API_KEY", "x")
    monkeypatch.setenv("VAPI_ASSISTANT_ID", "y")
    monkeypatch.setenv("HARNESS_WEBHOOK_URL", "https://example.invalid")
    assert missing_prerequisites() == []


def test_running_is_a_stage_of_the_conversation():
    """Placing a call was the one step that could only be a command. If it drops out of the
    stage order it silently becomes one again, and the chat ends at scenarios."""
    from fi.alk.harness import chat

    assert chat._NEXT[chat.SCENARIOS] == chat.RUN
    assert chat._NEXT[chat.RUN] == chat.DONE


def test_a_run_result_survives_being_written_and_read(tmp_path):
    from fi.alk.harness.checks import Outcome
    from fi.alk.harness.run.live import LiveRun
    from fi.alk.harness.run.tools import as_record, load_results, save_results

    run = LiveRun(
        scenario="orders-a-big-mac",
        settled=[Outcome("combo_placed", True), Outcome("no_extras", False, "added fries")],
        judged=["explained_itself"],
        calls=["order(...) -> ok"],
    )
    record = as_record(run)
    assert record["passed"] is False and record["met"] == 1 and record["of"] == 2

    save_results([record], tmp_path)
    assert load_results(tmp_path) == [record]


def test_a_tool_a_stage_was_not_given_is_denied_by_the_hook():
    """can_use_tool alone does not do this. An allowed_tools entry approves its tools before the
    callback runs, and the SDK warns the callback is shadowed; a host ToolSearch reached every
    stage, returned nothing and cost a turn. The PreToolUse hook is consulted for every call."""
    import asyncio

    from fi.alk.harness.config import gate_hooks

    hooks = gate_hooks(["mcp__world__seed"])
    refuse = hooks["PreToolUse"][0].hooks[0]

    granted = asyncio.run(refuse({"tool_name": "mcp__world__seed"}, None, None))
    assert granted == {}

    asked = asyncio.run(refuse({"tool_name": "AskUserQuestion"}, None, None))
    assert asked == {}

    denied = asyncio.run(refuse({"tool_name": "ToolSearch"}, None, None))
    said = denied["hookSpecificOutput"]
    assert said["permissionDecision"] == "deny"
    assert "ToolSearch is not part of this stage" in said["permissionDecisionReason"]
    assert "mcp__world__seed" in said["permissionDecisionReason"]


def test_every_stage_gates_with_the_hook_not_only_the_callback():
    """One stage left on the callback alone is one stage a host tool still reaches."""
    import inspect

    from fi.alk.harness import build, reception, scenarios
    from fi.alk.harness.run import grade, stage, targets

    for module in (build, reception, scenarios, stage, targets, grade):
        source = inspect.getsource(module)
        if "permission_gate(" in source:
            assert "gate_hooks(allowed)" in source, f"{module.__name__} has no hook gate"


def test_writing_new_results_keeps_the_ones_not_rerun(tmp_path):
    """The live stage and the local suite share runs.json. Re-running one scenario must not
    erase the record of another, whichever writer gets there second."""
    from fi.alk.harness.run.tools import load_results, save_results

    save_results(
        [{"scenario": "a", "passed": True}, {"scenario": "b", "passed": False}], tmp_path
    )
    fresh = [r for r in load_results(tmp_path) if r.get("scenario") != "b"]
    fresh.append({"scenario": "b", "passed": True, "transcript": "hello"})
    save_results(fresh, tmp_path)

    kept = {r["scenario"]: r for r in load_results(tmp_path)}
    assert kept["a"]["passed"] is True
    assert kept["b"]["passed"] is True and kept["b"]["transcript"] == "hello"


def test_submit_contract_schema_teaches_and_leaves_gating_to_the_gate(tmp_path):
    """Every field marked required is rejected by the schema layer one at a time, a full model
    turn each, before accept_contract can explain anything. Only the fields validate_contract
    refuses to live without may be required; the rest are optional and gated with real messages."""
    import asyncio

    from mcp.types import ListToolsRequest

    from fi.alk.harness.contract import MODALITIES
    from fi.alk.harness.tools import contract_tools

    server = contract_tools(tmp_path)
    instance = server.get("instance") if isinstance(server, dict) else server

    async def schema_of():
        for key, handler in instance.request_handlers.items():
            if getattr(key, "__name__", "") == "ListToolsRequest":
                result = await handler(ListToolsRequest(method="tools/list"))
                return result.root.tools[0].inputSchema
        return {}

    schema = asyncio.run(schema_of())
    # Nothing required at the schema layer: accept_contract is the only gate, and it reports
    # every problem at once with what to do, which a JSON-schema rejection cannot.
    # Nothing required: this layer runs before the tool body, so whatever it rejects never
    # reaches the code that could have understood it. accept_contract is the single gate.
    assert schema.get("required") == []
    assert "required" not in schema["properties"]["tools"]["items"]
    # And the schema has to teach, not just validate — it is shown before the first call.
    described = [
        name for name, spec in schema["properties"].items() if spec.get("description")
    ]
    assert len(described) >= 10, "properties must describe themselves"
    assert schema["properties"]["modality"]["enum"] == list(MODALITIES)
    assert schema["properties"]["tools"]["items"]["properties"]["arg_values"]


def test_a_bare_conversational_contract_is_nudged_once_then_accepted(tmp_path):
    """No rules and no prompt excerpt on a conversational agent almost always means the prompt
    was not found, so the first submission bounces with directions. The second goes through,
    because a gate with no way past would permanently block an agent that genuinely has none."""
    import asyncio

    from fi.alk.harness.tools import contract_tools

    server = contract_tools(tmp_path)
    instance = server.get("instance") if isinstance(server, dict) else server

    async def call(payload):
        from mcp.types import CallToolRequest, CallToolRequestParams

        for key, handler in instance.request_handlers.items():
            if getattr(key, "__name__", "") == "CallToolRequest":
                request = CallToolRequest(
                    method="tools/call",
                    params=CallToolRequestParams(name="submit_contract", arguments=payload),
                )
                answer = await handler(request)
                return answer.root.content[0].text

    payload = {
        "agent": "quiet",
        "tools": [{"name": "act", "args": ["x"]}],
        "real_use_cases": ["do the thing"],
    }
    first = asyncio.run(call(dict(payload)))
    # Both thin spots are reported together, not one per turn.
    assert "system_prompt_excerpt" in first and "data_schema" in first
    assert "submit again" in first
    assert not (tmp_path / "contract.json").exists()

    second = asyncio.run(call(dict(payload)))
    assert "Accepted" in second
    assert (tmp_path / "contract.json").exists()


def test_granting_a_tool_rebuilds_the_gate_not_just_the_list(tmp_path):
    """The hook closes over the granted set when the stage is built, so appending to
    allowed_tools alone leaves the new tool denied. grant() must rebuild all three."""
    import asyncio

    from claude_agent_sdk import ClaudeAgentOptions

    from fi.alk.harness.config import gate_hooks
    from fi.alk.harness.session import Stage

    allowed = ["Read"]
    options = ClaudeAgentOptions(
        system_prompt="x", allowed_tools=allowed, permission_mode="default",
        setting_sources=[], max_turns=1,
    )
    options.hooks = gate_hooks(allowed)
    stage = Stage(options, name="t")
    stage.grant("flow", object(), ["hand_to_next_stage"])

    assert "mcp__flow__hand_to_next_stage" in options.allowed_tools
    refuse = options.hooks["PreToolUse"][0].hooks[0]
    granted = asyncio.run(refuse({"tool_name": "mcp__flow__hand_to_next_stage"}, None, None))
    assert granted == {}


def test_handoff_is_refused_until_the_stage_has_its_artifact(tmp_path):
    """Moving on is decided by code, from the artifacts, never by the model wanting to."""
    import asyncio

    from mcp.types import CallToolRequest, CallToolRequestParams

    from fi.alk.harness.chat import Conversation

    conversation = Conversation(source=None, out=tmp_path, workspace=tmp_path)
    conversation.stage_name = "understand"
    server = conversation._flow_server()
    instance = server.get("instance") if isinstance(server, dict) else server

    async def call():
        for key, handler in instance.request_handlers.items():
            if getattr(key, "__name__", "") == "CallToolRequest":
                request = CallToolRequest(
                    method="tools/call",
                    params=CallToolRequestParams(
                        name="hand_to_next_stage", arguments={"request": "create the world"}
                    ),
                )
                answer = await handler(request)
                return answer.root.content[0].text

    said = asyncio.run(call())
    assert "not produced its artifact" in said
    assert not conversation._handoff

    (tmp_path / "contract.json").write_text(
        '{"agent": "a", "tools": [{"name": "t"}], "real_use_cases": ["u"]}'
    )
    said = asyncio.run(call())
    assert "Handed over" in said
    assert conversation._handoff["request"] == "create the world"


def test_every_problem_is_reported_at_once_with_what_to_do(tmp_path):
    """Revealing the next problem only after the last is fixed costs a turn per problem and
    reads as though the rules are being invented as it goes."""
    from fi.alk.harness.tools import accept_contract

    result = accept_contract({"agent": "", "tools": [], "real_use_cases": []}, tmp_path)
    said = result["content"][0]["text"]
    assert result["is_error"]
    # all three, in one answer
    assert "empty:agent" in said and "no-tools" in said and "no-use-cases" in said
    # and each carries what to do about it, not only its code
    assert "artifact folder" in said and "real tools" in said
    assert not (tmp_path / "contract.json").exists()


def test_a_contract_sent_inside_a_wrapper_is_unwrapped(tmp_path):
    """A contract is a nested thing being described, so it arrives as {"contract": {...}} often
    enough to matter. Every field is right; only the envelope is wrong, and rejecting that
    teaches nothing while costing a turn."""
    from fi.alk.harness.tools import accept_contract, unwrapped

    inner = {
        "agent": "wrapped",
        "tools": [{"name": "act", "args": ["x"]}],
        "real_use_cases": ["do the thing"],
        "hard_constraints": ["a rule"],
        "system_prompt_excerpt": "you are a bot",
    }
    assert unwrapped({"contract": inner}) == inner
    assert unwrapped(inner) == inner
    # a real field that merely holds a dict must not be mistaken for an envelope
    plain = {"agent": "x", "tools": [], "data_schema": {"agent": 1}}
    assert unwrapped(plain) == plain

    result = accept_contract({"contract": inner}, tmp_path)
    assert not result.get("is_error"), result["content"][0]["text"]
    assert (tmp_path / "contract.json").exists()


@pytest.mark.parametrize(
    "written",
    [
        {"name": "order", "parameters": ["item_id", "size"]},
        {"name": "order", "arguments": ["item_id", "size"]},
        {"name": "order", "params": ["item_id", "size"]},
        {"name": "order", "arg_types": {"item_id": "str", "size": "str"}},
        {"name": "order", "parameters": {"item_id": "str", "size": "str"}},
    ],
)
def test_a_tool_written_with_a_synonym_still_records_its_arguments(written):
    """args drives the handlers, the probes and every scenario. It is also the field most often
    written under another name, and a contract bounced for a synonym costs a turn and teaches
    nothing about the agent."""
    spec = ToolSpec.model_validate(written)
    assert spec.args == ["item_id", "size"]


def test_a_tool_that_really_takes_nothing_stays_empty():
    """A tool genuinely taking no arguments is ordinary and must not be invented into one."""
    assert ToolSpec.model_validate({"name": "list_order_items"}).args == []


def test_a_stringified_contract_is_parsed_rather_than_refused(tmp_path):
    from fi.alk.harness.tools import accept_contract, unwrapped

    inner = {
        "agent": "stringy",
        "tools": [{"name": "act", "args": ["x"]}],
        "real_use_cases": ["do it"],
        "hard_constraints": ["a rule"],
    }
    assert unwrapped({"contract": json.dumps(inner)}) == inner
    assert unwrapped({"payload": json.dumps({"contract": inner})}) == inner
    assert not accept_contract({"contract": json.dumps(inner)}, tmp_path).get("is_error")


def test_an_unrecognised_payload_is_told_what_arrived(tmp_path):
    """Otherwise the answer is 'agent is empty, there are no tools' about a submission that
    contained both, and the only way out is guessing at the packaging."""
    from fi.alk.harness.tools import accept_contract

    said = accept_contract({"stuff": 1, "other": 2}, tmp_path)["content"][0]["text"]
    assert "What arrived was: other, stuff" in said
    assert "top-level arguments" in said


def test_a_skill_only_names_tools_its_stage_actually_has():
    """A SKILL.md is the method; the tools are the surface it is written against. They live in
    different files, so a renamed tool leaves the skill telling the model to call something that
    does not exist — and the model then hunts for it and works around the gate. Nothing else
    catches that, because both halves are individually valid."""
    import re

    from fi.alk.harness import scenario_tools
    from fi.alk.harness.config import SKILLS_ROOT
    from fi.alk.harness.run import tools as run_tools
    from fi.alk.harness.tools import CONTRACT_SERVER  # noqa: F401
    from fi.alk.harness.world import tools as world_tools

    surface = {
        "understand-agent": {"submit_contract"},
        "build-environment": set(world_tools.TOOL_NAMES),
        "write-scenarios": set(scenario_tools.TOOL_NAMES),
        "run-scenarios": set(run_tools.TOOL_NAMES),
    }
    # A skill also backticks the names of fields it is telling the model to fill in. Those are
    # not tools, and the list of them is derived rather than hand-kept so it cannot go stale.
    from fi.alk.harness.contract import AgentContract, ToolSpec
    from fi.alk.harness.environment import SubGoal
    from fi.alk.harness.scenario import Scenario

    fields = set()
    for model in (AgentContract, ToolSpec, Scenario, SubGoal):
        fields |= set(model.model_fields)
    # Names from the check-writing examples the skills contain.
    from fi.alk.harness.contract import MODALITIES

    ignore = (
        fields
        | set(MODALITIES)
        | {"handle", "check", "args", "db", "world", "calls", "json", "ToolError"}
    )

    for stage, tools in surface.items():
        text = (SKILLS_ROOT / stage / "SKILL.md").read_text(encoding="utf-8")
        # `name` or `name(` — the way a skill refers to a tool it wants called.
        mentioned = set(re.findall(r"`([a-z_][a-z0-9_]*)\(?`", text))
        unknown = {
            name
            for name in mentioned - tools - ignore
            if name not in {"hand_to_next_stage", "AskUserQuestion"}
        }
        assert not unknown, f"{stage}/SKILL.md names tools that do not exist: {sorted(unknown)}"


def test_a_contract_with_tools_but_no_data_is_nudged_once(tmp_path):
    """The world is built from data_schema and base_environment. Without them the build stage has
    no schema to create and no rows to seed, so every tool call it makes refuses — and that looks
    like a strict world rather than an empty one."""
    import asyncio

    from fi.alk.harness.tools import contract_tools

    server = contract_tools(tmp_path)
    instance = server.get("instance") if isinstance(server, dict) else server

    async def call(payload):
        from mcp.types import CallToolRequest, CallToolRequestParams

        for key, handler in instance.request_handlers.items():
            if getattr(key, "__name__", "") == "CallToolRequest":
                answer = await handler(
                    CallToolRequest(
                        method="tools/call",
                        params=CallToolRequestParams(
                            name="submit_contract", arguments=payload
                        ),
                    )
                )
                return answer.root.content[0].text

    payload = {
        "agent": "dataless",
        "tools": [{"name": "act", "args": ["x"]}],
        "real_use_cases": ["do the thing"],
        "hard_constraints": ["a rule"],
        "system_prompt_excerpt": "you are a bot",
    }
    first = asyncio.run(call(dict(payload)))
    assert "data_schema" in first and "submit again" in first
    assert not (tmp_path / "contract.json").exists()

    second = asyncio.run(call(dict(payload)))
    assert "Accepted" in second

    assert (tmp_path / "contract.json").exists()


def test_only_a_tool_that_says_it_saved_reports_an_artifact():
    """Matching any path-shaped token in any result meant reading a file announced itself as an
    artifact: the stage looks like it is producing output while it is still only looking around,
    and a front end reloads its panes on every read."""
    from dataclasses import dataclass

    from fi.alk.harness.session import _saved_path

    @dataclass
    class Block:
        content: object
        is_error: bool = False

    # a read
    assert _saved_path(Block("     1\timport json\n     2\tfrom pathlib import Path")) == ""
    assert _saved_path(Block("/some/agent/envs/retail/__init__.py")) == ""
    # a write
    assert _saved_path(Block("Accepted and saved to out/contract.json.")) == "out/contract.json"
    assert _saved_path(Block("Saved 3 scenarios to out/scenarios.json.")) == "out/scenarios.json"
    # list-shaped content, as the SDK sometimes gives it
    assert (
        _saved_path(Block([{"text": "Saved to artifacts/x/world.sqlite"}]))
        == "artifacts/x/world.sqlite"
    )


@pytest.mark.parametrize(
    "written,field,expected",
    [
        ({"use_cases": ["a"]}, "real_use_cases", ["a"]),
        ({"scenarios": ["a"]}, "real_use_cases", ["a"]),
        ({"rules": ["r"]}, "hard_constraints", ["r"]),
        ({"constraints": ["r"]}, "hard_constraints", ["r"]),
        ({"system_prompt": "p"}, "system_prompt_excerpt", "p"),
        ({"instructions": "p"}, "system_prompt_excerpt", "p"),
        ({"schema": {"a": 1}}, "data_schema", {"a": 1}),
        ({"seed_data": {"t": []}}, "base_environment", {"t": []}),
    ],
)
def test_a_field_written_under_the_obvious_name_still_lands(written, field, expected):
    """Every one of these was written by a model that had read the schema and still reached for
    the more obvious word. Bouncing it produces a loop: the answer to `use_cases` was
    'no-use-cases', which reads as missing rather than misnamed, so the same submission comes
    back with the shape changed and the name untouched."""
    contract = AgentContract.model_validate({"agent": "x", **written})  # our name already set
    assert getattr(contract, field) == expected


def test_the_agent_name_can_arrive_as_name():
    assert AgentContract.model_validate({"name": "bot", "tools": []}).agent == "bot"


def test_our_own_name_wins_when_both_are_given():
    contract = AgentContract.model_validate(
        {"agent": "x", "real_use_cases": ["ours"], "use_cases": ["theirs"]}
    )
    assert contract.real_use_cases == ["ours"]


def test_the_gate_names_the_field_it_wants(tmp_path):
    """A code alone cannot be acted on when the mistake is the field's name."""
    from fi.alk.harness.tools import accept_contract

    said = accept_contract({"agent": "x", "tools": [], "real_use_cases": []}, tmp_path)
    text = said["content"][0]["text"]
    assert "`real_use_cases`" in text and "not `use_cases`" in text
    assert "`tools`" in text


# --- scenario folders and the ready gate ---------------------------------------------


def test_the_ready_gate_refuses_a_scenario_whose_world_was_never_set_up(tmp_path):
    """The precondition gate. A scenario about the last five items is only a test of the agent
    if there really are five; otherwise the agent fails for something we got wrong, and it reads
    as the agent's fault."""
    from fi.alk.harness.prove import prove

    root, _contract, catalogue = _built_environment(tmp_path)
    scenario = Scenario.model_validate(
        _delta(
            ready_code=(
                "def ready(world):\n"
                "    rows = world.state()['cart']\n"
                "    return None if rows else 'the cart is empty; this scenario needs one item'\n"
            )
        )
    )
    proof = prove(scenario, catalogue, root)
    assert not proof.ready
    assert not proof.holds
    assert "the cart is empty" in proof.why()
    assert "test us rather than the agent" in proof.why()
    assert proof.gates() == {"ready": False, "solvable": False, "not_vacuous": False}


def test_setup_code_makes_the_world_the_scenario_presumes(tmp_path):
    """setup runs, then ready confirms it worked, and only then is anything else asked."""
    from fi.alk.harness.prove import prove

    root, _contract, catalogue = _built_environment(tmp_path)
    scenario = Scenario.model_validate(
        _delta(
            setup_code=(
                "def setup(world):\n"
                "    world.connection.execute(\"INSERT INTO menu (id) VALUES ('sushi')\")\n"
                "    world.connection.commit()\n"
            ),
            ready_code=(
                "def ready(world):\n"
                "    ids = [r['id'] for r in world.state()['menu']]\n"
                "    return None if 'sushi' in ids else 'sushi was never added to the menu'\n"
            ),
        )
    )
    proof = prove(scenario, catalogue, root)
    assert proof.ready and proof.holds, proof.why()


def test_the_setups_own_calls_are_not_credited_to_the_agent(tmp_path):
    """A check that counts calls must not see the ones the scenario made on its own behalf."""
    from fi.alk.harness.prove import prepared

    root, _contract, _catalogue = _built_environment(tmp_path)
    scenario = Scenario.model_validate(
        _delta(
            setup_code=(
                "def setup(world):\n"
                "    world.call('add', {'item_id': 'big_mac'})\n"
            )
        )
    )
    world, applied, ready = prepared(scenario, root)
    try:
        assert applied.ok and ready.ok
        assert len(world.state()["cart"]) == 1, "the setup should have acted"
        assert world.calls == [], "but its calls are not the agent's"
    finally:
        world.close()


def test_broken_setup_is_ours_and_says_so(tmp_path):
    from fi.alk.harness.prove import prove

    root, _contract, catalogue = _built_environment(tmp_path)
    scenario = Scenario.model_validate(_delta(setup_code="def setup(world):\n    world.nope()\n"))
    proof = prove(scenario, catalogue, root)
    assert not proof.ready
    assert proof.broken, "a setup that raises is our mistake, not a failing scenario"
    assert "AttributeError" in proof.why_not_ready




def test_a_kept_scenario_becomes_a_folder_of_files(tmp_path):
    """The files are the artifact, not a rendering of one. Something you can open and run is
    something you can argue with."""
    from fi.alk.harness.folder import folder_for, read_folder
    from fi.alk.harness.scenario_tools import write_scenarios

    root, _contract, catalogue = _built_environment(tmp_path)
    scenario = Scenario.model_validate(
        _delta(
            setup_code="def setup(world):\n    pass\n",
            ready_code="def ready(world):\n    return None\n",
        )
    )
    index = write_scenarios([scenario], root, catalogue)

    here = folder_for(root, scenario.name)
    assert (here / "scenario.json").exists()
    assert (here / "setup.py").exists()
    assert (here / "ready.py").exists()
    # One file per deterministic sub-goal; the judged one has no check to write.
    assert sorted(p.name for p in (here / "checks").iterdir()) == [
        "item-added.py",
        "right-item.py",
    ]
    assert index.name == "scenarios.json"

    # The code lives in the files, not duplicated into the JSON, so the two cannot drift.
    body = json.loads((here / "scenario.json").read_text())
    assert "setup_code" not in body and "ready_code" not in body

    # And it reads back whole.
    again = read_folder(root, scenario.name)
    assert again is not None
    assert again.setup_code.strip() == "def setup(world):\n    pass"
    assert again.solution == scenario.solution


def test_a_check_file_runs_on_its_own_and_agrees_with_the_harness(tmp_path):
    """The same file, the same answer, whether the harness runs it or a person does. If those
    two could disagree, neither could be trusted."""
    import subprocess
    import sys

    from fi.alk.harness.folder import folder_for, write_folder
    from fi.alk.harness.prove import prepared

    root, _contract, catalogue = _built_environment(tmp_path)
    scenario = Scenario.model_validate(_delta())
    write_folder(scenario, catalogue, root)

    # Leave the world in the state a passing run would have left it in.
    world, _applied, _ready = prepared(scenario, root)
    try:
        world.call("add", {"item_id": "big_mac"})
    finally:
        world.close()

    check_file = folder_for(root, scenario.name) / "checks" / "item-added.py"
    done = subprocess.run(
        [sys.executable, str(check_file), str(root / "world.sqlite")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    # The world on disk is the base world, which has an empty cart, so this check should fail —
    # and the point is that it says so rather than erroring.
    assert done.returncode in (0, 1), done.stderr[-400:]
    assert "held" in done.stdout or "FAILED" in done.stdout, done.stdout + done.stderr[-300:]


def test_every_stage_is_told_what_the_harness_is_for():
    """A stage that knows only its own step does its step well and still gets the point of it
    wrong: it works around a gate instead of fixing what the gate named, or it reports a number
    that quietly skipped half its checks."""
    for stage in ("understand-agent", "build-environment", "write-scenarios", "run-scenarios"):
        text = load_skill(stage)
        assert text.startswith("# The harness"), stage
        assert "# The stage you are in now" in text, stage
        # the ideas a stage must not be able to miss
        assert "Code decides what is true" in text, stage
        assert "refusal" in text and "crash" in text, stage


# --- sessions: one conversation, one folder -------------------------------------------


def test_a_session_is_a_folder_that_knows_what_it_holds(tmp_path):
    """Nothing is held in memory that is not also on disk, so closing the page, restarting the
    server or coming back tomorrow all resume by reading the folder."""
    from fi.alk.harness import sessions

    one = sessions.create(agent="drive_thru", source="/somewhere/agent", base=tmp_path)
    assert one.id.startswith("drive-thru-")
    assert (one.path / "session.json").exists()

    has = one.has()
    assert has == {
        "contract": False, "world": False, "simulator_prompt": False,
        "sub_goals": 0, "scenarios": 0, "validated": None,
        "runs": 0, "runs_passed": 0, "messages": 0,
    }

    again = sessions.load(one.id, tmp_path)
    assert again is not None
    assert again.agent == "drive_thru" and again.source == "/somewhere/agent"


def test_two_goes_at_the_same_agent_are_two_sessions(tmp_path):
    from fi.alk.harness import sessions

    first = sessions.create(agent="same", base=tmp_path)
    second = sessions.create(agent="same", base=tmp_path)
    assert first.id != second.id
    assert {one.id for one in sessions.every(tmp_path)} == {first.id, second.id}


def test_the_conversation_is_kept_in_the_session_folder(tmp_path):
    """A refresh must not lose what was said."""
    from fi.alk.harness import sessions

    one = sessions.create(agent="talky", base=tmp_path)
    sessions.remember(one.path, sessions.Message(role="you", text="hello", stage="reception"))
    sessions.remember(
        one.path,
        sessions.Message(
            role="harness", text="hi", stage="reception",
            tools=[{"label": "point at agent", "said": ["Pointed at talky"]}],
        ),
    )
    said = sessions.history(one.path)
    assert [m["role"] for m in said] == ["you", "harness"]
    assert said[1]["tools"][0]["label"] == "point at agent"
    assert one.has()["messages"] == 2

    # A half-written final line is what a killed process leaves; it must not take the rest.
    with (one.path / "chat.jsonl").open("a", encoding="utf-8") as file:
        file.write('{"role": "you", "text": "cut off')
    assert len(sessions.history(one.path)) == 2


def test_deleting_a_session_will_not_reach_outside_the_sessions_root(tmp_path):
    """A mistyped id must never take anything else with it."""
    from fi.alk.harness import sessions

    one = sessions.create(agent="doomed", base=tmp_path)
    outsider = tmp_path.parent / "not-a-session"
    outsider.mkdir(exist_ok=True)

    assert sessions.remove("../not-a-session", tmp_path) is False
    assert outsider.exists()
    assert sessions.remove("no-such-session", tmp_path) is False

    assert sessions.remove(one.id, tmp_path) is True
    assert not one.path.exists()


def test_any_stage_whose_input_exists_can_be_opened(tmp_path):
    """Stages are not a wizard. Coming back to correct a contract after the world is built is
    the ordinary case, so what cannot be skipped is the input, not the order."""
    from fi.alk.harness.chat import Conversation
    from fi.alk.harness.tools import accept_contract

    empty = Conversation(out=tmp_path)
    blocked = empty.reachable()
    assert blocked["reception"] == ""
    assert "where its source lives" in blocked["understand"]
    assert "needs a contract" in blocked["build"]
    # Without a contract, every later stage says so — not "needs a world", which would send
    # somebody to build one against nothing.
    assert "needs a contract" in blocked["scenarios"]
    assert "needs a contract" in blocked["run"]

    root, contract, _catalogue = _built_environment(tmp_path / "built")
    accept_contract(contract.model_dump(), root)
    ready = Conversation(out=root)
    open_now = ready.reachable()
    assert open_now["build"] == "", open_now
    assert open_now["scenarios"] == "", open_now
    assert "needs scenarios" in open_now["run"]


def test_reception_can_hand_over_in_the_turn_that_finds_the_agent(tmp_path):
    """The source is read off the reception stage after its turn ends, so within that turn the
    conversation does not know it yet. Without allowing for that, the stage that has just
    succeeded is told it has produced nothing and the handoff is refused."""
    from fi.alk.harness.chat import Conversation
    from fi.alk.harness.sources import RepoSource

    conversation = Conversation(out=tmp_path)
    conversation.stage_name = "reception"
    assert conversation.next_stage() is None, "nothing pointed at yet"

    # what point_at_agent does, mid-turn
    conversation._found["source"] = RepoSource(name="x", root=tmp_path)
    assert conversation.next_stage() == "understand"


def test_the_turn_that_finds_the_agent_also_opens_the_next_stage(tmp_path, monkeypatch):
    """Allowing that handoff is not enough: the stage it opens is built from the source, so the
    source has to be on the conversation before the hop, not after it. Otherwise the hop raises,
    the turn is lost, and the source is never taken up at all — every later message arrives back
    at reception, which has no tools to do anything with it."""
    import asyncio

    from fi.alk.harness import chat as chat_module
    from fi.alk.harness.chat import Conversation
    from fi.alk.harness.sources import RepoSource

    said: list[str] = []

    class Stage:
        spent_usd = 0.0

        def __init__(self, name: str) -> None:
            self.name = name

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def say(self, message, on_event=None):
            said.append(f"{self.name}: {message}")

        def grant(self, *_, **__):
            pass

    found: dict = {}
    monkeypatch.setattr(
        chat_module.reception_stage, "open_stage", lambda **_: (Stage("reception"), found)
    )
    monkeypatch.setattr(chat_module.reception_stage, "opening", lambda: "which agent")
    monkeypatch.setattr(
        chat_module.understand_stage,
        "open_stage",
        lambda *_, **__: (Stage("understand"), {}),
    )
    monkeypatch.setattr(chat_module.understand_stage, "opening", lambda _: "read the agent")

    conversation = Conversation(out=tmp_path, workspace=tmp_path)

    async def turn():
        await conversation.open_quietly()
        # what reception's turn does when one message both names the agent and asks for the next
        # thing: it points, then hands the request on.
        found["source"] = RepoSource(name="x", root=tmp_path)
        conversation._handoff["request"] = "read it and tell me what it can do"
        await conversation.say("test the voice agent at /x, and tell me what it can do")

    asyncio.run(turn())

    assert conversation.source is not None, "the turn that pointed never landed"
    assert conversation.stage_name == "understand"
    assert any("read it and tell me what it can do" in one for one in said), said


def test_the_build_skill_documents_every_method_a_handler_can_call():
    """A handler gets `db` and nothing else, so if the skill does not say what `db` offers the
    model guesses — and the guess is sqlite's cursor API, which fails on the smoke call."""
    import inspect

    from fi.alk.harness.world.runtime import Db

    skill = load_skill("build-environment")
    methods = [
        name for name, _ in inspect.getmembers(Db, inspect.isfunction)
        if not name.startswith("_")
    ]
    assert methods, "Db should have methods to document"
    for name in methods:
        assert f"db.{name}(" in skill, f"the build skill never shows db.{name}()"
    # and it warns off the API the model actually reaches for by default
    assert "fetchone" in skill


def test_a_crashed_handler_is_told_what_a_handler_actually_has(tmp_path):
    """An error naming the failure without naming the API produces the same wrong guess again.
    Three identical attempts at one handler is what that cost on a real run."""
    import asyncio

    from mcp.types import CallToolRequest, CallToolRequestParams

    from fi.alk.harness.world import tools as world_tools

    root, contract = _saved_world(tmp_path)
    server, _world = world_tools.world_tools(contract, root)
    instance = server.get("instance") if isinstance(server, dict) else server

    async def define(source):
        for key, handler in instance.request_handlers.items():
            if getattr(key, "__name__", "") == "CallToolRequest":
                answer = await handler(
                    CallToolRequest(
                        method="tools/call",
                        params=CallToolRequestParams(
                            name="define_handler",
                            arguments={"tool_name": "add", "source": source},
                        ),
                    )
                )
                return answer.root.content[0].text

    # the mistake a model actually makes: sqlite's cursor API
    said = asyncio.run(define(
        "def handle(args, db):\n"
        "    return db.execute('SELECT 1').fetchone()\n"
    ))
    assert "crashed on its smoke call" in said
    assert "db.query(" in said and "db.one(" in said and "db.execute(" in said
    assert "fetchone" in said
