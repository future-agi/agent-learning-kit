def handle(args, db):
    # The owner's name rides along with the id, so an answer naming the rep rests on what was read.
    rules = db.query("SELECT t.rule_id, t.country, t.rep_id, r.name AS rep_name FROM territory_rules t LEFT JOIN reps r ON r.rep_id = t.rep_id ORDER BY t.rule_id")
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
        rep = db.one("SELECT name FROM reps WHERE rep_id = ?", [rule["rep_id"]])
        return {"kind": "rep", "id": rule["rep_id"], "name": rep["name"] if rep else None, "rule": "territory", "rule_id": rule["rule_id"]}
    return {"kind": "queue", "id": _param(db, "fallback_queue", "Q-SDR-FALLBACK"), "rule": "fallback", "rule_id": None}


