# Shared by the generator and copied verbatim into every handler that handles time.


def handle(args, db):
    out = []
    for s in db.query("SELECT * FROM services ORDER BY service_id", []):
        staff = [r["staff_id"] for r in db.query("SELECT staff_id, services FROM staff WHERE active = 1", []) if s["service_id"] in r["services"].split(",")]
        out.append({"service_id": s["service_id"], "name": s["name"], "duration_minutes": s["duration_minutes"], "price_cents": s["price_cents"], "staff_ids": staff})
    return {"services": out}
