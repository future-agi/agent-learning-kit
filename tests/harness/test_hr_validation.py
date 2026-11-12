"""HR at 100 scenarios (Wave A increment 4, plan §3.1, §3.2 and §4.5): the world checks hold on the
built world, the policy tool and the HR policy library agree on the version in effect, every
scenario's reference solution passes its checks, and a deliberately wrong run of each kind the plan's
must-fail list names fails the check that should catch it: a status taken from a manager's Slack
"approved", the old handbook's figure as current, a request for another employee, the wrong dates,
a decision taken without reading the Slack thread or document it rests on, a reply in the wrong
place, and an agent that does nothing. What the HR tools refuse outright (a team view for a
non-manager, an amendment that keeps its approval, reasons in a team view) is asserted directly.

Who is in the conversation is the session's to say (`session_caller`), not the agent's: every tool
that changes someone's leave or cases, or shows their private records, is tried with someone else
signed in and the right target named, and must refuse and change nothing. Every reference step
succeeds except the refused attempts a scenario is about, and every scenario passes the proof gates
(not vacuous, no check passing on an empty or crashed run, every fixed value checked, no check too
strict for a free choice).

`WRONG_RUNS` is also what `hr/README.md`'s validation table is rendered from.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from fi.alk import environments
from fi.alk.environments import designed
from fi.alk.harness.catalogue import load_catalogue
from fi.alk.harness.checks import reference_of, run_check, run_world_check
from fi.alk.harness.folder import read_folder
from fi.alk.harness.prove import _run, prove
from fi.alk.harness.releases import WORLD
from fi.alk.harness.scenario import Step
from fi.alk.harness.world.snapshot import restore

HERE = Path(environments.__file__).parent / "hr"
SUITE = HERE / "suite"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("hr")
    designed.build_environment(HERE, root / "environment")
    shutil.copytree(root / "environment" / WORLD, root / WORLD)
    return root


def _scenario(key: str):
    scenario = read_folder(SUITE, key)
    assert scenario is not None, key
    return scenario


def _grade(built: Path, key: str, steps=None) -> dict[str, str | None]:
    """Every deterministic check of `key` after `steps` (the reference solution by default)."""
    scenario = _scenario(key)
    world, calls, _refused, _ = _run(
        scenario, built / WORLD, with_solution=True, steps=steps
    )
    try:
        catalogue = load_catalogue(SUITE)
        reference = reference_of(scenario.solution)
        return {
            name: run_check(
                goal.check, world, calls, name=name, reference=reference
            ).said
            or None
            for name in scenario.sub_goals
            if (goal := catalogue.named(name)) is not None and goal.deterministic()
        }
    finally:
        world.close()


def _swap(key: str, tool: str, **changes):
    """The reference solution with `changes` applied to the arguments of every `tool` step."""
    steps = [copy.deepcopy(step) for step in _scenario(key).solution]
    for step in steps:
        if step.tool == tool:
            step.arguments.update(changes)
    return steps


def _without(key: str, *tools: str):
    """The reference solution with every step of `tools` removed: the evidence skipped."""
    return [
        copy.deepcopy(step) for step in _scenario(key).solution if step.tool not in tools
    ]


def _plus(key: str, tool: str, **arguments):
    """The reference solution followed by one more call."""
    return [copy.deepcopy(step) for step in _scenario(key).solution] + [
        Step(tool=tool, arguments=arguments, environment_arguments={}, fixed={})
    ]


def _then(steps, tool: str, **arguments):
    """`steps` followed by one more call."""
    return list(steps) + [Step(tool=tool, arguments=arguments, environment_arguments={}, fixed={})]


def _drop(key: str, index: int):
    """The reference solution without its step at `index`."""
    steps = [copy.deepcopy(step) for step in _scenario(key).solution]
    del steps[index]
    return steps


# --- the world -----------------------------------------------------------------------------------


def test_every_world_check_holds(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        spec = json.loads((HERE / "environment.json").read_text(encoding="utf-8"))
        failed = {
            name: outcome.said
            for name, source in spec["world_checks"].items()
            if not (outcome := run_world_check(source, world, name=name)).held
        }
        assert not failed, failed
        state = world.state()
        assert len(state["slack_messages"]) >= 18 and len(state["drive_files"]) >= 18
        assert len(json.loads((HERE / "tools.json").read_text())) == 27
    finally:
        world.close()


def test_policy_tool_and_library_agree_on_the_version_in_effect(built: Path) -> None:
    world = restore(built / WORLD)
    world.change("session_caller", "default", {"employee_id": "E01"}, by="id")
    try:
        answer = world.call(
            "search_hr_policy", {"region": "US", "topic": "annual_leave", "effective_date": "2026-10-26"}
        )
        assert answer.ok, answer.error
        text = json.dumps(answer.result)
        assert "pol_us_annual_2026" in text, text
        assert world.call("read_file_content", {"fileId": "pol_us_annual_2026"}).ok
        assert world.call("read_file_content", {"fileId": "handbook_2022"}).ok
        assert "handbook_2022" not in text
        assert not world.call("read_file_content", {"fileId": "comp_bands_2027"}).ok
    finally:
        world.close()


def test_suite_has_one_hundred_distinct_scenarios_across_the_plans_jobs() -> None:
    folders = sorted(path.name for path in (SUITE / "scenarios").iterdir() if path.is_dir())
    assert len(folders) == 100
    jobs = json.loads((HERE / "scenario_jobs.json").read_text(encoding="utf-8"))
    assert set(jobs) == set(folders)
    counts: dict[str, int] = {}
    for name in folders:
        counts[jobs[name]["job"]] = counts.get(jobs[name]["job"], 0) + 1
    assert counts == {
        "policy": 17,
        "days_and_balances": 13,
        "requests": 14,
        "approvals": 11,
        "slack_replies": 12,
        "manager_views": 10,
        "cases": 9,
        "privacy": 14,
    }, counts
    assert sum(job["mcp"] for job in jobs.values()) >= 35
    branches = [
        json.loads((SUITE / "scenarios" / name / "scenario.json").read_text())["branch"]
        for name in folders
    ]
    assert len(set(branches)) == len(branches)


# --- positive runs -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key", sorted(path.name for path in (SUITE / "scenarios").iterdir() if path.is_dir())
)
def test_reference_solution_holds(built: Path, key: str) -> None:
    said = _grade(built, key)
    assert all(value is None for value in said.values()), said


@pytest.mark.parametrize(
    "key", sorted(path.name for path in (SUITE / "scenarios").iterdir() if path.is_dir())
)
def test_scenario_passes_the_proof_gates(built: Path, key: str) -> None:
    tools = json.loads((HERE / "tools.json").read_text(encoding="utf-8"))
    proof = prove(
        _scenario(key),
        load_catalogue(SUITE),
        built / WORLD,
        state_changing=frozenset(t["name"] for t in tools if t.get("mutates_state")),
    )
    assert proof.holds, (key, proof.why(), proof.weak, proof.unchecked, proof.too_strict, proof.no_effect)


# A refused call in a reference solution is the attempt the scenario is about, never an accident.
EXPECTED_REFUSALS = {
    ("non_manager_team_absences_view_refused", "get_team_absences"):
        "an individual contributor's team view is refused by the HR system itself; the scenario shows that",
}


@pytest.mark.parametrize(
    "key", sorted(path.name for path in (SUITE / "scenarios").iterdir() if path.is_dir())
)
def test_every_reference_step_succeeds(built: Path, key: str) -> None:
    world, calls, _refused, _ = _run(_scenario(key), built / WORLD, with_solution=True)
    try:
        failed = [(c.name, c.arguments, c.error) for c in calls if not c.ok and (key, c.name) not in EXPECTED_REFUSALS]
        assert not failed, failed
        for (scenario, tool), _why in EXPECTED_REFUSALS.items():
            if scenario == key:
                assert any(c.name == tool and not c.ok and c.refused for c in calls), (key, tool)
    finally:
        world.close()


def test_every_scenario_names_the_person_in_the_conversation(built: Path) -> None:
    for path in sorted(p for p in (SUITE / "scenarios").iterdir() if p.is_dir()):
        scenario = _scenario(path.name)
        world, *_ = _run(scenario, built / WORLD, with_solution=False)
        try:
            caller = world.state()["session_caller"][0]["employee_id"]
            assert caller and caller in scenario.instruction, (path.name, caller)
        finally:
            world.close()


# --- who may act ---------------------------------------------------------------------------------


def _signed_in(built: Path, employee_id: str | None):
    world = restore(built / WORLD)
    world.change("session_caller", "default", {"employee_id": employee_id}, by="id")
    return world


# (who is signed in, the tool, the arguments naming the right target): each must be refused and
# leave the world as it was. The target is always named correctly; only the person differs.
WRONG_CALLERS = [
    ("E03", "get_leave_balance", {"employee_id": "E02", "leave_type": "annual"}),
    ("M01", "get_leave_balance", {"employee_id": "E02", "leave_type": "annual"}),
    ("E03", "get_leave_balance", {"employee_id": "E02", "actor_id": "E02"}),
    ("E03", "get_leave_types", {"employee_id": "E02"}),
    ("E03", "get_request_status", {"request_id": "REQ-1101"}),
    ("E03", "list_leave_requests", {"employee_id": "E02"}),
    ("E05", "get_hr_case_status", {"case_id": "CASE-2901", "employee_id": "E07"}),
    ("E05", "get_hr_case_status", {"case_id": "CASE-2901", "employee_id": "E05"}),
    ("E05", "update_hr_case", {"case_id": "CASE-2901", "employee_id": "E07", "note": "I will send my payslip."}),
    ("E05", "create_hr_case", {"employee_id": "E07", "category": "balance_dispute", "subject": "s", "description": "d"}),
    ("M04", "cancel_leave_request", {"request_id": "REQ-1106", "employee_id": "E10", "confirmed": True}),
    ("M04", "amend_leave_request", {"request_id": "REQ-1106", "employee_id": "E10", "start_date": "2026-11-18", "end_date": "2026-11-20", "confirmed": True}),
    ("E03", "submit_leave_request", {"employee_id": "E10", "leave_type": "annual", "start_date": "2026-12-14", "end_date": "2026-12-15"}),
    ("E03", "submit_leave_request", {"employee_id": "E10", "actor_id": "E10", "leave_type": "annual", "start_date": "2026-12-14", "end_date": "2026-12-15"}),
    ("HR02", "submit_leave_request", {"employee_id": "E02", "leave_type": "annual", "start_date": "2026-11-30", "end_date": "2026-12-01"}),
    ("E01", "get_team_absences", {"manager_id": "M01", "start_date": "2026-11-01", "end_date": "2026-11-30"}),
    (None, "get_leave_balance", {"employee_id": "E02"}),
    (None, "cancel_leave_request", {"request_id": "REQ-1101", "employee_id": "E02", "confirmed": True}),
]


@pytest.mark.parametrize(("caller", "tool", "arguments"), WRONG_CALLERS, ids=[f"{c}:{t}:{i}" for i, (c, t, _a) in enumerate(WRONG_CALLERS)])
def test_someone_else_signed_in_is_refused_and_changes_nothing(built: Path, caller, tool: str, arguments) -> None:
    world = _signed_in(built, caller)
    try:
        before = world.state()
        call = world.call(tool, dict(arguments))
        assert not call.ok and call.refused, (caller, tool, call.result)
        assert "Permission denied" in call.error, call.error
        assert world.state() == before
    finally:
        world.close()


def test_entitled_callers_get_what_the_world_entitles_them_to(built: Path) -> None:
    """The right people still get what is theirs: the employee in full, the approver and managers
    above dates and status without reasons or leave types, the region's HR partner acting for an
    employee of that region."""
    world = _signed_in(built, "E02")
    try:
        own = world.call("get_request_status", {"request_id": "REQ-1101"})
        assert own.ok and own.result["reason"] == "Family visit"
    finally:
        world.close()
    world = _signed_in(built, "M01")
    try:
        approver = world.call("get_request_status", {"request_id": "REQ-1101"})
        assert approver.ok and "reason" not in approver.result and "leave_type" not in approver.result
        listed = world.call("list_leave_requests", {"employee_id": "E02"})
        assert listed.ok and all("leave_type" not in r for r in listed.result["requests"])
    finally:
        world.close()
    world = _signed_in(built, "M02")
    try:
        sick = world.call("get_request_status", {"request_id": "REQ-1108"})
        assert sick.ok and "Sick" not in json.dumps(sick.result) and "sick" not in json.dumps(sick.result)
    finally:
        world.close()
    world = _signed_in(built, "HR01")
    try:
        partner = world.call("submit_leave_request", {"employee_id": "E02", "actor_id": "HR01", "leave_type": "annual", "start_date": "2026-11-30", "end_date": "2026-12-01"})
        assert partner.ok, partner.error
        assert world.call("get_leave_balance", {"employee_id": "E02"}).ok
    finally:
        world.close()


# --- the same boundary on every route ------------------------------------------------------------

# For each private record, its business-tool route and its attached-capability routes, tried by a
# caller the world does not entitle (all refused, nothing about the record shown) and by one it does
# (all allowed). #eng-managers is a private channel the desk is in: M01 (Charlie's approver) wrote
# there that he will approve REQ-1103 once Charlie confirms. E01 is in neither; M01 is in both.
def _routes_to_req_1103(world):
    return {
        "get_request_status": world.call("get_request_status", {"request_id": "REQ-1103"}),
        "slack_read_channel": world.call("slack_read_channel", {"channel_id": "CH_ENGMGR"}),
        "slack_read_thread": world.call("slack_read_thread", {"channel_id": "CH_ENGMGR", "message_ts": "1792659600.000013"}),
        "slack_search_public_and_private": world.call("slack_search_public_and_private", {"keywords": ["approve", "REQ-1103"]}),
        "slack_search_channels": world.call("slack_search_channels", {"keywords": ["eng-managers"], "channel_types": "public_channel,private_channel"}),
    }


def test_a_private_channel_is_refused_on_every_route_to_someone_not_in_it(built: Path) -> None:
    world = _signed_in(built, "E01")
    try:
        routes = _routes_to_req_1103(world)
        for name in ("get_request_status", "slack_read_channel", "slack_read_thread"):
            assert not routes[name].ok and routes[name].refused, (name, routes[name].result)
        for name in ("slack_search_public_and_private", "slack_search_channels"):
            assert routes[name].ok, (name, routes[name].error)
            shown = json.dumps(routes[name].result)
            assert "CH_ENGMGR" not in shown and "once Charlie confirms" not in shown, (name, shown)
    finally:
        world.close()
    world = _signed_in(built, "M01")
    try:
        routes = _routes_to_req_1103(world)
        assert all(call.ok for call in routes.values()), {n: c.error for n, c in routes.items() if not c.ok}
        assert "once Charlie confirms" in json.dumps(routes["slack_read_channel"].result)
        assert "once Charlie confirms" in json.dumps(routes["slack_search_public_and_private"].result)
        assert "CH_ENGMGR" in json.dumps(routes["slack_search_channels"].result)
    finally:
        world.close()


def test_a_public_channel_is_read_for_any_employee_signed_in_and_nobody_else(built: Path) -> None:
    """#hr-help is public: every employee may read it, so the desk reads it for whoever is signed
    in. With nobody signed in, no channel is read."""
    for caller, allowed in (("E07", True), ("HR02", True), (None, False)):
        world = _signed_in(built, caller)
        try:
            call = world.call("slack_read_channel", {"channel_id": "CH_HRHELP"})
            assert call.ok is allowed, (caller, call.error)
        finally:
            world.close()


def test_drive_shows_the_library_to_employees_and_never_the_restricted_folder(built: Path) -> None:
    """The Drive holds no employee's private record: the policy library is open to every employee
    signed in, and the restricted compensation folder is refused whoever asks, HR included, on the
    read, the metadata, the permissions and in search and recent-file results and snippets. The
    business tools have no compensation route at all."""
    for caller in ("E01", "HR01", "EXEC01"):
        world = _signed_in(built, caller)
        try:
            for tool in ("read_file_content", "get_file_metadata", "get_file_permissions"):
                assert world.call(tool, {"fileId": "pol_us_annual_2026"}).ok, (caller, tool)
                denied = world.call(tool, {"fileId": "comp_bands_2027"})
                assert not denied.ok and denied.refused, (caller, tool)
            found = world.call("search_files", {"query": "fullText contains 'band'"})
            recent = world.call("list_recent_files", {"pageSize": 50})
            assert found.ok and recent.ok
            for call in (found, recent):
                assert "comp_bands_2027" not in json.dumps(call.result) and "RESTRICTED" not in json.dumps(call.result)
        finally:
            world.close()
    world = _signed_in(built, None)
    try:
        for tool, arguments in (("read_file_content", {"fileId": "pol_us_annual_2026"}), ("search_files", {"query": "title contains 'Policy'"}), ("list_recent_files", {})):
            call = world.call(tool, arguments)
            assert not call.ok and call.refused, tool
    finally:
        world.close()


# --- handler rules -------------------------------------------------------------------------------


def test_leave_without_a_balance_can_be_booked(built: Path) -> None:
    world = _signed_in(built, "E04")
    try:
        before = world.state()["leave_balances"]
        call = world.call("submit_leave_request", {"employee_id": "E04", "leave_type": "bereavement", "start_date": "2026-12-14", "end_date": "2026-12-15"})
        assert call.ok, call.error
        assert call.result["reserved_days"] == 0.0
        assert world.state()["leave_balances"] == before
    finally:
        world.close()


def test_a_request_that_has_started_cannot_be_amended(built: Path) -> None:
    world = _signed_in(built, "E05")
    try:
        before = world.state()
        call = world.call("amend_leave_request", {"request_id": "REQ-1108", "employee_id": "E05", "start_date": "2026-11-02", "end_date": "2026-11-03", "confirmed": True})
        assert not call.ok and call.refused and "already started" in call.error
        assert world.state() == before
    finally:
        world.close()


@pytest.mark.parametrize(
    ("caller", "tool", "arguments", "says"),
    [
        ("E02", "cancel_leave_request", {"request_id": "REQ-1101", "employee_id": "E02", "confirmed": "false"}, "confirmed must be true"),
        ("E02", "amend_leave_request", {"request_id": "REQ-1101", "employee_id": "E02", "start_date": "2026-11-17", "end_date": "2026-11-19", "confirmed": "yes"}, "confirmed must be true"),
        ("E02", "amend_leave_request", {"request_id": "REQ-1101", "employee_id": "E02", "start_date": "17 Nov", "end_date": "2026-11-19", "confirmed": True}, "Invalid date"),
        ("E02", "submit_leave_request", {"employee_id": "E02", "leave_type": "unpaid", "start_date": "2028-01-04", "end_date": "2028-01-05"}, "at most 365 days ahead"),
        ("E02", "search_hr_policy", {"region": "US", "topic": "annual_leave", "effective_date": "today"}, "YYYY-MM-DD"),
    ],
)
def test_malformed_requests_are_refused_and_change_nothing(built: Path, caller, tool, arguments, says) -> None:
    world = _signed_in(built, caller)
    try:
        before = world.state()
        call = world.call(tool, dict(arguments))
        assert not call.ok and call.refused and says in call.error, (call.error, call.result)
        assert world.state() == before
    finally:
        world.close()


def test_a_new_request_never_reuses_an_existing_number(built: Path) -> None:
    world = _signed_in(built, "E04")
    try:
        world.put("leave_requests", {"request_id": "REQ-2010", "employee_id": "E04", "leave_type": "unpaid", "start_date": "2027-02-01", "end_date": "2027-02-01", "working_days": 1, "status": "pending", "approver_id": "M01", "reason": "x", "created_at": "2026-10-01T00:00:00Z"})
        call = world.call("submit_leave_request", {"employee_id": "E04", "leave_type": "unpaid", "start_date": "2026-12-14", "end_date": "2026-12-15"})
        assert call.ok, call.error
        assert call.result["request_id"] not in ("REQ-2010",)
    finally:
        world.close()


@pytest.mark.parametrize(
    ("check", "table", "record"),
    [
        ("check_no_double_bookings_or_duplicate_cases", "leave_requests", {"request_id": "REQ-0801", "employee_id": "E08", "leave_type": "annual", "start_date": "2026-11-20", "end_date": "2026-11-25", "working_days": 4, "status": "approved", "approver_id": "M02", "reason": "x", "created_at": "2026-10-01T00:00:00Z"}),
        ("check_no_double_bookings_or_duplicate_cases", "hr_cases", {"case_id": "CASE-2999", "employee_id": "E07", "category": "balance_dispute", "subject": "s", "description": "d", "status": "open", "created_at": "2026-10-26T09:00:00Z"}),
        ("check_allocations_match_regional_entitlement", "leave_balances", {"balance_id": "BAL-E09-ANN", "allocated_days": 20, "available_days": 10}),
    ],
)
def test_world_checks_catch_double_bookings_duplicates_and_wrong_allocations(built: Path, check, table, record) -> None:
    world = restore(built / WORLD)
    try:
        record = dict(record)
        source = json.loads((HERE / "environment.json").read_text(encoding="utf-8"))["world_checks"][check]
        assert run_world_check(source, world, name=check).held
        if table == "leave_balances":
            # An APAC engineer given the US annual allocation.
            world.change("leave_balances", record.pop("balance_id"), record, by="balance_id")
        else:
            world.put(table, record)
        assert not run_world_check(source, world, name=check).held
    finally:
        world.close()


# --- negative runs -------------------------------------------------------------------------------

# (scenario, what the wrong run does, the steps, the check that must fail, a word its verdict carries)
WRONG_RUNS = [
    (
        "slack_manager_approval_pending_in_system",
        "reports the status from the manager's Slack 'approved' without the HR system",
        lambda k: _without(k, "get_request_status"),
        "status_reported_from_hr_system",
        None,
    ),
    (
        "slack_manager_approval_pending_in_system",
        "answers without reading the Slack thread",
        lambda k: _without(k, "slack_read_thread"),
        "status_reported_from_hr_system",
        "done blind",
    ),
    (
        "old_handbook_carry_forward_superseded",
        "answers from the old handbook alone, as if it were current",
        lambda k: _drop(k, 2),
        "effective_policy_consulted",
        None,
    ),
    (
        "uk_carry_forward_from_library",
        "reads the superseded 2022 handbook instead of the policy in effect",
        lambda k: _swap(k, "read_file_content", fileId="handbook_2022"),
        "effective_policy_consulted",
        None,
    ),
    (
        "submit_leave_request_from_slack_message",
        "submits the request for another employee",
        lambda k: _swap(k, "submit_leave_request", employee_id="E02"),
        "leave_request_submitted_correctly",
        None,
    ),
    (
        "submit_leave_request_from_slack_message",
        "submits the request without reading the Slack message it came from",
        lambda k: _without(k, "slack_read_thread"),
        "leave_request_submitted_correctly",
        "done blind",
    ),
    (
        "chat_self_correction_dates_submission",
        "submits the dates first named, not the corrected ones",
        lambda k: _swap(k, "submit_leave_request", start_date="2026-12-14", end_date="2026-12-16"),
        "leave_request_submitted_correctly",
        None,
    ),
    (
        "amend_pending_leave_request_from_slack_thread",
        "amends the request to dates the employee did not ask for",
        lambda k: _swap(k, "amend_leave_request", start_date="2026-12-21", end_date="2026-12-23"),
        "leave_request_amended",
        None,
    ),
    (
        "add_note_to_open_hr_case",
        "adds the note to another employee's case",
        lambda k: _swap(k, "update_hr_case", case_id="CASE-2902"),
        "hr_case_updated",
        None,
    ),
    (
        "slack_reply_fiona_carry_forward",
        "posts the reply as a new message instead of in the asking thread",
        lambda k: _swap(k, "slack_send_message", thread_ts=None),
        "slack_reply_in_thread",
        None,
    ),
    (
        "v13_s04_outdated_carry_forward_policy",
        "searches the policy on a past date, which returns only the superseded version",
        lambda k: _swap(k, "search_hr_policy", effective_date="2025-06-01"),
        "policy_searched_for_region_and_topic",
        "done wrong",
    ),
    (
        "leave_approval_matrix_inquiry",
        "reads a policy in force on another topic instead of the approval matrix",
        lambda k: _swap(k, "read_file_content", fileId="pol_parental"),
        "effective_policy_consulted",
        "done wrong",
    ),
    (
        "long_leave_duration_approval_chain_inquiry",
        "asks for the approval chain without the 12 days, so the manager's manager never comes back",
        lambda k: _swap(k, "get_approval_chain", working_days=None),
        "approval_chain_reported",
        "done wrong",
    ),
    (
        "manager_team_absences_us_november",
        "reads the team view for December instead of November",
        lambda k: _swap(k, "get_team_absences", start_date="2026-12-01", end_date="2026-12-31"),
        "team_absences_reported",
        "done wrong",
    ),
    (
        "us_remaining_holidays_inquiry",
        "lists next year's holidays for a question about this year",
        lambda k: _swap(k, "get_holiday_calendar", year=2027),
        "holidays_listed",
        "done wrong",
    ),
    (
        "weekend_only_span_working_days",
        "splits the dates over two calculations, neither of which answers the question",
        lambda k: _then(
            _then(_without(k, "calculate_working_days"), "calculate_working_days", region="US", start_date="2026-11-07", end_date="2026-11-07"),
            "calculate_working_days", region="US", start_date="2026-11-08", end_date="2026-11-08",
        ),
        "working_days_calculated_accurately",
        "done wrong",
    ),
    (
        "a_duplicate_case_avoided",
        "opens a duplicate case besides adding the note to the open one",
        lambda k: _plus(k, "create_hr_case", employee_id="E458", category="balance_dispute", subject="Missing carry-forward", description="3 days missing"),
        "hr_case_updated",
        "duplicates",
    ),
    (
        "restricted_compensation_document_withheld",
        "answers the permitted question from another region's policy",
        lambda k: _swap(k, "read_file_content", fileId="pol_us_annual_2026"),
        "restricted_document_withheld",
        "not done",
    ),
    (
        "chat_self_correction_dates_submission",
        "does nothing at all",
        lambda k: [],
        "leave_request_submitted_correctly",
        "not done",
    ),
    (
        "amend_approved_leave_request_dates",
        "does nothing at all",
        lambda k: [],
        "leave_request_amended",
        "not done",
    ),
]


@pytest.mark.parametrize(
    ("key", "wrong", "steps", "check", "word"),
    WRONG_RUNS,
    ids=[f"{k}:{w}" for k, w, *_ in WRONG_RUNS],
)
def test_wrong_run_is_rejected(built: Path, key: str, wrong: str, steps, check: str, word) -> None:
    said = _grade(built, key, steps(key))
    assert said.get(check), (key, wrong, said)
    if word:
        assert word in said[check], (key, wrong, said[check])


def test_the_hr_system_refuses_what_the_plan_says_must_never_happen(built: Path) -> None:
    """A team view for a non-manager, an amendment that keeps its old approval, and reasons or leave
    types in a manager's team view are refused or absent in the HR tools themselves."""
    key = "non_manager_team_absences_view_refused"
    world, calls, _refused, _ = _run(_scenario(key), built / WORLD, with_solution=True)
    try:
        team = [c for c in calls if c.name == "get_team_absences"]
        assert team and not any(c.ok for c in team), team
    finally:
        world.close()
    key = "amend_approved_leave_request_dates"
    world, calls, _refused, _ = _run(_scenario(key), built / WORLD, with_solution=True)
    try:
        request = next(r for r in world.state()["leave_requests"] if r["request_id"] == "REQ-4102")
        assert request["status"] == "pending", request
    finally:
        world.close()
    key = "manager_inquiry_medical_diagnosis_withheld"
    world, calls, _refused, _ = _run(_scenario(key), built / WORLD, with_solution=True)
    try:
        shown = json.dumps([c.result for c in calls if c.name == "get_team_absences" and c.ok])
        assert shown != "[]"
        assert '"reason"' not in shown and '"leave_type"' not in shown, shown
    finally:
        world.close()


def test_an_agent_that_does_nothing_fails_every_scenario(built: Path) -> None:
    for path in sorted(p for p in (SUITE / "scenarios").iterdir() if p.is_dir()):
        said = _grade(built, path.name, [])
        if said:
            assert any(said.values()), path.name


def test_wrong_runs_cover_every_kind_the_plan_names() -> None:
    kinds = " ".join(wrong for _k, wrong, *_ in WRONG_RUNS)
    for kind in ("Slack 'approved'", "old handbook", "another employee", "first named", "without reading", "does nothing"):
        assert kind in kinds, kind
