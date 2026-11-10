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


