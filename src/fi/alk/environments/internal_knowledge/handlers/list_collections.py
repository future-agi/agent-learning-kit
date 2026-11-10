

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
    out = []
    for cid in sorted(_readable(db, e)):
        c = db.one("SELECT * FROM collections WHERE collection_id = ?", [cid])
        n = db.one("SELECT COUNT(*) AS n FROM kb_documents WHERE collection_id = ? AND status = 'approved'", [cid])["n"]
        out.append({"collection_id": cid, "name": c["name"], "owner_id": c["owner_id"], "documents_in_effect": n})
    return {"employee_id": e["employee_id"], "collections": out, "count": len(out)}
