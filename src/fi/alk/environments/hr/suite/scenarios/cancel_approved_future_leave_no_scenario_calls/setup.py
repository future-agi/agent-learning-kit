def setup(world):
    world.put("employees", {
        "employee_id": "E451",
        "name": "Li Wei",
        "email": "li.wei@example.com",
        "region": "APAC",
        "manager_id": "M04",
        "role": "frontend_developer",
    })
    world.put("leave_balances", {
        "balance_id": "BAL-E451-ANN",
        "employee_id": "E451",
        "leave_type": "annual",
        "allocated_days": 18,
        "used_days": 10,
        "reserved_days": 0,
        "available_days": 8,
    })
    world.put("leave_requests", {
        "request_id": "REQ-4051",
        "employee_id": "E451",
        "leave_type": "annual",
        "start_date": "2026-11-16",
        "end_date": "2026-11-17",
        "working_days": 2,
        "status": "approved",
        "reason": "Personal travel",
        "approver_id": "M04",
        "created_at": "2026-10-10T08:00:00Z",
        "decided_at": "2026-10-11T09:00:00Z",
        "decided_by": "M04",
        "updated_at": None,
    })
