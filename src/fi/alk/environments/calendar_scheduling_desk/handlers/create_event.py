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


def _check_window(db, start, now):
    """The configured scheduling window: how soon and how far ahead an event may start."""
    import json
    from datetime import timedelta

    policy = {r["name"]: json.loads(r["value"]) for r in db.query("SELECT name, value FROM environment_policy")}
    if "min_notice_hours" in policy and start < now + timedelta(hours=int(policy["min_notice_hours"])):
        raise ToolError(f"Policy violation: events need at least {policy['min_notice_hours']} hours' notice.", category="policy_blocked")
    if "max_horizon_days" in policy and start > now + timedelta(days=int(policy["max_horizon_days"])):
        raise ToolError(f"Policy violation: events start at most {policy['max_horizon_days']} days ahead.", category="policy_blocked")


def handle(args, db):
    import json
    import re

    cal_id = args.get("calendarId")
    if not cal_id:
        primary = db.one("SELECT calendar_id FROM calendar_calendars WHERE primary_calendar = TRUE")
        cal_id = primary["calendar_id"] if primary else "CAL-PRIMARY"
    cal = db.one("SELECT * FROM calendar_calendars WHERE calendar_id = ?", (cal_id,))
    if not cal:
        raise ToolError(f"Calendar not found: {cal_id}", category="not_found")
    if cal["access_role"] not in ("writer", "owner"):
        raise ToolError(f"Permission denied: calendar {cal_id!r} is read-only (access_role: {cal['access_role']}).", category="permission_denied")

    summary = args.get("summary")
    if not summary or not isinstance(summary, str):
        raise ToolError("Missing required parameter: summary", category="invalid_input")
    start_time, end_time = args.get("startTime"), args.get("endTime")
    iso_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
    if not start_time or not isinstance(start_time, str) or not re.match(iso_pattern, start_time):
        raise ToolError(f"startTime must be an ISO 8601 timestamp, e.g. '2026-04-30T10:00:00Z': {start_time!r}", category="invalid_input")
    if not end_time or not isinstance(end_time, str) or not re.match(iso_pattern, end_time):
        raise ToolError(f"endTime must be an ISO 8601 timestamp: {end_time!r}", category="invalid_input")
    time_zone = args.get("timeZone") or cal["time_zone"]
    start_time, end_time = _utc("startTime", start_time, time_zone), _utc("endTime", end_time, time_zone)
    if start_time >= end_time:
        raise ToolError("startTime must be before endTime", category="invalid_input")

    clock_row = db.one("SELECT now FROM clock LIMIT 1")
    now_str = clock_row["now"] if clock_row else "2026-10-26T09:00:00Z"

    req_app_row = db.one("SELECT value FROM environment_policy WHERE name = 'require_approval_for_mutations'")
    require_app = bool(json.loads(req_app_row["value"])) if req_app_row else True
    approval_id = args.get("approval_id")
    if require_app:
        if not approval_id:
            raise ToolError("Policy: creating an event needs an approval_id for this exact event; confirm it with the user first.", category="policy_blocked")
        _check_approval(db, approval_id, "create_event", cal_id, now_str)

    attendees_in = []
    for att in args.get("attendees") or []:
        if not isinstance(att, dict) or not att.get("email"):
            raise ToolError("attendees must be objects with an email", category="invalid_input")
        attendees_in.append((att["email"], att.get("displayName") or att["email"], bool(att.get("optionalAttendee"))))
    for em in args.get("attendeeEmails") or []:  # deprecated spelling
        if not isinstance(em, str) or "@" not in em:
            raise ToolError("attendeeEmails must be email addresses", category="invalid_input")
        if em not in [a[0] for a in attendees_in]:
            attendees_in.append((em, em, False))

    allowlist_row = db.one("SELECT value FROM environment_policy WHERE name = 'allowed_domains'")
    allowed = json.loads(allowlist_row["value"]) if allowlist_row else "*"
    if allowed != "*":
        for em, _, _ in attendees_in:
            if em.split("@")[-1] not in allowed.split(","):
                raise ToolError(f"Policy violation: attendee domain {em.split('@')[-1]!r} is not in the attendee allowlist.", category="policy_blocked")

    start, end = _moment(start_time, time_zone), _moment(end_time, time_zone)
    _check_window(db, start, _moment(now_str, "UTC"))
    cal_conflicts = [e for e in db.query("SELECT * FROM calendar_events WHERE calendar_id = ? AND status != 'cancelled'", (cal_id,)) if _overlaps(e, start, end)]
    if cal_conflicts:
        raise ToolError(f"Conflict: slot {start_time} - {end_time} conflicts with existing event {cal_conflicts[0]['event_id']} ({cal_conflicts[0]['summary']}).", category="conflict")
    for em, _, _ in attendees_in:
        att_conflicts = [e for e in db.query(
            "SELECT e.* FROM calendar_events e JOIN calendar_event_attendees a ON e.event_id = a.event_id "
            "WHERE a.email = ? AND e.status != 'cancelled'",
            (em,),
        ) if _overlaps(e, start, end)]
        if att_conflicts:
            raise ToolError(f"Conflict: attendee {em} has a conflicting event {att_conflicts[0]['event_id']}.", category="conflict")

    fault = db.one("SELECT * FROM calendar_fault_injections WHERE operation = 'create_event' AND is_active = TRUE AND fault_type != 'timeout_after_commit'")
    if fault:
        if fault["trigger_once"]:
            db.execute("UPDATE calendar_fault_injections SET is_active = FALSE WHERE fault_id = ?", (fault["fault_id"],))
        if fault["fault_type"] == "timeout":
            raise _kept(ToolError("Gateway timeout: request timed out waiting for upstream server.", category="timeout_unknown_outcome"))
        if fault["fault_type"] == "permission_denied":
            raise _kept(ToolError("Permission denied by upstream API.", category="permission_denied"))

    event_id = f"EVT-CR-{101 + db.one('SELECT COUNT(*) as c FROM calendar_events')['c']}"
    recurrence = args.get("recurrenceData") or []
    if not isinstance(recurrence, list) or not all(isinstance(r, str) for r in recurrence):
        raise ToolError("recurrenceData must be a list of RRULE, RDATE or EXDATE strings", category="invalid_input")
    db.execute(
        "INSERT INTO calendar_events (event_id, calendar_id, summary, description, location, start_time, end_time, time_zone, status, recurrence_rule, version, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, 1, ?, ?)",
        (event_id, cal_id, summary, args.get("description"), args.get("location"), start_time, end_time, time_zone, recurrence[0] if recurrence else None, now_str, now_str),
    )
    if require_app:
        db.execute("UPDATE calendar_approvals SET status = 'used' WHERE approval_id = ?", (approval_id,))
    res_attendees = []
    for em, d_name, optional in attendees_in:
        att_id = db.one("SELECT COALESCE(MAX(id), 0) + 1 AS c FROM calendar_event_attendees")["c"]
        db.execute(
            "INSERT INTO calendar_event_attendees (id, event_id, email, display_name, response_status, is_organizer, is_optional) VALUES (?, ?, ?, ?, 'needsAction', FALSE, ?)",
            (att_id, event_id, em, d_name, optional),
        )
        res_attendees.append({"email": em, "displayName": d_name, "responseStatus": "needsAction", "organizer": False, "optionalAttendee": optional})
        db.execute(
            "INSERT INTO calendar_simulated_outbox (message_id, event_id, recipient_email, action, subject, payload, sent_at) VALUES (?, ?, ?, 'invitation', ?, ?, ?)",
            (f"MSG-{event_id}-{em.replace('@', '_at_')}", event_id, em, f"Invitation: {summary}", json.dumps({"start": start_time, "end": end_time}), now_str),
        )

    # A timeout after the commit, as in update_event: the event is created, the reply is lost.
    late = db.one("SELECT * FROM calendar_fault_injections WHERE operation = 'create_event' AND is_active = TRUE AND fault_type = 'timeout_after_commit'")
    if late:
        if late["trigger_once"]:
            db.execute("UPDATE calendar_fault_injections SET is_active = FALSE WHERE fault_id = ?", (late["fault_id"],))
        raise _kept(ToolError("Gateway timeout: request timed out waiting for upstream server response.", category="timeout_unknown_outcome"))

    return {
        "id": event_id,
        "status": "confirmed",
        "htmlLink": f"https://calendar.example.com/event?eid={event_id}",
        "created": now_str,
        "updated": now_str,
        "summary": summary,
        "description": args.get("description"),
        "location": args.get("location"),
        "start": {"dateTime": start_time, "timeZone": time_zone},
        "end": {"dateTime": end_time, "timeZone": time_zone},
        "recurrence": recurrence,
        "recurringEventId": None,
        "attendees": res_attendees,
        "eventType": "DEFAULT",
        "version": 1,
    }
