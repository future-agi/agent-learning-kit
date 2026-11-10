

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
    _need(args, "patient_id")
    _verified(db, args["patient_id"])
    q, p = "SELECT * FROM referrals WHERE patient_id = ?", [args["patient_id"]]
    if args.get("specialty"):
        q += " AND specialty = ?"; p.append(args["specialty"])
    today = _now(db)[:10]
    out = []
    for r in db.query(q + " ORDER BY created_at", p):
        usable = r["status"] == "active" and (r["expires_on"] is None or r["expires_on"] >= today)
        out.append({"referral_id": r["referral_id"], "specialty": r["specialty"], "status": r["status"], "referring_practice": r["referring_practice"],
                    "expires_on": r["expires_on"], "usable_for_booking": usable})
    return {"patient_id": args["patient_id"], "referrals": out, "count": len(out)}
