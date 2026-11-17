def setup(world):
    world.put("leave_requests", {
        "request_id": "REQ-1003",
        "employee_id": "M03",
        "leave_type": "annual",
        "start_date": "2026-12-01",
        "end_date": "2026-12-03",
        "working_days": 3,
        "status": "pending",
        "approver_id": "EXEC01",
        "reason": "Executive leadership seminar",
        "created_at": "2026-10-20T10:00:00Z"
    })
    world.change("leave_balances", "BAL-M03-ANN", {"reserved_days": 3, "available_days": 12}, by="balance_id")
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "M03"}, by="id")
