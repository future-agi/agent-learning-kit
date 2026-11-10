def handle(args, db):
    l = _lead(db, args.get("lead_id"))
    out = _lead_view(l)
    out["notes"] = db.query("SELECT note_id, body, created_at FROM lead_notes WHERE lead_id = ?", [l["lead_id"]])
    out["demos"] = db.query("SELECT demo_id, slot_id, rep_id, status FROM demos WHERE lead_id = ?", [l["lead_id"]])
    return out


def _lead(db, lead_id):
    if not lead_id:
        raise ToolError("lead_id is required.", category="invalid_input")
    row = db.one("SELECT * FROM leads WHERE lead_id = ?", [lead_id])
    if row is None:
        raise ToolError(f"Lead {lead_id!r} not found.", category="not_found")
    return row


def _lead_view(l):
    return {k: l[k] for k in ("lead_id", "contact_id", "account_id", "source", "use_case", "seats", "timeline", "country", "budget", "status", "owner_rep_id", "queue_id", "created_at", "updated_at")}
