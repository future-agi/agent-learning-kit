def handle(args, db):
    q = "SELECT * FROM leads WHERE status NOT IN ('disqualified', 'closed')"
    p = []
    # Staff see the pipeline; a prospect sees only their own leads, whatever filter is asked for.
    kind, who = _caller(db)
    if kind == "contact":
        q += " AND contact_id = ?"
        p.append(who["contact_id"])
    elif kind != "staff":
        raise ToolError("Permission denied: nobody is signed in to this conversation, so no lead can be listed.", category="permission_denied")
    for k in ("contact_id", "account_id", "owner_rep_id"):
        if args.get(k):
            q += f" AND {k} = ?"
            p.append(args[k])
    rows = [_lead_view(l) for l in db.query(q + " ORDER BY lead_id", p)]
    return {"leads": rows, "count": len(rows)}


def _lead_view(l):
    return {k: l[k] for k in ("lead_id", "contact_id", "account_id", "source", "use_case", "seats", "timeline", "country", "budget", "status", "owner_rep_id", "queue_id", "created_at", "updated_at")}


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
