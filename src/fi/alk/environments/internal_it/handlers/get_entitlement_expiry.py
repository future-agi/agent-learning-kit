def handle(args, db):
    employee_id = args.get("employee_id")
    if not employee_id:
        raise ToolError("employee_id is required.")
    if not db.one("SELECT 1 FROM employees WHERE employee_id = ?", [employee_id]):
        raise ToolError(f"Employee {employee_id!r} not found.")
    now = db.one("SELECT now FROM clock")["now"]
    rows = db.query("SELECT * FROM entitlements WHERE employee_id = ? AND status = 'active' ORDER BY expires_at", [employee_id])
    from datetime import datetime
    def days_left(exp):
        if not exp:
            return None
        return (datetime.fromisoformat(exp.replace("Z", "+00:00")) - datetime.fromisoformat(now.replace("Z", "+00:00"))).days
    return {"employee_id": employee_id, "entitlements": [{"entitlement_id": r["entitlement_id"], "app_id": r["app_id"], "role_name": r["role_name"], "expires_at": r["expires_at"], "days_left": days_left(r["expires_at"])} for r in rows]}
