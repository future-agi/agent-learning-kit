# Shared by the generator and copied verbatim into every handler that handles time.
from datetime import datetime

ZONES = {"America/New_York": (-300, -240), "Europe/London": (0, 60)}


def _iso(utc):
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _dt(s):
    return datetime.fromisoformat(str(s).replace("Z", "")[:19])


def _now(db):
    row = db.one("SELECT now FROM clock LIMIT 1")
    return _dt(row["now"] if row else "2026-10-26T09:00:00Z")


def _next_id(db, table, column, prefix, start):
    n = start
    while db.one(f"SELECT 1 AS x FROM {table} WHERE {column} = ?", [f"{prefix}{n}"]):
        n += 1
    return f"{prefix}{n}"


iso = _iso


def handle(args, db):
    name, email, phone = args.get("name"), (args.get("email") or "").strip(), (args.get("phone") or "").strip()
    if not name or not email or "@" not in email or not phone:
        raise ToolError("name, a valid email and phone are required.", category="invalid_input")
    if db.one("SELECT 1 AS x FROM customers WHERE LOWER(email) = ?", [email.lower()]):
        raise ToolError("A customer with this email already exists; look them up with get_customer instead of creating another.", category="conflict")
    tz = args.get("time_zone") or "America/New_York"
    if tz not in ZONES:
        raise ToolError(f"Unknown time zone {tz!r}; use America/New_York or Europe/London.", category="invalid_input")
    cid = _next_id(db, "customers", "customer_id", "CU", 600)
    now = _now(db)
    db.execute("INSERT INTO customers (customer_id, name, email, phone, preferred_language, time_zone, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", [cid, name, email, phone, args.get("preferred_language") or "en", tz, iso(now)])
    return {"customer_id": cid, "name": name, "email": email}
