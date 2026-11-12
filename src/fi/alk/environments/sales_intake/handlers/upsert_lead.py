import json


def _handle(args, db):
    email = args.get("email")
    if not email or "@" not in email:
        raise ToolError("A business email is required.", category="invalid_input")
    norm = _norm(email)
    now = _now(db)
    key = args.get("request_key")
    contact = db.one("SELECT * FROM contacts WHERE email_normalized = ?", [norm])
    facts = {k: args.get(k) for k in ("use_case", "seats", "timeline", "budget") if args.get(k) not in (None, "")}
    if "seats" in facts:
        try:
            facts["seats"] = int(facts["seats"])
        except (TypeError, ValueError):
            raise ToolError("seats must be a number.", category="invalid_input")
    # A known person's lead is changed only by that person or by staff; anyone may introduce a
    # new person, since that touches nobody's record.
    kind, who = _caller(db)
    if contact is not None and kind != "staff" and not (kind == "contact" and who["contact_id"] == contact["contact_id"]):
        raise ToolError(f"Permission denied: {norm} is the email of contact {contact['contact_id']}, not of {_named(kind, who)}, the person in this conversation; only they or BrightDesk staff can update their lead.", category="permission_denied")
    if key:
        prior = db.one("SELECT * FROM lead_request_keys WHERE request_key = ?", [key])
        if prior:
            if prior["email_normalized"] != norm or json.loads(prior["facts"]) != facts:
                raise ToolError(f"request_key {key!r} was already used for another request ({prior['email_normalized']}, {prior['facts']}); use a new key for a new request.", category="conflict")
            lead = db.one("SELECT * FROM leads WHERE lead_id = ?", [prior["lead_id"]])
            return dict(_lead_view(lead), created=False, idempotent_replay=True)
    created_contact = False
    if contact is None:
        name = (args.get("name") or "").strip()
        if not name:
            raise ToolError("A new contact needs the person's name.", category="invalid_input")
        domain = norm.split("@", 1)[1]
        account = db.one("SELECT * FROM accounts WHERE domain = ?", [domain])
        if account is None:
            company = (args.get("company") or "").strip()
            if not company:
                raise ToolError("A new company needs its name as the prospect gave it.", category="invalid_input")
            n = db.one("SELECT COUNT(*) AS c FROM accounts")["c"]
            aid = f"ACC{501 + n}"
            db.execute("INSERT INTO accounts (account_id, name, domain, country, employee_count, industry, created_at, updated_at) VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)",
                       [aid, company, domain, args.get("country"), now, now])
        else:
            aid = account["account_id"]
        n = db.one("SELECT COUNT(*) AS c FROM contacts")["c"]
        cid = f"C{501 + n}"
        db.execute("INSERT INTO contacts (contact_id, account_id, name, email, email_normalized, title, country, consent_status, opted_out_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'unknown', NULL, ?, ?)",
                   [cid, aid, name, email.strip(), norm, args.get("title"), args.get("country"), now, now])
        contact = db.one("SELECT * FROM contacts WHERE contact_id = ?", [cid])
        created_contact = True
    open_lead = db.one("SELECT * FROM leads WHERE contact_id = ? AND status NOT IN ('disqualified', 'closed') ORDER BY created_at DESC", [contact["contact_id"]])
    if open_lead:
        # A lead already past qualification keeps its status only while the rubric still holds:
        # a fact that would fail it is re-evaluated with qualify_lead, never slipped in here.
        if open_lead["status"] in ("qualified", "assigned", "demo_booked") and any(open_lead[k] != v for k, v in facts.items()):
            failing = _rubric_failures(db, dict(open_lead, **facts))
            if failing:
                raise ToolError(f"Lead {open_lead['lead_id']} is {open_lead['status']}; these facts would fail the rubric ({'; '.join(failing)}). Confirm them with the prospect and re-evaluate with qualify_lead.", category="policy_blocked")
        sets, vals = ["updated_at = ?"], [now]
        for k, v in facts.items():
            sets.append(f"{k} = ?"); vals.append(v)
        if args.get("country") and not open_lead["country"]:
            sets.append("country = ?"); vals.append(args["country"])
        db.execute(f"UPDATE leads SET {', '.join(sets)} WHERE lead_id = ?", vals + [open_lead["lead_id"]])
        lead = db.one("SELECT * FROM leads WHERE lead_id = ?", [open_lead["lead_id"]])
        _remember(db, key, lead["lead_id"], norm, facts, now)
        return dict(_lead_view(lead), created=False, contact_created=False, message=f"Existing contact {contact['contact_id']} and open lead {lead['lead_id']} updated; nothing duplicated.")
    n = db.one("SELECT COUNT(*) AS c FROM leads")["c"]
    lid = f"L{501 + n}"
    db.execute("INSERT INTO leads (lead_id, contact_id, account_id, source, use_case, seats, timeline, country, budget, status, owner_rep_id, queue_id, request_key, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', NULL, NULL, ?, ?, ?)",
               [lid, contact["contact_id"], contact["account_id"], args.get("source") or "chat", facts.get("use_case"), facts.get("seats"), facts.get("timeline"),
                args.get("country") or contact["country"], facts.get("budget"), key, now, now])
    lead = db.one("SELECT * FROM leads WHERE lead_id = ?", [lid])
    _remember(db, key, lid, norm, facts, now)
    return dict(_lead_view(lead), created=True, contact_created=created_contact)

def _now(db):
    row = db.one("SELECT now FROM clock")
    return row["now"] if row else "2026-11-10T15:00:00Z"


def _norm(email):
    return (email or "").strip().lower()


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


def _remember(db, key, lead_id, norm, facts, now):
    """Record a keyed request so that its retry, and only its retry, is answered as a replay."""
    if key:
        db.execute("INSERT INTO lead_request_keys (request_key, lead_id, email_normalized, facts, created_at) VALUES (?, ?, ?, ?, ?)",
                   [key, lead_id, norm, json.dumps(facts, sort_keys=True), now])


def _rubric_failures(db, lead):
    """Why these facts would not qualify (the same rubric qualify_lead applies)."""
    row = db.one("SELECT value FROM environment_policy WHERE name = 'min_qualifying_seats'")
    minimum = int(json.loads(row["value"])) if row else 20
    failing = []
    if lead.get("seats") is not None and int(lead["seats"]) < minimum:
        failing.append(f"seats {lead['seats']} below {minimum}")
    if lead.get("use_case") and lead["use_case"] not in ("support", "customer_service", "helpdesk"):
        failing.append(f"use case {lead['use_case']!r} outside the rubric")
    if lead.get("timeline") and lead["timeline"] not in ("0-3 months", "3-6 months"):
        failing.append(f"timeline {lead['timeline']!r} beyond six months")
    return failing

_WRITES = ("accounts", "contacts", "lead_request_keys", "leads")

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
