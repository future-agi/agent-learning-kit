def handle(args, db):
    email, name, account_id = args.get("email"), args.get("name"), args.get("account_id")
    if not (email or name or account_id):
        raise ToolError("Give an email, a name or an account_id.", category="invalid_input")
    # Staff search the whole CRM; a prospect finds only their own record, so a search can never
    # tell them who else is on file.
    kind, who = _caller(db)
    if kind == "contact":
        rows = [who]
    elif kind == "staff":
        rows = db.query("SELECT * FROM contacts")
    else:
        raise ToolError("Permission denied: nobody is signed in to this conversation, so no contact can be searched.", category="permission_denied")
    out = []
    for c in rows:
        if email and c["email_normalized"] != _norm(email):
            continue
        if name and name.strip().lower() not in c["name"].lower():
            continue
        if account_id and c["account_id"] != account_id:
            continue
        out.append({"contact_id": c["contact_id"], "account_id": c["account_id"], "name": c["name"], "email": c["email"], "consent_status": c["consent_status"]})
    return {"contacts": out, "count": len(out), "matched_on": "normalized email" if email else "name or account"}


def _norm(email):
    return (email or "").strip().lower()


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
