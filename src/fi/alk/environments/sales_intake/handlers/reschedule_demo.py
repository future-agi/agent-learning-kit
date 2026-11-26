def handle(args, db):
    # Moving a demo is one call so the prospect is never left with nothing: the new time is
    # checked and booked, and only then is the old one released. The harness runs a call as one
    # transaction, so a refusal (the new slot taken, a slot on another calendar) leaves the
    # original demo, its event and its slot exactly as they were. Cancelling first and booking
    # after is two calls, and a slot lost in between costs the prospect their demo.
    d = db.one("SELECT * FROM demos WHERE demo_id = ?", [args.get("demo_id")])
    if d is None:
        raise ToolError(f"Demo {args.get('demo_id')!r} not found.", category="not_found")
    l = _lead(db, d["lead_id"])
    _for_lead(db, l)
    key = args.get("request_key")
    if key:
        prior = db.one("SELECT * FROM demos WHERE request_key = ?", [key])
        if prior:
            if (prior["lead_id"], prior["slot_id"]) != (l["lead_id"], args.get("slot_id")):
                raise ToolError(f"request_key {key!r} was already used for another booking; use a new key for a new request.", category="conflict")
            return {"demo_id": prior["demo_id"], "lead_id": prior["lead_id"], "slot_id": prior["slot_id"], "rep_id": prior["rep_id"], "calendar_event_id": prior["calendar_event_id"], "status": prior["status"], "idempotent_replay": True}
    if not args.get("prospect_confirmed"):
        raise ToolError("Move the demo only to the slot the prospect chose: offer slots, let them pick, then pass prospect_confirmed=true.", category="policy_blocked")
    if d["status"] != "booked":
        raise ToolError(f"Demo {d['demo_id']} is {d['status']}, not booked; there is nothing to move. Book a new demo with book_demo.", category="conflict")
    c = _contact(db, l["contact_id"])
    if c["consent_status"] == "opted_out":
        raise ToolError(f"Contact {c['contact_id']} opted out; no demo invitation may be sent.", category="policy_blocked")
    slot = db.one("SELECT * FROM demo_slots WHERE slot_id = ?", [args.get("slot_id")])
    if slot is None:
        raise ToolError(f"Slot {args.get('slot_id')!r} not found. Demo {d['demo_id']} stays booked at its current time.", category="not_found")
    if slot["slot_id"] == d["slot_id"]:
        raise ToolError(f"Demo {d['demo_id']} is already in slot {slot['slot_id']}.", category="invalid_input")
    if slot["rep_id"] != l["owner_rep_id"]:
        raise ToolError(f"Slot {slot['slot_id']} is on {slot['rep_id']}'s calendar; lead {l['lead_id']} belongs to {l['owner_rep_id']}. Demo {d['demo_id']} stays booked at its current time.", category="policy_blocked")
    rep = db.one("SELECT * FROM reps WHERE rep_id = ?", [slot["rep_id"]])
    clash = db.one("SELECT 1 FROM calendar_events WHERE calendar_id = ? AND status != 'cancelled' AND event_id != ? AND start_time < ? AND end_time > ?", [rep["calendar_id"], d["calendar_event_id"], slot["end_time"], slot["start_time"]])
    if slot["status"] != "open" or clash:
        raise ToolError(f"Slot {slot['slot_id']} is no longer free; demo {d['demo_id']} stays booked at its current time. Offer fresh slots.", category="conflict")
    now = _now(db)
    n = db.one("SELECT COUNT(*) AS c FROM demos")["c"]
    demo_id = f"D{501 + n}"
    event_id = f"EV-{demo_id}"
    account = db.one("SELECT name FROM accounts WHERE account_id = ?", [l["account_id"]])
    db.execute("INSERT INTO calendar_events (event_id, calendar_id, summary, description, location, start_time, end_time, time_zone, status, recurring_event_id, recurrence_rule, original_start_time, version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', NULL, NULL, NULL, 1, ?, ?)",
               [event_id, rep["calendar_id"], f"BrightDesk demo: {account['name'] if account else l['account_id']}", f"Lead {l['lead_id']} (moved from {d['demo_id']})", "Video call", slot["start_time"], slot["end_time"], rep["time_zone"], now, now])
    aid = db.one("SELECT COALESCE(MAX(id), 0) + 1 AS c FROM calendar_event_attendees")["c"]
    db.execute("INSERT INTO calendar_event_attendees (id, event_id, email, display_name, response_status, is_organizer, is_optional) VALUES (?, ?, ?, ?, 'accepted', TRUE, FALSE)", [aid, event_id, rep["email"], rep["name"]])
    db.execute("INSERT INTO calendar_event_attendees (id, event_id, email, display_name, response_status, is_organizer, is_optional) VALUES (?, ?, ?, ?, 'needsAction', FALSE, FALSE)", [aid + 1, event_id, c["email"], c["name"]])
    db.execute("INSERT INTO demos (demo_id, lead_id, rep_id, slot_id, calendar_event_id, status, prospect_confirmed, request_key, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'booked', TRUE, ?, ?, ?)",
               [demo_id, l["lead_id"], rep["rep_id"], slot["slot_id"], event_id, key, now, now])
    db.execute("UPDATE demo_slots SET status = 'booked' WHERE slot_id = ?", [slot["slot_id"]])
    # The new time is secured; now the old one is released.
    db.execute("UPDATE demos SET status = 'rescheduled', updated_at = ? WHERE demo_id = ?", [now, d["demo_id"]])
    db.execute("UPDATE calendar_events SET status = 'cancelled', updated_at = ? WHERE event_id = ?", [now, d["calendar_event_id"]])
    db.execute("UPDATE demo_slots SET status = 'open' WHERE slot_id = ?", [d["slot_id"]])
    db.execute("UPDATE leads SET status = 'demo_booked', updated_at = ? WHERE lead_id = ?", [now, l["lead_id"]])
    old = db.one("SELECT start_time FROM demo_slots WHERE slot_id = ?", [d["slot_id"]])
    return {"demo_id": demo_id, "lead_id": l["lead_id"], "rep_id": rep["rep_id"], "slot_id": slot["slot_id"], "start_time": slot["start_time"], "end_time": slot["end_time"],
            "rep_time_zone": rep["time_zone"], "calendar_event_id": event_id, "status": "booked",
            "replaces": {"demo_id": d["demo_id"], "slot_id": d["slot_id"], "start_time": old["start_time"] if old else None, "status": "rescheduled"},
            "message": f"Demo moved on {rep['name']}'s calendar; the earlier time is released."}

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
