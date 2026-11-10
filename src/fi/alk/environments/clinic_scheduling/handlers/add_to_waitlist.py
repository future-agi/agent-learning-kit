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


def _verified(db, patient_id):
    if not db.one("SELECT patient_id FROM patients WHERE patient_id = ?", [patient_id]):
        raise ToolError(f"Patient {patient_id!r} not found.", category="not_found")
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
    if not _referral_ok(db, pid, args["specialty"]):
        raise ToolError(f"{args['specialty']} needs an active referral before the patient can wait for a slot.", category="policy_blocked")
    same = db.one("SELECT * FROM waitlist WHERE request_key = ?", [args["request_key"]]) or db.one("SELECT * FROM waitlist WHERE patient_id = ? AND specialty = ? AND status = 'active'", [pid, args["specialty"]])
    if same:
        return {"waitlist_id": same["waitlist_id"], "status": same["status"], "earliest_date": same["earliest_date"], "latest_date": same["latest_date"], "idempotent": True}
    wid = _next_id(db, "waitlist", "waitlist_id", "W", 101)
    db.execute("INSERT INTO waitlist (waitlist_id, patient_id, specialty, provider_id, earliest_date, latest_date, status, request_key, created_at) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
               [wid, pid, args["specialty"], args.get("provider_id"), args["earliest_date"], args["latest_date"], args["request_key"], _now(db)])
    return {"waitlist_id": wid, "status": "active", "earliest_date": args["earliest_date"], "latest_date": args["latest_date"], "idempotent": False}
