import json
from datetime import datetime, timedelta


def _now(db):
    row = db.one("SELECT now FROM clock LIMIT 1")
    return row["now"] if row else "2026-10-26T09:00:00Z"


def _dt(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _param(db, name, default):
    row = db.one("SELECT value FROM environment_policy WHERE name = ?", [name])
    return json.loads(row["value"]) if row else default


def _need(args, *names):
    missing = [n for n in names if args.get(n) in (None, "")]
    if missing:
        raise ToolError(f"Missing required argument(s): {', '.join(missing)}", category="invalid_input")


def _caller(db):
    row = db.one("SELECT caller_id FROM session_caller WHERE id = 'default'")
    return row["caller_id"] if row else None


def _entitled(db, patient_id):
    """Whether the person signed in to this conversation may act for `patient_id`: the patient
    themself, or someone the clinic records as their guardian or proxy. The session says who is
    talking; a patient id, or a name and date of birth, the desk is told grants nothing."""
    caller = _caller(db)
    return caller is not None and (caller == patient_id or db.one(
        "SELECT patient_id FROM caller_access WHERE caller_id = ? AND patient_id = ?", [caller, patient_id]) is not None)


def _verified(db, patient_id):
    if not db.one("SELECT patient_id FROM patients WHERE patient_id = ?", [patient_id]):
        raise ToolError(f"Patient {patient_id!r} not found.", category="not_found")
    if not _entitled(db, patient_id):
        raise ToolError(f"Permission denied: the person in this conversation is not {patient_id} and holds no recorded guardian or proxy authority for them; a relative or colleague needs proxy access, which the front desk arranges.", category="permission_denied")
    if not db.one("SELECT patient_id FROM verified_patients WHERE patient_id = ?", [patient_id]):
        raise ToolError(f"Patient {patient_id} has not been verified in this conversation; verify the patient's full name and date of birth first.", category="permission_denied")


def _own_appointment(db, appointment_id):
    appt = db.one("SELECT * FROM appointments WHERE appointment_id = ?", [appointment_id])
    if appt is None:
        raise ToolError(f"Appointment {appointment_id!r} not found.", category="not_found")
    _verified(db, appt["patient_id"])
    return appt


def _appointment_view(a):
    return {k: a[k] for k in ("appointment_id", "patient_id", "provider_id", "location_id", "specialty", "start_time", "end_time", "reason", "status", "calendar_event_id")}


def handle(args, db):
    _need(args, "appointment_id")
    a = _own_appointment(db, args["appointment_id"])
    if a["status"] == "cancelled":
        return dict(_appointment_view(a), idempotent=True)
    if a["status"] != "scheduled":
        raise ToolError(f"Appointment {a['appointment_id']} is {a['status']} and cannot be cancelled.", category="conflict")
    if args.get("confirmed") is not True:
        raise ToolError("Confirm with the patient which appointment to cancel, then call again with confirmed=true.", category="policy_blocked")
    now = _now(db)
    hours = int(_param(db, "change_notice_hours", 24))
    if _dt(a["start_time"]) - _dt(now) < timedelta(hours=hours):
        raise ToolError(f"Appointment {a['appointment_id']} starts within {hours} hours; late cancellations go to the front desk.", category="policy_blocked")
    db.execute("UPDATE appointments SET status = 'cancelled', cancel_reason = ?, cancelled_at = ?, updated_at = ? WHERE appointment_id = ?", [args.get("reason") or "patient request", now, now, a["appointment_id"]])
    db.execute("UPDATE calendar_events SET status = 'cancelled', updated_at = ? WHERE event_id = ?", [now, a["calendar_event_id"]])
    b = db.one("SELECT * FROM appointments WHERE appointment_id = ?", [a["appointment_id"]])
    return dict(_appointment_view(b), idempotent=False)
