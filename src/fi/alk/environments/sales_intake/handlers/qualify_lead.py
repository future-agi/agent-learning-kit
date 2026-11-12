def _handle(args, db):
    l = _lead(db, args.get("lead_id"))
    _for_lead(db, l)
    if l["status"] in ("disqualified", "closed"):
        raise ToolError(f"Lead {l['lead_id']} is {l['status']}.", category="conflict")
    facts = args.get("facts") or {}
    if not isinstance(facts, dict):
        raise ToolError("facts must be an object of rubric fields.", category="invalid_input")
    known = {r["field"]: r for r in db.query("SELECT * FROM icp_rules")}
    unknown = sorted(k for k in facts if k not in known)
    if unknown:
        raise ToolError(f"Unknown rubric field(s) {unknown}; the rubric has {sorted(known)}.", category="invalid_input")
    now = _now(db)
    merged = {k: l[k] for k in known}
    for k, v in facts.items():
        if v in (None, ""):
            continue
        if k == "seats":
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ToolError("seats must be a number.", category="invalid_input")
        merged[k] = v
    missing = [k for k, r in known.items() if r["required"] and merged.get(k) in (None, "")]
    reasons = []
    if not missing:
        if int(merged["seats"]) < int(_param(db, "min_qualifying_seats", 20)):
            reasons.append(f"seats {merged['seats']} below {_param(db, 'min_qualifying_seats', 20)}")
        if merged["use_case"] not in ("support", "customer_service", "helpdesk"):
            reasons.append(f"use case {merged['use_case']!r} outside the rubric")
        if merged["timeline"] not in ("0-3 months", "3-6 months"):
            reasons.append(f"timeline {merged['timeline']!r} beyond six months")
    status = "needs_information" if missing else ("disqualified" if reasons else ("qualified" if l["status"] not in ("assigned", "demo_booked") else l["status"]))
    # A routed lead's country decides its owner: moving it would leave it with the wrong rep.
    if (l["owner_rep_id"] or l["queue_id"]) and (merged["country"] or "").lower() != (l["country"] or "").lower():
        raise ToolError(f"Lead {l['lead_id']} is already routed to {l['owner_rep_id'] or l['queue_id']} for {l['country']}; a change of country needs re-routing by a person, not a rubric update.", category="policy_blocked")
    # A booked demo is a commitment to the prospect: it is cancelled with them first, never left
    # standing on a lead that no longer qualifies.
    if status in ("disqualified", "needs_information") and db.one("SELECT 1 FROM demos WHERE lead_id = ? AND status = 'booked'", [l["lead_id"]]):
        raise ToolError(f"Lead {l['lead_id']} has a booked demo; these facts would make it {status}. Cancel the demo with the prospect first.", category="conflict")
    db.execute("UPDATE leads SET use_case = ?, seats = ?, timeline = ?, country = ?, budget = ?, status = ?, updated_at = ? WHERE lead_id = ?",
               [merged["use_case"], merged["seats"], merged["timeline"], merged["country"], merged["budget"], status, now, l["lead_id"]])
    return {"lead_id": l["lead_id"], "status": status, "missing_fields": missing, "reasons": reasons,
            "facts": merged, "message": "Ask the prospect for the missing fields; do not guess them." if missing else f"Lead {status}."}

def _now(db):
    row = db.one("SELECT now FROM clock")
    return row["now"] if row else "2026-11-10T15:00:00Z"


def _param(db, name, default):
    import json
    row = db.one("SELECT value FROM environment_policy WHERE name = ?", [name])
    return json.loads(row["value"]) if row else default


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


_WRITES = ("leads",)

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
