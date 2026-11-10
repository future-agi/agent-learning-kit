def handle(args, db):
    rules = db.query("SELECT field, required, rule, description FROM icp_rules ORDER BY field")
    return {"fields": [dict(r, required=bool(r["required"])) for r in rules], "min_qualifying_seats": _param(db, "min_qualifying_seats", 20),
            "note": "Only these fields exist in the rubric. A lead missing a required field needs information; it is never qualified on a guess."}


def _param(db, name, default):
    import json
    row = db.one("SELECT value FROM environment_policy WHERE name = ?", [name])
    return json.loads(row["value"]) if row else default


