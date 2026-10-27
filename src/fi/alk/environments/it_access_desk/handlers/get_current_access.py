def handle(args, db):
    emp_id = args.get("employee_id")
    if not emp_id:
        raise ToolError("Missing required parameter: employee_id")
    emp = db.one("SELECT * FROM employees WHERE employee_id = ?", [emp_id])
    if emp is None:
        raise ToolError(f"Employee {emp_id!r} not found.")
    app_id = args.get("app_id")
    query = "SELECT entitlement_id, app_id, role_name, status, granted_at, expires_at, granted_by FROM entitlements WHERE employee_id = ? AND status = 'active'"
    params = [emp_id]
    if app_id:
        query += " AND app_id = ?"
        params.append(app_id)
    ents = db.query(query, params)
    req_query = "SELECT request_id, app_id, role_name, status, approver_id, created_at FROM access_requests WHERE employee_id = ? AND status IN ('pending', 'approved')"
    req_params = [emp_id]
    if app_id:
        req_query += " AND app_id = ?"
        req_params.append(app_id)
    reqs = db.query(req_query, req_params)
    return {
        "employee_id": emp_id,
        "active_entitlements": ents,
        "pending_requests": reqs
    }