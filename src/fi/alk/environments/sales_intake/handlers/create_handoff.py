def handle(args, db):
    l = _lead(db, args.get("lead_id"))
    kind = args.get("kind") or "rep_handoff"
    if kind not in ("rep_handoff", "specialist", "follow_up"):
        raise ToolError("kind must be rep_handoff, specialist or follow_up.", category="invalid_input")
    summary = (args.get("summary") or "").strip()
    if not summary:
        raise ToolError("summary is required.", category="invalid_input")
    key = args.get("request_key")
    if key:
        prior = db.one("SELECT * FROM handoffs WHERE request_key = ?", [key])
        if prior:
            return dict(prior, idempotent_replay=True)
    c = _contact(db, l["contact_id"])
    if kind == "follow_up" and c["consent_status"] == "opted_out":
        raise ToolError(f"Contact {c['contact_id']} opted out; no follow-up outreach may be planned.", category="policy_blocked")
    if kind == "rep_handoff":
        if l["status"] not in ("qualified", "demo_booked"):
            raise ToolError(f"Lead {l['lead_id']} is {l['status']}; a rep handoff needs a qualified lead.", category="policy_blocked")
        if not (l["owner_rep_id"] or l["queue_id"]):
            raise ToolError(f"Lead {l['lead_id']} has no owner yet.", category="policy_blocked")
    to = l["owner_rep_id"] or l["queue_id"] or _param(db, "fallback_queue", "Q-SDR-FALLBACK")
    now = _now(db)
    n = db.one("SELECT COUNT(*) AS c FROM handoffs")["c"]
    hid = f"H{501 + n}"
    db.execute("INSERT INTO handoffs (handoff_id, lead_id, kind, assigned_to, summary, status, request_key, created_at) VALUES (?, ?, ?, ?, ?, 'open', ?, ?)", [hid, l["lead_id"], kind, to, summary, key, now])
    return {"handoff_id": hid, "lead_id": l["lead_id"], "kind": kind, "assigned_to": to, "status": "open", "created_at": now}

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


def _contact(db, contact_id):
    row = db.one("SELECT * FROM contacts WHERE contact_id = ?", [contact_id])
    if row is None:
        raise ToolError(f"Contact {contact_id!r} not found.", category="not_found")
    return row


