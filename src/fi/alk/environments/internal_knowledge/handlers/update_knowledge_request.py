def _now(db):
    row = db.one("SELECT now FROM clock")
    return row["now"] if row else "2026-11-10T09:00:00Z"


def _employee(db, employee_id):
    if not employee_id or not isinstance(employee_id, str):
        raise ToolError("employee_id is required: the employee asking.", category="invalid_input")
    e = db.one("SELECT * FROM employees WHERE employee_id = ?", [employee_id])
    if e is None:
        raise ToolError(f"Employee {employee_id!r} not found.", category="not_found")
    if e["status"] != "active":
        raise ToolError(f"Employee {employee_id} is not active; the knowledge desk serves active employees only.", category="permission_denied")
    return e


def handle(args, db):
    e = _employee(db, args.get("employee_id"))
    rid = args.get("request_id")
    note, status = args.get("note"), args.get("status")
    if not rid or not isinstance(rid, str):
        raise ToolError("request_id is required.", category="invalid_input")
    if not note and not status:
        raise ToolError("Give a note to add, or status 'withdrawn'.", category="invalid_input")
    if status and status != "withdrawn":
        raise ToolError("The only status an employee may set is 'withdrawn'.", category="invalid_input")
    r = db.one("SELECT * FROM knowledge_requests WHERE request_id = ?", [rid])
    if r is None:
        raise ToolError(f"Knowledge request {rid!r} not found.", category="not_found")
    if r["requester_id"] != e["employee_id"]:
        raise ToolError("Only the requester may change a knowledge request.", category="permission_denied")
    if r["status"] in ("closed", "withdrawn"):
        raise ToolError(f"Knowledge request {rid} is {r['status']} and cannot be changed; open a new one.", category="conflict")
    notes = r["notes"] + ("; " if r["notes"] and note else "") + (note or "")
    new_status = status or r["status"]
    db.execute("UPDATE knowledge_requests SET notes = ?, status = ?, updated_at = ? WHERE request_id = ?", [notes, new_status, _now(db), rid])
    return {"request_id": rid, "status": new_status, "notes": notes}
