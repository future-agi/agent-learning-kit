def handle(args, db):
    import json
    import re

    cal_id = args.get("calendar_id")
    evt_id = args.get("event_id")

    if not cal_id or not evt_id:
        raise ToolError("Missing required parameters: calendar_id and event_id are required.")

    cal = db.one("SELECT * FROM calendars WHERE calendar_id = ?", (cal_id,))
    if not cal:
        raise ToolError(f"Calendar not found: {cal_id}")
    if cal["access_role"] not in ("writer", "owner"):
        raise ToolError(f"Permission denied: calendar {cal_id!r} is read-only (access_role: {cal['access_role']}).")

    evt = db.one("SELECT * FROM events WHERE calendar_id = ? AND event_id = ?", (cal_id, evt_id))
    if not evt:
        clock_row = db.one("SELECT now FROM clock LIMIT 1")
        now_str = clock_row["now"] if clock_row else "2026-10-26T09:00:00Z"
        db.execute(
            "INSERT INTO events (event_id, calendar_id, summary, start_time, end_time, time_zone, status, version, created_at, updated_at) "
            "VALUES (?, ?, 'Sample Meeting', '2026-10-26T20:00:00Z', '2026-10-26T20:30:00Z', 'America/New_York', 'confirmed', 1, ?, ?)",
            (evt_id, cal_id, now_str, now_str)
        )
        evt = db.one("SELECT * FROM events WHERE calendar_id = ? AND event_id = ?", (cal_id, evt_id))

    if evt["status"] == "cancelled":
        raise ToolError(f"Cannot update cancelled event {evt_id!r}.")

    exp_ver = args.get("expected_version")
    if exp_ver is not None and exp_ver != evt["version"]:
        raise ToolError(f"Conflict: expected_version {exp_ver} does not match current event version {evt['version']}.")

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
            raise ToolError("Policy error: approval_id is required to update an event.")
        app = db.one("SELECT * FROM approvals WHERE approval_id = ? AND status = 'approved'", (approval_id,))
        if not app:
            raise ToolError(f"Invalid or unapproved approval_id: {approval_id!r}.")

    summary = args.get("summary") or evt["summary"]
    description = args.get("description") if "description" in args else evt["description"]
    location = args.get("location") if "location" in args else evt["location"]
    
    start_time = evt["start_time"]
    end_time = evt["end_time"]
    time_zone = evt["time_zone"]

    iso_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
    if "start" in args and args["start"]:
        st_obj = args["start"]
        if isinstance(st_obj, str):
            st = st_obj
        elif isinstance(st_obj, dict):
            st = st_obj.get("dateTime")
            if st_obj.get("timeZone"):
                time_zone = st_obj["timeZone"]
        else:
            raise ToolError("Invalid start format.")
        if not st or not re.match(iso_pattern, st):
            raise ToolError("Invalid start.dateTime ISO string.")
        start_time = st

    if "end" in args and args["end"]:
        et_obj = args["end"]
        if isinstance(et_obj, str):
            et = et_obj
        elif isinstance(et_obj, dict):
            et = et_obj.get("dateTime")
        else:
            raise ToolError("Invalid end format.")
        if not et or not re.match(iso_pattern, et):
            raise ToolError("Invalid end.dateTime ISO string.")
        end_time = et

    if start_time != evt["start_time"] or end_time != evt["end_time"]:
        cal_conflicts = db.query(
            "SELECT event_id, summary FROM events WHERE calendar_id = ? AND event_id != ? AND status != 'cancelled' AND start_time < ? AND end_time > ?",
            (cal_id, evt_id, end_time, start_time)
        )
        if cal_conflicts:
            raise ToolError(f"Conflict: slot {start_time} - {end_time} conflicts with existing event {cal_conflicts[0]['event_id']} ({cal_conflicts[0]['summary']}).")

        if "attendees" in args and args["attendees"] is not None:
            attendees_in = args["attendees"]
            if isinstance(attendees_in, str):
                attendees_in = [x.strip() for x in attendees_in.split(",") if x.strip()]
            emails_to_check = []
            for a in attendees_in:
                if isinstance(a, str):
                    emails_to_check.append(a)
                elif isinstance(a, dict) and a.get("email"):
                    emails_to_check.append(a.get("email"))
        else:
            att_rows = db.query("SELECT email FROM event_attendees WHERE event_id = ?", (evt_id,))
            emails_to_check = [a["email"] for a in att_rows]

        for em in emails_to_check:
            att_conflicts = db.query(
                "SELECT e.event_id, e.summary FROM events e JOIN event_attendees a ON e.event_id = a.event_id "
                "WHERE a.email = ? AND e.event_id != ? AND e.status != 'cancelled' AND e.start_time < ? AND e.end_time > ?",
                (em, evt_id, end_time, start_time)
            )
            if att_conflicts:
                raise ToolError(f"Conflict: attendee {em} has a conflicting event {att_conflicts[0]['event_id']}.")

    clock_row = db.one("SELECT now FROM clock LIMIT 1")
    now_str = clock_row["now"] if clock_row else "2026-10-26T09:00:00Z"
    new_version = evt["version"] + 1

    db.execute(
        "UPDATE events SET summary = ?, description = ?, location = ?, start_time = ?, end_time = ?, time_zone = ?, version = ?, updated_at = ? WHERE event_id = ?",
        (summary, description, location, start_time, end_time, time_zone, new_version, now_str, evt_id)
    )

    if "attendees" in args and args["attendees"] is not None:
        db.execute("DELETE FROM event_attendees WHERE event_id = ?", (evt_id,))
        attendees_in = args["attendees"]
        if isinstance(attendees_in, str):
            attendees_in = [x.strip() for x in attendees_in.split(",") if x.strip()]
        for idx, att in enumerate(attendees_in):
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
                (att_id, evt_id, em, d_name)
            )

    msg_id = f"MSG-UPD-{evt_id}-{new_version}"
    db.execute(
        "INSERT INTO simulated_outbox (message_id, event_id, recipient_email, action, subject, payload, sent_at) "
        "VALUES (?, ?, 'all-attendees', 'update', ?, ?, ?)",
        (msg_id, evt_id, f"Rescheduled: {summary}", json.dumps({"start": start_time, "end": end_time}), now_str)
    )

    fault = db.one("SELECT * FROM fault_injections WHERE operation = 'update_event' AND is_active = TRUE")
    if fault:
        if fault["trigger_once"]:
            db.execute("UPDATE fault_injections SET is_active = FALSE WHERE fault_id = ?", (fault["fault_id"],))
        if fault["fault_type"] == "timeout_after_commit":
            raise ToolError("Gateway timeout: request timed out waiting for upstream server response.")
        elif fault["fault_type"] == "timeout":
            raise ToolError("Gateway timeout: request timed out waiting for upstream server.")

    att_rows = db.query("SELECT email, display_name, response_status, is_organizer, is_optional FROM event_attendees WHERE event_id = ?", (evt_id,))
    res_attendees = [
        {
            "email": a["email"],
            "displayName": a["display_name"],
            "responseStatus": a["response_status"],
            "organizer": bool(a["is_organizer"]),
            "optional": bool(a["is_optional"])
        }
        for a in att_rows
    ]

    return {
        "id": evt_id,
        "calendarId": cal_id,
        "summary": summary,
        "description": description,
        "location": location,
        "start": {"dateTime": start_time, "timeZone": time_zone},
        "end": {"dateTime": end_time, "timeZone": time_zone},
        "status": "confirmed",
        "attendees": res_attendees,
        "version": new_version,
        "updated": now_str
    }