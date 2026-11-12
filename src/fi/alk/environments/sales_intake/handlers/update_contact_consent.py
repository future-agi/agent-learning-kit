def _handle(args, db):
    c = _contact(db, args.get("contact_id"))
    # Consent is the person's own to give or withdraw: not a colleague's, not a rep's.
    kind, who = _caller(db)
    if not (kind == "contact" and who["contact_id"] == c["contact_id"]):
        raise ToolError(f"Permission denied: only {c['name']} ({c['contact_id']}) can change their own consent; the person in this conversation is {_named(kind, who)}.", category="permission_denied")
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


_WRITES = ("consent_log", "contacts", "outreach_queue")

def _all_or_nothing(db, run):
    """Run the call so that a failed call changes nothing. A handler gets no transaction of its own,
    so every table this tool writes is held first and put back if the call raises, whether a write
    failed partway or the call refused after writing. Only a fault the world simulates on purpose
    (an error marked keep_writes: a rival booking, a commit whose reply was lost, a fault used up)
    keeps what it wrote."""
    held = {table: db.query(f"SELECT * FROM {table}") for table in _WRITES}
    try:
        return run()
    except Exception as failed:
        if not getattr(failed, "keep_writes", False):
            for table, rows in held.items():
                if _same(db.query(f"SELECT * FROM {table}"), rows):
                    continue
                db.execute(f"DELETE FROM {table}")
                for row in rows:
                    cols = list(row)
                    db.execute(
                        f"INSERT INTO {table} ({', '.join(chr(34) + c + chr(34) for c in cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [row[c] for c in cols],
                    )
        raise

def _same(rows, held):
    """Whether a table still holds exactly the rows it held, in any order: only a table the call
    changed is rewritten."""
    import json

    def key(row):
        return json.dumps(row, sort_keys=True, default=str)

    return sorted(map(key, rows)) == sorted(map(key, held))


def handle(args, db):
    return _all_or_nothing(db, lambda: _handle(args, db))
