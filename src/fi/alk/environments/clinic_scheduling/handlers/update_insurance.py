

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
    _need(args, "patient_id", "payer", "member_id")
    _verified(db, args["patient_id"])
    if args.get("confirmed") is not True:
        raise ToolError("Read the new insurer and member number back to the patient, then call again with confirmed=true.", category="policy_blocked")
    if not db.one("SELECT payer FROM payers WHERE payer = ?", [args["payer"]]):
        known = [r["payer"] for r in db.query("SELECT payer FROM payers ORDER BY payer")]
        raise ToolError(f"Unknown insurer {args['payer']!r}; the clinic records {known}.", category="invalid_input")
    now = _now(db)
    if db.one("SELECT patient_id FROM insurance WHERE patient_id = ?", [args["patient_id"]]):
        db.execute("UPDATE insurance SET payer = ?, member_id = ?, group_number = ?, updated_at = ? WHERE patient_id = ?", [args["payer"], args["member_id"], args.get("group_number") or "", now, args["patient_id"]])
    else:
        db.execute("INSERT INTO insurance (patient_id, payer, member_id, group_number, updated_at) VALUES (?, ?, ?, ?, ?)", [args["patient_id"], args["payer"], args["member_id"], args.get("group_number") or "", now])
    return {"patient_id": args["patient_id"], "payer": args["payer"], "member_id": args["member_id"], "group_number": args.get("group_number") or "",
            "note": "Recorded for billing; whether a visit is covered is decided by the insurer, not the clinic."}
