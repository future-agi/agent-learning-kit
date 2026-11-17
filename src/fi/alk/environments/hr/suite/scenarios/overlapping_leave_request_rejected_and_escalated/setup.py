def setup(world):
    # Hannah Abbott (E08) already has REQ-1105 approved for 23-27 November, so the overlapping
    # booking is another employee's: REQ-0801, 20-25 November, its 4 days counted as used.
    world.put("employees", {
        "employee_id": "E462",
        "name": "Megan Price",
        "email": "megan.price@example.com",
        "region": "UK",
        "manager_id": "M02",
        "role": "marketing_specialist",
    })
    world.put("leave_balances", {"balance_id": "BAL-E462-ANN", "employee_id": "E462", "leave_type": "annual", "allocated_days": 25, "used_days": 9, "reserved_days": 0, "available_days": 16})
    world.put("leave_balances", {"balance_id": "BAL-E462-SCK", "employee_id": "E462", "leave_type": "sick", "allocated_days": 10, "used_days": 0, "reserved_days": 0, "available_days": 10})
    world.put("leave_requests", {
        "request_id": "REQ-0801",
        "employee_id": "E462",
        "leave_type": "annual",
        "start_date": "2026-11-20",
        "end_date": "2026-11-25",
        "working_days": 4,
        "status": "approved",
        "approver_id": "M02",
        "reason": "Existing holiday",
        "created_at": "2026-10-01T10:00:00Z",
        "decided_by": "M02",
        "decided_at": "2026-10-02T09:00:00Z",
    })
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "E462"}, by="id")
