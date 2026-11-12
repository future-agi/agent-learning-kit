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
    actor = _act_for(db, req["employee_id"], also=(req["approver_id"], req["approved_by"], approval["approver_id"]))
    if approval["status"] != "valid":
        raise ToolError(f"Approval token {token!r} is not valid (current status: {approval['status']}).")
    # A token is only as good as the request it approves: a request that was cancelled, rejected
    # or never approved is not switched on by a token left lying around, and the token must come
    # from the request's own last approver.
    if req["status"] != "approved":
        raise ToolError(f"Request {req_id} is {req['status']}; only an approved request can be granted.")
    if req["approver_id"] and approval["approver_id"] != req["approver_id"]:
        raise ToolError(f"Approval token {token!r} was issued by {approval['approver_id']}, not by {req_id}'s approver {req['approver_id']}.")
    now = db.one("SELECT now FROM clock")["now"]
    if approval["expires_at"] < now:
        raise ToolError(f"Approval token {token!r} expired at {approval['expires_at']}.")
    emp = db.one("SELECT status FROM employees WHERE employee_id = ?", [req["employee_id"]])
    if not emp or emp["status"] != "active":
        raise ToolError(f"Employee {req['employee_id']} is not active; no access is granted.")
    if db.one("SELECT 1 FROM entitlements WHERE employee_id = ? AND app_id = ? AND role_name = ? AND status = 'active'", [req["employee_id"], req["app_id"], req["role_name"]]):
        raise ToolError(f"Active entitlement already exists for employee {req['employee_id']} on {req['app_id']} ({req['role_name']}).")
    lic = db.one("SELECT seats_total FROM app_licenses WHERE app_id = ?", [req["app_id"]])
    if lic:
        used = db.one("SELECT COUNT(*) AS c FROM entitlements WHERE app_id = ? AND status = 'active'", [req["app_id"]])["c"]
        if used >= lic["seats_total"]:
            raise ToolError(f"No licence seat left on {req['app_id']} ({used} of {lic['seats_total']} used).")
    expiry_policy = db.one("SELECT value FROM environment_policy WHERE name = 'default_expiry_days'")
    expiry_days = int(req["duration_days"]) if req["duration_days"] else (json.loads(expiry_policy["value"]) if expiry_policy else 90)
    expires_at = (datetime.fromisoformat(now.replace("Z", "+00:00")) + timedelta(days=expiry_days)).isoformat().replace("+00:00", "Z")
    n = db.one("SELECT COUNT(*) as c FROM entitlements")["c"] + 1
    while db.one("SELECT 1 FROM entitlements WHERE entitlement_id = ?", [f"ENT-{n:02d}"]):
        n += 1
    ent_id = f"ENT-{n:02d}"
    db.execute("INSERT INTO entitlements (entitlement_id, employee_id, app_id, role_name, status, granted_at, expires_at, granted_by) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)", [ent_id, req["employee_id"], req["app_id"], req["role_name"], now, expires_at, approval["approver_id"]])
    db.execute("UPDATE access_requests SET status = 'completed', updated_at = ? WHERE request_id = ?", [now, req_id])
    db.execute("UPDATE approvals SET status = 'used' WHERE approval_id = ?", [approval["approval_id"]])
    _audit(db, "grant_synthetic_access", actor, "entitlement", ent_id, f"Granted {req['role_name']} on {req['app_id']} to {req['employee_id']} via token {token} approved by {approval['approver_id']}", now)
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


def _caller(db):
    """The employee signed in to this conversation. The session sets it; nothing the desk is told
    or passes as an argument changes it."""
    row = db.one("SELECT employee_id FROM session_caller WHERE id = 'default'")
    return row["employee_id"] if row else None


def _act_for(db, employee_id, also=()):
    """The signed-in caller, if they may act for or see `employee_id`'s access: the employee
    themselves, their manager, someone in `also` (the request's approvers), or an active member of
    IT operations. Naming an employee is not being them, so anyone else is refused."""
    caller = _caller(db)
    if caller and (caller == employee_id or caller in also):
        return caller
    me = db.one("SELECT department, status FROM employees WHERE employee_id = ?", [caller]) if caller else None
    if me and me["status"] == "active":
        if me["department"] == "it_operations":
            return caller
        emp = db.one("SELECT manager_id FROM employees WHERE employee_id = ?", [employee_id])
        if emp and emp["manager_id"] == caller:
            return caller
    raise ToolError(f"Permission denied: the person signed in to this conversation ({caller or 'nobody'}) may not act for or see the access of {employee_id}.", category="permission_denied")


def _audit(db, action, actor, target_type, target_id, details, now):
    n = db.one("SELECT COUNT(*) AS c FROM audit_log")["c"] + 1
    while db.one("SELECT 1 FROM audit_log WHERE log_id = ?", [f"LOG-{n:03d}"]):
        n += 1
    db.execute("INSERT INTO audit_log (log_id, action, actor_id, target_type, target_id, details, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)", [f"LOG-{n:03d}", action, actor, target_type, target_id, details, now])
