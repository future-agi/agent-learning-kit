

def _now(db):
    row = db.one("SELECT now FROM clock LIMIT 1")
    return row["now"] if row else "2026-10-26T09:00:00Z"


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


def _handle(args, db):
    _need(args, "full_name", "dob")
    name = " ".join(str(args["full_name"]).split()).lower()
    # Only a patient the person talking may act for can be verified: someone who knows a partner's or
    # a colleague's date of birth is still not them. A record that matches but is not theirs gets the
    # same answer as no record, so the desk cannot be used to test whether someone is a patient.
    rows = [p for p in db.query("SELECT * FROM patients WHERE dob = ?", [args["dob"]])
            if " ".join(p["full_name"].split()).lower() == name and _entitled(db, p["patient_id"])]
    if not rows:
        raise ToolError("No patient this caller may act for matches that name and date of birth. Nothing about any patient can be shared; "
                        "a relative or colleague needs proxy authority, which the front desk arranges.", category="permission_denied")
    p = rows[0]
    if not db.one("SELECT patient_id FROM verified_patients WHERE patient_id = ?", [p["patient_id"]]):
        db.execute("INSERT INTO verified_patients (patient_id, verified_at) VALUES (?, ?)", [p["patient_id"], _now(db)])
    ins = db.one("SELECT payer FROM insurance WHERE patient_id = ?", [p["patient_id"]])
    return {"verified": True, "patient_id": p["patient_id"], "full_name": p["full_name"], "guardian_name": p["guardian_name"],
            "has_email": bool(p["email"]), "has_phone": bool(p["phone"]), "is_new_patient": bool(p["is_new_patient"]), "payer": ins["payer"] if ins else None}


_WRITES = ("verified_patients",)

def _all_or_nothing(db, run):
    """Run the call so that a failed call changes nothing. A handler gets no transaction of its own,
    so every table this tool writes is held first and put back if the call raises, whether a write
    failed partway or the call refused after writing. Only a fault the world simulates on purpose
    (an error marked keep_writes: a rival booking, a commit whose reply was lost, a fault used up)
    keeps what it wrote."""
    held = {table: db.query(f"SELECT * FROM {table}") for table in _WRITES}
    try:
        return run()
    except Exception as failed:
        if not getattr(failed, "keep_writes", False):
            for table, rows in held.items():
                if _same(db.query(f"SELECT * FROM {table}"), rows):
                    continue
                db.execute(f"DELETE FROM {table}")
                for row in rows:
                    cols = list(row)
                    db.execute(
                        f"INSERT INTO {table} ({', '.join(chr(34) + c + chr(34) for c in cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [row[c] for c in cols],
                    )
        raise

def _same(rows, held):
    """Whether a table still holds exactly the rows it held, in any order: only a table the call
    changed is rewritten."""
    import json

    def key(row):
        return json.dumps(row, sort_keys=True, default=str)

    return sorted(map(key, rows)) == sorted(map(key, held))


def handle(args, db):
    return _all_or_nothing(db, lambda: _handle(args, db))
