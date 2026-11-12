def handle(args, db):
    req_id = args.get("request_id")
    if not req_id:
        raise ToolError("Missing required parameter: request_id")
    req = db.one("SELECT * FROM access_requests WHERE request_id = ?", [req_id])
    if req is None:
        raise ToolError(f"Access request {req_id!r} not found.")
    # The answer carries the approval token, so only the requester, their manager, the request's
    # approvers or IT operations see it.
    _act_for(db, req["employee_id"], also=(req["approver_id"], req["approved_by"]))
    approval = db.one("SELECT * FROM approvals WHERE request_id = ? ORDER BY created_at DESC", [req_id])
    approval_info = None
    if approval:
        approval_info = {
            "approval_id": approval["approval_id"],
            "approver_id": approval["approver_id"],
            "created_at": approval["created_at"],
            "expires_at": approval["expires_at"],
            "status": approval["status"],
            "token": approval["token"]
        }
    return {
        "approval": approval_info,
        "app_id": req["app_id"],
        "approver_id": req["approver_id"],
        "approved_by": req["approved_by"],
        "employee_id": req["employee_id"],
        "request_id": req["request_id"],
        "request_status": req["status"],
        "role_name": req["role_name"],
        "duration_days": req["duration_days"]
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
