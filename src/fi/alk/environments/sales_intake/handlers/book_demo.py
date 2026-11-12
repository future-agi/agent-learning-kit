def _handle(args, db):
    l = _lead(db, args.get("lead_id"))
    _for_lead(db, l)
    key = args.get("request_key")
    if key:
        prior = db.one("SELECT * FROM demos WHERE request_key = ?", [key])
        if prior:
            if (prior["lead_id"], prior["slot_id"]) != (l["lead_id"], args.get("slot_id")):
                raise ToolError(f"request_key {key!r} was already used for another booking; use a new key for a new request.", category="conflict")
            return {"demo_id": prior["demo_id"], "lead_id": prior["lead_id"], "slot_id": prior["slot_id"], "rep_id": prior["rep_id"], "calendar_event_id": prior["calendar_event_id"], "status": prior["status"], "idempotent_replay": True}
    if not args.get("prospect_confirmed"):
        raise ToolError("Book only the slot the prospect chose: offer slots, let them pick, then pass prospect_confirmed=true.", category="policy_blocked")
    if not l["owner_rep_id"]:
        raise ToolError(f"Lead {l['lead_id']} has no owner; demos go on the assigned owner's calendar.", category="policy_blocked")
    c = _contact(db, l["contact_id"])
    if c["consent_status"] == "opted_out":
        raise ToolError(f"Contact {c['contact_id']} opted out; no demo invitation may be sent.", category="policy_blocked")
    if db.one("SELECT 1 FROM demos WHERE lead_id = ? AND status = 'booked'", [l["lead_id"]]):
        raise ToolError(f"Lead {l['lead_id']} already has a booked demo; cancel or keep it.", category="conflict")
    slot = db.one("SELECT * FROM demo_slots WHERE slot_id = ?", [args.get("slot_id")])
    if slot is None:
        raise ToolError(f"Slot {args.get('slot_id')!r} not found.", category="not_found")
    if slot["rep_id"] != l["owner_rep_id"]:
        raise ToolError(f"Slot {slot['slot_id']} is on {slot['rep_id']}'s calendar; lead {l['lead_id']} belongs to {l['owner_rep_id']}.", category="policy_blocked")
    rep = db.one("SELECT * FROM reps WHERE rep_id = ?", [slot["rep_id"]])
    if slot["status"] != "open" or db.one("SELECT 1 FROM calendar_events WHERE calendar_id = ? AND status != 'cancelled' AND start_time < ? AND end_time > ?", [rep["calendar_id"], slot["end_time"], slot["start_time"]]):
        raise ToolError(f"Slot {slot['slot_id']} is no longer free; offer fresh slots.", category="conflict")
    now = _now(db)
    n = db.one("SELECT COUNT(*) AS c FROM demos")["c"]
    demo_id = f"D{501 + n}"
    event_id = f"EV-{demo_id}"
    account = db.one("SELECT name FROM accounts WHERE account_id = ?", [l["account_id"]])
    db.execute("INSERT INTO calendar_events (event_id, calendar_id, summary, description, location, start_time, end_time, time_zone, status, recurring_event_id, recurrence_rule, original_start_time, version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', NULL, NULL, NULL, 1, ?, ?)",
               [event_id, rep["calendar_id"], f"BrightDesk demo: {account['name'] if account else l['account_id']}", f"Lead {l['lead_id']}", "Video call", slot["start_time"], slot["end_time"], rep["time_zone"], now, now])
    aid = db.one("SELECT COALESCE(MAX(id), 0) + 1 AS c FROM calendar_event_attendees")["c"]
    db.execute("INSERT INTO calendar_event_attendees (id, event_id, email, display_name, response_status, is_organizer, is_optional) VALUES (?, ?, ?, ?, 'accepted', TRUE, FALSE)", [aid, event_id, rep["email"], rep["name"]])
    db.execute("INSERT INTO calendar_event_attendees (id, event_id, email, display_name, response_status, is_organizer, is_optional) VALUES (?, ?, ?, ?, 'needsAction', FALSE, FALSE)", [aid + 1, event_id, c["email"], c["name"]])
    db.execute("INSERT INTO demos (demo_id, lead_id, rep_id, slot_id, calendar_event_id, status, prospect_confirmed, request_key, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'booked', TRUE, ?, ?, ?)",
               [demo_id, l["lead_id"], rep["rep_id"], slot["slot_id"], event_id, key, now, now])
    db.execute("UPDATE demo_slots SET status = 'booked' WHERE slot_id = ?", [slot["slot_id"]])
    db.execute("UPDATE leads SET status = 'demo_booked', updated_at = ? WHERE lead_id = ?", [now, l["lead_id"]])
    return {"demo_id": demo_id, "lead_id": l["lead_id"], "rep_id": rep["rep_id"], "slot_id": slot["slot_id"], "start_time": slot["start_time"], "end_time": slot["end_time"],
            "calendar_event_id": event_id, "status": "booked", "message": f"Demo booked on {rep['name']}'s calendar."}

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


_WRITES = ("calendar_event_attendees", "calendar_events", "demo_slots", "demos", "leads")

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
