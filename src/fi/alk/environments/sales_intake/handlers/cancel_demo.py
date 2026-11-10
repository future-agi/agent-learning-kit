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


def _param(db, name, default):
    import json
    row = db.one("SELECT value FROM environment_policy WHERE name = ?", [name])
    return json.loads(row["value"]) if row else default


def _norm(email):
    return (email or "").strip().lower()


def _lead(db, lead_id):
    if not lead_id:
        raise ToolError("lead_id is required.", category="invalid_input")
    row = db.one("SELECT * FROM leads WHERE lead_id = ?", [lead_id])
    if row is None:
        raise ToolError(f"Lead {lead_id!r} not found.", category="not_found")
    return row


def _contact(db, contact_id):
    row = db.one("SELECT * FROM contacts WHERE contact_id = ?", [contact_id])
    if row is None:
        raise ToolError(f"Contact {contact_id!r} not found.", category="not_found")
    return row


def _expected_owner(db, country):
    rule = db.one("SELECT * FROM territory_rules WHERE lower(country) = lower(?)", [country or ""])
    if rule:
        return {"kind": "rep", "id": rule["rep_id"], "rule": "territory", "rule_id": rule["rule_id"]}
    return {"kind": "queue", "id": _param(db, "fallback_queue", "Q-SDR-FALLBACK"), "rule": "fallback", "rule_id": None}


def _lead_view(l):
    return {k: l[k] for k in ("lead_id", "contact_id", "account_id", "source", "use_case", "seats", "timeline", "country", "budget", "status", "owner_rep_id", "queue_id", "created_at", "updated_at")}
