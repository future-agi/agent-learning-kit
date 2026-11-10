# Shared by the generator and copied verbatim into every handler that handles time.
import re
from datetime import datetime


def _iso(utc):
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _dt(s):
    return datetime.fromisoformat(str(s).replace("Z", "")[:19])


def _now(db):
    row = db.one("SELECT now FROM clock LIMIT 1")
    return _dt(row["now"] if row else "2026-10-26T09:00:00Z")


def _customer(db, customer_id):
    row = db.one("SELECT * FROM customers WHERE customer_id = ?", [customer_id])
    if row is None:
        raise ToolError(f"Customer {customer_id!r} not found.", category="not_found")
    return row


def _location(db, location_id):
    row = db.one("SELECT * FROM locations WHERE location_id = ?", [location_id])
    if row is None:
        raise ToolError(f"Location {location_id!r} not found.", category="not_found")
    return row


def _service(db, service_id):
    row = db.one("SELECT * FROM services WHERE service_id = ?", [service_id])
    if row is None:
        raise ToolError(f"Service {service_id!r} not found.", category="not_found")
    return row


def _staff(db, staff_id):
    row = db.one("SELECT * FROM staff WHERE staff_id = ?", [staff_id])
    if row is None or not row["active"]:
        raise ToolError(f"Staff member {staff_id!r} not found or not active.", category="not_found")
    return row


def _next_id(db, table, column, prefix, start):
    n = start
    while db.one(f"SELECT 1 AS x FROM {table} WHERE {column} = ?", [f"{prefix}{n}"]):
        n += 1
    return f"{prefix}{n}"


iso = _iso


def handle(args, db):
    cust, service, loc = _customer(db, args.get("customer_id")), _service(db, args.get("service_id")), _location(db, args.get("location_id"))
    date = str(args.get("preferred_date") or "")
    try:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            raise ValueError(date)
        datetime.fromisoformat(date)
    except ValueError:
        raise ToolError("preferred_date must be YYYY-MM-DD", category="invalid_input")
    if args.get("staff_id"):
        _staff(db, args["staff_id"])
    dup = db.one("SELECT waitlist_id FROM waitlist WHERE customer_id = ? AND service_id = ? AND preferred_date = ? AND status = 'active'", [cust["customer_id"], service["service_id"], date])
    if dup:
        raise ToolError(f"Already on the waitlist ({dup['waitlist_id']}) for that service and date.", category="conflict")
    wid = _next_id(db, "waitlist", "waitlist_id", "W", 600)
    db.execute("INSERT INTO waitlist (waitlist_id, customer_id, service_id, location_id, staff_id, preferred_date, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'active', ?)", [wid, cust["customer_id"], service["service_id"], loc["location_id"], args.get("staff_id"), date, iso(_now(db))])
    return {"waitlist_id": wid, "status": "active", "preferred_date": date}
