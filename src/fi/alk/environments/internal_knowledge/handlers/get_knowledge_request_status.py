

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


def handle(args, db):
    e = _employee(db, args.get("employee_id"))
    rid = args.get("request_id")
    if not rid or not isinstance(rid, str):
        raise ToolError("request_id is required.", category="invalid_input")
    r = db.one("SELECT * FROM knowledge_requests WHERE request_id = ?", [rid])
    if r is None:
        raise ToolError(f"Knowledge request {rid!r} not found.", category="not_found")
    if r["requester_id"] != e["employee_id"]:
        raise ToolError("Only the requester may see a knowledge request.", category="permission_denied")
    return {k: r[k] for k in ("request_id", "question", "collection_id", "assigned_to", "status", "notes", "created_at", "updated_at")}
