# Shared by the generator and copied verbatim into every handler that handles time.
from datetime import datetime


def _iso(utc):
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _dt(s):
    return datetime.fromisoformat(str(s).replace("Z", "")[:19])


def _now(db):
    row = db.one("SELECT now FROM clock LIMIT 1")
    return _dt(row["now"] if row else "2026-10-26T09:00:00Z")


def _appointment(db, appointment_id, customer_id):
    row = db.one("SELECT * FROM appointments WHERE appointment_id = ?", [appointment_id])
    if row is None:
        raise ToolError(f"Appointment {appointment_id!r} not found.", category="not_found")
    if row["customer_id"] != customer_id:
        raise ToolError(f"Appointment {appointment_id} does not belong to customer {customer_id}.", category="permission_denied")
    return row


def _next_id(db, table, column, prefix, start):
    n = start
    while db.one(f"SELECT 1 AS x FROM {table} WHERE {column} = ?", [f"{prefix}{n}"]):
        n += 1
    return f"{prefix}{n}"


iso = _iso


def handle(args, db):
    a = _appointment(db, args.get("appointment_id"), args.get("customer_id"))
    kind = args.get("kind")
    ok = {"booked": a["status"] == "booked" and not a["rescheduled_from"], "rescheduled": a["status"] == "booked" and bool(a["rescheduled_from"]), "cancelled": a["status"] == "cancelled"}
    if kind not in ok:
        raise ToolError("kind must be booked, rescheduled or cancelled.", category="invalid_input")
    if not ok[kind]:
        raise ToolError(f"Appointment {a['appointment_id']} is {a['status']}; a '{kind}' confirmation would state something that has not happened.", category="conflict")
    dup = db.one("SELECT outbox_id FROM confirmation_outbox WHERE appointment_id = ? AND kind = ?", [a["appointment_id"], kind])
    if dup:
        return {"outbox_id": dup["outbox_id"], "kind": kind, "idempotent": True}
    oid = _next_id(db, "confirmation_outbox", "outbox_id", "O", 600)
    db.execute("INSERT INTO confirmation_outbox (outbox_id, appointment_id, customer_id, kind, channel, status, created_at) VALUES (?, ?, ?, ?, ?, 'queued', ?)", [oid, a["appointment_id"], a["customer_id"], kind, args.get("channel") or "email", iso(_now(db))])
    return {"outbox_id": oid, "kind": kind, "idempotent": False}
