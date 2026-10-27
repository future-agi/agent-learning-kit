def handle(args, db):
    import json
    import re
    from datetime import datetime

    cal_id = args.get("calendar_id")
    if not cal_id:
        raise ToolError("Missing required parameter: calendar_id")

    cal = db.one("SELECT * FROM calendars WHERE calendar_id = ?", (cal_id,))
    if not cal:
        raise ToolError(f"Calendar not found: {cal_id}")
    if cal["access_role"] not in ("writer", "owner"):
        raise ToolError(f"Permission denied: calendar {cal_id!r} is read-only (access_role: {cal['access_role']}).")

    req_app_row = db.one("SELECT value FROM environment_policy WHERE name = 'require_approval_for_mutations'")
    require_app = True
    if req_app_row:
        try:
            require_app = bool(json.loads(req_app_row["value"]))
        except Exception:
            require_app = True

    approval_id = args.get("approval_id")
    if require_app:
        if not approval_id:
            raise ToolError("Policy error: approval_id is required to create an event.")
        app = db.one("SELECT * FROM approvals WHERE approval_id = ? AND status = 'approved'", (approval_id,))
        if not app:
            raise ToolError(f"Invalid or unapproved approval_id: {approval_id!r}.")

    summary = args.get("summary")
    if not summary:
        raise ToolError("Missing required parameter: summary")

    start_obj = args.get("start")
    end_obj = args.get("end")
    if not start_obj or not end_obj:
        raise ToolError("Missing required parameters: start and end are required.")

    time_zone = cal["time_zone"]
    if isinstance(start_obj, str):
        start_time = start_obj
    elif isinstance(start_obj, dict):
        start_time = start_obj.get("dateTime")
        if start_obj.get("timeZone"):
            time_zone = start_obj["timeZone"]
    else:
        raise ToolError("Invalid start format.")

    if isinstance(end_obj, str):
        end_time = end_obj
    elif isinstance(end_obj, dict):
        end_time = end_obj.get("dateTime")
    else:
        raise ToolError("Invalid end format.")

    iso_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
    if not start_time or not re.match(iso_pattern, start_time):
        raise ToolError(f"Invalid ISO datetime string for start: {start_time}")
    if not end_time or not re.match(iso_pattern, end_time):
        raise ToolError(f"Invalid ISO datetime string for end: {end_time}")

    cal_conflicts = db.query(
        "SELECT event_id, summary FROM events WHERE calendar_id = ? AND status != 'cancelled' AND start_time < ? AND end_time > ?",
        (cal_id, end_time, start_time)
    )
    if cal_conflicts:
        raise ToolError(f"Conflict: slot {start_time} - {end_time} conflicts with existing event {cal_conflicts[0]['event_id']} ({cal_conflicts[0]['summary']}).")

    attendees_in = args.get("attendees", [])
    if isinstance(attendees_in, str):
        attendees_in = [x.strip() for x in attendees_in.split(",") if x.strip()]

    allowlist_row = db.one("SELECT value FROM environment_policy WHERE name = 'allowed_domains'")
    if allowlist_row:
        try:
            allowed = json.loads(allowlist_row["value"])
            if allowed != "*":
                for att in attendees_in:
                    em = att if isinstance(att, str) else att.get("email", "")
                    domain = em.split("@")[-1] if "@" in em else ""
                    if domain not in allowed:
                        raise ToolError(f"Policy violation: attendee domain {domain!r} is not in the attendee allowlist.")
        except Exception as e:
            if isinstance(e, ToolError):
                raise

    for att in attendees_in:
        em = att if isinstance(att, str) else att.get("email", "")
        if em:
            att_conflicts = db.query(
                "SELECT e.event_id, e.summary FROM events e JOIN event_attendees a ON e.event_id = a.event_id "
                "WHERE a.email = ? AND e.status != 'cancelled' AND e.start_time < ? AND e.end_time > ?",
                (em, end_time, start_time)
            )
            if att_conflicts:
                raise ToolError(f"Conflict: attendee {em} has a conflicting event {att_conflicts[0]['event_id']}.")

    fault = db.one("SELECT * FROM fault_injections WHERE operation = 'create_event' AND is_active = TRUE")
    if fault:
        if fault["trigger_once"]:
            db.execute("UPDATE fault_injections SET is_active = FALSE WHERE fault_id = ?", (fault["fault_id"],))
        if fault["fault_type"] == "timeout":
            raise ToolError("Gateway timeout: request timed out waiting for upstream server.")
        elif fault["fault_type"] == "permission_denied":
            raise ToolError("Permission denied by upstream API.")

    clock_row = db.one("SELECT now FROM clock LIMIT 1")
    now_str = clock_row["now"] if clock_row else "2026-10-26T09:00:00Z"

    events_count = db.one("SELECT COUNT(*) as c FROM events")["c"]
    event_id = f"EVT-CR-{101 + events_count}"

    description = args.get("description")
    location = args.get("location")
    rrule = args.get("recurrence")

    db.execute(
        "INSERT INTO events (event_id, calendar_id, summary, description, location, start_time, end_time, time_zone, status, recurrence_rule, version, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, 1, ?, ?)",
        (event_id, cal_id, summary, description, location, start_time, end_time, time_zone, rrule, now_str, now_str)
    )

    res_attendees = []
    for att in attendees_in:
        if isinstance(att, str):
            em = att
            d_name = att
        elif isinstance(att, dict):
            em = att.get("email")
            d_name = att.get("displayName") or em
        else:
            continue
        att_count = db.one("SELECT COUNT(*) as c FROM event_attendees")["c"]
        att_id = att_count + 1
        db.execute(
            "INSERT INTO event_attendees (id, event_id, email, display_name, response_status, is_organizer, is_optional) "
            "VALUES (?, ?, ?, ?, 'needsAction', FALSE, FALSE)",
            (att_id, event_id, em, d_name)
        )
        res_attendees.append({
            "email": em,
            "displayName": d_name,
            "responseStatus": "needsAction"
        })
        msg_id = f"MSG-{event_id}-{em.replace('@', '_at_')}"
        db.execute(
            "INSERT INTO simulated_outbox (message_id, event_id, recipient_email, action, subject, payload, sent_at) "
            "VALUES (?, ?, ?, 'invitation', ?, ?, ?)",
            (msg_id, event_id, em, f"Invitation: {summary}", json.dumps({"start": start_time, "end": end_time}), now_str)
        )

    return {
        "id": event_id,
        "calendarId": cal_id,
        "summary": summary,
        "description": description,
        "location": location,
        "start": {"dateTime": start_time, "timeZone": time_zone},
        "end": {"dateTime": end_time, "timeZone": time_zone},
        "status": "confirmed",
        "attendees": res_attendees,
        "version": 1,
        "created": now_str,
        "updated": now_str
    }