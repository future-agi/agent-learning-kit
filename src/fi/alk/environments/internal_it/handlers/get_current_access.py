def handle(args, db):
    emp_id = args.get("employee_id")
    if not emp_id:
        raise ToolError("Missing required parameter: employee_id")
    emp = db.one("SELECT * FROM employees WHERE employee_id = ?", [emp_id])
    if emp is None:
        raise ToolError(f"Employee {emp_id!r} not found.")
    _act_for(db, emp_id)
    app_id = args.get("app_id")
    where, params = "employee_id = ?", [emp_id]
    if app_id:
        where += " AND app_id = ?"
        params.append(app_id)
    cols = "entitlement_id, app_id, role_name, status, granted_at, expires_at, granted_by"
    ents = db.query(f"SELECT {cols} FROM entitlements WHERE {where} AND status = 'active' ORDER BY entitlement_id", params)
    # Access that has ended (expired or revoked) is shown apart, so "it stopped working" and
    # "it was already removed" are answered from the record rather than from an empty list.
    ended = db.query(f"SELECT {cols} FROM entitlements WHERE {where} AND status != 'active' ORDER BY entitlement_id", params)
    reqs = db.query(f"SELECT request_id, app_id, role_name, status, approver_id, created_at FROM access_requests WHERE {where} AND status IN ('pending', 'approved') ORDER BY request_id", params)
    return {
        "employee_id": emp_id,
        "active_entitlements": ents,
        "ended_entitlements": ended,
        "pending_requests": reqs
    }


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
