def handle(args, db):
    req_id = args.get("request_id")
    if not req_id:
        raise ToolError("Missing required parameter: request_id")
    req = db.one("SELECT * FROM access_requests WHERE request_id = ?", [req_id])
    if req is None:
        raise ToolError(f"Access request {req_id!r} not found.")
    approval = db.one("SELECT * FROM approvals WHERE request_id = ?", [req_id])
    approval_info = None
    if approval:
        approval_info = {
            "approval_id": approval["approval_id"],
            "approver_id": approval["approver_id"],
            "created_at": approval["created_at"],
            "expires_at": approval["expires_at"],
            "status": approval["status"],
            "token": approval["token"]
        }
    return {
        "approval": approval_info,
        "app_id": req["app_id"],
        "approver_id": req["approver_id"],
        "employee_id": req["employee_id"],
        "request_id": req["request_id"],
        "request_status": req["status"],
        "role_name": req["role_name"]
    }