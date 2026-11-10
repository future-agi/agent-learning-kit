

def _need(args, *names):
    missing = [n for n in names if args.get(n) in (None, "")]
    if missing:
        raise ToolError(f"Missing required argument(s): {', '.join(missing)}", category="invalid_input")


def _verified(db, patient_id):
    if not db.one("SELECT patient_id FROM patients WHERE patient_id = ?", [patient_id]):
        raise ToolError(f"Patient {patient_id!r} not found.", category="not_found")
    if not db.one("SELECT patient_id FROM verified_patients WHERE patient_id = ?", [patient_id]):
        raise ToolError(f"Patient {patient_id} has not been verified in this conversation; verify the patient's full name and date of birth first.", category="permission_denied")


def _appointment_view(a):
    return {k: a[k] for k in ("appointment_id", "patient_id", "provider_id", "location_id", "specialty", "start_time", "end_time", "reason", "status", "calendar_event_id")}


def handle(args, db):
    _need(args, "patient_id")
    _verified(db, args["patient_id"])
    rows = db.query("SELECT * FROM appointments WHERE patient_id = ? ORDER BY start_time", [args["patient_id"]])
    return {"patient_id": args["patient_id"], "appointments": [_appointment_view(a) for a in rows], "count": len(rows)}
