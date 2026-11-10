def handle(args, db):
    d = db.one("SELECT * FROM demos WHERE demo_id = ?", [args.get("demo_id")])
    if d is None:
        raise ToolError(f"Demo {args.get('demo_id')!r} not found.", category="not_found")
    if not args.get("confirmed"):
        raise ToolError("Confirm which demo is cancelled with the person, then pass confirmed=true.", category="policy_blocked")
    if d["status"] == "cancelled":
        return {"demo_id": d["demo_id"], "status": "cancelled", "idempotent_replay": True}
    now = _now(db)
    db.execute("UPDATE demos SET status = 'cancelled', updated_at = ? WHERE demo_id = ?", [now, d["demo_id"]])
    db.execute("UPDATE calendar_events SET status = 'cancelled', updated_at = ? WHERE event_id = ?", [now, d["calendar_event_id"]])
    db.execute("UPDATE demo_slots SET status = 'open' WHERE slot_id = ?", [d["slot_id"]])
    db.execute("UPDATE leads SET status = 'qualified', updated_at = ? WHERE lead_id = ? AND status = 'demo_booked'", [now, d["lead_id"]])
    return {"demo_id": d["demo_id"], "lead_id": d["lead_id"], "status": "cancelled", "calendar_event_id": d["calendar_event_id"], "slot_id": d["slot_id"]}

def _now(db):
    row = db.one("SELECT now FROM clock")
    return row["now"] if row else "2026-11-10T15:00:00Z"


