# Shared by the generator and copied verbatim into every handler that handles time.
import json
from datetime import datetime


def _dt(s):
    return datetime.fromisoformat(str(s).replace("Z", "")[:19])


def _now(db):
    row = db.one("SELECT now FROM clock LIMIT 1")
    return _dt(row["now"] if row else "2026-10-26T09:00:00Z")


def _param(db, name, default):
    row = db.one("SELECT value FROM environment_policy WHERE name = ?", [name])
    return json.loads(row["value"]) if row else default


def _appointment(db, appointment_id, customer_id):
    row = db.one("SELECT * FROM appointments WHERE appointment_id = ?", [appointment_id])
    if row is None:
        raise ToolError(f"Appointment {appointment_id!r} not found.", category="not_found")
    if row["customer_id"] != customer_id:
        raise ToolError(f"Appointment {appointment_id} does not belong to customer {customer_id}.", category="permission_denied")
    _entitled(db, customer_id)
    return row


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
    window, fee = int(_param(db, "late_cancel_window_hours", 24)), int(_param(db, "late_cancel_fee_cents", 2500))
    out = {"window_hours": window, "late_fee_cents": fee, "rule": f"Cancelling less than {window} hours before the start carries a ${fee / 100:.2f} late-cancellation fee, which must be disclosed and accepted first. Rescheduling carries no fee."}
    if args.get("appointment_id"):
        a = _appointment(db, args["appointment_id"], args.get("customer_id"))
        hours = (_dt(a["start_utc"]) - _now(db)).total_seconds() / 3600
        out.update({"appointment_id": a["appointment_id"], "status": a["status"], "local_start": a["local_start"], "time_zone": a["time_zone"], "hours_until_start": round(hours, 1), "late": 0 < hours < window, "fee_applies_cents": fee if 0 < hours < window else 0})
    return out
