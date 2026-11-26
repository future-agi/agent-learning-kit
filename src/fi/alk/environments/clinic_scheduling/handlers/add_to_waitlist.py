import json
from datetime import date


def _now(db):
    row = db.one("SELECT now FROM clock LIMIT 1")
    return row["now"] if row else "2026-10-26T09:00:00Z"


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


def _referral_ok(db, patient_id, specialty):
    spec = db.one("SELECT * FROM specialties WHERE specialty = ?", [specialty])
    if not spec or not spec["referral_required"]:
        return True
    today = _now(db)[:10]
    return db.one("SELECT referral_id FROM referrals WHERE patient_id = ? AND specialty = ? AND status = 'active' AND (expires_on IS NULL OR expires_on >= ?)",
                  [patient_id, specialty, today]) is not None


def _next_id(db, table, column, prefix, start):
    taken = {r[column] for r in db.query(f"SELECT {column} FROM {table}")}
    n = start + len(taken)
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"


def handle(args, db):
    _need(args, "patient_id", "specialty", "earliest_date", "latest_date", "request_key")
    pid = args["patient_id"]
    _verified(db, pid)
    if not _param(db, "waitlist_enabled", True):
        raise ToolError("The waitlist is closed.", category="policy_blocked")
    if not db.one("SELECT specialty FROM specialties WHERE specialty = ?", [args["specialty"]]):
        raise ToolError(f"Unknown specialty {args['specialty']!r}.", category="invalid_input")
    try:
        if date.fromisoformat(args["latest_date"]) < date.fromisoformat(args["earliest_date"]):
            raise ToolError("latest_date is before earliest_date.", category="invalid_input")
    except ValueError:
        raise ToolError("Dates must be YYYY-MM-DD.", category="invalid_input")
    if args.get("provider_id"):
        prov = db.one("SELECT specialty FROM providers WHERE provider_id = ?", [args["provider_id"]])
        if prov is None:
            raise ToolError(f"Unknown provider {args['provider_id']!r}.", category="invalid_input")
        if prov["specialty"] != args["specialty"]:
            raise ToolError(f"Provider {args['provider_id']} is {prov['specialty']}, not {args['specialty']}.", category="invalid_input")
    if not _referral_ok(db, pid, args["specialty"]):
        raise ToolError(f"{args['specialty']} needs an active referral before the patient can wait for a slot.", category="policy_blocked")
    asked = (pid, args["specialty"], args.get("provider_id") or None, args["earliest_date"], args["latest_date"])

    def request(w):
        return (w["patient_id"], w["specialty"], w["provider_id"] or None, w["earliest_date"], w["latest_date"])

    # A replay is the same request only when it asks for the same thing: a reused key, or the
    # patient's one active entry for the specialty, with other dates or another provider is a
    # conflict naming the entry already there, not a success that changed nothing.
    keyed = db.one("SELECT * FROM waitlist WHERE request_key = ?", [args["request_key"]])
    if keyed and request(keyed) != asked:
        raise ToolError(f"request_key {args['request_key']!r} was already used for another waitlist entry; use a new key for this one.", category="conflict")
    same = keyed or db.one("SELECT * FROM waitlist WHERE patient_id = ? AND specialty = ? AND status = 'active'", [pid, args["specialty"]])
    if same and request(same) != asked:
        raise ToolError(f"Patient {pid} is already on the {args['specialty']} waitlist as {same['waitlist_id']} "
                        f"({same['earliest_date']} to {same['latest_date']}{', provider ' + same['provider_id'] if same['provider_id'] else ''}); "
                        "one active entry per specialty.", category="conflict")
    if same:
        return {"waitlist_id": same["waitlist_id"], "status": same["status"], "earliest_date": same["earliest_date"], "latest_date": same["latest_date"], "idempotent": True}
    wid = _next_id(db, "waitlist", "waitlist_id", "W", 101)
    db.execute("INSERT INTO waitlist (waitlist_id, patient_id, specialty, provider_id, earliest_date, latest_date, status, request_key, created_at) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
               [wid, pid, args["specialty"], args.get("provider_id"), args["earliest_date"], args["latest_date"], args["request_key"], _now(db)])
    return {"waitlist_id": wid, "status": "active", "earliest_date": args["earliest_date"], "latest_date": args["latest_date"], "idempotent": False}
