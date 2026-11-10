def handle(args, db):
    import datetime
    import json
    
    employee_id = args.get("employee_id")
    leave_type = args.get("leave_type")
    start_str = args.get("start_date")
    end_str = args.get("end_date")
    reason = args.get("reason", "")
    actor_id = args.get("actor_id")
    
    if not employee_id or not leave_type or not start_str or not end_str:
        raise ToolError("employee_id, leave_type, start_date, and end_date are required.")
        
    if actor_id and actor_id != employee_id:
        raise ToolError(f"Permission denied: actor {actor_id} cannot submit leave requests for employee {employee_id}.")
        
    emp = db.one("SELECT * FROM employees WHERE employee_id = ?", (employee_id,))
    if not emp:
        raise ToolError(f"Employee {employee_id} not found.")
        
    allowed_types_row = db.one("SELECT value FROM environment_policy WHERE name = ?", ("allowed_leave_types",))
    if allowed_types_row:
        allowed_val = json.loads(allowed_types_row["value"])
        if isinstance(allowed_val, str):
            allowed_list = [t.strip() for t in allowed_val.split(",")]
        else:
            allowed_list = list(allowed_val)
        if leave_type not in allowed_list:
            raise ToolError(f"Leave type {leave_type!r} is not allowed. Permitted types: {allowed_list}")
            
    try:
        start_dt = datetime.datetime.strptime(start_str, "%Y-%m-%d").date()
        end_dt = datetime.datetime.strptime(end_str, "%Y-%m-%d").date()
    except Exception as e:
        raise ToolError(f"Invalid date format: must be YYYY-MM-DD. Error: {e}")
        
    if start_dt > end_dt:
        raise ToolError(f"start_date ({start_str}) cannot be after end_date ({end_str}).")
        
    region = emp["region"]
    holidays_rows = db.query("SELECT holiday_date FROM holiday_calendars WHERE region = ? AND holiday_date >= ? AND holiday_date <= ?", (region, start_str, end_str))
    holiday_dates = {r["holiday_date"] for r in holidays_rows}
    
    curr = start_dt
    one_day = datetime.timedelta(days=1)
    working_days = 0
    while curr <= end_dt:
        curr_str = curr.strftime("%Y-%m-%d")
        if curr.weekday() not in (5, 6) and curr_str not in holiday_dates:
            working_days += 1
        curr += one_day
        
    if working_days <= 0:
        raise ToolError("Requested date span contains 0 working days.")
        
    overlap = db.query(
        "SELECT request_id, start_date, end_date FROM leave_requests WHERE employee_id = ? AND status IN ('pending', 'approved') AND NOT (end_date < ? OR start_date > ?)",
        (employee_id, start_str, end_str)
    )
    if overlap:
        raise ToolError(f"Overlapping leave request already exists: {overlap[0]['request_id']} ({overlap[0]['start_date']} to {overlap[0]['end_date']}).")
        
    reserved_days = 0
    if leave_type != "unpaid":
        bal = db.one("SELECT * FROM leave_balances WHERE employee_id = ? AND leave_type = ?", (employee_id, leave_type))
        if not bal:
            raise ToolError(f"No {leave_type} leave balance found for employee {employee_id}.")
        avail = float(bal["available_days"])
        if avail < working_days:
            raise ToolError(f"Insufficient {leave_type} leave balance: requested {working_days} days, but only {avail} days available.")
            
        res_pol_row = db.one("SELECT value FROM environment_policy WHERE name = ?", ("balance_reservation_policy",))
        res_pol = json.loads(res_pol_row["value"]) if res_pol_row else "reserve_on_submit"
        if res_pol == "reserve_on_submit":
            new_reserved = float(bal["reserved_days"]) + working_days
            new_available = float(bal["available_days"]) - working_days
            db.execute(
                "UPDATE leave_balances SET reserved_days = ?, available_days = ? WHERE employee_id = ? AND leave_type = ?",
                (new_reserved, new_available, employee_id, leave_type)
            )
            reserved_days = working_days

    count_row = db.one("SELECT COUNT(*) as cnt FROM leave_requests")
    count = count_row["cnt"] if count_row else 0
    request_id = f"REQ-{2001 + count}"
    
    clock_row = db.one("SELECT now FROM clock")
    now_str = clock_row["now"] if clock_row else "2026-10-26T09:00:00Z"
    
    approver_id = emp["manager_id"]
    status = "pending"
    
    db.execute(
        "INSERT INTO leave_requests (request_id, employee_id, leave_type, start_date, end_date, working_days, status, approver_id, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (request_id, employee_id, leave_type, start_str, end_str, working_days, status, approver_id, reason, now_str)
    )
    
    return {
        "request_id": request_id,
        "employee_id": employee_id,
        "leave_type": leave_type,
        "start_date": start_str,
        "end_date": end_str,
        "working_days": float(working_days),
        "status": status,
        "approver_id": approver_id,
        "reason": reason,
        "reserved_days": float(reserved_days)
    }
