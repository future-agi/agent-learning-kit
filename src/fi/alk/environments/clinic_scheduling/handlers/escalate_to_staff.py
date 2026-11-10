

def _now(db):
    row = db.one("SELECT now FROM clock LIMIT 1")
    return row["now"] if row else "2026-10-26T09:00:00Z"


def _need(args, *names):
    missing = [n for n in names if args.get(n) in (None, "")]
    if missing:
        raise ToolError(f"Missing required argument(s): {', '.join(missing)}", category="invalid_input")


def _next_id(db, table, column, prefix, start):
    taken = {r[column] for r in db.query(f"SELECT {column} FROM {table}")}
    n = start + len(taken)
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"


def handle(args, db):
    _need(args, "team", "reason")
    route = db.one("SELECT * FROM escalation_routes WHERE team = ?", [args["team"]])
    if route is None:
        raise ToolError("team must be nurse_line, front_desk or referrals_team.", category="invalid_input")
    urgency = args.get("urgency") or "routine"
    if urgency not in ("routine", "urgent"):
        raise ToolError("urgency must be routine or urgent.", category="invalid_input")
    pid = args.get("patient_id")
    if pid and not db.one("SELECT patient_id FROM patients WHERE patient_id = ?", [pid]):
        raise ToolError(f"Patient {pid!r} not found.", category="not_found")
    eid = _next_id(db, "escalations", "escalation_id", "ESC", 101)
    db.execute("INSERT INTO escalations (escalation_id, team, urgency, reason, patient_id, created_at) VALUES (?, ?, ?, ?, ?, ?)", [eid, args["team"], urgency, args["reason"], pid, _now(db)])
    return {"escalation_id": eid, "team": args["team"], "urgency": urgency, "destination": route["destination"], "guidance": route["used_for"]}
