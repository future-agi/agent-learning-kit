import json

def handle(args, db):
    request_id = args.get("request_id")
    approver_id = args.get("approver_id")
    decision = args.get("decision")
    if not request_id or not approver_id or decision not in ("approve", "reject"):
        raise ToolError("request_id, approver_id and decision (approve or reject) are required.")
    req = db.one("SELECT * FROM access_requests WHERE request_id = ?", [request_id])
    if not req:
        raise ToolError(f"Access request {request_id!r} not found.")
    if req["status"] != "pending":
        raise ToolError(f"Request {request_id} is {req['status']}; only a pending request can be decided.")
    if req["approver_id"] != approver_id:
        raise ToolError(f"Permission denied: {approver_id} is not the approver of {request_id} ({req['approver_id']} is).")
    # Who is signed in to this conversation is set by the session, not by anything the desk is told:
    # naming the approver is not being the approver.
    caller = db.one("SELECT employee_id FROM session_caller WHERE id = 'default'")
    if not caller or caller["employee_id"] != approver_id:
        raise ToolError(f"Permission denied: only {approver_id}, signed in to this conversation, can decide {request_id}; the person signed in is {(caller or {}).get('employee_id') or 'nobody'}.")
    if approver_id == req["employee_id"]:
        raise ToolError(f"Permission denied: {approver_id} cannot decide their own request {request_id}.")
    approver = db.one("SELECT status FROM employees WHERE employee_id = ?", [approver_id])
    if not approver or approver["status"] != "active":
        raise ToolError(f"Approver {approver_id} is not an active employee.")
    now = db.one("SELECT now FROM clock")["now"]
    if decision == "reject":
        db.execute("UPDATE access_requests SET status = 'rejected', updated_at = ? WHERE request_id = ?", [now, request_id])
        _audit(db, "record_approval_decision", approver_id, "access_request", request_id, "rejected", now)
        return {"request_id": request_id, "decision": "reject", "request_status": "rejected", "token": None}
    emp = db.one("SELECT * FROM employees WHERE employee_id = ?", [req["employee_id"]])
    app = db.one("SELECT * FROM applications WHERE app_id = ?", [req["app_id"]])
    role = db.one("SELECT * FROM roles WHERE app_id = ? AND role_name = ?", [req["app_id"], req["role_name"]])
    chain = _approvers(emp, app, role) if emp and app and role else []
    if approver_id in chain and chain.index(approver_id) < len(chain) - 1:
        # A role that needs both approvals: the first one is recorded and the request moves on to
        # the second approver; no token exists until the last approver decides.
        nxt = chain[chain.index(approver_id) + 1]
        db.execute("UPDATE access_requests SET approved_by = ?, approver_id = ?, updated_at = ? WHERE request_id = ?", [approver_id, nxt, now, request_id])
        _audit(db, "record_approval_decision", approver_id, "access_request", request_id, f"approved; awaiting {nxt}", now)
        return {"request_id": request_id, "decision": "approve", "request_status": "pending", "token": None, "awaiting_approver_id": nxt}
    token, expires = _issue_token(db, req, approver_id, now)
    db.execute("UPDATE access_requests SET status = 'approved', updated_at = ? WHERE request_id = ?", [now, request_id])
    _audit(db, "record_approval_decision", approver_id, "access_request", request_id, f"approved; token {token}", now)
    return {"request_id": request_id, "decision": "approve", "request_status": "approved", "token": token, "expires_at": expires}


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
