def _kept(error):
    """A fault this world simulates on purpose: what the call wrote before it stays written."""
    error.keep_writes = True
    return error


def handle(args, db):
    import json
    import re

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
    if exp_ver is not None and exp_ver != evt["version"]:
        raise ToolError(f"Conflict: expected_version {exp_ver} does not match current event version {evt['version']}.", category="conflict")

    req_app_row = db.one("SELECT value FROM environment_policy WHERE name = 'require_approval_for_mutations'")
    require_app = bool(json.loads(req_app_row["value"])) if req_app_row else True
    approval_id = args.get("approval_id")
    if require_app:
        if not approval_id:
            raise ToolError("Policy: updating an event needs an approval_id for this exact change; confirm it with the user first.", category="policy_blocked")
        if not db.one("SELECT * FROM calendar_approvals WHERE approval_id = ? AND status = 'approved'", (approval_id,)):
            raise ToolError(f"Invalid or unapproved approval_id: {approval_id!r}.", category="policy_blocked")

    summary = args.get("summary") or evt["summary"]
    description = args["description"] if "description" in args else evt["description"]
    location = args["location"] if "location" in args else evt["location"]
    start_time, end_time, time_zone = evt["start_time"], evt["end_time"], evt["time_zone"]
    iso_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
    if args.get("startTime"):
        if not isinstance(args["startTime"], str) or not re.match(iso_pattern, args["startTime"]):
            raise ToolError("startTime must be an ISO 8601 timestamp", category="invalid_input")
        duration = None
        if not args.get("endTime"):
            from datetime import datetime
            duration = datetime.fromisoformat(end_time.replace("Z", "+00:00")) - datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        start_time = args["startTime"]
        if duration is not None:  # only the start moved: the duration is preserved, in the start's offset
            from datetime import datetime
            new_end = datetime.fromisoformat(start_time.replace("Z", "+00:00")) + duration
            end_time = new_end.strftime("%Y-%m-%dT%H:%M:%SZ") if start_time.endswith("Z") else new_end.isoformat()
    if args.get("endTime"):
        if not isinstance(args["endTime"], str) or not re.match(iso_pattern, args["endTime"]):
            raise ToolError("endTime must be an ISO 8601 timestamp", category="invalid_input")
        end_time = args["endTime"]
    if args.get("timeZone"):
        time_zone = args["timeZone"]
    if start_time >= end_time:
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
            if em.split("@")[-1] not in allowed:
                raise ToolError(f"Policy violation: attendee domain {em.split('@')[-1]!r} is not in the attendee allowlist.", category="policy_blocked")
    emails_after = [em for em in current if em not in removed] + [em for em, _, _ in added if em not in current]

    if start_time != evt["start_time"] or end_time != evt["end_time"]:
        cal_conflicts = db.query(
            "SELECT event_id, summary FROM calendar_events WHERE calendar_id = ? AND event_id != ? AND status != 'cancelled' AND start_time < ? AND end_time > ?",
            (cal_id, evt_id, end_time, start_time),
        )
        if cal_conflicts:
            raise ToolError(f"Conflict: slot {start_time} - {end_time} conflicts with existing event {cal_conflicts[0]['event_id']} ({cal_conflicts[0]['summary']}).", category="conflict")
        for em in emails_after:
            att_conflicts = db.query(
                "SELECT e.event_id FROM calendar_events e JOIN calendar_event_attendees a ON e.event_id = a.event_id "
                "WHERE a.email = ? AND e.event_id != ? AND e.status != 'cancelled' AND e.start_time < ? AND e.end_time > ?",
                (em, evt_id, end_time, start_time),
            )
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

    clock_row = db.one("SELECT now FROM clock LIMIT 1")
    now_str = clock_row["now"] if clock_row else "2026-10-26T09:00:00Z"
    new_version = evt["version"] + 1
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
