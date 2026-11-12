def setup(world):
    world.put("employees", {
        "employee_id": "E404",
        "name": "Audrey Bennett",
        "email": "audrey.bennett@example.com",
        "region": "UK",
        "manager_id": "M02",
        "role": "marketing_manager"
    })
    world.put("leave_balances", {
        "balance_id": "BAL-E404-ANN",
        "employee_id": "E404",
        "leave_type": "annual",
        "allocated_days": 25,
        "used_days": 15,
        "reserved_days": 0,
        "available_days": 10
    })
    world.put("leave_requests", {
        "request_id": "REQ-4102",
        "employee_id": "E404",
        "leave_type": "annual",
        "start_date": "2026-11-02",
        "end_date": "2026-11-06",
        "working_days": 5,
        "status": "approved",
        "approver_id": "M02",
        "reason": "Holiday",
        "created_at": "2026-10-12T09:00:00Z",
        "updated_at": None,
        "decided_by": "M02",
        "decided_at": "2026-10-13T11:00:00Z"
    })
