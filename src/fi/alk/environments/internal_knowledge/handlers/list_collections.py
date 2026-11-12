def _now(db):
    row = db.one("SELECT now FROM clock")
    return row["now"] if row else "2026-11-10T09:00:00Z"


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


def handle(args, db):
    e = _employee(db, args.get("employee_id"))
    out = []
    for cid in sorted(_readable(db, e)):
        c = db.one("SELECT * FROM collections WHERE collection_id = ?", [cid])
        # An approved version dated later is scheduled, not in effect yet.
        n = db.one("SELECT COUNT(*) AS n FROM kb_documents WHERE collection_id = ? AND status = 'approved' AND effective_date <= ?", [cid, _now(db)[:10]])["n"]
        out.append({"collection_id": cid, "name": c["name"], "owner_id": c["owner_id"], "documents_in_effect": n})
    return {"employee_id": e["employee_id"], "collections": out, "count": len(out)}
