def handle(args, db):
    email, name, account_id = args.get("email"), args.get("name"), args.get("account_id")
    if not (email or name or account_id):
        raise ToolError("Give an email, a name or an account_id.", category="invalid_input")
    rows = db.query("SELECT * FROM contacts")
    out = []
    for c in rows:
        if email and c["email_normalized"] != _norm(email):
            continue
        if name and name.strip().lower() not in c["name"].lower():
            continue
        if account_id and c["account_id"] != account_id:
            continue
        out.append({"contact_id": c["contact_id"], "account_id": c["account_id"], "name": c["name"], "email": c["email"], "consent_status": c["consent_status"]})
    return {"contacts": out, "count": len(out), "matched_on": "normalized email" if email else "name or account"}


def _norm(email):
    return (email or "").strip().lower()


