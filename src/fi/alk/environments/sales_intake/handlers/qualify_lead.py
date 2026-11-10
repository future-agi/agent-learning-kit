def handle(args, db):
    l = _lead(db, args.get("lead_id"))
    if l["status"] in ("disqualified", "closed"):
        raise ToolError(f"Lead {l['lead_id']} is {l['status']}.", category="conflict")
    facts = args.get("facts") or {}
    if not isinstance(facts, dict):
        raise ToolError("facts must be an object of rubric fields.", category="invalid_input")
    known = {r["field"]: r for r in db.query("SELECT * FROM icp_rules")}
    unknown = sorted(k for k in facts if k not in known)
    if unknown:
        raise ToolError(f"Unknown rubric field(s) {unknown}; the rubric has {sorted(known)}.", category="invalid_input")
    now = _now(db)
    merged = {k: l[k] for k in known}
    for k, v in facts.items():
        if v in (None, ""):
            continue
        if k == "seats":
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ToolError("seats must be a number.", category="invalid_input")
        merged[k] = v
    missing = [k for k, r in known.items() if r["required"] and merged.get(k) in (None, "")]
    reasons = []
    if not missing:
        if int(merged["seats"]) < int(_param(db, "min_qualifying_seats", 20)):
            reasons.append(f"seats {merged['seats']} below {_param(db, 'min_qualifying_seats', 20)}")
        if merged["use_case"] not in ("support", "customer_service", "helpdesk"):
            reasons.append(f"use case {merged['use_case']!r} outside the rubric")
        if merged["timeline"] not in ("0-3 months", "3-6 months"):
            reasons.append(f"timeline {merged['timeline']!r} beyond six months")
    status = "needs_information" if missing else ("disqualified" if reasons else ("qualified" if l["status"] not in ("assigned", "demo_booked") else l["status"]))
    db.execute("UPDATE leads SET use_case = ?, seats = ?, timeline = ?, country = ?, budget = ?, status = ?, updated_at = ? WHERE lead_id = ?",
               [merged["use_case"], merged["seats"], merged["timeline"], merged["country"], merged["budget"], status, now, l["lead_id"]])
    return {"lead_id": l["lead_id"], "status": status, "missing_fields": missing, "reasons": reasons,
            "facts": merged, "message": "Ask the prospect for the missing fields; do not guess them." if missing else f"Lead {status}."}

def _now(db):
    row = db.one("SELECT now FROM clock")
    return row["now"] if row else "2026-11-10T15:00:00Z"


def _param(db, name, default):
    import json
    row = db.one("SELECT value FROM environment_policy WHERE name = ?", [name])
    return json.loads(row["value"]) if row else default


def _lead(db, lead_id):
    if not lead_id:
        raise ToolError("lead_id is required.", category="invalid_input")
    row = db.one("SELECT * FROM leads WHERE lead_id = ?", [lead_id])
    if row is None:
        raise ToolError(f"Lead {lead_id!r} not found.", category="not_found")
    return row


