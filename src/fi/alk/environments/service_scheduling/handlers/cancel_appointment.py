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
    return row


iso = _iso


def handle(args, db):
    a = _appointment(db, args.get("appointment_id"), args.get("customer_id"))
    if a["status"] != "booked":
        raise ToolError(f"Appointment {a['appointment_id']} is already {a['status']}.", category="conflict")
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
