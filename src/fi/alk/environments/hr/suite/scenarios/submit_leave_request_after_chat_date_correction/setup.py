def setup(world):
    world.put("employees", {
        "employee_id": "E412",
        "name": "Devin Ross",
        "email": "devin.ross@example.com",
        "region": "US",
        "manager_id": "M01",
        "role": "software_engineer"
    })
    world.put("leave_balances", {
        "balance_id": "BAL-E412-ANN",
        "employee_id": "E412",
        "leave_type": "annual",
        "allocated_days": 20,
        "used_days": 10,
        "reserved_days": 0,
        "available_days": 10
    })
