"""Handler behaviour of the MCP capabilities that matching the official interface changed
(review of #239): a start-only update keeps the start's UTC offset and the event's duration,
replacing a non-last attendee does not reuse a surviving attendee id, and a drafts search
applies its operators instead of dropping them."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import pytest

from fi.alk import environments
from fi.alk.environments import designed
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


def test_calendar_start_only_update_keeps_offset_and_duration(tmp_path: Path) -> None:
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
        approval_id="APP-RESCHEDULE-01",
    )
    assert moved["end"]["dateTime"] == "2026-12-02T10:30:00+05:30"
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
        approval_id="APP-RESCHEDULE-01",
        removedAttendeeEmails=["alice@company.com"],
        addedAttendees=[{"email": "charlie@company.com"}],
    )
    assert sorted(a["email"] for a in updated["attendees"]) == [
        "bob@company.com",
        "charlie@company.com",
    ]
    rows = [r for r in world.state()["event_attendees"] if r["event_id"] == event["id"]]
    assert len({r["id"] for r in rows}) == len(rows) == 2


@pytest.mark.parametrize("package", ["gmail_inbox_triage", "banking"])
def test_drafts_search_applies_its_operators(package: str, tmp_path: Path) -> None:
    world = _world(package, tmp_path)
    _ok(world, "create_draft", to=["alice@example.com"], subject="Alice", body="hi")
    _ok(world, "create_draft", to=["bob@example.com"], subject="Bob", body="hi")
    found = _ok(world, "list_drafts", query="to:alice@example.com")["drafts"]
    assert [d["toRecipients"] for d in found] == [["alice@example.com"]]
    assert len(_ok(world, "list_drafts", query="subject:bob")["drafts"]) == 1
    assert len(_ok(world, "list_drafts")["drafts"]) == 2
    refused = world.call("list_drafts", {"query": "from:alice@example.com"})
    assert not refused.ok and "Unsupported" in (refused.error or "")
