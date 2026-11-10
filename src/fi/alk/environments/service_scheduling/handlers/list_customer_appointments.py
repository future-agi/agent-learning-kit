# Shared by the generator and copied verbatim into every handler that handles time.


def _customer(db, customer_id):
    row = db.one("SELECT * FROM customers WHERE customer_id = ?", [customer_id])
    if row is None:
        raise ToolError(f"Customer {customer_id!r} not found.", category="not_found")
    return row


def _view(a):
    return {k: a[k] for k in ("appointment_id", "customer_id", "service_id", "staff_id", "location_id", "start_utc", "end_utc", "local_start", "utc_offset", "time_zone", "status", "calendar_event_id", "rescheduled_from", "late_fee_cents")}


def handle(args, db):
    _customer(db, args.get("customer_id"))
    rows = db.query("SELECT * FROM appointments WHERE customer_id = ? ORDER BY start_utc", [args.get("customer_id")])
    if args.get("status"):
        rows = [r for r in rows if r["status"] == args["status"]]
    return {"appointments": [_view(r) for r in rows]}
