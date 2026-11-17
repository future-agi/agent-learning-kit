import json


def handle(args, db):
    q, p = "SELECT * FROM providers WHERE 1 = 1", []
    if args.get("specialty"):
        q += " AND specialty = ?"; p.append(args["specialty"])
    out = []
    for prov in db.query(q + " ORDER BY provider_id", p):
        payers = [r["payer"] for r in db.query("SELECT payer FROM provider_payers WHERE provider_id = ? ORDER BY payer", [prov["provider_id"]])]
        sessions = db.query("SELECT location_id, weekday, start_hhmm, end_hhmm FROM provider_sessions WHERE provider_id = ? ORDER BY weekday", [prov["provider_id"]])
        langs = prov["languages"] if isinstance(prov["languages"], list) else json.loads(prov["languages"])
        if args.get("payer") and args["payer"] not in payers:
            continue
        if args.get("location_id") and args["location_id"] not in {s["location_id"] for s in sessions}:
            continue
        if args.get("language") and args["language"] not in langs:
            continue
        spec = db.one("SELECT referral_required FROM specialties WHERE specialty = ?", [prov["specialty"]])
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        out.append({"provider_id": prov["provider_id"], "name": prov["name"], "specialty": prov["specialty"], "languages": langs,
                    "payers_accepted": payers, "referral_required": bool(spec["referral_required"]), "calendar_id": prov["calendar_id"],
                    "sessions": [f"{days[s['weekday']]} {s['start_hhmm']}-{s['end_hhmm']} at {s['location_id']}" for s in sessions]})
    return {"providers": out, "count": len(out)}
