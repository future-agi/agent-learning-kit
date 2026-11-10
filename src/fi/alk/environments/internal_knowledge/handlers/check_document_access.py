

def _employee(db, employee_id):
    if not employee_id or not isinstance(employee_id, str):
        raise ToolError("employee_id is required: the employee asking.", category="invalid_input")
    e = db.one("SELECT * FROM employees WHERE employee_id = ?", [employee_id])
    if e is None:
        raise ToolError(f"Employee {employee_id!r} not found.", category="not_found")
    if e["status"] != "active":
        raise ToolError(f"Employee {employee_id} is not active; the knowledge desk serves active employees only.", category="permission_denied")
    return e


def _readable(db, e):
    return {r["collection_id"] for r in db.query("SELECT collection_id FROM collection_access WHERE role = ?", [e["role"]])}


def handle(args, db):
    e = _employee(db, args.get("employee_id"))
    fid = args.get("drive_file_id")
    if not fid or not isinstance(fid, str):
        raise ToolError("drive_file_id is required.", category="invalid_input")
    d = db.one("SELECT * FROM kb_documents WHERE drive_file_id = ?", [fid])
    if d is None:
        raise ToolError(f"Document {fid!r} not found in the corpus.", category="not_found")
    if d["collection_id"] not in _readable(db, e):
        return {"drive_file_id": fid, "allowed": False, "reason": "outside the collections this employee's role may read"}
    return {"drive_file_id": fid, "allowed": True, "reason": f"collection {d['collection_id']} is readable by role {e['role']}", "title": d["title"]}
