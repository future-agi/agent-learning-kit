def setup(world):
    world.put("employees", {
        "employee_id": "E456",
        "name": "Kenji Takahashi",
        "email": "kenji.takahashi@example.com",
        "region": "APAC",
        "manager_id": "M04",
        "role": "software_engineer",
    })
    world.put("leave_balances", {
        "balance_id": "BAL-E456-ANN",
        "employee_id": "E456",
        "leave_type": "annual",
        "allocated_days": 18,
        "used_days": 0,
        "reserved_days": 0,
        "available_days": 18,
    })
    world.put("leave_balances", {
        "balance_id": "BAL-E456-SCK",
        "employee_id": "E456",
        "leave_type": "sick",
        "allocated_days": 12,
        "used_days": 0,
        "reserved_days": 0,
        "available_days": 12,
    })
    # The person in this conversation, as the session (not the agent) knows them.
    world.change("session_caller", "default", {"employee_id": "E456"}, by="id")
