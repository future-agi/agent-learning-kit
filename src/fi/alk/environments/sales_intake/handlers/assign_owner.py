def handle(args, db):
    l = _lead(db, args.get("lead_id"))
    _for_lead(db, l)
    if l["status"] in ("disqualified", "closed"):
        raise ToolError(f"Lead {l['lead_id']} is {l['status']}; it is not routed.", category="conflict")
    if l["status"] in ("new", "needs_information"):
        raise ToolError(f"Lead {l['lead_id']} is not qualified yet (status {l['status']}); qualify it first.", category="policy_blocked")
    rep_id, queue_id = args.get("rep_id"), args.get("queue_id")
    if bool(rep_id) == bool(queue_id):
        raise ToolError("Give exactly one of rep_id or queue_id.", category="invalid_input")
    expected = _expected_owner(db, l["country"])
    target = rep_id or queue_id
    if expected["id"] != target:
        raise ToolError(f"Routing rule: a lead from {l['country']!r} goes to {expected['id']} ({expected['rule']}), not {target}.", category="policy_blocked")
    if rep_id and not db.one("SELECT 1 FROM reps WHERE rep_id = ? AND active", [rep_id]):
        raise ToolError(f"Rep {rep_id!r} not found or inactive.", category="not_found")
    if (l["owner_rep_id"] or l["queue_id"]) == target:
        return dict(_lead_view(l), message=f"Lead {l['lead_id']} is already with {target}.", idempotent_replay=True)
    now = _now(db)
    db.execute("UPDATE leads SET owner_rep_id = ?, queue_id = ?, updated_at = ? WHERE lead_id = ?", [rep_id, queue_id, now, l["lead_id"]])
    n = db.one("SELECT COUNT(*) AS c FROM routing_log")["c"]
    db.execute("INSERT INTO routing_log (entry_id, lead_id, rule, assigned_to, created_at) VALUES (?, ?, ?, ?, ?)", [f"RL{501 + n}", l["lead_id"], expected["rule"], target, now])
    l = db.one("SELECT * FROM leads WHERE lead_id = ?", [l["lead_id"]])
    return dict(_lead_view(l), rule=expected["rule"], message=f"Lead {l['lead_id']} assigned to {target} by the {expected['rule']} rule.")

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


def _expected_owner(db, country):
    rule = db.one("SELECT * FROM territory_rules WHERE lower(country) = lower(?)", [country or ""])
    if rule:
        return {"kind": "rep", "id": rule["rep_id"], "rule": "territory", "rule_id": rule["rule_id"]}
    return {"kind": "queue", "id": _param(db, "fallback_queue", "Q-SDR-FALLBACK"), "rule": "fallback", "rule_id": None}


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
