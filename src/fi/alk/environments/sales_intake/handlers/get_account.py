def handle(args, db):
    aid = args.get("account_id")
    if not aid:
        raise ToolError("account_id is required.", category="invalid_input")
    a = db.one("SELECT * FROM accounts WHERE account_id = ?", [aid])
    if a is None:
        raise ToolError(f"Account {aid!r} not found.", category="not_found")
    contacts = db.query("SELECT contact_id, name, title FROM contacts WHERE account_id = ?", [aid])
    leads = db.query("SELECT lead_id, contact_id, status FROM leads WHERE account_id = ?", [aid])
    return {"account_id": a["account_id"], "name": a["name"], "domain": a["domain"], "country": a["country"],
            "employee_count": a["employee_count"], "industry": a["industry"], "contacts": contacts, "leads": leads,
            "note": "Only facts the company gave us are recorded; unknown fields stay empty."}

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
