


def _readable(db, e):
    return {r["collection_id"] for r in db.query("SELECT collection_id FROM collection_access WHERE role = ?", [e["role"]])}


def handle(args, db):
    eid = args.get("employee_id")
    if not eid or not isinstance(eid, str):
        raise ToolError("employee_id is required.", category="invalid_input")
    e = db.one("SELECT * FROM employees WHERE employee_id = ?", [eid])
    if e is None:
        raise ToolError(f"Employee {eid!r} not found.", category="not_found")
    readable = sorted(_readable(db, e)) if e["status"] == "active" else []
    return {"employee_id": e["employee_id"], "name": e["name"], "email": e["email"], "team_id": e["team_id"], "role": e["role"],
            "title": e["title"], "location": e["location"], "status": e["status"], "readable_collections": readable}
