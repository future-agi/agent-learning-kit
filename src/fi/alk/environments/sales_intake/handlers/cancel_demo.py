def handle(args, db):
    d = db.one("SELECT * FROM demos WHERE demo_id = ?", [args.get("demo_id")])
    if d is None:
        raise ToolError(f"Demo {args.get('demo_id')!r} not found.", category="not_found")
    _for_lead(db, db.one("SELECT * FROM leads WHERE lead_id = ?", [d["lead_id"]]))
    if not args.get("confirmed"):
        raise ToolError("Confirm which demo is cancelled with the person, then pass confirmed=true.", category="policy_blocked")
    if d["status"] == "cancelled":
        return {"demo_id": d["demo_id"], "status": "cancelled", "idempotent_replay": True}
    now = _now(db)
    db.execute("UPDATE demos SET status = 'cancelled', updated_at = ? WHERE demo_id = ?", [now, d["demo_id"]])
    db.execute("UPDATE calendar_events SET status = 'cancelled', updated_at = ? WHERE event_id = ?", [now, d["calendar_event_id"]])
    db.execute("UPDATE demo_slots SET status = 'open' WHERE slot_id = ?", [d["slot_id"]])
    db.execute("UPDATE leads SET status = 'qualified', updated_at = ? WHERE lead_id = ? AND status = 'demo_booked'", [now, d["lead_id"]])
    return {"demo_id": d["demo_id"], "lead_id": d["lead_id"], "status": "cancelled", "calendar_event_id": d["calendar_event_id"], "slot_id": d["slot_id"]}

def _now(db):
    row = db.one("SELECT now FROM clock")
    return row["now"] if row else "2026-11-10T15:00:00Z"


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
