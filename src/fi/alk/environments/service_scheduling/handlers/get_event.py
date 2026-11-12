def _caller(db):
    """Who is in this conversation, as the session knows it (session_caller), never as the
    conversation tells it: a customer, a front-desk member, or nobody identified."""
    row = db.one("SELECT customer_id, desk_id FROM session_caller WHERE id = 'default'")
    return (row["customer_id"], row["desk_id"]) if row else (None, None)


def _desk(db):
    """Whether the caller is an active front-desk member, who may act for any customer."""
    _, desk_id = _caller(db)
    return bool(desk_id and db.one("SELECT 1 AS x FROM desk_staff WHERE desk_id = ? AND role = 'front_desk' AND active = 1", [desk_id]))


def _shown(db, r, att_rows):
    """An event as this caller may see it. Another customer's booking on a staff calendar shows as
    busy time, not as who is booked: the front desk sees everything, a customer their own."""
    d = r["description"] or ""
    if not d.startswith("appointment:") or _desk(db):
        return r, att_rows
    a = db.one("SELECT customer_id FROM appointments WHERE appointment_id = ?", [d.split(":", 1)[1]])
    if a and a["customer_id"] == _caller(db)[0]:
        return r, att_rows
    r = dict(r)
    r.update({"summary": "Booked", "description": None})
    return r, []

def handle(args, db):
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

    r = db.one("SELECT * FROM calendar_events WHERE calendar_id = ? AND event_id = ?", (cal_id, evt_id))
    if not r:
        raise ToolError(f"Event {evt_id!r} not found on calendar {cal_id!r}.", category="not_found")

    r, rows = _shown(db, r, db.query("SELECT email, display_name, response_status, is_organizer, is_optional FROM calendar_event_attendees WHERE event_id = ?", (evt_id,)))
    attendees = [
        {
            "email": a["email"],
            "displayName": a["display_name"],
            "responseStatus": a["response_status"],
            "organizer": bool(a["is_organizer"]),
            "optionalAttendee": bool(a["is_optional"]),
        }
        for a in rows
    ]
    return {
        "id": r["event_id"],
        "status": r["status"],
        "htmlLink": f"https://calendar.example.com/event?eid={r['event_id']}",
        "created": r["created_at"],
        "updated": r["updated_at"],
        "summary": r["summary"],
        "description": r["description"],
        "location": r["location"],
        "start": {"dateTime": r["start_time"], "timeZone": r["time_zone"]},
        "end": {"dateTime": r["end_time"], "timeZone": r["time_zone"]},
        "recurrence": [r["recurrence_rule"]] if r["recurrence_rule"] else [],
        "recurringEventId": r["recurring_event_id"],
        "attendees": attendees,
        "eventType": "DEFAULT",
        "version": r["version"],
    }
