"""A sub-goal's check can be handed the scenario's reference solution and grade correctness on it."""

from __future__ import annotations

from typing import Any

from fi.alk.harness.checks import run_check, takes_reference
from fi.alk.harness.world.runtime import Call

CHECK = '''
def check(world, calls, reference):
    wanted = next((s["arguments"] for s in reference if s["tool"] == "file_claim"), None)
    if wanted is None:
        return None
    made = [c for c in calls if c.name == "file_claim" and c.ok]
    if not made:
        return "not done: no claim was filed"
    filed = {c.result.get("claim_id") for c in made}
    rows = [r for r in world.state()["claims"] if r["id"] in filed]
    if not rows:
        return "not done: the claim left no record"
    for field in ("policy", "loss_date"):
        if str(rows[0].get(field)) != str(wanted[field]):
            return f"done wrong: {field} expected {wanted[field]!r}, got {rows[0].get(field)!r}"
    return None
'''
REFERENCE = [{"tool": "file_claim", "arguments": {"policy": "MM-7", "loss_date": "2026-10-11"}}]


class _World:
    def __init__(self, claims: list[dict[str, Any]]) -> None:
        self.claims = claims

    def state(self) -> dict[str, Any]:
        return {"claims": self.claims}


def _filed(**row: Any) -> tuple[_World, list[Call]]:
    call = Call(name="file_claim", arguments=dict(row), result={"claim_id": "c1"}, ok=True)
    return _World([{"id": "c1", **row}]), [call]


def test_a_check_given_the_reference_grades_correctness_from_the_call_and_the_record():
    world, calls = _filed(policy="MM-7", loss_date="2026-10-11")
    assert run_check(CHECK, world, calls, reference=REFERENCE).held
    world, calls = _filed(policy="MM-7", loss_date="2026-10-12")
    said = run_check(CHECK, world, calls, reference=REFERENCE)
    assert not said.held and said.said == "done wrong: loss_date expected '2026-10-11', got '2026-10-12'"
    said = run_check(CHECK, _World([]), [], reference=REFERENCE)
    assert not said.held and said.said == "not done: no claim was filed"


def test_a_two_argument_check_runs_as_before_and_is_never_given_the_reference():
    two = "def check(world, calls):\n    return None if world.state()['claims'] else 'no claim'\n"
    world, calls = _filed(policy="MM-7", loss_date="2026-10-11")
    assert run_check(two, world, calls, reference=REFERENCE).held
    assert not run_check(two, _World([]), []).held
    assert takes_reference(lambda world, calls, reference: None)
    assert not takes_reference(lambda world, calls: None)
    assert not takes_reference(type("Deferred", (), {"_alk_source": "def check(:", "_alk_entry": "check"})())


def _call(name: str, ok: bool = True, **arguments: Any) -> Call:
    return Call(name=name, arguments=arguments, result={}, ok=ok)


# Checks written the way the guidance asks, one per row of the comparison table.
EXACT = '''
def check(world, calls, reference):
    want = next(s["arguments"]["account_id"] for s in reference if s["tool"] == "open")
    made = [c for c in calls if c.name == "open" and c.ok]
    if not made:
        return "not done: nothing was opened"
    if not any(str(c.arguments.get("account_id")) == str(want) for c in made):
        return f"done wrong: account_id expected {want!r}"
    return None
'''
FINAL_NO_DUPLICATE = '''
def check(world, calls, reference):
    want = [s["arguments"]["item"] for s in reference if s["tool"] == "book"][-1]
    rows = [r for r in world.state()["bookings"] if r.get("status") != "cancelled"]
    if not rows:
        return "not done: nothing is booked"
    if len(rows) > 1:
        return f"done wrong: {len(rows)} live bookings where one is expected"
    if rows[0]["item"] != want:
        return f"done wrong: item expected {want!r}, got {rows[0]['item']!r}"
    return None
'''
NO_FORBIDDEN_CHANGE = '''
def check(world, calls, reference):
    if any(c.name == "refund" and c.ok for c in calls):
        return "done wrong: a refund was made that the rules forbid"
    return None
'''


def test_case_table_exact_identifier_keeps_leading_zeros():
    ref = [{"tool": "open", "arguments": {"account_id": "0042"}}]
    assert run_check(EXACT, _World([]), [_call("open", account_id="0042")], reference=ref).held
    said = run_check(EXACT, _World([]), [_call("open", account_id="42")], reference=ref)
    assert not said.held and said.said == "done wrong: account_id expected '0042'"


def test_case_table_a_failed_call_is_not_done():
    ref = [{"tool": "open", "arguments": {"account_id": "0042"}}]
    said = run_check(EXACT, _World([]), [_call("open", ok=False, account_id="0042")], reference=ref)
    assert not said.held and said.said.startswith("not done")


class _Bookings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def state(self) -> dict[str, Any]:
        return {"bookings": self.rows}


def test_case_table_a_correction_counts_by_the_final_value_and_a_leftover_is_a_duplicate():
    ref = [{"tool": "book", "arguments": {"item": "A"}}, {"tool": "book", "arguments": {"item": "B"}}]
    corrected = _Bookings([{"item": "A", "status": "cancelled"}, {"item": "B", "status": "live"}])
    assert run_check(FINAL_NO_DUPLICATE, corrected, [], reference=ref).held
    left = _Bookings([{"item": "A", "status": "live"}, {"item": "B", "status": "live"}])
    said = run_check(FINAL_NO_DUPLICATE, left, [], reference=ref)
    assert not said.held and said.said == "done wrong: 2 live bookings where one is expected"


def test_case_table_a_refusal_checks_the_forbidden_change_is_absent():
    assert run_check(NO_FORBIDDEN_CHANGE, _World([]), [_call("lookup")], reference=[]).held
    assert not run_check(NO_FORBIDDEN_CHANGE, _World([]), [_call("refund")], reference=[]).held


def test_a_check_that_raises_is_an_execution_error_not_an_agent_failure():
    broken = "def check(world, calls, reference):\n    return reference[5]['tool']\n"
    said = run_check(broken, _World([]), [], reference=[])
    assert said.broken and not said.held


def test_a_check_compiled_to_run_in_the_hosted_worker_is_still_given_the_reference():
    from fi.alk.harness.scenario_source import _compile_entry

    deferred = _compile_entry(CHECK, label="t", entry="check", defer_execution=True)
    assert takes_reference(deferred)
    two = _compile_entry("def check(world, calls):\n    return None\n", label="t", entry="check", defer_execution=True)
    assert not takes_reference(two)
    world, calls = _filed(policy="MM-7", loss_date="2026-10-12")
    assert deferred(world, calls, REFERENCE) == "done wrong: loss_date expected '2026-10-11', got '2026-10-12'"


def test_a_reference_check_must_say_where_each_compared_value_comes_from():
    from fi.alk.harness.catalogue import SubGoal, validate_sub_goal

    goal = SubGoal(name="claim_filed", what="the claim is filed for the caller's policy", check=CHECK)
    assert any("where each value it compares comes from" in one for one in validate_sub_goal(goal))
    sourced = goal.model_copy(update={"reference": "policy, loss_date: what the caller said"})
    assert not any("where each value it compares comes from" in one for one in validate_sub_goal(sourced))


def test_a_check_is_given_only_the_arguments_a_step_marks_fixed():
    from fi.alk.harness.checks import reference_of
    from fi.alk.harness.scenario import Step

    marked = Step(tool="hand_over", arguments={"account": "A-1", "queue": "picked"}, fixed={"account": "the caller's record"})
    unmarked = {"tool": "look_up", "arguments": {"account": "A-1"}}
    assert reference_of([marked, unmarked]) == [
        {"tool": "hand_over", "arguments": {"account": "A-1"}},
        {"tool": "look_up", "arguments": {"account": "A-1"}},
    ]



def test_a_fixed_value_must_be_where_its_stated_source_says():
    from fi.alk.harness.scenario import Scenario, Step
    from fi.alk.harness.scenario_tools import _fixed_sources_that_do_not_hold

    rules = "7. Anything needing a specialist is NOT yours to do. Explain briefly and hand it over.\n8. Use the priority line for emergencies."

    class Trial:
        def state(self) -> dict[str, Any]:
            return {"accounts": [{"id": "A-77"}]}

    def problems(source: str, value: str) -> list[str]:
        step = Step(tool="hand_over", arguments={"line": value}, fixed={"line": source})
        return _fixed_sources_that_do_not_hold(Scenario(name="s", instruction="My account is A-12.", solution=[step]), Trial(), rules)

    assert problems("rule 7: specialist line for this", "specialist")
    assert problems('rule: "hand it over"', "specialist")
    assert problems("specialist", "specialist")
    assert problems("caller: they asked for it", "specialist")
    assert not problems('rule: "Use the priority line for emergencies"', "priority")
    assert not problems("caller: says A-12", "A-12")
    assert not problems("record: accounts.id", "A-77")
    assert not problems("record: accounts.id, under the tool's prefix", "account:A-77")
    assert problems("record: accounts.id, under the tool's prefix", "account:A-78")
    assert problems("record: accounts.id, under the tool's prefix", "account:9999")
    assert problems("record: accounts", "id")
    assert problems("record: accounts.id", "7")
    assert not problems("record: accounts.id, in the tool's path form", "accounts/A-77")
    assert not problems("caller: says their account", "my_account")


def test_a_solution_step_must_match_the_agents_contract(tmp_path):
    import json

    from fi.alk.harness.scenario import Scenario, Step
    from fi.alk.harness.scenario_tools import _steps_the_contract_refuses

    tools = [{"name": "hand_over", "args": ["line", "reason"], "arg_values": {"line": ["general", "priority"]}}]
    (tmp_path / "contract.json").write_text(json.dumps({"tools": tools}))

    def problems(*steps: Step) -> list[str]:
        return _steps_the_contract_refuses(Scenario(name="s", instruction="Help.", solution=list(steps)), tmp_path)

    assert not problems(Step(tool="hand_over", arguments={"line": "priority", "reason": "injury"}))
    assert not problems(Step(tool="hand_over", arguments={"line": "$call.look_up.line"}))
    assert problems(Step(tool="look_up", arguments={}))
    assert problems(Step(tool="hand_over", arguments={"queue": "general"}))
    assert problems(Step(tool="hand_over", arguments={"line": "claims"}))


def test_a_setup_writing_records_an_in_process_agent_never_loads_is_refused(tmp_path):
    import json

    from fi.alk.harness.scenario import Scenario
    from fi.alk.harness.scenario_tools import _records_the_agent_cannot_see

    adds = "def setup(world):\n    world.put('orders', {'order_id': '9', 'amount': 5})\n"
    changes = "def setup(world):\n    world.change('orders', {'order_id': '1'}, {'amount': 5})\n"

    def problems(store: dict, setup: str) -> list[str]:
        (tmp_path / "contract.json").write_text(json.dumps({"data_store": store}))
        return _records_the_agent_cannot_see(Scenario(name="s", instruction="Help.", setup_code=setup), tmp_path)

    in_process = {"kind": "in_process", "configured_by": "hardcoded", "database": "in_memory"}
    assert problems(in_process, adds)
    assert not problems(in_process, "")
    assert problems(in_process, changes)
    assert not problems({**in_process, "loader_module": "agent.data"}, adds)
    assert not problems({"kind": "postgres", "configured_by": "env"}, adds)

    # An actor's record change is just as invisible to it; a part spoken on the conversation is not.
    def with_actor(store: dict, actor: dict) -> list[str]:
        (tmp_path / "contract.json").write_text(json.dumps({"data_store": store}))
        scenario = Scenario(name="s", instruction="Help.", actors=[actor])
        return _records_the_agent_cannot_see(scenario, tmp_path)

    approves = {"id": "manager", "actions": [{"action": "approvals.put", "arguments": {"status": "approved"}}]}
    assert with_actor(in_process, approves)
    assert not with_actor(in_process, {"id": "partner", "instruction": "Ask whether it can go to my card."})
    assert not with_actor({"kind": "postgres", "configured_by": "env"}, approves)
    # An agent recorded as loading the world's records once, at start, never sees a later change; one that reads
    # them on every call does, and one whose loader timing is not recorded gets a note instead of a refusal.
    from fi.alk.harness.scenario_tools import late_changes_note

    later = {"id": "manager", "actions": [{"action": "approvals.put", "trigger": {"kind": "on_tool", "tool": "ask"}}]}
    once = {**in_process, "loaded_by": "load_profile", "loads": "start"}
    assert not with_actor(once, approves)
    assert with_actor(once, later)
    assert not with_actor({**once, "loads": "each_call"}, later)
    unknown = {**in_process, "loaded_by": "load_profile"}
    assert not with_actor(unknown, later)
    (tmp_path / "contract.json").write_text(json.dumps({"data_store": unknown}))
    noted = Scenario(name="s", instruction="Help.", actors=[later])
    assert "If this agent's loader runs only when it starts" in late_changes_note(noted, tmp_path)
    (tmp_path / "contract.json").write_text(json.dumps({"data_store": once}))
    assert late_changes_note(noted, tmp_path) == ""
    assert with_actor(in_process, later)


def test_the_proof_varies_every_argument_nothing_fixes_and_keeps_chained_values():
    from fi.alk.harness.prove import _other_choices
    from fi.alk.harness.scenario import Scenario, Step

    scenario = Scenario(
        name="s",
        instruction="i",
        solution=[
            Step(tool="open", arguments={"account": "A-1", "note": "picked"}, fixed={"account": "caller: A-1"}),
            Step(tool="confirm", arguments={"token": "$call.open.token"}, fixed={}),
        ],
    )
    varied, changed = _other_choices(scenario)
    assert varied[0].arguments == {"account": "A-1", "note": "another note"}
    assert varied[1].arguments == {"token": "$call.open.token"} and changed == {"open.note"}
    unfixed = scenario.model_copy(update={"solution": [Step(tool="open", arguments={"note": "x"})]})
    assert _other_choices(unfixed) == (unfixed.solution, set())


def test_a_changed_value_reaches_what_the_step_executes_or_is_not_probed():
    from fi.alk.harness.prove import _with_value
    from fi.alk.harness.scenario import Step

    plain = Step(tool="pay", arguments={"account": "A-1"})
    assert _with_value(plain, "account", "A-2").arguments == {"account": "A-2"}
    carried = Step(tool="pay", arguments={"account": "A-1"}, environment_arguments={"account": "A-1", "via": "api"})
    assert _with_value(carried, "account", "A-2").environment_arguments == {"account": "A-2", "via": "api"}
    reshaped = Step(tool="pay", arguments={"account": "A-1"}, environment_arguments={"acct_no": "A-1"})
    assert _with_value(reshaped, "account", "A-2") is None


def _notes_world(tmp_path):
    from fi.alk.harness.world import GeneratedWorld
    from fi.alk.harness.world.snapshot import save

    class W(GeneratedWorld):
        name = "notes"
        tools = [{"name": "save_note"}, {"name": "book"}, {"name": "lookup"}]
        handlers = {
            "save_note": (
                "def handle(args, db):\n"
                "    db.execute('INSERT INTO notes (customer, note) VALUES (?, ?)', [args['customer'], args['note']])\n"
                "    return {'ok': 1}\n"
            ),
            "book": (
                "def handle(args, db):\n"
                "    db.execute('DELETE FROM bookings')\n"
                "    db.execute('INSERT INTO bookings (time) VALUES (?)', [args['time']])\n"
                "    return {'ok': 1}\n"
            ),
            "lookup": "def handle(args, db):\n    return {'account': args['account']}\n",
        }

    world = W(":memory:")
    world.connection.executescript("CREATE TABLE notes(customer TEXT, note TEXT); CREATE TABLE bookings(time TEXT);")
    world.connection.commit()
    save(world, tmp_path, notes="test", sequences=[])
    return tmp_path


def _proof(root, solution, check):
    from fi.alk.harness.catalogue import Catalogue, SubGoal
    from fi.alk.harness.prove import prove
    from fi.alk.harness.scenario import Scenario

    catalogue = Catalogue(sub_goals=[SubGoal(name="right", what="right", check=check)])
    scenario = Scenario(
        name="s", use_case="u", instruction="i", tests="t", fixture={"origin": "seed"}, solution=solution, sub_goals=["right"]
    )
    return prove(scenario, catalogue, root)


def test_a_check_that_the_saved_note_is_the_one_sent_is_not_called_too_strict(tmp_path):
    from fi.alk.harness.scenario import Step

    check = (
        "def check(world, calls, reference):\n"
        "    sent = [c.arguments['note'] for c in calls if c.name == 'save_note' and c.ok]\n"
        "    saved = [r['note'] for r in world.state()['notes']]\n"
        "    return None if sent and sent == saved else 'done wrong: the saved note is not the one sent'\n"
    )
    solution = [Step(tool="save_note", arguments={"customer": "C-1", "note": "left earbud broken"}, fixed={"customer": "caller: C-1"})]
    proof = _proof(_notes_world(tmp_path), solution, check)
    assert proof.too_strict == []


def test_a_draft_a_later_step_corrects_is_not_a_value_the_checks_must_catch(tmp_path):
    from fi.alk.harness.scenario import Step

    check = (
        "def check(world, calls, reference):\n"
        "    want = [s['arguments']['time'] for s in reference if s['tool'] == 'book'][-1]\n"
        "    rows = world.state()['bookings']\n"
        "    return None if [r['time'] for r in rows] == [want] else 'done wrong: time'\n"
    )
    solution = [
        Step(tool="book", arguments={"time": "4 pm"}, fixed={"time": "caller: 4 pm"}),
        Step(tool="book", arguments={"time": "6 pm"}, fixed={"time": "caller: 6 pm"}),
    ]
    proof = _proof(_notes_world(tmp_path), solution, check)
    assert proof.unchecked == []


def test_a_wrong_read_only_lookup_is_still_a_value_the_checks_must_catch(tmp_path):
    from fi.alk.harness.scenario import Step

    check = "def check(world, calls, reference):\n    return None if any(c.name == 'lookup' and c.ok for c in calls) else 'not done'\n"
    solution = [Step(tool="lookup", arguments={"account": "A"}, fixed={"account": "caller: A"})]
    proof = _proof(_notes_world(tmp_path), solution, check)
    assert proof.unchecked == ["lookup.account"]


def test_a_writer_is_told_which_coverage_levels_already_hold_their_share():
    from fi.alk.harness.scenario import Scenario
    from fi.alk.harness.scenario_tools import _full_levels

    grid = {"task": ["book", "cancel", "change"], "overlay": ["none", "a", "b"]}
    kept = [Scenario(name=f"s{i}", instruction="i", coverage={"task": "book", "overlay": "a"}) for i in range(4)]
    assert _full_levels(grid, kept, 12) == ["task book"]
    assert _full_levels(grid, kept[:3], 12) == []
    assert _full_levels(grid, kept, 9) == []


def test_the_agents_prompt_is_copied_from_the_lines_named_and_nothing_outside_the_source(tmp_path):
    from fi.alk.harness.contract import AgentContract, PromptSource
    from fi.alk.harness.tools import _copy_prompt

    (tmp_path / "prompt.py").write_text("x = 1\nYou book rides.\nNever share PINs.\n")
    contract = AgentContract(agent="a", system_prompt_excerpt="quoted", system_prompt_from=[PromptSource(path="prompt.py", start_line=2, end_line=3)])
    assert _copy_prompt(contract, tmp_path) == []
    assert contract.system_prompt_excerpt == "You book rides.\nNever share PINs."
    for where in (PromptSource(path="../outside.py", start_line=1, end_line=1), PromptSource(path="prompt.py", start_line=2, end_line=9)):
        kept = AgentContract(agent="a", system_prompt_excerpt="quoted", system_prompt_from=[where])
        assert _copy_prompt(kept, tmp_path) and kept.system_prompt_excerpt == "quoted"


def _booking_world(tmp_path, handle: str):
    from fi.alk.harness.world import GeneratedWorld
    from fi.alk.harness.world.snapshot import save

    class W(GeneratedWorld):
        name = "bookings"
        tools = [{"name": "book"}]
        handlers = {"book": handle}

    world = W(":memory:")
    world.connection.executescript("CREATE TABLE bookings(time TEXT);")
    world.connection.commit()
    save(world, tmp_path, notes="test", sequences=[])
    return tmp_path


_NORMALISES = (
    "import datetime\n"
    "def handle(args, db):\n"
    "    for form in ('%Y-%m-%d', '%d %B %Y'):\n"
    "        try:\n"
    "            day = datetime.datetime.strptime(args['time'], form).date().isoformat()\n"
    "            break\n"
    "        except ValueError:\n"
    "            day = None\n"
    "    db.execute('INSERT INTO bookings (time) VALUES (?)', [day])\n"
    "    return {'ok': 1}\n"
)
_AS_GIVEN = "def handle(args, db):\n    db.execute('INSERT INTO bookings (time) VALUES (?)', [args['time']])\n    return {'ok': 1}\n"
_ISO_ONLY = (
    "import datetime\n"
    "def handle(args, db):\n"
    "    datetime.date.fromisoformat(args['time'])\n"
    "    db.execute('INSERT INTO bookings (time) VALUES (?)', [args['time']])\n"
    "    return {'ok': 1}\n"
)


def _date_proof(root, compare: str):
    from fi.alk.harness.scenario import Step

    check = (
        "def check(world, calls, reference):\n"
        "    want = [s['arguments'].get('time') for s in reference if s['tool'] == 'book'][-1]\n"
        "    sent = [c.arguments.get('time') for c in calls if c.name == 'book' and c.ok]\n"
        "    made = [r['time'] for r in world.state()['bookings']]\n"
        f"    return None if made and {compare} else f'done wrong: time expected {{want!r}}'\n"
    )
    solution = [Step(tool="book", arguments={"time": "1984-06-25"}, fixed={"time": "caller: their birthday"})]
    return _proof(root, solution, check)


def test_a_check_that_compares_a_date_as_text_fails_the_date_said_another_way(tmp_path):
    # The world stores both forms as the same day, so a check on the call's text holds the agent to a form.
    root = _booking_world(tmp_path, _NORMALISES)
    assert _date_proof(root, "sent[-1] == want").too_strict == ["right (the same date said another way: book.time)"]
    assert _date_proof(root, "made[-1] == want").too_strict == []


def test_a_date_the_world_stores_as_given_or_refuses_leaves_the_check_free_to_want_its_form(tmp_path):
    as_given = _booking_world(tmp_path / "given", _AS_GIVEN)
    assert _date_proof(as_given, "made[-1] == want").too_strict == []
    iso_only = _booking_world(tmp_path / "iso", _ISO_ONLY)
    assert _date_proof(iso_only, "sent[-1] == want").too_strict == []


def test_a_check_that_wants_the_prefixed_id_the_world_resolves_the_same_is_too_strict(tmp_path):
    from fi.alk.harness.world import GeneratedWorld
    from fi.alk.harness.world.snapshot import save
    from fi.alk.harness.scenario import Step

    class W(GeneratedWorld):
        name = "rides"
        tools = [{"name": "pay"}]
        handlers = {
            "pay": (
                "def handle(args, db):\n"
                "    method = str(args['method']).split(':')[-1]\n"
                "    db.execute('INSERT INTO charges (method) VALUES (?)', [method])\n"
                "    return {'ok': 1}\n"
            )
        }

    world = W(":memory:")
    world.connection.executescript("CREATE TABLE charges(method TEXT);")
    world.connection.commit()
    save(world, tmp_path, notes="test", sequences=[])

    def proof_of(compare: str):
        check = (
            "def check(world, calls, reference):\n"
            "    want = [s['arguments'].get('method') for s in reference if s['tool'] == 'pay'][-1]\n"
            "    paid = [c.arguments.get('method') for c in calls if c.name == 'pay' and c.ok]\n"
            "    made = [r['method'] for r in world.state()['charges']]\n"
            f"    return None if paid and {compare} else 'done wrong: method expected ' + repr(want)\n"
        )
        solution = [Step(tool="pay", arguments={"method": "saved_card:pm_1"}, fixed={"method": "their record"})]
        return _proof(tmp_path, solution, check)

    assert proof_of("paid[-1] == want").too_strict == ["right (the same record named without its prefix: pay.method)"]
    assert proof_of("made[-1] == want.split(':')[-1]").too_strict == []


def test_a_prefixed_id_the_world_stores_as_given_leaves_the_check_free_to_want_its_form(tmp_path):
    from fi.alk.harness.scenario import Step

    root = _booking_world(tmp_path, _AS_GIVEN)
    check = (
        "def check(world, calls, reference):\n"
        "    want = [s['arguments'].get('time') for s in reference if s['tool'] == 'book'][-1]\n"
        "    made = [r['time'] for r in world.state()['bookings']]\n"
        "    return None if made and made[-1] == want else 'done wrong'\n"
    )
    solution = [Step(tool="book", arguments={"time": "slot:9am"}, fixed={"time": "their record"})]
    assert _proof(root, solution, check).too_strict == []


def _claims_world(tmp_path):
    from fi.alk.harness.world import GeneratedWorld
    from fi.alk.harness.world.snapshot import save

    class W(GeneratedWorld):
        name = "claims"
        tools = [{"name": "check_status"}]
        handlers = {
            "check_status": (
                "def handle(args, db):\n"
                "    claim = str(args['claim']).replace('-', '').upper()\n"
                "    db.execute('INSERT INTO lookups (claim) VALUES (?)', [claim])\n"
                "    return {'status': 'open'}\n"
            )
        }

    world = W(":memory:")
    world.connection.executescript("CREATE TABLE lookups(claim TEXT);")
    world.connection.commit()
    save(world, tmp_path, notes="test", sequences=[])
    return tmp_path


def test_a_check_that_compares_a_claim_id_as_text_fails_the_id_typed_without_separators(tmp_path):
    from fi.alk.harness.scenario import Step

    root = _claims_world(tmp_path)

    def proof_of(compare: str):
        check = (
            "def check(world, calls, reference):\n"
            "    want = [s['arguments'].get('claim') for s in reference if s['tool'] == 'check_status'][-1]\n"
            "    sent = [c.arguments.get('claim') for c in calls if c.name == 'check_status' and c.ok]\n"
            f"    return None if sent and {compare} else 'wrong claim: expected ' + repr(want)\n"
        )
        solution = [Step(tool="check_status", arguments={"claim": "CLM-88213"}, fixed={"claim": "their claim"})]
        return _proof(root, solution, check)

    assert proof_of("sent[-1] == want").too_strict == [
        "right (the same id typed without separators: check_status.claim; compare ids with same_id)"
    ]
    assert proof_of("same_id(sent[-1], want)").too_strict == []


def test_same_id_ignores_separators_and_case_but_not_the_number():
    from fi.alk.harness.checks import same_id

    assert same_id("CLM-88213", "CLM88213")
    assert same_id("clm 88213", "CLM-88213")
    assert same_id("ord_1043", "ORD-1043")
    assert not same_id("CLM-88213", "CLM-88214")
    assert not same_id(None, "CLM-88213")


def test_same_id_still_tells_apart_identifiers_that_name_different_records():
    from fi.alk.harness.checks import same_id

    assert not same_id("CLM-88213", "CLA-88213")
    assert not same_id("CLM-88213", "CLM-882130")
    assert not same_id("ORD-01", "ORD-1")
    assert not same_id("INV 1043", "1043")
    assert not same_id("", "")
    assert not same_id("--", " ")
    assert not same_id("CLM-88213", "")


def _raw_claims_world(tmp_path):
    """A world that stores the claim exactly as sent, so an id typed differently is a different record."""
    from fi.alk.harness.world import GeneratedWorld
    from fi.alk.harness.world.snapshot import save

    class W(GeneratedWorld):
        name = "raw_claims"
        tools = [{"name": "check_status"}]
        handlers = {
            "check_status": (
                "def handle(args, db):\n"
                "    db.execute('INSERT INTO lookups (claim) VALUES (?)', [str(args['claim'])])\n"
                "    return {'status': 'open'}\n"
            )
        }

    world = W(":memory:")
    world.connection.executescript("CREATE TABLE lookups(claim TEXT);")
    world.connection.commit()
    save(world, tmp_path, notes="test", sequences=[])
    return tmp_path


def test_an_exact_comparison_is_kept_when_the_world_treats_the_id_typed_differently_as_another_record(tmp_path):
    from fi.alk.harness.scenario import Step

    root = _raw_claims_world(tmp_path)
    check = (
        "def check(world, calls, reference):\n"
        "    want = [s['arguments'].get('claim') for s in reference if s['tool'] == 'check_status'][-1]\n"
        "    sent = [c.arguments.get('claim') for c in calls if c.name == 'check_status' and c.ok]\n"
        "    return None if sent and sent[-1] == want else 'wrong claim: expected ' + repr(want)\n"
    )
    solution = [Step(tool="check_status", arguments={"claim": "CLM-88213"}, fixed={"claim": "their claim"})]
    # The world stores what it was given, so "CLM88213" ends in a different state: the strict check is right.
    assert _proof(root, solution, check).too_strict == []


def test_a_fixed_value_that_is_not_an_identifier_is_never_replayed_without_separators(tmp_path):
    from fi.alk.harness.prove import _unseparated_ids
    from fi.alk.harness.scenario import Step

    scenario = type("S", (), {"solution": [
        Step(tool="note", arguments={"text": "call back after lunch", "date": "2026-07-21", "claim": "CLM-88213"},
             fixed={"text": "x", "date": "y", "claim": "z"}),
    ]})()
    labels = [label for label, _ in _unseparated_ids(scenario)]
    assert labels == ["note.claim"]


def test_the_spoken_date_replay_runs_only_for_a_language_it_can_write():
    from types import SimpleNamespace

    from fi.alk.harness.prove import _said_another_way

    class _Step(SimpleNamespace):
        def model_copy(self, update):
            return _Step(**{**vars(self), **update})

    step = _Step(tool="book", arguments={"date": "1984-06-25"}, fixed=["date"], environment_arguments=None)

    def scenario(languages):
        return SimpleNamespace(solution=[step], persona={"languages": languages})

    # A language whose spoken form the proof does not write: nothing is said another way, so nothing is called
    # too strict on the strength of an English month name.
    assert _said_another_way(scenario(["Hindi"])) == ([step], set())
    _, changed = _said_another_way(scenario(["English"]))
    assert changed == {"book.date"}
    _, changed = _said_another_way(scenario([]))
    assert changed == {"book.date"}
