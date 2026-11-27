# Shared by the generator and copied verbatim into every handler that handles time.


def handle(args, db):
    out = []
    for r in db.query("SELECT * FROM staff WHERE active = 1 ORDER BY staff_id", []):
        if args.get("service_id") and args["service_id"] not in r["services"].split(","):
            continue
        if args.get("location_id") and r["location_id"] != args["location_id"]:
            continue
        if args.get("language") and args["language"] not in r["languages"].split(","):
            continue
        cal = db.one("SELECT access_role FROM calendar_calendars WHERE calendar_id = ?", [r["calendar_id"]])
        out.append({"staff_id": r["staff_id"], "name": r["name"], "location_id": r["location_id"], "languages": r["languages"].split(","), "services": r["services"].split(","), "calendar_id": r["calendar_id"], "online_booking": bool(cal and cal["access_role"] in ("writer", "owner"))})
    return {"staff": out}
