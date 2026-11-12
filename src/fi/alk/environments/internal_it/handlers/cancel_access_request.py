def handle(args, db):
    request_id = args.get("request_id")
    employee_id = args.get("employee_id")
    confirmed = args.get("confirmed")
    if not request_id or not employee_id:
        raise ToolError("request_id and employee_id are required.")
    if confirmed is None:
        raise ToolError("confirmed is required.")
    if not confirmed:
        raise ToolError("Confirm the cancellation with the employee first.")
    req = db.one("SELECT * FROM access_requests WHERE request_id = ?", [request_id])
    if not req:
        raise ToolError(f"Access request {request_id!r} not found.")
    if req["employee_id"] != employee_id:
        raise ToolError(f"Request {request_id} is not {employee_id}'s.")
    actor = _act_for(db, employee_id)
    now = db.one("SELECT now FROM clock")["now"]
    if req["status"] == "cancelled":
        # A replay of a cancellation already made: nothing changes, and the answer says when it
        # happened so it is not taken for one made now.
        return {"request_id": request_id, "status": "cancelled", "idempotent": True, "cancelled_at": req["updated_at"]}
    if req["status"] not in ("pending", "approved"):
        raise ToolError(f"Request {request_id} is {req['status']} and cannot be cancelled.")
    db.execute("UPDATE access_requests SET status = 'cancelled', updated_at = ? WHERE request_id = ?", [now, request_id])
    db.execute("UPDATE approvals SET status = 'void' WHERE request_id = ? AND status = 'valid'", [request_id])
    _audit(db, "cancel_access_request", actor, "access_request", request_id, f"cancelled for {employee_id}", now)
    return {"request_id": request_id, "status": "cancelled", "idempotent": False, "cancelled_at": now}


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


def _audit(db, action, actor, target_type, target_id, details, now):
    n = db.one("SELECT COUNT(*) AS c FROM audit_log")["c"] + 1
    while db.one("SELECT 1 FROM audit_log WHERE log_id = ?", [f"LOG-{n:03d}"]):
        n += 1
    db.execute("INSERT INTO audit_log (log_id, action, actor_id, target_type, target_id, details, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)", [f"LOG-{n:03d}", action, actor, target_type, target_id, details, now])
