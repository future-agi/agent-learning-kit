def handle(args, db):
    evt_id = args.get("eventId")
    # The reps' calendars and the desk's Slack are internal: they name other customers.
    if _caller(db)[0] != "staff":
        raise ToolError("Permission denied: this is BrightDesk's internal workspace; only staff in this conversation can read it.", category="permission_denied")
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
