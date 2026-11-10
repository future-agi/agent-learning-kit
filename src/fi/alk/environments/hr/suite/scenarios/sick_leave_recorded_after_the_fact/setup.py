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
        "allocated_days": 20,
        "used_days": 0,
        "reserved_days": 0,
        "available_days": 20,
    })
    world.put("leave_balances", {
        "balance_id": "BAL-E456-SCK",
        "employee_id": "E456",
        "leave_type": "sick",
        "allocated_days": 10,
        "used_days": 0,
        "reserved_days": 0,
        "available_days": 10,
    })
