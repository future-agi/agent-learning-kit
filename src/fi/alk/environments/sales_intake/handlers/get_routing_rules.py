def handle(args, db):
    rules = db.query("SELECT rule_id, country, rep_id FROM territory_rules ORDER BY rule_id")
    out = {"territories": rules, "fallback_queue": _param(db, "fallback_queue", "Q-SDR-FALLBACK"),
           "note": "A lead goes to the rep who owns its country; a country with no owner goes to the fallback queue, never to an arbitrary rep."}
    if args.get("country"):
        out["for_country"] = _expected_owner(db, args["country"])
    return out


def _param(db, name, default):
    import json
    row = db.one("SELECT value FROM environment_policy WHERE name = ?", [name])
    return json.loads(row["value"]) if row else default


def _expected_owner(db, country):
    rule = db.one("SELECT * FROM territory_rules WHERE lower(country) = lower(?)", [country or ""])
    if rule:
        return {"kind": "rep", "id": rule["rep_id"], "rule": "territory", "rule_id": rule["rule_id"]}
    return {"kind": "queue", "id": _param(db, "fallback_queue", "Q-SDR-FALLBACK"), "rule": "fallback", "rule_id": None}


