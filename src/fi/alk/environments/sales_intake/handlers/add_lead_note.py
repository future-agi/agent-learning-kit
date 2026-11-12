def _handle(args, db):
    l = _lead(db, args.get("lead_id"))
    _for_lead(db, l, colleague=True)
    body = (args.get("body") or "").strip()
    if not body:
        raise ToolError("body is required.", category="invalid_input")
    now = _now(db)
    n = db.one("SELECT COUNT(*) AS c FROM lead_notes")["c"]
    nid = f"N{501 + n}"
    db.execute("INSERT INTO lead_notes (note_id, lead_id, body, created_at) VALUES (?, ?, ?, ?)", [nid, l["lead_id"], body, now])
    db.execute("UPDATE leads SET updated_at = ? WHERE lead_id = ?", [now, l["lead_id"]])
    return {"note_id": nid, "lead_id": l["lead_id"], "created_at": now}

def _now(db):
    row = db.one("SELECT now FROM clock")
    return row["now"] if row else "2026-11-10T15:00:00Z"


def _lead(db, lead_id):
    if not lead_id:
        raise ToolError("lead_id is required.", category="invalid_input")
    row = db.one("SELECT * FROM leads WHERE lead_id = ?", [lead_id])
    if row is None:
        raise ToolError(f"Lead {lead_id!r} not found.", category="not_found")
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


def _for_lead(db, lead, colleague=False):
    """Refuse unless the person in this conversation may act on this lead: BrightDesk staff work the
    whole pipeline, a prospect only their own lead, or, where `colleague`, a lead of their own
    company (a colleague's report is recorded as a note, never as the lead's facts)."""
    kind, who = _caller(db)
    if kind == "staff":
        return
    if kind == "contact" and (lead["contact_id"] == who["contact_id"] or (colleague and lead["account_id"] == who["account_id"])):
        return
    raise ToolError(f"Permission denied: lead {lead['lead_id']} is not the record of {_named(kind, who)}, the person in this conversation; only its own contact or BrightDesk staff can act on it.", category="permission_denied")


_WRITES = ("lead_notes", "leads")

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
