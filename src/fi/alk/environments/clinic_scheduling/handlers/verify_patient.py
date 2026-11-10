

def _now(db):
    row = db.one("SELECT now FROM clock LIMIT 1")
    return row["now"] if row else "2026-10-26T09:00:00Z"


def _need(args, *names):
    missing = [n for n in names if args.get(n) in (None, "")]
    if missing:
        raise ToolError(f"Missing required argument(s): {', '.join(missing)}", category="invalid_input")


def handle(args, db):
    _need(args, "full_name", "dob")
    name = " ".join(str(args["full_name"]).split()).lower()
    rows = [p for p in db.query("SELECT * FROM patients WHERE dob = ?", [args["dob"]]) if " ".join(p["full_name"].split()).lower() == name]
    if not rows:
        raise ToolError("No patient matches that name and date of birth. Nothing about any patient can be shared.", category="permission_denied")
    p = rows[0]
    if not db.one("SELECT patient_id FROM verified_patients WHERE patient_id = ?", [p["patient_id"]]):
        db.execute("INSERT INTO verified_patients (patient_id, verified_at) VALUES (?, ?)", [p["patient_id"], _now(db)])
    ins = db.one("SELECT payer FROM insurance WHERE patient_id = ?", [p["patient_id"]])
    return {"verified": True, "patient_id": p["patient_id"], "full_name": p["full_name"], "guardian_name": p["guardian_name"],
            "has_email": bool(p["email"]), "has_phone": bool(p["phone"]), "is_new_patient": bool(p["is_new_patient"]), "payer": ins["payer"] if ins else None}
