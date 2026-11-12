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
    import re

    cal_id = args.get("calendarId")
    if not cal_id:
        primary = db.one("SELECT calendar_id FROM calendar_calendars WHERE primary_calendar = TRUE")
        cal_id = primary["calendar_id"] if primary else "CAL-PRIMARY"
    cal = db.one("SELECT * FROM calendar_calendars WHERE calendar_id = ?", (cal_id,))
    if not cal:
        raise ToolError(f"Calendar not found: {cal_id}", category="not_found")

    iso_pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
    start_time = args.get("startTime")
    end_time = args.get("endTime")
    if start_time and not re.match(iso_pattern, start_time):
        raise ToolError(f"startTime must be an ISO 8601 timestamp: {start_time}", category="invalid_input")
    if end_time and not re.match(iso_pattern, end_time):
        raise ToolError(f"endTime must be an ISO 8601 timestamp: {end_time}", category="invalid_input")
    if start_time and end_time and start_time >= end_time:
        raise ToolError("startTime must be less than endTime", category="invalid_input")
    page_size = args.get("pageSize")
    if page_size is None:
        page_size = 100
    if not isinstance(page_size, int) or page_size < 1 or page_size > 250:
        raise ToolError("pageSize must be an integer from 1 to 250", category="invalid_input")
    offset = 0
    if args.get("pageToken"):
        try:
            offset = int(args["pageToken"])
        except ValueError:
            raise ToolError(f"Invalid pageToken: {args['pageToken']}", category="invalid_input")
    order_by = args.get("orderBy") or "default"
    if order_by not in ("default", "startTime", "startTimeDesc", "lastModified"):
        raise ToolError(f"Unknown orderBy {order_by!r}", category="invalid_input")
    full_text = (args.get("fullText") or "").lower().split()

    query = "SELECT * FROM calendar_events WHERE calendar_id = ? AND status != 'cancelled'"
    params = [cal_id]
    if start_time:
        query += " AND end_time >= ?"
        params.append(start_time)
    if end_time:
        query += " AND start_time <= ?"
        params.append(end_time)
    query += {"startTimeDesc": " ORDER BY start_time DESC", "lastModified": " ORDER BY updated_at DESC"}.get(order_by, " ORDER BY start_time ASC")
    rows = db.query(query, params)

    events = []
    for r in rows:
        att_rows = db.query("SELECT email, display_name, response_status, is_organizer, is_optional FROM calendar_event_attendees WHERE event_id = ?", (r["event_id"],))
        # Redacted before the search too, so a keyword cannot reveal who is booked.
        r, att_rows = _shown(db, r, att_rows)
        if full_text:
            text = f"{r['summary']} {r['description'] or ''} {r['location'] or ''} {' '.join(a['email'] for a in att_rows)}".lower()
            if not all(term in text for term in full_text):
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
    clock_row = db.one("SELECT now FROM clock LIMIT 1")
    result = {
        "summary": cal["summary"],
        "description": cal["description"],
        "updated": clock_row["now"] if clock_row else "2026-10-26T09:00:00Z",
        "timeZone": cal["time_zone"],
        "accessRole": cal["access_role"],
        "defaultReminders": [],
        "events": page,
    }
    if offset + page_size < len(events):
        result["nextPageToken"] = str(offset + page_size)
    return result
