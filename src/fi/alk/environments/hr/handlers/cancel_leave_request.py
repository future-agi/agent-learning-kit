def handle(args, db):
    request_id = args.get("request_id")
    employee_id = args.get("employee_id")
    confirmed = args.get("confirmed")
    if not request_id or not employee_id:
        raise ToolError("request_id and employee_id are required.")
    if confirmed is None:
        raise ToolError("confirmed is required.")
    if not confirmed:
        raise ToolError("Policy: confirm the cancellation with the employee first.")
    req = db.one("SELECT * FROM leave_requests WHERE request_id = ?", (request_id,))
    if not req:
        raise ToolError(f"Leave request {request_id} not found.")
    if req["employee_id"] != employee_id:
        raise ToolError(f"Permission denied: request {request_id} is not {employee_id}'s.")
    now = db.one("SELECT now FROM clock")["now"]
    if req["status"] == "cancelled":
        return {"request_id": request_id, "status": "cancelled", "idempotent": True, "days_returned": 0.0}
    if req["status"] not in ("pending", "approved"):
        raise ToolError(f"Request {request_id} is {req['status']} and cannot be cancelled.")
    if req["start_date"] <= now[:10]:
        raise ToolError(f"Request {request_id} has already started; ask HR to adjust taken leave through a case.")
    days = float(req["working_days"])
    bal = db.one("SELECT * FROM leave_balances WHERE employee_id = ? AND leave_type = ?", (employee_id, req["leave_type"]))
    if bal and req["leave_type"] != "unpaid":
        if req["status"] == "pending":
            db.execute("UPDATE leave_balances SET reserved_days = ?, available_days = ? WHERE balance_id = ?", (float(bal["reserved_days"]) - days, float(bal["available_days"]) + days, bal["balance_id"]))
        else:
            db.execute("UPDATE leave_balances SET used_days = ?, available_days = ? WHERE balance_id = ?", (float(bal["used_days"]) - days, float(bal["available_days"]) + days, bal["balance_id"]))
    db.execute("UPDATE leave_requests SET status = 'cancelled', updated_at = ? WHERE request_id = ?", (now, request_id))
    return {"request_id": request_id, "status": "cancelled", "idempotent": False, "days_returned": days}
