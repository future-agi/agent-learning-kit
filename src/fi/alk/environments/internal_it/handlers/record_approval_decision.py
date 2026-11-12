import json
from datetime import datetime, timedelta

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
    approver = db.one("SELECT status FROM employees WHERE employee_id = ?", [approver_id])
    if not approver or approver["status"] != "active":
        raise ToolError(f"Approver {approver_id} is not an active employee.")
    now = db.one("SELECT now FROM clock")["now"]
    if decision == "reject":
        db.execute("UPDATE access_requests SET status = 'rejected', updated_at = ? WHERE request_id = ?", [now, request_id])
        return {"request_id": request_id, "decision": "reject", "request_status": "rejected", "token": None}
    row = db.one("SELECT value FROM environment_policy WHERE name = 'approval_token_days'")
    days = int(json.loads(row["value"])) if row else 7
    expires = (datetime.fromisoformat(now.replace("Z", "+00:00")) + timedelta(days=days)).isoformat().replace("+00:00", "Z")
    n = db.one("SELECT COUNT(*) AS c FROM approvals")["c"] + 1
    token = f"TOK-{request_id}-{n:03d}"
    db.execute("INSERT INTO approvals (approval_id, token, request_id, approver_id, employee_id, app_id, role_name, status, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'valid', ?, ?)", [f"APR-{n:03d}", token, request_id, approver_id, req["employee_id"], req["app_id"], req["role_name"], expires, now])
    db.execute("UPDATE access_requests SET status = 'approved', updated_at = ? WHERE request_id = ?", [now, request_id])
    return {"request_id": request_id, "decision": "approve", "request_status": "approved", "token": token, "expires_at": expires}
