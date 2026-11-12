def handle(args, db):
    rep = db.one("SELECT * FROM reps WHERE rep_id = ?", [args.get("rep_id")])
    if rep is None:
        raise ToolError(f"Rep {args.get('rep_id')!r} not found.", category="not_found")
    start = args.get("start_time") or _now(db)
    end = args.get("end_time") or "2026-11-17T00:00:00Z"
    busy = db.query("SELECT event_id, summary, start_time, end_time FROM calendar_events WHERE calendar_id = ? AND status != 'cancelled' AND end_time > ? AND start_time < ? ORDER BY start_time",
                    [rep["calendar_id"], start, end])
    # A rep's events name other customers; a prospect is told only when the rep is busy.
    if _caller(db)[0] != "staff":
        busy = [{"start_time": b["start_time"], "end_time": b["end_time"]} for b in busy]
    return {"rep_id": rep["rep_id"], "name": rep["name"], "time_zone": rep["time_zone"], "calendar_id": rep["calendar_id"], "busy": busy,
            "note": "Busy times come from the rep's calendar; offer demo slots from list_demo_slots."}

def _now(db):
    row = db.one("SELECT now FROM clock")
    return row["now"] if row else "2026-11-10T15:00:00Z"


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
