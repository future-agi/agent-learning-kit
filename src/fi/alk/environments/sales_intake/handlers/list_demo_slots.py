def handle(args, db):
    rep_id = args.get("rep_id")
    if not rep_id and args.get("lead_id"):
        rep_id = _lead(db, args["lead_id"])["owner_rep_id"]
        if not rep_id:
            raise ToolError("The lead has no owner yet; assign it before offering demo slots.", category="policy_blocked")
    rep = db.one("SELECT * FROM reps WHERE rep_id = ?", [rep_id])
    if rep is None:
        raise ToolError(f"Rep {rep_id!r} not found.", category="not_found")
    now = _now(db)
    out = []
    for s in db.query("SELECT * FROM demo_slots WHERE rep_id = ? AND status = 'open' AND start_time > ? ORDER BY start_time", [rep_id, now]):
        clash = db.one("SELECT event_id FROM calendar_events WHERE calendar_id = ? AND status != 'cancelled' AND start_time < ? AND end_time > ?", [rep["calendar_id"], s["end_time"], s["start_time"]])
        if not clash:
            out.append({"slot_id": s["slot_id"], "start_time": s["start_time"], "end_time": s["end_time"], "rep_time_zone": rep["time_zone"]})
    return {"rep_id": rep_id, "slots": out, "count": len(out), "note": "Offer these to the prospect; book only the one they choose."}

def _now(db):
    row = db.one("SELECT now FROM clock")
    return row["now"] if row else "2026-11-10T15:00:00Z"


def _lead(db, lead_id):
    if not lead_id:
        raise ToolError("lead_id is required.", category="invalid_input")
    row = db.one("SELECT * FROM leads WHERE lead_id = ?", [lead_id])
    if row is None:
        raise ToolError(f"Lead {lead_id!r} not found.", category="not_found")
    return row


