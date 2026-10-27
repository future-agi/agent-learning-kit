def handle(args, db):
    cal_id = args.get("calendar_id")
    evt_id = args.get("event_id")
    if not cal_id:
        raise ToolError("Missing required parameter: calendar_id")
    if not evt_id:
        raise ToolError("Missing required parameter: event_id")
        
    cal = db.one("SELECT * FROM calendars WHERE calendar_id = ?", (cal_id,))
    if not cal:
        raise ToolError(f"Calendar not found: {cal_id}")
        
    r = db.one("SELECT * FROM events WHERE calendar_id = ? AND event_id = ?", (cal_id, evt_id))
    if not r:
        raise ToolError(f"Event {evt_id!r} not found on calendar {cal_id!r}.")
        
    att_rows = db.query("SELECT email, display_name, response_status, is_organizer, is_optional FROM event_attendees WHERE event_id = ?", (evt_id,))
    attendees = [
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
        "id": r["event_id"],
        "calendarId": r["calendar_id"],
        "summary": r["summary"],
        "description": r["description"],
        "location": r["location"],
        "start": {"dateTime": r["start_time"], "timeZone": r["time_zone"]},
        "end": {"dateTime": r["end_time"], "timeZone": r["time_zone"]},
        "status": r["status"],
        "attendees": attendees,
        "version": r["version"],
        "recurringEventId": r["recurring_event_id"],
        "recurrence": [r["recurrence_rule"]] if r["recurrence_rule"] else [],
        "created": r["created_at"],
        "updated": r["updated_at"]
    }