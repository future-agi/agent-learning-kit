# Shared by the generator and copied verbatim into every handler that handles time.
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


def _next_id(db, table, column, prefix, start):
    n = start
    while db.one(f"SELECT 1 AS x FROM {table} WHERE {column} = ?", [f"{prefix}{n}"]):
        n += 1
    return f"{prefix}{n}"


iso = _iso


def handle(args, db):
    cat = args.get("category")
    if cat not in ("read_only_calendar", "out_of_scope", "complaint", "failure", "other") or not args.get("details"):
        raise ToolError("category (read_only_calendar, out_of_scope, complaint, failure, other) and details are required.", category="invalid_input")
    if args.get("customer_id"):
        _customer(db, args["customer_id"])
    hid = _next_id(db, "handoffs", "handoff_id", "HO", 600)
    db.execute("INSERT INTO handoffs (handoff_id, customer_id, category, details, status, created_at) VALUES (?, ?, ?, ?, 'open', ?)", [hid, args.get("customer_id"), cat, args["details"], iso(_now(db))])
    return {"handoff_id": hid, "status": "open"}
