import json

def handle(args, db):
    emp_id = args.get("employee_id")
    app_id = args.get("app_id")
    role_name = args.get("role_name")
    justification = args.get("justification")
    if not emp_id or not app_id or not role_name or not justification:
        raise ToolError("Missing required parameters: employee_id, app_id, role_name, justification")
    
    emp = db.one("SELECT * FROM employees WHERE employee_id = ?", [emp_id])
    if emp is None:
        raise ToolError(f"Employee {emp_id!r} not found.")
    if emp["status"] != "active":
        raise ToolError(f"Employee {emp_id!r} is not active.")
        
    app = db.one("SELECT * FROM applications WHERE app_id = ?", [app_id])
    if app is None:
        raise ToolError(f"Application {app_id!r} not found.")
        
    role = db.one("SELECT * FROM roles WHERE app_id = ? AND role_name = ?", [app_id, role_name])
    if role is None:
        raise ToolError(f"Role {role_name!r} not found for application {app_id!r}.")
        
    existing_ent = db.one("SELECT * FROM entitlements WHERE employee_id = ? AND app_id = ? AND role_name = ? AND status = 'active'", [emp_id, app_id, role_name])
    if existing_ent:
        raise ToolError(f"Employee {emp_id} already has active role '{role_name}' on {app_id}.")
        
    dedup_policy = db.one("SELECT value FROM environment_policy WHERE name = 'prevent_duplicate_pending_requests'")
    dedup_enabled = json.loads(dedup_policy["value"]) if dedup_policy else True
    if dedup_enabled:
        pending = db.one("SELECT * FROM access_requests WHERE employee_id = ? AND app_id = ? AND role_name = ? AND status = 'pending'", [emp_id, app_id, role_name])
        if pending:
            raise ToolError(f"A pending request ({pending['request_id']}) already exists for employee {emp_id}, app {app_id}, and role {role_name}.")
            
    sod_policy = db.one("SELECT value FROM environment_policy WHERE name = 'sod_enabled'")
    sod_enabled = json.loads(sod_policy["value"]) if sod_policy else True
    if sod_enabled:
        active_roles = db.query("SELECT role_name FROM entitlements WHERE employee_id = ? AND app_id = ? AND status = 'active'", [emp_id, app_id])
        active_role_names = [r["role_name"] for r in active_roles]
        sod_rules = db.query("SELECT * FROM sod_rules WHERE app_id = ?", [app_id])
        for rule in sod_rules:
            if (rule["role_a"] == role_name and rule["role_b"] in active_role_names) or (rule["role_b"] == role_name and rule["role_a"] in active_role_names):
                raise ToolError(f"Cannot create request: violates Separation of Duties ({rule['description']}).")
                
    approver_id = emp["manager_id"]
    if role["requires_approval"] == "app_owner":
        approver_id = app["owner_id"]
    elif role["requires_approval"] == "none":
        approver_id = None
        
    now_row = db.one("SELECT now FROM clock")
    now = now_row["now"] if now_row else "2026-10-26T09:00:00Z"
    
    count_row = db.one("SELECT COUNT(*) as c FROM access_requests")
    req_num = count_row["c"] + 1
    req_id = f"AR{req_num:02d}"
    
    db.execute("INSERT INTO access_requests (request_id, employee_id, app_id, role_name, justification, status, approver_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)", [req_id, emp_id, app_id, role_name, justification, approver_id, now, now])
    
    log_count = db.one("SELECT COUNT(*) as c FROM audit_log")["c"] + 1
    db.execute("INSERT INTO audit_log (log_id, action, actor_id, target_type, target_id, details, timestamp) VALUES (?, 'create_access_request', ?, 'access_request', ?, ?, ?)", [f"LOG-{log_count:03d}", emp_id, req_id, f"Requested {role_name} on {app_id} (approver: {approver_id})", now])
    
    return {
        "approver_id": approver_id,
        "created_at": now,
        "employee_id": emp_id,
        "message": "Access request created and routed for approval.",
        "request_id": req_id,
        "role_name": role_name,
        "status": "pending"
    }