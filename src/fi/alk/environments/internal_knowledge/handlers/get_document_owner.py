

def _employee(db, employee_id):
    if not employee_id or not isinstance(employee_id, str):
        raise ToolError("employee_id is required: the employee asking.", category="invalid_input")
    e = db.one("SELECT * FROM employees WHERE employee_id = ?", [employee_id])
    if e is None:
        raise ToolError(f"Employee {employee_id!r} not found.", category="not_found")
    # The session says who is in the conversation. The employee_id the agent passes is checked
    # against it and never grants anything by itself: naming a People Ops reader does not unlock
    # the restricted collection, and naming a requester does not open their request.
    caller = db.one("SELECT employee_id FROM session_caller WHERE id = 'default'")
    if caller is None or not caller["employee_id"]:
        raise ToolError("No employee is signed in to this conversation; the desk acts only for the signed-in employee.", category="permission_denied")
    if caller["employee_id"] != employee_id:
        raise ToolError(f"Employee {employee_id} is not the employee signed in to this conversation; the desk answers and acts only for the signed-in employee.", category="permission_denied")
    if e["status"] != "active":
        raise ToolError(f"Employee {employee_id} is not active; the knowledge desk serves active employees only.", category="permission_denied")
    return e


def _readable(db, e):
    return {r["collection_id"] for r in db.query("SELECT collection_id FROM collection_access WHERE role = ?", [e["role"]])}


def _doc(db, e, fid):
    if not fid or not isinstance(fid, str):
        raise ToolError("drive_file_id is required.", category="invalid_input")
    d = db.one("SELECT * FROM kb_documents WHERE drive_file_id = ?", [fid])
    if d is None:
        raise ToolError(f"Document {fid!r} not found in the corpus.", category="not_found")
    if d["collection_id"] not in _readable(db, e):
        raise ToolError("This document is not available to this employee's role.", category="permission_denied")
    return d


def handle(args, db):
    e = _employee(db, args.get("employee_id"))
    d = _doc(db, e, args.get("drive_file_id"))
    o = db.one("SELECT * FROM employees WHERE employee_id = ?", [d["owner_id"]])
    c = db.one("SELECT * FROM collections WHERE collection_id = ?", [d["collection_id"]])
    return {"drive_file_id": d["drive_file_id"], "owner_id": o["employee_id"], "owner_name": o["name"], "owner_team": o["team_id"],
            "owner_slack_user": "U_" + o["employee_id"], "collection_owner_id": c["owner_id"]}
