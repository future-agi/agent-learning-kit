def handle(args, db):
    c = _contact(db, args.get("contact_id"))
    leads = db.query("SELECT lead_id, status FROM leads WHERE contact_id = ?", [c["contact_id"]])
    return {"contact_id": c["contact_id"], "account_id": c["account_id"], "name": c["name"], "email": c["email"], "title": c["title"], "country": c["country"],
            "consent_status": c["consent_status"], "opted_out_at": c["opted_out_at"], "leads": leads}


def _contact(db, contact_id):
    row = db.one("SELECT * FROM contacts WHERE contact_id = ?", [contact_id])
    if row is None:
        raise ToolError(f"Contact {contact_id!r} not found.", category="not_found")
    return row


