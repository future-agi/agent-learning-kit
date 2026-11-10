def handle(args, db):
    email = args.get("email")
    if not email or "@" not in email:
        raise ToolError("A business email is required.", category="invalid_input")
    norm = _norm(email)
    now = _now(db)
    key = args.get("request_key")
    if key:
        prior = db.one("SELECT * FROM leads WHERE request_key = ?", [key])
        if prior:
            return dict(_lead_view(prior), created=False, idempotent_replay=True)
    contact = db.one("SELECT * FROM contacts WHERE email_normalized = ?", [norm])
    facts = {k: args.get(k) for k in ("use_case", "seats", "timeline", "budget") if args.get(k) not in (None, "")}
    if "seats" in facts:
        try:
            facts["seats"] = int(facts["seats"])
        except (TypeError, ValueError):
            raise ToolError("seats must be a number.", category="invalid_input")
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
        sets, vals = ["updated_at = ?"], [now]
        for k, v in facts.items():
            sets.append(f"{k} = ?"); vals.append(v)
        if args.get("country") and not open_lead["country"]:
            sets.append("country = ?"); vals.append(args["country"])
        db.execute(f"UPDATE leads SET {', '.join(sets)} WHERE lead_id = ?", vals + [open_lead["lead_id"]])
        lead = db.one("SELECT * FROM leads WHERE lead_id = ?", [open_lead["lead_id"]])
        return dict(_lead_view(lead), created=False, contact_created=False, message=f"Existing contact {contact['contact_id']} and open lead {lead['lead_id']} updated; nothing duplicated.")
    n = db.one("SELECT COUNT(*) AS c FROM leads")["c"]
    lid = f"L{501 + n}"
    db.execute("INSERT INTO leads (lead_id, contact_id, account_id, source, use_case, seats, timeline, country, budget, status, owner_rep_id, queue_id, request_key, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', NULL, NULL, ?, ?, ?)",
               [lid, contact["contact_id"], contact["account_id"], args.get("source") or "chat", facts.get("use_case"), facts.get("seats"), facts.get("timeline"),
                args.get("country") or contact["country"], facts.get("budget"), key, now, now])
    lead = db.one("SELECT * FROM leads WHERE lead_id = ?", [lid])
    return dict(_lead_view(lead), created=True, contact_created=created_contact)

def _now(db):
    row = db.one("SELECT now FROM clock")
    return row["now"] if row else "2026-11-10T15:00:00Z"


def _param(db, name, default):
    import json
    row = db.one("SELECT value FROM environment_policy WHERE name = ?", [name])
    return json.loads(row["value"]) if row else default


def _norm(email):
    return (email or "").strip().lower()


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


def _expected_owner(db, country):
    rule = db.one("SELECT * FROM territory_rules WHERE lower(country) = lower(?)", [country or ""])
    if rule:
        return {"kind": "rep", "id": rule["rep_id"], "rule": "territory", "rule_id": rule["rule_id"]}
    return {"kind": "queue", "id": _param(db, "fallback_queue", "Q-SDR-FALLBACK"), "rule": "fallback", "rule_id": None}


def _lead_view(l):
    return {k: l[k] for k in ("lead_id", "contact_id", "account_id", "source", "use_case", "seats", "timeline", "country", "budget", "status", "owner_rep_id", "queue_id", "created_at", "updated_at")}
