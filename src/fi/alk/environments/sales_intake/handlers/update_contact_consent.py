def handle(args, db):
    c = _contact(db, args.get("contact_id"))
    status = args.get("status")
    if status not in ("opted_out", "opted_in"):
        raise ToolError("status must be opted_out or opted_in.", category="invalid_input")
    if not args.get("confirmed"):
        raise ToolError("Read the change back to the person and pass confirmed=true once they agree.", category="policy_blocked")
    if status == "opted_in" and c["consent_status"] == "opted_out" and (args.get("source") or "") != "prospect_request":
        raise ToolError("An opt-out is lifted only at the person's own request (source prospect_request).", category="policy_blocked")
    now = _now(db)
    if c["consent_status"] == status:
        return {"contact_id": c["contact_id"], "consent_status": status, "message": "Already recorded.", "idempotent_replay": True}
    db.execute("UPDATE contacts SET consent_status = ?, opted_out_at = ?, updated_at = ? WHERE contact_id = ?",
               [status, now if status == "opted_out" else None, now, c["contact_id"]])
    n = db.one("SELECT COUNT(*) AS c FROM consent_log")["c"]
    db.execute("INSERT INTO consent_log (entry_id, contact_id, status, source, created_at) VALUES (?, ?, ?, ?, ?)", [f"CL{501 + n}", c["contact_id"], status, args.get("source") or "chat", now])
    stopped = []
    if status == "opted_out":
        for item in db.query("SELECT item_id FROM outreach_queue WHERE contact_id = ? AND status = 'planned'", [c["contact_id"]]):
            db.execute("UPDATE outreach_queue SET status = 'cancelled', updated_at = ? WHERE item_id = ?", [now, item["item_id"]])
            stopped.append(item["item_id"])
    return {"contact_id": c["contact_id"], "consent_status": status, "planned_outreach_cancelled": stopped,
            "message": "Opt-out recorded; planned follow-ups stopped. Contact and audit data are kept." if status == "opted_out" else "Opt-in recorded."}

def _now(db):
    row = db.one("SELECT now FROM clock")
    return row["now"] if row else "2026-11-10T15:00:00Z"


def _contact(db, contact_id):
    row = db.one("SELECT * FROM contacts WHERE contact_id = ?", [contact_id])
    if row is None:
        raise ToolError(f"Contact {contact_id!r} not found.", category="not_found")
    return row


