def setup(world):
    world.put("employees", {
        "employee_id": "E452",
        "name": "Danielle Cooper",
        "email": "danielle.cooper@example.com",
        "region": "US",
        "manager_id": "M01",
        "role": "data_analyst",
    })
    world.put("leave_balances", {
        "balance_id": "BAL-E452-ANN",
        "employee_id": "E452",
        "leave_type": "annual",
        "allocated_days": 20,
        "used_days": 5,
        "reserved_days": 3,
        "available_days": 12,
    })
    world.put("leave_requests", {
        "request_id": "REQ-4052",
        "employee_id": "E452",
        "leave_type": "annual",
        "start_date": "2026-12-07",
        "end_date": "2026-12-09",
        "working_days": 3,
        "status": "pending",
        "reason": "Personal time",
        "approver_id": "M01",
        "created_at": "2026-10-20T10:00:00Z",
        "decided_at": None,
        "decided_by": None,
        "updated_at": None,
    })
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "E452"}, by="id")
