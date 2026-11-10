import json


def _now(db):
    row = db.one("SELECT now FROM clock LIMIT 1")
    return row["now"] if row else "2026-10-26T09:00:00Z"


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


def _next_id(db, table, column, prefix, start):
    taken = {r[column] for r in db.query(f"SELECT {column} FROM {table}")}
    n = start + len(taken)
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"


def handle(args, db):
    _need(args, "patient_id", "channel")
    if args["channel"] not in ("sms", "email"):
        raise ToolError("channel must be sms or email.", category="invalid_input")
    _verified(db, args["patient_id"])
    p = db.one("SELECT * FROM patients WHERE patient_id = ?", [args["patient_id"]])
    dest = p["email"] if args["channel"] == "email" else p["phone"]
    if not dest:
        raise ToolError(f"No {args['channel']} contact is on file for {args['patient_id']}; offer the other channel.", category="invalid_input")
    specialty = "all"
    if args.get("appointment_id"):
        a = _own_appointment(db, args["appointment_id"])
        if a["patient_id"] != args["patient_id"]:
            raise ToolError("That appointment belongs to another patient.", category="permission_denied")
        specialty = a["specialty"]
    forms = [r["form_id"] for r in db.query("SELECT form_id FROM intake_forms WHERE specialty IN ('all', ?) ORDER BY form_id", [specialty])]
    now = _now(db)
    same = db.one("SELECT * FROM intake_sends WHERE patient_id = ? AND channel = ? AND created_at = ?", [args["patient_id"], args["channel"], now])
    if same:
        return {"send_id": same["send_id"], "channel": same["channel"], "destination_masked": dest[:3] + "***", "forms": same["form_ids"] if isinstance(same["form_ids"], list) else json.loads(same["form_ids"]), "idempotent": True}
    sid = _next_id(db, "intake_sends", "send_id", "IS", 101)
    db.execute("INSERT INTO intake_sends (send_id, patient_id, appointment_id, channel, destination, form_ids, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
               [sid, args["patient_id"], args.get("appointment_id"), args["channel"], dest, json.dumps(forms), now])
    return {"send_id": sid, "channel": args["channel"], "destination_masked": dest[:3] + "***", "forms": forms, "idempotent": False}
