def _handle(args, db):
    import json

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
        raise ToolError(f"Event {evt_id!r} is already cancelled.", category="conflict")

    req_app_row = db.one("SELECT value FROM environment_policy WHERE name = 'require_approval_for_mutations'")
    require_app = bool(json.loads(req_app_row["value"])) if req_app_row else True
    approval_id = args.get("approval_id")
    if require_app:
        if not approval_id:
            raise ToolError("Policy: deleting an event needs an approval_id for this exact event; confirm it with the user first.", category="policy_blocked")
        if not db.one("SELECT * FROM calendar_approvals WHERE approval_id = ? AND status = 'approved'", (approval_id,)):
            raise ToolError(f"Invalid or unapproved approval_id: {approval_id!r}.", category="policy_blocked")

    recurring_action = args.get("recurring_action") or "this_event"
    if recurring_action not in ("this_event", "all_events"):
        raise ToolError("recurring_action must be this_event or all_events", category="invalid_input")
    clock_row = db.one("SELECT now FROM clock LIMIT 1")
    now_str = clock_row["now"] if clock_row else "2026-10-26T09:00:00Z"
    if recurring_action == "all_events" and evt["recurrence_rule"]:
        db.execute("UPDATE calendar_events SET status = 'cancelled', updated_at = ? WHERE recurring_event_id = ? OR event_id = ?", (now_str, evt_id, evt_id))
    else:
        db.execute("UPDATE calendar_events SET status = 'cancelled', updated_at = ? WHERE event_id = ?", (now_str, evt_id))
    db.execute(
        "INSERT INTO calendar_simulated_outbox (message_id, event_id, recipient_email, action, subject, payload, sent_at) VALUES (?, ?, 'all-attendees', 'cancellation', ?, ?, ?)",
        (f"MSG-DEL-{evt_id}", evt_id, f"Cancelled: {evt['summary']}", json.dumps({"status": "cancelled"}), now_str),
    )
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
        "status": "cancelled",
        "htmlLink": f"https://calendar.example.com/event?eid={evt_id}",
        "created": evt["created_at"],
        "updated": now_str,
        "summary": evt["summary"],
        "description": evt["description"],
        "location": evt["location"],
        "start": {"dateTime": evt["start_time"], "timeZone": evt["time_zone"]},
        "end": {"dateTime": evt["end_time"], "timeZone": evt["time_zone"]},
        "recurrence": [evt["recurrence_rule"]] if evt["recurrence_rule"] else [],
        "recurringEventId": evt["recurring_event_id"],
        "attendees": attendees,
        "eventType": "DEFAULT",
        "version": evt["version"],
    }


_WRITES = ("calendar_events", "calendar_simulated_outbox")

def _all_or_nothing(db, run):
    """Run the call so that a failed call changes nothing. A handler gets no transaction of its own,
    so every table this tool writes is held first and put back if the call raises, whether a write
    failed partway or the call refused after writing. Only a fault the world simulates on purpose
    (an error marked keep_writes: a rival booking, a commit whose reply was lost, a fault used up)
    keeps what it wrote."""
    held = {table: db.query(f"SELECT * FROM {table}") for table in _WRITES}
    try:
        return run()
    except Exception as failed:
        if not getattr(failed, "keep_writes", False):
            for table, rows in held.items():
                if _same(db.query(f"SELECT * FROM {table}"), rows):
                    continue
                db.execute(f"DELETE FROM {table}")
                for row in rows:
                    cols = list(row)
                    db.execute(
                        f"INSERT INTO {table} ({', '.join(chr(34) + c + chr(34) for c in cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [row[c] for c in cols],
                    )
        raise

def _same(rows, held):
    """Whether a table still holds exactly the rows it held, in any order: only a table the call
    changed is rewritten."""
    import json

    def key(row):
        return json.dumps(row, sort_keys=True, default=str)

    return sorted(map(key, rows)) == sorted(map(key, held))


def handle(args, db):
    return _all_or_nothing(db, lambda: _handle(args, db))
