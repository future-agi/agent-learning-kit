

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


def handle(args, db):
    _need(args, "appointment_id")
    a = _own_appointment(db, args["appointment_id"])
    prov = db.one("SELECT name FROM providers WHERE provider_id = ?", [a["provider_id"]])
    loc = db.one("SELECT name, address FROM locations WHERE location_id = ?", [a["location_id"]])
    return dict(_appointment_view(a), provider_name=prov["name"], location_name=loc["name"], location_address=loc["address"], reschedule_count=a["reschedule_count"])
