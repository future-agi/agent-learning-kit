def handle(args, db):
    employee_id = args.get("employee_id")
    status = args.get("status")
    if not employee_id:
        raise ToolError("employee_id is required.")
    if not db.one("SELECT 1 FROM employees WHERE employee_id = ?", [employee_id]):
        raise ToolError(f"Employee {employee_id!r} not found.")
    sql = "SELECT * FROM access_requests WHERE employee_id = ?"
    params = [employee_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    rows = db.query(sql + " ORDER BY created_at", params)
    return {"employee_id": employee_id, "requests": [{"request_id": r["request_id"], "app_id": r["app_id"], "role_name": r["role_name"], "status": r["status"], "approver_id": r["approver_id"], "duration_days": r["duration_days"], "created_at": r["created_at"]} for r in rows], "count": len(rows)}
