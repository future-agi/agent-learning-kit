def handle(args, db):
    from datetime import datetime
    employee_id = args.get("employee_id")
    if not employee_id:
        raise ToolError("employee_id is required.")
    if not db.one("SELECT 1 FROM employees WHERE employee_id = ?", [employee_id]):
        raise ToolError(f"Employee {employee_id!r} not found.")
    _act_for(db, employee_id)
    now = db.one("SELECT now FROM clock")["now"]

    def days_left(exp):
        if not exp:
            return None
        return (datetime.fromisoformat(exp.replace("Z", "+00:00")) - datetime.fromisoformat(now.replace("Z", "+00:00"))).days

    rows = db.query("SELECT * FROM entitlements WHERE employee_id = ? ORDER BY expires_at", [employee_id])
    return {
        "employee_id": employee_id,
        "entitlements": [{"entitlement_id": r["entitlement_id"], "app_id": r["app_id"], "role_name": r["role_name"], "expires_at": r["expires_at"], "days_left": days_left(r["expires_at"])} for r in rows if r["status"] == "active"],
        # Ended access keeps its dates: when it expired or what was removed.
        "ended": [{"entitlement_id": r["entitlement_id"], "app_id": r["app_id"], "role_name": r["role_name"], "status": r["status"], "expires_at": r["expires_at"]} for r in rows if r["status"] != "active"],
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
