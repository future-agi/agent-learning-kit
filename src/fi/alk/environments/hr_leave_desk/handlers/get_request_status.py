def handle(args, db):
    request_id = args.get("request_id")
    if not request_id:
        raise ToolError("request_id is required.")
        
    req = db.one("SELECT * FROM leave_requests WHERE request_id = ?", (request_id,))
    if not req:
        raise ToolError(f"Leave request {request_id} not found.")
        
    actor_id = args.get("actor_id")
    if actor_id and actor_id != req["employee_id"]:
        actor = db.one("SELECT * FROM employees WHERE employee_id = ?", (actor_id,))
        if not actor or (actor["role"] not in ("hr_business_partner", "hr_specialist", "hr_generalist", "director", "executive") and actor["employee_id"] != req["approver_id"]):
            raise ToolError(f"Permission denied: actor {actor_id} is not authorized to view request {request_id}.")
            
    return {
        "request_id": req["request_id"],
        "employee_id": req["employee_id"],
        "leave_type": req["leave_type"],
        "start_date": req["start_date"],
        "end_date": req["end_date"],
        "working_days": float(req["working_days"]),
        "status": req["status"],
        "approver_id": req["approver_id"],
        "reason": req["reason"],
        "created_at": req["created_at"]
    }
