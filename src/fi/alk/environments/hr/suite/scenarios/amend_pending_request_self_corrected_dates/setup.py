def setup(world):
    world.put("employees", {
        "employee_id": "E414",
        "name": "Lachlan Bailey",
        "email": "lachlan.bailey@example.com",
        "region": "APAC",
        "manager_id": "M04",
        "role": "software_engineer"
    })
    world.put("leave_balances", {
        "balance_id": "BAL-E414-ANN",
        "employee_id": "E414",
        "leave_type": "annual",
        "allocated_days": 20,
        "used_days": 5,
        "reserved_days": 3,
        "available_days": 12
    })
    world.put("leave_requests", {
        "request_id": "REQ-4014",
        "employee_id": "E414",
        "leave_type": "annual",
        "start_date": "2026-12-07",
        "end_date": "2026-12-09",
        "working_days": 3,
        "status": "pending",
        "approver_id": "M04",
        "reason": "Personal trip",
        "created_at": "2026-10-20T10:00:00Z",
        "updated_at": None,
        "decided_by": None,
        "decided_at": None
    })
