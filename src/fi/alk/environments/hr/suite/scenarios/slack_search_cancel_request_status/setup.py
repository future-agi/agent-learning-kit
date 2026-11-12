def setup(world):
    world.put("leave_requests", {
        "request_id": "REQ-4001",
        "employee_id": "E11",
        "leave_type": "annual",
        "start_date": "2026-11-02",
        "end_date": "2026-11-02",
        "working_days": 1,
        "status": "pending",
        "approver_id": "M04",
        "reason": "Trip",
        "created_at": "2026-10-20T10:00:00Z"
    })
    world.change("leave_balances", "BAL-E11-ANN", {"reserved_days": 1, "available_days": 0}, by="balance_id")
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "HR02"}, by="id")
