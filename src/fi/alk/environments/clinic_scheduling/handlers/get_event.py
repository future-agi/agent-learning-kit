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
    if _hidden_patient(db, r):
        raise ToolError(f"Event {evt_id!r} is another patient's booking, or a patient not yet verified in this conversation; their visit details are theirs. Free and busy times come from list_events or find_available_slots.", category="permission_denied")

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
