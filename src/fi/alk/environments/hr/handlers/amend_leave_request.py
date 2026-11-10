def _working_days(db, region, start, end):
    import datetime
    s = datetime.datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.datetime.strptime(end, "%Y-%m-%d").date()
    if s > e:
        raise ToolError(f"start_date ({start}) cannot be after end_date ({end}).")
    hol = {r["holiday_date"] for r in db.query("SELECT holiday_date FROM holiday_calendars WHERE region = ? AND holiday_date >= ? AND holiday_date <= ?", (region, start, end))}
    n, d = 0, s
    while d <= e:
        if d.weekday() < 5 and d.strftime("%Y-%m-%d") not in hol:
            n += 1
        d += datetime.timedelta(days=1)
    return n

def handle(args, db):
    import json
    request_id = args.get("request_id")
    employee_id = args.get("employee_id")
    start = args.get("start_date")
    end = args.get("end_date")
    confirmed = args.get("confirmed")
    if not request_id or not employee_id or not start or not end:
        raise ToolError("request_id, employee_id, start_date and end_date are required.")
    if confirmed is None:
        raise ToolError("confirmed is required.")
    if not confirmed:
        raise ToolError("Policy: confirm the new dates with the employee before amending.")
    req = db.one("SELECT * FROM leave_requests WHERE request_id = ?", (request_id,))
    if not req:
        raise ToolError(f"Leave request {request_id} not found.")
    if req["employee_id"] != employee_id:
        raise ToolError(f"Permission denied: request {request_id} is not {employee_id}'s.")
    if req["status"] not in ("pending", "approved"):
        raise ToolError(f"Request {request_id} is {req['status']} and cannot be amended.")
    emp = db.one("SELECT * FROM employees WHERE employee_id = ?", (employee_id,))
    days = _working_days(db, emp["region"], start, end)
    if days <= 0:
        raise ToolError("The new date span contains 0 working days.")
    overlap = db.query("SELECT request_id FROM leave_requests WHERE employee_id = ? AND request_id != ? AND status IN ('pending', 'approved') AND NOT (end_date < ? OR start_date > ?)", (employee_id, request_id, start, end))
    if overlap:
        raise ToolError(f"The new dates overlap request {overlap[0]['request_id']}.")
    old_days = float(req["working_days"])
    bal = db.one("SELECT * FROM leave_balances WHERE employee_id = ? AND leave_type = ?", (employee_id, req["leave_type"]))
    if bal and req["leave_type"] != "unpaid":
        held = float(bal["reserved_days"]) if req["status"] == "pending" else float(bal["used_days"])
        free = float(bal["available_days"]) + old_days
        if days > free:
            raise ToolError(f"Insufficient {req['leave_type']} balance for the new dates: {days} days needed, {free} available.")
        # The amendment goes back to pending: its days move to reserved, whatever the old status was.
        if req["status"] == "pending":
            db.execute("UPDATE leave_balances SET reserved_days = ?, available_days = ? WHERE balance_id = ?", (float(bal["reserved_days"]) - old_days + days, free - days, bal["balance_id"]))
        else:
            db.execute("UPDATE leave_balances SET used_days = ?, reserved_days = ?, available_days = ? WHERE balance_id = ?", (float(bal["used_days"]) - old_days, float(bal["reserved_days"]) + days, free - days, bal["balance_id"]))
    now = db.one("SELECT now FROM clock")["now"]
    db.execute("UPDATE leave_requests SET start_date = ?, end_date = ?, working_days = ?, status = 'pending', decided_by = NULL, decided_at = NULL, updated_at = ? WHERE request_id = ?", (start, end, days, now, request_id))
    return {"request_id": request_id, "employee_id": employee_id, "start_date": start, "end_date": end, "working_days": float(days), "status": "pending", "previous_status": req["status"], "approver_id": req["approver_id"], "note": "An amended request needs a new approval."}
