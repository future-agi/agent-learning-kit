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
    return row


def handle(args, db):
    window, fee = int(_param(db, "late_cancel_window_hours", 24)), int(_param(db, "late_cancel_fee_cents", 2500))
    out = {"window_hours": window, "late_fee_cents": fee, "rule": f"Cancelling less than {window} hours before the start carries a ${fee / 100:.2f} late-cancellation fee, which must be disclosed and accepted first. Rescheduling carries no fee."}
    if args.get("appointment_id"):
        a = _appointment(db, args["appointment_id"], args.get("customer_id"))
        hours = (_dt(a["start_utc"]) - _now(db)).total_seconds() / 3600
        out.update({"appointment_id": a["appointment_id"], "hours_until_start": round(hours, 1), "late": 0 < hours < window, "fee_applies_cents": fee if 0 < hours < window else 0})
    return out
