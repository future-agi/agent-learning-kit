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


def _verified(db, patient_id):
    if not db.one("SELECT patient_id FROM patients WHERE patient_id = ?", [patient_id]):
        raise ToolError(f"Patient {patient_id!r} not found.", category="not_found")
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



def _together(db, touched, writes):
    """Run `writes` so that every row lands or none does. A handler gets no transaction of its
    own, so the rows in `touched` ((table, column, value) each) are held first and put back if a
    write fails: an appointment never survives without its calendar event, or the reverse."""
    held = [(t, c, v, db.query(f"SELECT * FROM {t} WHERE {c} = ?", [v])) for t, c, v in touched]
    try:
        return writes()
    except Exception:
        for t, c, v, rows in held:
            db.execute(f"DELETE FROM {t} WHERE {c} = ?", [v])
            for row in rows:
                cols = list(row)
                db.execute(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [row[k] for k in cols])
        raise


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

    def cancel():
        db.execute("UPDATE appointments SET status = 'cancelled', cancel_reason = ?, cancelled_at = ?, updated_at = ? WHERE appointment_id = ?", [args.get("reason") or "patient request", now, now, a["appointment_id"]])
        db.execute("UPDATE calendar_events SET status = 'cancelled', updated_at = ? WHERE event_id = ?", [now, a["calendar_event_id"]])

    _together(db, [("appointments", "appointment_id", a["appointment_id"]), ("calendar_events", "event_id", a["calendar_event_id"])], cancel)
    b = db.one("SELECT * FROM appointments WHERE appointment_id = ?", [a["appointment_id"]])
    return dict(_appointment_view(b), idempotent=False)
