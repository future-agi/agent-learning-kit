def handle(args, db):
    evt_id = args.get("eventId")
    if not evt_id or not isinstance(evt_id, str):
        raise ToolError("Missing required parameter: eventId", category="invalid_input")
    cal_id = args.get("calendarId")
    if not cal_id:
        primary = db.one("SELECT calendar_id FROM calendars WHERE primary_calendar = TRUE")
        cal_id = primary["calendar_id"] if primary else "CAL-PRIMARY"
    cal = db.one("SELECT * FROM calendars WHERE calendar_id = ?", (cal_id,))
    if not cal:
        raise ToolError(f"Calendar not found: {cal_id}", category="not_found")

    r = db.one("SELECT * FROM events WHERE calendar_id = ? AND event_id = ?", (cal_id, evt_id))
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
        for a in db.query("SELECT email, display_name, response_status, is_organizer, is_optional FROM event_attendees WHERE event_id = ?", (evt_id,))
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
