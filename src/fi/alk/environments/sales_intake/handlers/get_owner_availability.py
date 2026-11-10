def handle(args, db):
    rep = db.one("SELECT * FROM reps WHERE rep_id = ?", [args.get("rep_id")])
    if rep is None:
        raise ToolError(f"Rep {args.get('rep_id')!r} not found.", category="not_found")
    start = args.get("start_time") or _now(db)
    end = args.get("end_time") or "2026-11-17T00:00:00Z"
    busy = db.query("SELECT event_id, summary, start_time, end_time FROM calendar_events WHERE calendar_id = ? AND status != 'cancelled' AND end_time > ? AND start_time < ? ORDER BY start_time",
                    [rep["calendar_id"], start, end])
    return {"rep_id": rep["rep_id"], "name": rep["name"], "time_zone": rep["time_zone"], "calendar_id": rep["calendar_id"], "busy": busy,
            "note": "Busy times come from the rep's calendar; offer demo slots from list_demo_slots."}

def _now(db):
    row = db.one("SELECT now FROM clock")
    return row["now"] if row else "2026-11-10T15:00:00Z"


