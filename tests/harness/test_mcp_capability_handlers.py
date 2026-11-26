"""Handler behaviour of the MCP capabilities that matching the official interface changed
(review of #239): a start-only update keeps the moment it names and the event's duration,
replacing a non-last attendee does not reuse a surviving attendee id, and a drafts search
applies its operators instead of dropping them. Calendar times are compared, stored and returned
in UTC whatever offset they were given with, an approval covers one change, and the capabilities'
policy parameters are enforced."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from fi.alk import environments
from fi.alk.environments import designed
from fi.alk.harness.checks import run_world_check
from fi.alk.harness.releases import WORLD
from fi.alk.harness.world.snapshot import restore

ROOT = Path(environments.__file__).parent


def _world(package: str, tmp_path: Path):
    designed.build_environment(ROOT / package, tmp_path / "environment")
    shutil.copytree(tmp_path / "environment" / WORLD, tmp_path / WORLD)
    return restore(tmp_path / WORLD)


def _ok(world, tool: str, **arguments):
    call = world.call(tool, arguments)
    assert call.ok, (tool, call.error)
    return call.result


def _approve(world, operation: str, target_id: str, **fields) -> str:
    """A fresh approval of `operation` on `target_id` by the acting user."""
    approval = f"AP-{len(world.state()['calendar_approvals']) + 1}"
    world.put(
        "calendar_approvals",
        {
            "approval_id": approval,
            "actor_id": world.state()["current_actor"][0]["user_id"],
            "operation": operation,
            "target_id": target_id,
            "payload_hash": "x",
            "status": "approved",
            "created_at": "2026-10-26T08:00:00Z",
            "expires_at": "2026-12-31T08:00:00Z",
            **fields,
        },
    )
    return approval


# Package -> a calendar it may write and who must be in the conversation.
CALENDARS = {
    "calendar_scheduling_desk": ("CAL-PRIMARY", None),
    "clinic_scheduling": ("CAL-DESK", None),
    "service_scheduling": ("CAL-DESK", {"desk_id": "DS01"}),
    "sales_intake": ("CAL-SALESDESK", {"person_id": "U_A03"}),
}


def _calendar_world(package: str, tmp_path: Path):
    world = _world(package, tmp_path)
    calendar, caller = CALENDARS[package]
    if caller:
        world.change("session_caller", "default", caller, by="id")
    return world, calendar


def _create(world, calendar: str, start: str, end: str, **arguments):
    return world.call(
        "create_event",
        {
            "calendarId": calendar,
            "summary": "Sync",
            "startTime": start,
            "endTime": end,
            "approval_id": _approve(world, "create_event", calendar),
            **arguments,
        },
    )


@pytest.mark.parametrize("package", CALENDARS)
def test_calendar_compares_times_as_moments(package: str, tmp_path: Path) -> None:
    world, calendar = _calendar_world(package, tmp_path)
    who = [{"email": "bob@company.com"}]
    made = _create(
        world,
        calendar,
        "2026-12-01T09:00:00-05:00",
        "2026-12-01T15:00:00Z",
        attendees=who,
    )
    assert made.ok, made.error  # mixed offsets, one hour
    assert made.result["start"]["dateTime"] == "2026-12-01T14:00:00Z"
    assert made.result["end"]["dateTime"] == "2026-12-01T15:00:00Z"

    # The same hour, however it is written, is taken: on the calendar and for the attendee.
    for start, end in (
        ("2026-12-01T14:00:00Z", "2026-12-01T15:00:00Z"),
        ("2026-12-01T19:30:00+05:30", "2026-12-01T20:30:00+05:30"),
    ):
        again = _create(world, calendar, start, end)
        assert not again.ok and "conflict" in again.error, again.result
    # Read as text these run backwards or overlap; as moments they are valid and free.
    for start, end in (
        ("2026-12-03T20:00:00+05:30", "2026-12-03T15:00:00Z"),
        ("2026-12-01T10:00:00-05:00", "2026-12-01T11:00:00-05:00"),
    ):
        free = _create(world, calendar, start, end)
        assert free.ok, free.error
    backwards = _create(
        world, calendar, "2026-12-04T09:00:00-05:00", "2026-12-04T13:00:00Z"
    )
    assert not backwards.ok and "before endTime" in backwards.error

    window = {"calendarId": calendar}
    for start, end in (
        ("2026-12-01T13:30:00Z", "2026-12-01T14:30:00Z"),
        ("2026-12-01T08:30:00-05:00", "2026-12-01T09:30:00-05:00"),
        ("2026-12-01T19:00:00+05:30", "2026-12-01T14:30:00Z"),
    ):
        listed = _ok(world, "list_events", **window, startTime=start, endTime=end)
        assert [e["id"] for e in listed["events"]] == [made.result["id"]], (start, end)
    everything = _ok(world, "list_events", **window, startTime="2026-12-01T00:00:00Z")
    starts = [e["start"]["dateTime"] for e in everything["events"]]
    assert starts == sorted(starts) and len(starts) == 3

    for start, end in (
        ("2026-12-01T08:00:00-05:00", "2026-12-01T11:00:00-05:00"),
        ("2026-12-01T13:00:00Z", "2026-12-01T16:00:00Z"),
        ("2026-12-01T08:00:00", "2026-12-01T16:00:00Z"),  # no offset: local to timeZone
    ):
        slots = _ok(
            world,
            "suggest_time",
            attendeeEmails=["bob@company.com"],
            startTime=start,
            endTime=end,
            timeZone="America/New_York",
        )["timeSlots"]
        assert [s["start"]["dateTime"] for s in slots] == [
            "2026-12-01T13:00:00Z",
            "2026-12-01T15:30:00Z",
        ], (start, end)

    # A time without an offset is local to the time zone the call gives.
    local = _create(
        world,
        calendar,
        "2026-12-07T09:00:00",
        "2026-12-07T10:00:00",
        timeZone="America/New_York",
    )
    assert local.ok and local.result["start"]["dateTime"] == "2026-12-07T14:00:00Z", (
        local.error
    )
    for bad in ("2026-13-07T09:00:00Z", "2026-12-07T09:00:00"):
        refused = _create(
            world, calendar, bad, "2026-12-07T23:00:00Z", timeZone="Nowhere/Else"
        )
        assert not refused.ok and "invalid_input" in refused.error


@pytest.mark.parametrize("package", CALENDARS)
def test_calendar_all_day_dates_are_kept(package: str, tmp_path: Path) -> None:
    world, calendar = _calendar_world(package, tmp_path)
    world.put(
        "calendar_events",
        {
            "event_id": "EVT-DAY",
            "calendar_id": calendar,
            "summary": "Offsite",
            "start_time": "2026-12-09",
            "end_time": "2026-12-10",
            "time_zone": "America/New_York",
            "status": "confirmed",
            "version": 1,
            "created_at": "2026-10-26T09:00:00Z",
            "updated_at": "2026-10-26T09:00:00Z",
        },
    )
    taken = _create(
        world, calendar, "2026-12-09T23:00:00-05:00", "2026-12-09T23:30:00-05:00"
    )
    assert not taken.ok and "EVT-DAY" in taken.error
    free = _create(world, calendar, "2026-12-10T05:00:00Z", "2026-12-10T05:30:00Z")
    assert free.ok, free.error
    renamed = _ok(
        world,
        "update_event",
        calendarId=calendar,
        eventId="EVT-DAY",
        summary="Team offsite",
        expected_version=1,
        approval_id=_approve(world, "update_event", "EVT-DAY"),
    )
    assert (renamed["start"]["dateTime"], renamed["end"]["dateTime"]) == (
        "2026-12-09",
        "2026-12-10",
    )


@pytest.mark.parametrize("package", CALENDARS)
def test_calendar_approval_covers_one_change(package: str, tmp_path: Path) -> None:
    world, calendar = _calendar_world(package, tmp_path)
    event = _create(
        world, calendar, "2026-12-01T14:00:00Z", "2026-12-01T15:00:00Z"
    ).result["id"]
    other = _create(
        world, calendar, "2026-12-02T14:00:00Z", "2026-12-02T15:00:00Z"
    ).result["id"]
    changes = (
        (
            "create_event",
            calendar,
            {
                "calendarId": calendar,
                "summary": "x",
                "startTime": "2026-12-03T14:00:00Z",
                "endTime": "2026-12-03T15:00:00Z",
            },
        ),
        (
            "update_event",
            event,
            {
                "calendarId": calendar,
                "eventId": event,
                "summary": "y",
                "expected_version": 1,
            },
        ),
        ("delete_event", event, {"calendarId": calendar, "eventId": event}),
    )
    for operation, target, arguments in changes:
        for wrong in (
            {
                "operation": "create_event"
                if operation != "create_event"
                else "delete_event"
            },
            {"target_id": other},
            {"target_id": None},
            {"actor_id": "someone.else@example.com"},
            {"expires_at": "2026-10-01T00:00:00Z"},
            {"status": "pending"},
        ):
            approval = _approve(
                world, **{"operation": operation, "target_id": target, **wrong}
            )
            before = world.state()
            refused = world.call(operation, {**arguments, "approval_id": approval})
            assert not refused.ok and "policy_blocked" in refused.error, (
                operation,
                wrong,
                refused.result,
            )
            assert world.state() == before
        approval = _approve(world, operation, target)
        assert world.call(operation, {**arguments, "approval_id": approval}).ok, (
            operation
        )
        again = {
            "create_event": {
                "startTime": "2026-12-04T14:00:00Z",
                "endTime": "2026-12-04T15:00:00Z",
            },
            "update_event": {"expected_version": 2},
            "delete_event": {"eventId": event},
        }[operation]
        if operation == "delete_event":
            world.change(
                "calendar_events", event, {"status": "confirmed"}, by="event_id"
            )
        reused = world.call(operation, {**arguments, **again, "approval_id": approval})
        assert not reused.ok and "already used" in reused.error, operation


@pytest.mark.parametrize("package", CALENDARS)
def test_calendar_update_needs_the_version_it_was_decided_on(
    package: str, tmp_path: Path
) -> None:
    world, calendar = _calendar_world(package, tmp_path)
    event = _create(
        world, calendar, "2026-12-01T14:00:00Z", "2026-12-01T15:00:00Z"
    ).result["id"]
    change = {
        "calendarId": calendar,
        "eventId": event,
        "summary": "Renamed",
        "approval_id": _approve(world, "update_event", event),
    }
    for version in ({}, {"expected_version": 7}):
        refused = world.call("update_event", {**change, **version})
        assert not refused.ok and "expected_version" in refused.error
    assert world.call("update_event", {**change, "expected_version": 1}).ok


@pytest.mark.parametrize("package", CALENDARS)
def test_calendar_timeout_before_the_commit_changes_nothing(
    package: str, tmp_path: Path
) -> None:
    world, calendar = _calendar_world(package, tmp_path)
    event = _create(
        world, calendar, "2026-12-01T14:00:00Z", "2026-12-01T15:00:00Z"
    ).result["id"]
    for fault, landed in (("timeout", False), ("timeout_after_commit", True)):
        world.put(
            "calendar_fault_injections",
            {
                "fault_id": fault,
                "operation": "update_event",
                "target_id": None,
                "fault_type": fault,
                "trigger_once": True,
                "is_active": True,
            },
        )
        approval = _approve(world, "update_event", event)
        call = world.call(
            "update_event",
            {
                "calendarId": calendar,
                "eventId": event,
                "summary": fault,
                "expected_version": 1,
                "approval_id": approval,
            },
        )
        assert not call.ok and "timeout" in call.error
        row = next(
            e for e in world.state()["calendar_events"] if e["event_id"] == event
        )
        used = next(
            a
            for a in world.state()["calendar_approvals"]
            if a["approval_id"] == approval
        )
        assert (row["summary"] == fault, used["status"] == "used") == (
            landed,
            landed,
        ), fault


def test_calendar_scheduling_window(tmp_path: Path) -> None:
    """cfg_scheduling_notice: at least two hours' notice, at most sixty days ahead."""
    world, calendar = _calendar_world("calendar_scheduling_desk", tmp_path)
    for start, end, why in (
        ("2026-10-26T10:30:00Z", "2026-10-26T11:00:00Z", "notice"),
        ("2020-12-01T14:00:00Z", "2020-12-01T15:00:00Z", "notice"),
        ("2026-12-26T14:00:00Z", "2026-12-26T15:00:00Z", "days ahead"),
    ):
        refused = _create(world, calendar, start, end)
        assert not refused.ok and why in refused.error, (start, refused.error)
    made = _create(world, calendar, "2026-10-26T11:00:00Z", "2026-10-26T11:30:00Z")
    assert made.ok, made.error
    change = {
        "eventId": made.result["id"],
        "expected_version": 1,
        "approval_id": _approve(world, "update_event", made.result["id"]),
    }
    late = world.call("update_event", {**change, "startTime": "2027-03-01T14:00:00Z"})
    assert not late.ok and "days ahead" in late.error
    # An event already inside the notice window can still be renamed.
    world.change("clock", 1, {"now": "2026-10-26T10:30:00Z"}, by="id")
    assert world.call("update_event", {**change, "summary": "Renamed"}).ok


@pytest.mark.parametrize("package", CALENDARS)
def test_calendar_attendee_allowlist_matches_whole_domains(
    package: str, tmp_path: Path
) -> None:
    world, calendar = _calendar_world(package, tmp_path)
    if package == "calendar_scheduling_desk":
        world.change(
            "environment_policy",
            "allowed_domains",
            {"value": '"company.com"'},
            by="name",
        )
    else:
        world.put(
            "environment_policy", {"name": "allowed_domains", "value": '"company.com"'}
        )
    refused = _create(
        world,
        calendar,
        "2026-12-01T14:00:00Z",
        "2026-12-01T15:00:00Z",
        attendees=[{"email": "x@pany.com"}],
    )
    assert not refused.ok and "allowlist" in refused.error
    kept = _create(
        world,
        calendar,
        "2026-12-01T14:00:00Z",
        "2026-12-01T15:00:00Z",
        attendees=[{"email": "x@company.com"}],
    )
    assert kept.ok, kept.error


def test_calendar_start_only_update_keeps_moment_and_duration(tmp_path: Path) -> None:
    world = _world("calendar_scheduling_desk", tmp_path)
    event = _ok(
        world,
        "create_event",
        summary="Sync",
        startTime="2026-12-01T14:00:00Z",
        endTime="2026-12-01T14:30:00Z",
        approval_id="APP-1001",
    )
    moved = _ok(
        world,
        "update_event",
        eventId=event["id"],
        startTime="2026-12-02T10:00:00+05:30",
        expected_version=1,
        approval_id="APP-RESCHEDULE-02",
    )
    assert moved["start"]["dateTime"] == "2026-12-02T04:30:00Z"
    assert moved["end"]["dateTime"] == "2026-12-02T05:00:00Z"
    assert _ok(world, "get_event", eventId=event["id"])["start"] == moved["start"]
    start = datetime.fromisoformat(moved["start"]["dateTime"])
    end = datetime.fromisoformat(moved["end"]["dateTime"])
    assert (end - start).total_seconds() == 30 * 60


def test_calendar_replacing_a_non_last_attendee(tmp_path: Path) -> None:
    world = _world("calendar_scheduling_desk", tmp_path)
    event = _ok(
        world,
        "create_event",
        summary="Review",
        startTime="2026-12-03T14:00:00Z",
        endTime="2026-12-03T15:00:00Z",
        approval_id="APP-1001",
        attendees=[{"email": "alice@company.com"}, {"email": "bob@company.com"}],
    )
    updated = _ok(
        world,
        "update_event",
        eventId=event["id"],
        expected_version=1,
        approval_id="APP-RESCHEDULE-02",
        removedAttendeeEmails=["alice@company.com"],
        addedAttendees=[{"email": "charlie@company.com"}],
    )
    assert sorted(a["email"] for a in updated["attendees"]) == [
        "bob@company.com",
        "charlie@company.com",
    ]
    rows = [
        r
        for r in world.state()["calendar_event_attendees"]
        if r["event_id"] == event["id"]
    ]
    assert len({r["id"] for r in rows}) == len(rows) == 2


@pytest.mark.parametrize("package", ["gmail_inbox_triage", "banking"])
def test_drafts_search_applies_its_operators(package: str, tmp_path: Path) -> None:
    world = _world(package, tmp_path)
    if package == "banking":
        # In banking a support colleague drafts to anyone, a customer only to themselves.
        world.change("session_caller", "default", {"staff_id": "ST01"}, by="id")
    _ok(world, "create_draft", to=["alice@example.com"], subject="Alice", body="hi")
    _ok(world, "create_draft", to=["bob@example.com"], subject="Bob", body="hi")
    found = _ok(world, "list_drafts", query="to:alice@example.com")["drafts"]
    assert [d["toRecipients"] for d in found] == [["alice@example.com"]]
    assert len(_ok(world, "list_drafts", query="subject:bob")["drafts"]) == 1
    assert len(_ok(world, "list_drafts")["drafts"]) == 2
    refused = world.call("list_drafts", {"query": "from:alice@example.com"})
    assert not refused.ok and "Unsupported" in (refused.error or "")


@pytest.mark.parametrize(
    ("package", "calls"),
    [
        ("gmail_inbox_triage", ("search_threads", "list_drafts")),
        ("calendar_scheduling_desk", ("list_calendars", "list_events")),
    ],
)
def test_a_negative_page_token_is_refused(package: str, calls, tmp_path: Path) -> None:
    world = _world(package, tmp_path)
    for tool in calls:
        refused = world.call(tool, {"pageToken": "-1"})
        assert not refused.ok and "Invalid pageToken" in refused.error, tool


def test_thread_search_answers_or_refuses_every_operator(tmp_path: Path) -> None:
    world = _world("gmail_inbox_triage", tmp_path)

    def found(query):
        return [
            t["id"]
            for t in _ok(world, "search_threads", query=query, pageSize=50)["threads"]
        ]

    assert found('"production outage"') == ["T-07"]
    assert found('"outage production"') == []
    assert found("outage production") == ["T-07"]
    assert found('subject:"production outage"') == ["T-07"]
    assert "T-25" not in found("after:2026/10/01") and "T-07" in found(
        "after:2026-10-01"
    )
    assert found("before:2026/10/01") == ["T-25"]
    assert found("invoice or INV-9901") == found("INV-9901 or invoice")
    for query in (
        "is:starred",
        "-from:bob",
        "{outage invoice}",
        "(outage)",
        "subject:(a b)",
        "outage OR invoice",
        "filename:pdf",
        "larger:5M",
        "after:yesterday",
        "in:trash",
    ):
        refused = world.call("search_threads", {"query": query})
        assert not refused.ok and "invalid_input" in refused.error, (
            query,
            refused.result,
        )


def test_a_new_conversation_draft_leaves_the_mailbox_valid(tmp_path: Path) -> None:
    world = _world("gmail_inbox_triage", tmp_path)
    _ok(world, "create_draft", to=["a@example.com"], subject="Hello", body="Hi")
    spec = json.loads((ROOT / "gmail_inbox_triage" / "environment.json").read_text())
    for name, source in spec["world_checks"].items():
        outcome = run_world_check(source, world, name=name)
        assert outcome.held, (name, outcome.said)


def test_drive_folder_allowlist_filters_search_and_listing(tmp_path: Path) -> None:
    world = _world("drive_policy_search", tmp_path)

    def folders():
        files = _ok(world, "list_recent_files", pageSize=50)["files"]
        files += _ok(
            world, "search_files", query="fullText contains 'policy'", pageSize=50
        )["files"]
        return {f["parentId"] for f in files}

    assert folders() == {"f_public", "f_internal", "f_archive", "f_drafts"}
    world.change(
        "environment_policy",
        "folder_allowlist",
        {"value": '"f_public, f_archive"'},
        by="name",
    )
    assert folders() == {"f_public", "f_archive"}


@pytest.mark.parametrize(
    "package",
    [
        "calendar_scheduling_desk",
        "slack_channel_digest",
        "gmail_inbox_triage",
        "drive_policy_search",
    ],
)
def test_every_policy_parameter_is_read_by_a_handler(package: str) -> None:
    rules = json.loads((ROOT / package / "policy.json").read_text())["rules"]
    source = "".join(p.read_text() for p in (ROOT / package / "handlers").glob("*.py"))
    for rule in rules:
        for name in rule.get("parameters", {}):
            assert f"'{name}'" in source or f'"{name}"' in source, (rule["id"], name)


def test_slack_post_is_bound_to_its_approval(tmp_path: Path) -> None:
    world = _world("slack_channel_digest", tmp_path)
    text = world.state()["slack_approvals"][0]["approved_text"]
    post = {
        "channel_id": "C01",
        "message": text,
        "approval_id": "APP-101",
        "request_key": "K1",
    }
    for wrong, why in (
        ({"message": "Something else"}, "different text"),
        ({"channel_id": "C02"}, "for channel"),
        *(
            ({"message": f"{text} {mention}"}, "Broadcast")
            for mention in (
                "<!channel>",
                "<!here>",
                "<!everyone>",
                "<!here|here>",
                "@channel",
            )
        ),
    ):
        refused = world.call("slack_send_message", {**post, **wrong})
        assert not refused.ok and why in refused.error, (wrong, refused.error)
    world.change(
        "slack_approvals",
        "APP-101",
        {"expires_at": "2026-10-26T12:00:00Z"},
        by="approval_id",
    )
    expired = world.call("slack_send_message", post)
    assert not expired.ok and "expired" in expired.error
    world.change(
        "slack_approvals",
        "APP-101",
        {"expires_at": "2026-10-27T12:00:00Z"},
        by="approval_id",
    )
    assert _ok(world, "slack_send_message", **post)["status"] == "delivered"
    assert _ok(world, "slack_send_message", **post)["status"] == "idempotent_replay"
    again = world.call("slack_send_message", {**post, "request_key": "K2"})
    assert not again.ok and "already used" in again.error
    assert len(world.state()["slack_messages"]) == 1
