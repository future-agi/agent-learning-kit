# Shared by the generator and copied verbatim into every handler that handles time.
import json
from datetime import datetime


def _iso(utc):
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


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


iso = _iso


def _writable(db, staff_id, what):
    """Refuse a change on a calendar that is read-only here: it is managed elsewhere, so it goes
    through the front desk (create_handoff), like a booking with that staff member."""
    staff = db.one("SELECT name, calendar_id FROM staff WHERE staff_id = ?", [staff_id])
    cal = db.one("SELECT access_role FROM calendar_calendars WHERE calendar_id = ?", [staff["calendar_id"]]) if staff else None
    if not cal or cal["access_role"] not in ("writer", "owner"):
        raise ToolError(f"{staff['name'] if staff else staff_id}'s calendar is read-only here: {what} goes through the front desk (create_handoff).", category="policy_blocked")


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
    if a["status"] != "booked":
        raise ToolError(f"Appointment {a['appointment_id']} is already {a['status']}.", category="conflict")
    _writable(db, a["staff_id"], "cancelling this appointment")
    if args.get("confirmed") is not True:
        raise ToolError("Cancel only after the customer confirmed which appointment to cancel.", category="policy_blocked")
    now = _now(db)
    window, fee = int(_param(db, "late_cancel_window_hours", 24)), int(_param(db, "late_cancel_fee_cents", 2500))
    hours = (_dt(a["start_utc"]) - now).total_seconds() / 3600
    late_fee = fee if 0 < hours < window else 0
    if late_fee and args.get("acknowledged_fee") is not True:
        raise ToolError(f"This is a late cancellation ({hours:.1f} hours before the start): a ${late_fee / 100:.2f} fee applies. Disclose it and cancel only if the customer accepts.", category="policy_blocked")
    db.execute("UPDATE appointments SET status = 'cancelled', late_fee_cents = ?, cancelled_at = ?, updated_at = ? WHERE appointment_id = ?", [late_fee, iso(now), iso(now), a["appointment_id"]])
    db.execute("UPDATE calendar_events SET status = 'cancelled', updated_at = ? WHERE event_id = ?", [iso(now), a["calendar_event_id"]])
    return {"appointment_id": a["appointment_id"], "status": "cancelled", "late_fee_cents": late_fee}
