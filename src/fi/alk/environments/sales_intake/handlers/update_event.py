def _kept(error):
    """A fault this world simulates on purpose: what the call wrote before it stays written."""
    error.keep_writes = True
    return error


def _moment(value, zone):
    """The moment an ISO 8601 time names, in UTC. A time without an offset, and an all-day date
    (its midnight), are local to `zone`."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (moment if moment.tzinfo else moment.replace(tzinfo=ZoneInfo(zone))).astimezone(timezone.utc)


def _utc(name, value, zone):
    """A time argument as events store it, in UTC."""
    try:
        return _moment(value, zone).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        raise ToolError(f"{name} {value!r} is not a time in {zone!r}", category="invalid_input")


def _overlaps(event, start, end):
    return _moment(event["start_time"], event["time_zone"]) < end and _moment(event["end_time"], event["time_zone"]) > start


def _check_approval(db, approval_id, operation, target_id, now):
    """An approval covers one change: the acting user's own, of this operation on this target,
    before it expires."""
    row = db.one("SELECT * FROM calendar_approvals WHERE approval_id = ?", (approval_id,))
    if row and row["status"] == "used":
        raise ToolError(f"Approval {approval_id!r} was already used; one approval covers one change.", category="policy_blocked")
    if not row or row["status"] != "approved":
        raise ToolError(f"Invalid or unapproved approval_id: {approval_id!r}.", category="policy_blocked")
    actor = db.one("SELECT user_id FROM current_actor WHERE id = 'default'")
    if not actor or row["actor_id"] != actor["user_id"]:
        raise ToolError(f"Approval {approval_id!r} belongs to another user.", category="policy_blocked")
    if (row["operation"], row["target_id"]) != (operation, target_id):
        raise ToolError(f"Approval {approval_id!r} is for {row['operation']} on {row['target_id']}, not {operation} on {target_id}.", category="policy_blocked")
    if _moment(row["expires_at"], "UTC") <= _moment(now, "UTC"):
        raise ToolError(f"Approval {approval_id!r} expired at {row['expires_at']}.", category="policy_blocked")


def handle(args, db):
    import json
    import re

    # Only staff write to calendars (demos are booked with book_demo, which checks the lead's own contact).
    if _caller(db)[0] != "staff":
        raise ToolError("Permission denied: calendars are written only by BrightDesk staff; only BrightDesk staff in this conversation can change events.", category="permission_denied")
    evt_id = args.get("eventId")
    if not evt_id or not isinstance(evt_id, str):
        raise ToolError("Missing required parameter: eventId", category="invalid_input")
    cal_id = args.get("calendarId")
    if not cal_id:
        primary = db.one("SELECT calendar_id FROM calendar_calendars WHERE primary_calendar = TRUE")
        cal_id = primary["calendar_id"] if primary else "CAL-PRIMARY"
    cal = db.one("SELECT * FROM calendar_calendars WHERE calendar_id = ?", (cal_id,))
    if not cal:
        raise ToolError(f"Calendar not found: {cal_id}", category="not_found")
    if cal["access_role"] not in ("writer", "owner"):
        raise ToolError(f"Permission denied: calendar {cal_id!r} is read-only (access_role: {cal['access_role']}).", category="permission_denied")
    evt = db.one("SELECT * FROM calendar_events WHERE calendar_id = ? AND event_id = ?", (cal_id, evt_id))
    if not evt:
        raise ToolError(f"Event {evt_id!r} not found on calendar {cal_id!r}.", category="not_found")
    if evt["status"] == "cancelled":
        raise ToolError(f"Cannot update cancelled event {evt_id!r}.", category="conflict")

    exp_ver = args.get("expected_version")
    if exp_ver is None:
        raise ToolError("Missing required parameter: expected_version, the version of the event the change was decided on", category="invalid_input")
    if exp_ver != evt["version"]:
        raise ToolError(f"Conflict: expected_version {exp_ver} does not match current event version {evt['version']}.", category="conflict")

    clock_row = db.one("SELECT now FROM clock LIMIT 1")
    now_str = clock_row["now"] if clock_row else "2026-10-26T09:00:00Z"

    req_app_row = db.one("SELECT value FROM environment_policy WHERE name = 'require_approval_for_mutations'")
    require_app = bool(json.loads(req_app_row["value"])) if req_app_row else True
    approval_id = args.get("approval_id")
    if require_app:
        if not approval_id:
            raise ToolError("Policy: updating an event needs an approval_id for this exact change; confirm it with the user first.", category="policy_blocked")
        _check_approval(db, approval_id, "update_event", evt_id, now_str)

    summary = args.get("summary") or evt["summary"]
    description = args["description"] if "description" in args else evt["description"]
    location = args["location"] if "location" in args else evt["location"]
    start_time, end_time, time_zone = evt["start_time"], evt["end_time"], args.get("timeZone") or evt["time_zone"]
    iso_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
    if args.get("startTime"):
        if not isinstance(args["startTime"], str) or not re.match(iso_pattern, args["startTime"]):
            raise ToolError("startTime must be an ISO 8601 timestamp", category="invalid_input")
        start_time = _utc("startTime", args["startTime"], time_zone)
        if not args.get("endTime"):  # only the start moved: the duration is preserved
            duration = _moment(evt["end_time"], evt["time_zone"]) - _moment(evt["start_time"], evt["time_zone"])
            end_time = (_moment(start_time, "UTC") + duration).strftime("%Y-%m-%dT%H:%M:%SZ")
    if args.get("endTime"):
        if not isinstance(args["endTime"], str) or not re.match(iso_pattern, args["endTime"]):
            raise ToolError("endTime must be an ISO 8601 timestamp", category="invalid_input")
        end_time = _utc("endTime", args["endTime"], time_zone)
    if _moment(start_time, evt["time_zone"]) >= _moment(end_time, evt["time_zone"]):
        raise ToolError("startTime must be before endTime", category="invalid_input")

    current = {a["email"]: a for a in db.query("SELECT * FROM calendar_event_attendees WHERE event_id = ?", (evt_id,))}
    removed = args.get("removedAttendeeEmails") or []
    added = []
    for att in args.get("addedAttendees") or []:
        if not isinstance(att, dict) or not att.get("email"):
            raise ToolError("addedAttendees must be objects with an email", category="invalid_input")
        added.append((att["email"], att.get("displayName") or att["email"], bool(att.get("optionalAttendee"))))
    for em in args.get("addedAttendeeEmails") or []:  # deprecated spelling
        if em not in [a[0] for a in added]:
            added.append((em, em, False))
    allowlist_row = db.one("SELECT value FROM environment_policy WHERE name = 'allowed_domains'")
    allowed = json.loads(allowlist_row["value"]) if allowlist_row else "*"
    if allowed != "*":
        for em, _, _ in added:
            if em.split("@")[-1] not in allowed.split(","):
                raise ToolError(f"Policy violation: attendee domain {em.split('@')[-1]!r} is not in the attendee allowlist.", category="policy_blocked")
    emails_after = [em for em in current if em not in removed] + [em for em, _, _ in added if em not in current]

    if start_time != evt["start_time"] or end_time != evt["end_time"]:
        start, end = _moment(start_time, evt["time_zone"]), _moment(end_time, evt["time_zone"])
        cal_conflicts = [e for e in db.query("SELECT * FROM calendar_events WHERE calendar_id = ? AND event_id != ? AND status != 'cancelled'", (cal_id, evt_id)) if _overlaps(e, start, end)]
        if cal_conflicts:
            raise ToolError(f"Conflict: slot {start_time} - {end_time} conflicts with existing event {cal_conflicts[0]['event_id']} ({cal_conflicts[0]['summary']}).", category="conflict")
        for em in emails_after:
            att_conflicts = [e for e in db.query(
                "SELECT e.* FROM calendar_events e JOIN calendar_event_attendees a ON e.event_id = a.event_id "
                "WHERE a.email = ? AND e.event_id != ? AND e.status != 'cancelled'",
                (em, evt_id),
            ) if _overlaps(e, start, end)]
            if att_conflicts:
                raise ToolError(f"Conflict: attendee {em} has a conflicting event {att_conflicts[0]['event_id']}.", category="conflict")

    # A timeout or a refusal before the commit changes nothing but uses the fault up, as in
    # create_event: only `timeout_after_commit` lands the change and loses the reply.
    fault = db.one("SELECT * FROM calendar_fault_injections WHERE operation = 'update_event' AND is_active = TRUE AND fault_type != 'timeout_after_commit'")
    if fault:
        if fault["trigger_once"]:
            db.execute("UPDATE calendar_fault_injections SET is_active = FALSE WHERE fault_id = ?", (fault["fault_id"],))
        if fault["fault_type"] == "timeout":
            raise _kept(ToolError("Gateway timeout: request timed out waiting for upstream server.", category="timeout_unknown_outcome"))
        if fault["fault_type"] == "permission_denied":
            raise _kept(ToolError("Permission denied by upstream API.", category="permission_denied"))

    new_version = evt["version"] + 1
    if require_app:
        db.execute("UPDATE calendar_approvals SET status = 'used' WHERE approval_id = ?", (approval_id,))
    db.execute(
        "UPDATE calendar_events SET summary = ?, description = ?, location = ?, start_time = ?, end_time = ?, time_zone = ?, version = ?, updated_at = ? WHERE event_id = ?",
        (summary, description, location, start_time, end_time, time_zone, new_version, now_str, evt_id),
    )
    for em in removed:
        db.execute("DELETE FROM calendar_event_attendees WHERE event_id = ? AND email = ?", (evt_id, em))
    for em, d_name, optional in added:
        if em in current:
            continue
        att_id = db.one("SELECT COALESCE(MAX(id), 0) + 1 AS c FROM calendar_event_attendees")["c"]
        db.execute(
            "INSERT INTO calendar_event_attendees (id, event_id, email, display_name, response_status, is_organizer, is_optional) VALUES (?, ?, ?, ?, 'needsAction', FALSE, ?)",
            (att_id, evt_id, em, d_name, optional),
        )
    db.execute(
        "INSERT INTO calendar_simulated_outbox (message_id, event_id, recipient_email, action, subject, payload, sent_at) VALUES (?, ?, 'all-attendees', 'update', ?, ?, ?)",
        (f"MSG-UPD-{evt_id}-{new_version}", evt_id, f"Rescheduled: {summary}", json.dumps({"start": start_time, "end": end_time}), now_str),
    )

    # A timeout after the commit: the change is made, the reply is lost.
    fault = db.one("SELECT * FROM calendar_fault_injections WHERE operation = 'update_event' AND is_active = TRUE AND fault_type = 'timeout_after_commit'")
    if fault:
        if fault["trigger_once"]:
            db.execute("UPDATE calendar_fault_injections SET is_active = FALSE WHERE fault_id = ?", (fault["fault_id"],))
        raise _kept(ToolError("Gateway timeout: request timed out waiting for upstream server response.", category="timeout_unknown_outcome"))

    attendees = [
        {
            "email": a["email"],
            "displayName": a["display_name"],
            "responseStatus": a["response_status"],
            "organizer": bool(a["is_organizer"]),
            "optionalAttendee": bool(a["is_optional"]),
        }
        for a in db.query("SELECT email, display_name, response_status, is_organizer, is_optional FROM calendar_event_attendees WHERE event_id = ?", (evt_id,))
    ]
    return {
        "id": evt_id,
        "status": "confirmed",
        "htmlLink": f"https://calendar.example.com/event?eid={evt_id}",
        "created": evt["created_at"],
        "updated": now_str,
        "summary": summary,
        "description": description,
        "location": location,
        "start": {"dateTime": start_time, "timeZone": time_zone},
        "end": {"dateTime": end_time, "timeZone": time_zone},
        "recurrence": [evt["recurrence_rule"]] if evt["recurrence_rule"] else [],
        "recurringEventId": evt["recurring_event_id"],
        "attendees": attendees,
        "eventType": "DEFAULT",
        "version": new_version,
    }


def _caller(db):
    """Who is in this conversation, as the session set it: ("contact", row) for a prospect speaking
    for themselves, ("staff", row) for a BrightDesk employee (their Slack user), or (None, None).
    An ID or an email the desk is told never changes it."""
    row = db.one("SELECT person_id FROM session_caller WHERE id = 'default'")
    person = row["person_id"] if row else None
    if person:
        contact = db.one("SELECT * FROM contacts WHERE contact_id = ?", [person])
        if contact:
            return "contact", contact
        staff = db.one("SELECT * FROM slack_users WHERE user_id = ?", [person])
        if staff:
            return "staff", staff
    return None, None


def _named(kind, who):
    if kind == "contact":
        return f"{who['name']} ({who['contact_id']})"
    if kind == "staff":
        return f"{who['name']} ({who['user_id']})"
    return "nobody signed in"
