

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


def _verified(db, patient_id):
    if not db.one("SELECT patient_id FROM patients WHERE patient_id = ?", [patient_id]):
        raise ToolError(f"Patient {patient_id!r} not found.", category="not_found")
    if not _entitled(db, patient_id):
        raise ToolError(f"Permission denied: the person in this conversation is not {patient_id} and holds no recorded guardian or proxy authority for them; a relative or colleague needs proxy access, which the front desk arranges.", category="permission_denied")
    if not db.one("SELECT patient_id FROM verified_patients WHERE patient_id = ?", [patient_id]):
        raise ToolError(f"Patient {patient_id} has not been verified in this conversation; verify the patient's full name and date of birth first.", category="permission_denied")


def _next_id(db, table, column, prefix, start):
    taken = {r[column] for r in db.query(f"SELECT {column} FROM {table}")}
    n = start + len(taken)
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"


def handle(args, db):
    _need(args, "patient_id", "specialty", "request_key")
    _verified(db, args["patient_id"])
    spec = db.one("SELECT * FROM specialties WHERE specialty = ?", [args["specialty"]])
    if spec is None:
        raise ToolError(f"Unknown specialty {args['specialty']!r}.", category="invalid_input")
    if not spec["referral_required"]:
        raise ToolError(f"{spec['display_name']} needs no referral; book it directly.", category="invalid_input")
    same = db.one("SELECT * FROM referrals WHERE request_key = ?", [args["request_key"]])
    if same and (same["patient_id"] != args["patient_id"] or same["specialty"] != args["specialty"]):
        raise ToolError(f"request_key {args['request_key']!r} was already used for another referral request; use a new key for this one.", category="conflict")
    if same:
        return {"referral_id": same["referral_id"], "status": same["status"], "idempotent": True}
    open_ = db.one("SELECT * FROM referrals WHERE patient_id = ? AND specialty = ? AND status IN ('requested', 'pending', 'active') AND (expires_on IS NULL OR expires_on >= ?)", [args["patient_id"], args["specialty"], _now(db)[:10]])
    if open_:
        raise ToolError(f"Referral {open_['referral_id']} for {args['specialty']} is already {open_['status']}.", category="conflict")
    now = _now(db)
    rid = _next_id(db, "referrals", "referral_id", "RF", 101)
    db.execute("INSERT INTO referrals (referral_id, patient_id, specialty, status, referring_practice, expires_on, request_key, created_at, updated_at) VALUES (?, ?, ?, 'requested', ?, NULL, ?, ?, ?)",
               [rid, args["patient_id"], args["specialty"], args.get("referring_practice") or "to be confirmed by the referrals team", args["request_key"], now, now])
    return {"referral_id": rid, "status": "requested", "usable_for_booking": False,
            "next_step": "The referrals team asks the patient's practice for the referral; booking opens once it is active.", "idempotent": False}
