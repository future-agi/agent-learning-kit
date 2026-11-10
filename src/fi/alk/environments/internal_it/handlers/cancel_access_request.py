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
    now = db.one("SELECT now FROM clock")["now"]
    if req["status"] == "cancelled":
        return {"request_id": request_id, "status": "cancelled", "idempotent": True}
    if req["status"] not in ("pending", "approved"):
        raise ToolError(f"Request {request_id} is {req['status']} and cannot be cancelled.")
    db.execute("UPDATE access_requests SET status = 'cancelled', updated_at = ? WHERE request_id = ?", [now, request_id])
    db.execute("UPDATE approvals SET status = 'void' WHERE request_id = ? AND status = 'valid'", [request_id])
    n = db.one("SELECT COUNT(*) AS c FROM audit_log")["c"] + 1
    db.execute("INSERT INTO audit_log (log_id, action, actor_id, target_type, target_id, details, timestamp) VALUES (?, 'cancel_access_request', ?, 'access_request', ?, 'cancelled by the employee', ?)", [f"LOG-{n:03d}", employee_id, request_id, now])
    return {"request_id": request_id, "status": "cancelled", "idempotent": False}
