import json

def handle(args, db):
    emp_id = args.get("employee_id")
    app_id = args.get("app_id")
    role_name = args.get("role_name")
    if not emp_id or not app_id or not role_name:
        raise ToolError("Missing required parameters: employee_id, app_id, role_name")
    
    emp = db.one("SELECT * FROM employees WHERE employee_id = ?", [emp_id])
    if emp is None:
        raise ToolError(f"Employee {emp_id!r} not found.")
    
    app = db.one("SELECT * FROM applications WHERE app_id = ?", [app_id])
    if app is None:
        raise ToolError(f"Application {app_id!r} not found.")
    
    role = db.one("SELECT * FROM roles WHERE app_id = ? AND role_name = ?", [app_id, role_name])
    if role is None:
        raise ToolError(f"Role {role_name!r} not found for application {app_id!r}.")
    
    existing_ent = db.one("SELECT * FROM entitlements WHERE employee_id = ? AND app_id = ? AND role_name = ? AND status = 'active'", [emp_id, app_id, role_name])
    if existing_ent:
        return {
            "allowed": False,
            "status": "already_assigned",
            "sod_conflict": False,
            "approval_required": role["requires_approval"],
            "approver_ids": [],
            "reason": f"Employee {emp_id} already has active role '{role_name}' on {app_id}."
        }
    
    sod_policy = db.one("SELECT value FROM environment_policy WHERE name = 'sod_enabled'")
    sod_enabled = json.loads(sod_policy["value"]) if sod_policy else True
    
    sod_conflict = False
    sod_reason = ""
    if sod_enabled:
        active_roles = db.query("SELECT role_name FROM entitlements WHERE employee_id = ? AND app_id = ? AND status = 'active'", [emp_id, app_id])
        active_role_names = [r["role_name"] for r in active_roles]
        
        sod_rules = db.query("SELECT * FROM sod_rules WHERE app_id = ?", [app_id])
        for rule in sod_rules:
            if (rule["role_a"] == role_name and rule["role_b"] in active_role_names) or (rule["role_b"] == role_name and rule["role_a"] in active_role_names):
                sod_conflict = True
                sod_reason = f"Separation of Duties violation: cannot hold both '{rule['role_a']}' and '{rule['role_b']}' ({rule['description']})."
                break
                
    if sod_conflict:
        return {
            "allowed": False,
            "status": "sod_violation",
            "sod_conflict": True,
            "approval_required": role["requires_approval"],
            "approver_ids": [],
            "reason": sod_reason
        }
    
    approvers = []
    if role["requires_approval"] == "manager":
        if emp["manager_id"]:
            approvers.append(emp["manager_id"])
    elif role["requires_approval"] == "app_owner":
        approvers.append(app["owner_id"])
    elif role["requires_approval"] == "both":
        if emp["manager_id"]:
            approvers.append(emp["manager_id"])
        if app["owner_id"] not in approvers:
            approvers.append(app["owner_id"])
            
    return {
        "allowed": True,
        "status": "permitted",
        "sod_conflict": False,
        "approval_required": role["requires_approval"],
        "approver_ids": approvers,
        "reason": "Policy check passed. Required approvals identified."
    }