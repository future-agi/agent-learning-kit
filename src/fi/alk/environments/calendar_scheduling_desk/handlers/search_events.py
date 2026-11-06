def handle(args, db):
    query = args.get("query")
    if not query or not isinstance(query, str):
        raise ToolError("Missing required parameter: query", category="invalid_input")
    page_size = args.get("pageSize")
    if page_size is None:
        page_size = 20
    if not isinstance(page_size, int) or page_size < 1 or page_size > 250:
        raise ToolError("pageSize must be an integer from 1 to 250", category="invalid_input")
    offset = 0
    if args.get("pageToken"):
        try:
            offset = int(args["pageToken"])
        except ValueError:
            raise ToolError(f"Invalid pageToken: {args['pageToken']}", category="invalid_input")

    # The user's primary calendar only, as the official tool searches.
    primary = db.one("SELECT calendar_id FROM calendars WHERE primary_calendar = TRUE")
    cal_id = primary["calendar_id"] if primary else "CAL-PRIMARY"
    terms = query.lower().split()
    events = []
    for r in db.query("SELECT * FROM events WHERE calendar_id = ? AND status != 'cancelled' ORDER BY start_time ASC", (cal_id,)):
        att_rows = db.query("SELECT email, display_name, response_status, is_organizer, is_optional FROM event_attendees WHERE event_id = ?", (r["event_id"],))
        text = f"{r['summary']} {r['description'] or ''} {r['location'] or ''} {' '.join(a['email'] + ' ' + (a['display_name'] or '') for a in att_rows)}".lower()
        if not all(term in text for term in terms):
            continue
        events.append({
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
            "attendees": [
                {
                    "email": a["email"],
                    "displayName": a["display_name"],
                    "responseStatus": a["response_status"],
                    "organizer": bool(a["is_organizer"]),
                    "optionalAttendee": bool(a["is_optional"]),
                }
                for a in att_rows
            ],
            "eventType": "DEFAULT",
            "version": r["version"],
        })
    page = events[offset : offset + page_size]
    result = {"events": page}
    if offset + page_size < len(events):
        result["nextPageToken"] = str(offset + page_size)
    return result
