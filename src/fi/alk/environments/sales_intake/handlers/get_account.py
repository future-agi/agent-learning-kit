def handle(args, db):
    aid = args.get("account_id")
    if not aid:
        raise ToolError("account_id is required.", category="invalid_input")
    a = db.one("SELECT * FROM accounts WHERE account_id = ?", [aid])
    if a is None:
        raise ToolError(f"Account {aid!r} not found.", category="not_found")
    contacts = db.query("SELECT contact_id, name, title FROM contacts WHERE account_id = ?", [aid])
    leads = db.query("SELECT lead_id, contact_id, status FROM leads WHERE account_id = ?", [aid])
    # The company's own facts are a directory entry; who else works there and their enquiries are
    # not: a prospect sees only themselves and their own leads.
    kind, who = _caller(db)
    if kind != "staff":
        mine = who["contact_id"] if kind == "contact" else None
        contacts = [c for c in contacts if c["contact_id"] == mine]
        leads = [l for l in leads if l["contact_id"] == mine]
    return {"account_id": a["account_id"], "name": a["name"], "domain": a["domain"], "country": a["country"],
            "employee_count": a["employee_count"], "industry": a["industry"], "contacts": contacts, "leads": leads,
            "note": "Only facts the company gave us are recorded; unknown fields stay empty."}


def _caller(db):
    """Who is in this conversation, as the session set it: ("contact", row) for a prospect speaking
    for themselves, ("staff", row) for a BrightDesk employee (their Slack user), or (None, None).
    An ID or an email the desk is told never changes it."""
    row = db.one("SELECT person_id FROM session_caller WHERE id = 'default'")
    person = row["person_id"] if row else None
    if person:
        contact = db.one("SELECT * FROM contacts WHERE contact_id = ?", [person])
        if contact:
            return "contact", contact
        staff = db.one("SELECT * FROM slack_users WHERE user_id = ?", [person])
        if staff:
            return "staff", staff
    return None, None


def _named(kind, who):
    if kind == "contact":
        return f"{who['name']} ({who['contact_id']})"
    if kind == "staff":
        return f"{who['name']} ({who['user_id']})"
    return "nobody signed in"
