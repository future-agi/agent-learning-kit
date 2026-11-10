def setup(world):
    world.put("employees", {
        "employee_id": "M416",
        "name": "Alistair Finch",
        "email": "alistair.finch@example.com",
        "region": "UK",
        "manager_id": "M03",
        "role": "engineering_manager"
    })
    world.put("employees", {
        "employee_id": "E417",
        "name": "Chloe Davies",
        "email": "chloe.davies@example.com",
        "region": "UK",
        "manager_id": "M416",
        "role": "qa_engineer"
    })
    world.put("leave_requests", {
        "request_id": "REQ-4017",
        "employee_id": "E417",
        "leave_type": "annual",
        "start_date": "2026-11-02",
        "end_date": "2026-11-06",
        "working_days": 5,
        "status": "approved",
        "approver_id": "M416",
        "reason": "Family holiday",
        "created_at": "2026-10-15T10:00:00Z",
        "updated_at": None,
        "decided_by": "M416",
        "decided_at": "2026-10-16T11:00:00Z"
    })
