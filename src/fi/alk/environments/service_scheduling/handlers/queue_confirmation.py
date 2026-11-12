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
    _entitled(db, customer_id)
    return row


def _next_id(db, table, column, prefix, start):
    n = start
    while db.one(f"SELECT 1 AS x FROM {table} WHERE {column} = ?", [f"{prefix}{n}"]):
        n += 1
    return f"{prefix}{n}"


iso = _iso


def _caller(db):
    """Who is in this conversation, as the session knows it (session_caller), never as the
    conversation tells it: a customer, a front-desk member, or nobody identified."""
    row = db.one("SELECT customer_id, desk_id FROM session_caller WHERE id = 'default'")
    return (row["customer_id"], row["desk_id"]) if row else (None, None)


def _desk(db):
    """Whether the caller is an active front-desk member, who may act for any customer."""
    _, desk_id = _caller(db)
    return bool(desk_id and db.one("SELECT 1 AS x FROM desk_staff WHERE desk_id = ? AND role = 'front_desk' AND active = 1", [desk_id]))


def _entitled(db, customer_id):
    """Refuse unless the caller is this customer or the front desk: a customer ID the agent passes
    names a record, it does not prove who is asking."""
    if _desk(db):
        return
    caller, _ = _caller(db)
    if not caller or caller != customer_id:
        raise ToolError(f"Permission denied: the person in this conversation ({caller or 'not identified'}) cannot see or change {customer_id}'s records.", category="permission_denied")


def handle(args, db):
    a = _appointment(db, args.get("appointment_id"), args.get("customer_id"))
    kind = args.get("kind")
    ok = {"booked": a["status"] == "booked" and not a["rescheduled_from"], "rescheduled": a["status"] == "booked" and bool(a["rescheduled_from"]), "cancelled": a["status"] == "cancelled"}
    if kind not in ok:
        raise ToolError("kind must be booked, rescheduled or cancelled.", category="invalid_input")
    if not ok[kind]:
        raise ToolError(f"Appointment {a['appointment_id']} is {a['status']}; a '{kind}' confirmation would state something that has not happened.", category="conflict")
    channel = args.get("channel") or "email"
    # Only a message still waiting to go out on the same channel is the same request; one already
    # sent (or on another channel) is a resend and is queued afresh.
    dup = db.one("SELECT outbox_id FROM confirmation_outbox WHERE appointment_id = ? AND kind = ? AND channel = ? AND status = 'queued'", [a["appointment_id"], kind, channel])
    if dup:
        return {"outbox_id": dup["outbox_id"], "kind": kind, "idempotent": True}
    oid = _next_id(db, "confirmation_outbox", "outbox_id", "O", 600)
    db.execute("INSERT INTO confirmation_outbox (outbox_id, appointment_id, customer_id, kind, channel, status, created_at) VALUES (?, ?, ?, ?, ?, 'queued', ?)", [oid, a["appointment_id"], a["customer_id"], kind, channel, iso(_now(db))])
    return {"outbox_id": oid, "kind": kind, "idempotent": False}
