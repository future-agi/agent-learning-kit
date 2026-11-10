import json
from datetime import datetime, timedelta

def handle(args, db):
    req_id = args.get("request_id")
    token = args.get("approval_token")
    if not req_id or not token:
        raise ToolError("Missing required parameters: request_id, approval_token")
        
    req = db.one("SELECT * FROM access_requests WHERE request_id = ?", [req_id])
    if req is None:
        raise ToolError(f"Access request {req_id!r} not found.")
        
    approval = db.one("SELECT * FROM approvals WHERE token = ?", [token])
    if approval is None:
        raise ToolError(f"Approval token {token!r} is invalid or does not exist.")
        
    if (approval["request_id"] != req_id or 
        approval["employee_id"] != req["employee_id"] or 
        approval["app_id"] != req["app_id"] or 
        approval["role_name"] != req["role_name"]):
        raise ToolError(f"Approval token scope mismatch: token {token!r} was issued for request {approval['request_id']} ({approval['employee_id']}/{approval['app_id']}/{approval['role_name']}), not for {req_id} ({req['employee_id']}/{req['app_id']}/{req['role_name']}).")
        
    if approval["status"] != "valid":
        raise ToolError(f"Approval token {token!r} is not valid (current status: {approval['status']}).")
        
    now_row = db.one("SELECT now FROM clock")
    now = now_row["now"] if now_row else "2026-10-26T09:00:00Z"
    
    if approval["expires_at"] < now:
        raise ToolError(f"Approval token {token!r} expired at {approval['expires_at']}.")
        
    existing = db.one("SELECT * FROM entitlements WHERE employee_id = ? AND app_id = ? AND role_name = ? AND status = 'active'", [req["employee_id"], req["app_id"], req["role_name"]])
    if existing:
        raise ToolError(f"Active entitlement already exists for employee {req['employee_id']} on {req['app_id']} ({req['role_name']}).")
        
    expiry_policy = db.one("SELECT value FROM environment_policy WHERE name = 'default_expiry_days'")
    expiry_days = json.loads(expiry_policy["value"]) if expiry_policy else 90
    
    try:
        now_dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
        exp_dt = now_dt + timedelta(days=expiry_days)
        expires_at = exp_dt.isoformat().replace("+00:00", "Z")
    except Exception:
        expires_at = "2027-01-24T09:00:00Z"
        
    ent_count = db.one("SELECT COUNT(*) as c FROM entitlements")["c"] + 1
    ent_id = f"ENT-{ent_count:02d}"
    
    db.execute("INSERT INTO entitlements (entitlement_id, employee_id, app_id, role_name, status, granted_at, expires_at, granted_by) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)", [ent_id, req["employee_id"], req["app_id"], req["role_name"], now, expires_at, approval["approver_id"]])
    
    db.execute("UPDATE access_requests SET status = 'completed', updated_at = ? WHERE request_id = ?", [now, req_id])
    db.execute("UPDATE approvals SET status = 'used' WHERE approval_id = ?", [approval["approval_id"]])
    
    log_count = db.one("SELECT COUNT(*) as c FROM audit_log")["c"] + 1
    db.execute("INSERT INTO audit_log (log_id, action, actor_id, target_type, target_id, details, timestamp) VALUES (?, 'grant_synthetic_access', ?, 'entitlement', ?, ?, ?)", [f"LOG-{log_count:03d}", approval["approver_id"], ent_id, f"Granted {req['role_name']} on {req['app_id']} to {req['employee_id']} via token {token}", now])
    
    return {
        "app_id": req["app_id"],
        "employee_id": req["employee_id"],
        "entitlement_id": ent_id,
        "expires_at": expires_at,
        "granted_at": now,
        "granted_by": approval["approver_id"],
        "message": "Synthetic access granted successfully.",
        "role_name": req["role_name"],
        "status": "active"
    }