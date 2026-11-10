

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


def handle(args, db):
    _need(args, "hold_id")
    h = db.one("SELECT * FROM slot_holds WHERE hold_id = ?", [args["hold_id"]])
    if h is None:
        raise ToolError(f"Hold {args['hold_id']!r} not found.", category="not_found")
    _verified(db, h["patient_id"])
    if h["status"] == "released":
        return {"hold_id": h["hold_id"], "status": "released", "idempotent": True}
    if h["status"] != "active":
        raise ToolError(f"Hold {h['hold_id']} is {h['status']}; nothing to release.", category="conflict")
    db.execute("UPDATE slot_holds SET status = 'released', updated_at = ? WHERE hold_id = ?", [_now(db), h["hold_id"]])
    return {"hold_id": h["hold_id"], "status": "released", "idempotent": False}
