def handle(args, db):
    c = _contact(db, args.get("contact_id"))
    kind, who = _caller(db)
    if kind != "staff" and not (kind == "contact" and who["contact_id"] == c["contact_id"]):
        raise ToolError(f"Permission denied: contact {c['contact_id']} is not {_named(kind, who)}, the person in this conversation; another person's record is not shared.", category="permission_denied")
    leads = db.query("SELECT lead_id, status FROM leads WHERE contact_id = ?", [c["contact_id"]])
    return {"contact_id": c["contact_id"], "account_id": c["account_id"], "name": c["name"], "email": c["email"], "title": c["title"], "country": c["country"],
            "consent_status": c["consent_status"], "opted_out_at": c["opted_out_at"], "leads": leads}


def _contact(db, contact_id):
    row = db.one("SELECT * FROM contacts WHERE contact_id = ?", [contact_id])
    if row is None:
        raise ToolError(f"Contact {contact_id!r} not found.", category="not_found")
    return row


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
