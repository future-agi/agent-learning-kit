def handle(args, db):
    employee_id = args.get("employee_id")
    status = args.get("status")
    if not employee_id:
        raise ToolError("employee_id is required.")
    if not db.one("SELECT 1 FROM employees WHERE employee_id = ?", [employee_id]):
        raise ToolError(f"Employee {employee_id!r} not found.")
    _act_for(db, employee_id)
    sql = "SELECT * FROM access_requests WHERE employee_id = ?"
    params = [employee_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    rows = db.query(sql + " ORDER BY created_at", params)
    return {"employee_id": employee_id, "requests": [{"request_id": r["request_id"], "app_id": r["app_id"], "role_name": r["role_name"], "status": r["status"], "approver_id": r["approver_id"], "duration_days": r["duration_days"], "created_at": r["created_at"]} for r in rows], "count": len(rows)}


def _caller(db):
    """The employee signed in to this conversation. The session sets it; nothing the desk is told
    or passes as an argument changes it."""
    row = db.one("SELECT employee_id FROM session_caller WHERE id = 'default'")
    return row["employee_id"] if row else None


def _act_for(db, employee_id, also=()):
    """The signed-in caller, if they may act for or see `employee_id`'s access: the employee
    themselves, their manager, someone in `also` (the request's approvers), or an active member of
    IT operations. Naming an employee is not being them, so anyone else is refused."""
    caller = _caller(db)
    if caller and (caller == employee_id or caller in also):
        return caller
    me = db.one("SELECT department, status FROM employees WHERE employee_id = ?", [caller]) if caller else None
    if me and me["status"] == "active":
        if me["department"] == "it_operations":
            return caller
        emp = db.one("SELECT manager_id FROM employees WHERE employee_id = ?", [employee_id])
        if emp and emp["manager_id"] == caller:
            return caller
    raise ToolError(f"Permission denied: the person signed in to this conversation ({caller or 'nobody'}) may not act for or see the access of {employee_id}.", category="permission_denied")
