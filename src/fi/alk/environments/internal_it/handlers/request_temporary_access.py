import json

def handle(args, db):
    emp_id = args.get("employee_id")
    app_id = args.get("app_id")
    role_name = args.get("role_name")
    days = args.get("duration_days")
    justification = args.get("justification")
    if not emp_id or not app_id or not role_name or not justification:
        raise ToolError("employee_id, app_id, role_name and justification are required.")
    if not isinstance(days, int) or isinstance(days, bool) or days < 1:
        raise ToolError("duration_days must be a positive integer.")
    row = db.one("SELECT value FROM environment_policy WHERE name = 'max_temporary_days'")
    limit = int(json.loads(row["value"])) if row else 30
    if days > limit:
        raise ToolError(f"Policy: temporary access lasts at most {limit} days.")
    emp = db.one("SELECT * FROM employees WHERE employee_id = ?", [emp_id])
    if not emp:
        raise ToolError(f"Employee {emp_id!r} not found.")
    if emp["status"] != "active":
        raise ToolError(f"Employee {emp_id!r} is not active.")
    app = db.one("SELECT * FROM applications WHERE app_id = ?", [app_id])
    role = db.one("SELECT * FROM roles WHERE app_id = ? AND role_name = ?", [app_id, role_name])
    if not app or not role:
        raise ToolError(f"Role {role_name!r} not found for application {app_id!r}.")
    if db.one("SELECT 1 FROM entitlements WHERE employee_id = ? AND app_id = ? AND role_name = ? AND status = 'active'", [emp_id, app_id, role_name]):
        raise ToolError(f"Employee {emp_id} already has active role '{role_name}' on {app_id}.")
    if db.one("SELECT 1 FROM access_requests WHERE employee_id = ? AND app_id = ? AND role_name = ? AND status = 'pending'", [emp_id, app_id, role_name]):
        raise ToolError("A pending request for this role already exists.")
    active = [r["role_name"] for r in db.query("SELECT role_name FROM entitlements WHERE employee_id = ? AND app_id = ? AND status = 'active'", [emp_id, app_id])]
    for rule in db.query("SELECT * FROM sod_rules WHERE app_id = ?", [app_id]):
        if (rule["role_a"] == role_name and rule["role_b"] in active) or (rule["role_b"] == role_name and rule["role_a"] in active):
            raise ToolError(f"Cannot create request: violates Separation of Duties ({rule['description']}).")
    approver = app["owner_id"] if role["requires_approval"] == "app_owner" else emp["manager_id"]
    now = db.one("SELECT now FROM clock")["now"]
    n = db.one("SELECT COUNT(*) AS c FROM access_requests")["c"] + 1
    req_id = f"AR{n:02d}T"
    db.execute("INSERT INTO access_requests (request_id, employee_id, app_id, role_name, justification, status, approver_id, created_at, updated_at, duration_days) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)", [req_id, emp_id, app_id, role_name, justification, approver, now, now, days])
    return {"request_id": req_id, "employee_id": emp_id, "app_id": app_id, "role_name": role_name, "duration_days": days, "status": "pending", "approver_id": approver}
