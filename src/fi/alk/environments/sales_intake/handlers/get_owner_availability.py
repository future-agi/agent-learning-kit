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
