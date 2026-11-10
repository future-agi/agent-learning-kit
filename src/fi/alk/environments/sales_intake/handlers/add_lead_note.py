def handle(args, db):
    l = _lead(db, args.get("lead_id"))
    body = (args.get("body") or "").strip()
    if not body:
        raise ToolError("body is required.", category="invalid_input")
    now = _now(db)
    n = db.one("SELECT COUNT(*) AS c FROM lead_notes")["c"]
    nid = f"N{501 + n}"
    db.execute("INSERT INTO lead_notes (note_id, lead_id, body, created_at) VALUES (?, ?, ?, ?)", [nid, l["lead_id"], body, now])
    db.execute("UPDATE leads SET updated_at = ? WHERE lead_id = ?", [now, l["lead_id"]])
    return {"note_id": nid, "lead_id": l["lead_id"], "created_at": now}

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
