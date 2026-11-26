def _caller(db):
    """The employee signed in to this conversation, as the session recorded them."""
    row = db.one("SELECT employee_id FROM session_caller WHERE id = 'default'")
    caller = db.one("SELECT * FROM employees WHERE employee_id = ?", [row["employee_id"]]) if row and row["employee_id"] else None
    if caller is None:
        raise ToolError("No employee is signed in to this conversation; the desk acts only for the signed-in employee.", category="permission_denied")
    return caller


def _may_see(db, caller, target):
    """Whether the signed-in employee may see `target`'s profile and access: their own, or by world
    data, as People Ops or as the lead of the target's team. An employee_id argument never grants it."""
    if caller["employee_id"] == target["employee_id"]:
        return True
    if caller["status"] != "active":
        return False
    if caller["role"] == "people_ops":
        return True
    return db.one("SELECT 1 FROM teams WHERE team_id = ? AND lead_id = ?", [target["team_id"], caller["employee_id"]]) is not None


def _readable(db, e):
    return {r["collection_id"] for r in db.query("SELECT collection_id FROM collection_access WHERE role = ?", [e["role"]])}


def handle(args, db):
    eid = args.get("employee_id")
    if not eid or not isinstance(eid, str):
        raise ToolError("employee_id is required.", category="invalid_input")
    e = db.one("SELECT * FROM employees WHERE employee_id = ?", [eid])
    if e is None:
        raise ToolError(f"Employee {eid!r} not found.", category="not_found")
    if not _may_see(db, _caller(db), e):
        raise ToolError(f"Employee {eid} is not the employee signed in to this conversation; their profile and access are theirs to see.", category="permission_denied")
    readable = sorted(_readable(db, e)) if e["status"] == "active" else []
    return {"employee_id": e["employee_id"], "name": e["name"], "email": e["email"], "team_id": e["team_id"], "role": e["role"],
            "title": e["title"], "location": e["location"], "status": e["status"], "readable_collections": readable}
