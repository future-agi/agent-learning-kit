def _hidden_patient(db, event):
    """The patient a provider-calendar booking belongs to, when this conversation may not see it, or
    None. Busy and free time on a provider's calendar is public (it is the clinic's availability);
    which appointment a booking is, and so whose visit, is the patient's: shown only once that
    patient is verified by the person talking, who must be them or hold recorded guardian or proxy
    authority for them. Blocks and the desk's own calendar carry no patient and stay open."""
    owners = [a["patient_id"] for a in db.query("SELECT patient_id FROM appointments WHERE calendar_event_id = ?", (event["event_id"],))]
    if not owners and str(event.get("description") or "").startswith("appointment_id: "):
        owners = [a["patient_id"] for a in db.query("SELECT patient_id FROM appointments WHERE appointment_id = ?", (event["description"][len("appointment_id: "):],))]
    row = db.one("SELECT caller_id FROM session_caller WHERE id = 'default'")
    caller = row["caller_id"] if row else None
    for pid in owners:
        entitled = caller is not None and (caller == pid or db.one(
            "SELECT patient_id FROM caller_access WHERE caller_id = ? AND patient_id = ?", (caller, pid)) is not None)
        if not entitled or not db.one("SELECT patient_id FROM verified_patients WHERE patient_id = ?", (pid,)):
            return pid
    return None


def _busy(event):
    """Someone else's booking as availability alone: when, where and that it is taken."""
    return dict(event, event_id="busy", summary="Busy (booked)", description=None, recurrence_rule=None, recurring_event_id=None)


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
        hidden = _hidden_patient(db, r)
        att_rows = [] if hidden else db.query("SELECT email, display_name, response_status, is_organizer, is_optional FROM calendar_event_attendees WHERE event_id = ?", (r["event_id"],))
        if hidden:
            # Matched and returned only as what it shows: a search term cannot find a booking by
            # its appointment id when the booking itself would not be shown.
            r = _busy(r)
        if full_text:
            text = f"{r['summary']} {r['description'] or ''} {r['location'] or ''} {' '.join(a['email'] for a in att_rows)}".lower()
            if not all(term in text for term in full_text):
                continue
        events.append({
            "id": r["event_id"],
            "status": r["status"],
            "htmlLink": None if hidden else f"https://calendar.example.com/event?eid={r['event_id']}",
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
