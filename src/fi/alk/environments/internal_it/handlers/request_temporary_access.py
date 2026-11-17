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
    if emp is None:
        raise ToolError(f"Employee {emp_id!r} not found.")
    actor = _act_for(db, emp_id)
    if emp["status"] != "active":
        raise ToolError(f"Employee {emp_id!r} is not active.")
    app = db.one("SELECT * FROM applications WHERE app_id = ?", [app_id])
    if app is None:
        raise ToolError(f"Application {app_id!r} not found.")
    role = db.one("SELECT * FROM roles WHERE app_id = ? AND role_name = ?", [app_id, role_name])
    if role is None:
        raise ToolError(f"Role {role_name!r} not found for application {app_id!r}.")
    if db.one("SELECT 1 FROM entitlements WHERE employee_id = ? AND app_id = ? AND role_name = ? AND status = 'active'", [emp_id, app_id, role_name]):
        raise ToolError(f"Employee {emp_id} already has active role '{role_name}' on {app_id}.")
    dedup_policy = db.one("SELECT value FROM environment_policy WHERE name = 'prevent_duplicate_pending_requests'")
    if json.loads(dedup_policy["value"]) if dedup_policy else True:
        pending = db.one("SELECT * FROM access_requests WHERE employee_id = ? AND app_id = ? AND role_name = ? AND status = 'pending'", [emp_id, app_id, role_name])
        if pending:
            raise ToolError(f"A pending request ({pending['request_id']}) already exists for employee {emp_id}, app {app_id}, and role {role_name}.")
    sod_policy = db.one("SELECT value FROM environment_policy WHERE name = 'sod_enabled'")
    if json.loads(sod_policy["value"]) if sod_policy else True:
        active = [r["role_name"] for r in db.query("SELECT role_name FROM entitlements WHERE employee_id = ? AND app_id = ? AND status = 'active'", [emp_id, app_id])]
        for rule in db.query("SELECT * FROM sod_rules WHERE app_id = ?", [app_id]):
            if (rule["role_a"] == role_name and rule["role_b"] in active) or (rule["role_b"] == role_name and rule["role_a"] in active):
                raise ToolError(f"Cannot create request: violates Separation of Duties ({rule['description']}).")
    chain = _approvers(emp, app, role)
    now = db.one("SELECT now FROM clock")["now"]

    n = db.one("SELECT COUNT(*) AS c FROM access_requests")["c"] + 1
    while db.one("SELECT 1 FROM access_requests WHERE request_id = ?", [f"AR{n:02d}T"]):
        n += 1
    req_id = f"AR{n:02d}T"
    approver = chain[0] if chain else None
    db.execute("INSERT INTO access_requests (request_id, employee_id, app_id, role_name, justification, status, approver_id, created_at, updated_at, duration_days) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)", [req_id, emp_id, app_id, role_name, justification, approver, now, now, days])
    _audit(db, "request_temporary_access", actor, "access_request", req_id, f"Requested {role_name} on {app_id} for {emp_id} (approvers: {', '.join(chain) or 'none'})", now)
    out = {"request_id": req_id, "employee_id": emp_id, "app_id": app_id, "role_name": role_name, "duration_days": days, "status": "pending", "approver_id": approver, "approvers": chain, "created_at": now}
    if not chain:
        # The catalogue asks no approval for this role: the request is approved on creation and
        # its token issued at once, so the grant needs nothing further.
        req = db.one("SELECT * FROM access_requests WHERE request_id = ?", [req_id])
        token, expires = _issue_token(db, req, "no_approval_required", now)
        db.execute("UPDATE access_requests SET status = 'approved' WHERE request_id = ?", [req_id])
        out.update(status="approved", approval_token=token, token_expires_at=expires, message="No approval is required for this role; the request is approved and its token issued.")
    else:
        out["message"] = f"Access request created and routed for approval to {' then '.join(chain)}."
    return out


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


def _approvers(emp, app, role):
    """Who must approve, in order: the catalogue's manager, application owner, or both (manager
    first, then owner). An employee without a manager goes to the application owner, and nobody
    approves their own request: a step that would be theirs is left to the other approver."""
    if role["requires_approval"] == "none":
        return []
    manager, owner = emp["manager_id"], app["owner_id"]
    chain = {"app_owner": [owner], "both": [manager or owner, owner]}.get(role["requires_approval"], [manager or owner])
    chain = [p for p in chain if p and p != emp["employee_id"]]
    if not chain and manager and manager != emp["employee_id"]:
        chain = [manager]
    return [p for i, p in enumerate(chain) if p not in chain[:i]]


def _issue_token(db, req, approver_id, now):
    """An approval token bound to one request, employee, application and role, valid for the
    policy's number of days."""
    from datetime import datetime, timedelta
    row = db.one("SELECT value FROM environment_policy WHERE name = 'approval_token_days'")
    days = int(json.loads(row["value"])) if row else 7
    expires = (datetime.fromisoformat(now.replace("Z", "+00:00")) + timedelta(days=days)).isoformat().replace("+00:00", "Z")
    n = db.one("SELECT COUNT(*) AS c FROM approvals")["c"] + 1
    while db.one("SELECT 1 FROM approvals WHERE approval_id = ?", [f"APR-{n:03d}"]):
        n += 1
    token = f"TOK-{req['request_id']}-{n:03d}"
    db.execute("INSERT INTO approvals (approval_id, token, request_id, approver_id, employee_id, app_id, role_name, status, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'valid', ?, ?)", [f"APR-{n:03d}", token, req["request_id"], approver_id, req["employee_id"], req["app_id"], req["role_name"], expires, now])
    return token, expires


def _audit(db, action, actor, target_type, target_id, details, now):
    n = db.one("SELECT COUNT(*) AS c FROM audit_log")["c"] + 1
    while db.one("SELECT 1 FROM audit_log WHERE log_id = ?", [f"LOG-{n:03d}"]):
        n += 1
    db.execute("INSERT INTO audit_log (log_id, action, actor_id, target_type, target_id, details, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)", [f"LOG-{n:03d}", action, actor, target_type, target_id, details, now])
